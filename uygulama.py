from flask import Flask, jsonify, render_template_string
import requests
import threading
import time
import os
import websocket
import json
import random
import string
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
APP_VERSION = "7.1-MOBILE-RADAR"

TV_URL = "https://scanner.tradingview.com/turkey/scan"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/json",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/"
}

# Ücretsiz toplu piyasa kaynağı için güvenli en hızlı yenileme aralığı.
# Her hisse için ayrı ayrı bağlantı açmak yerine bütün BIST listesini tek
# istekte yeniliyoruz. Böylece yaklaşık 650 hissenin tamamı aynı anda taranır.
MARKET_REFRESH_SECONDS = min(
    60, max(10, int(os.getenv("MARKET_REFRESH_SECONDS", "10")))
)
MARKET_BACKGROUND_ENABLED = os.getenv(
    "MARKET_BACKGROUND_ENABLED", "1"
).strip().lower() not in {"0", "false", "no"}

CACHE = {
    "stocks": [],
    "updated": 0,
    "last_error": None
}
CACHE_LOCK = threading.RLock()
MARKET_FETCH_LOCK = threading.Lock()
MARKET_WORKER_LOCK = threading.Lock()
MARKET_WORKER_STARTED = False

LIVE_CACHE = {}
LIVE_LOCK = threading.Lock()

# Halka acik fiyat/hacim verisinden uretilen OLASI kurumsal hareket esikleri.
# Bu modul gercek MKK virman kaydi veya kurum isimleri uretmez.
VIRMAN_MIN_RELATIVE_VOLUME = 2.50
VIRMAN_MIN_TRANSACTION_TL = 50_000_000
VIRMAN_MIN_ABNORMAL_TL = 25_000_000
VIRMAN_MIN_SCORE = 70
VIRMAN_ALERT_COOLDOWN = 3 * 60 * 60
VIRMAN_MAX_TELEGRAM = 5

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Günlük teknik izleme sepeti Telegram'a yalnızca kullanıcı Render ortam
# değişkeninden özellikle açarsa gider. Varsayılan kapalıdır.
TELEGRAM_DAILY_BASKET_ENABLED = os.getenv(
    "TELEGRAM_DAILY_BASKET_ENABLED", "0"
).strip().lower() in {"1", "true", "yes", "on"}
try:
    TELEGRAM_DAILY_BASKET_HOUR = min(
        23, max(0, int(os.getenv("TELEGRAM_DAILY_BASKET_HOUR", "10")))
    )
except (TypeError, ValueError):
    TELEGRAM_DAILY_BASKET_HOUR = 10
TURKEY_TZ = timezone(timedelta(hours=3))
DAILY_BASKET_LAST_SENT = {"date": ""}
DAILY_BASKET_LOCK = threading.Lock()

VIRMAN_LAST_SENT = {}
VIRMAN_ALERT_LOCK = threading.Lock()

# Lisansli canli AKD / Takas saglayicisi icin genel REST baglantisi.
# Ornek URL: https://saglayici.example/akd/{symbol}
AKD_API_URL = os.getenv("AKD_API_URL", "").strip()
TAKAS_API_URL = os.getenv("TAKAS_API_URL", "").strip()
MARKET_DATA_API_KEY = os.getenv("MARKET_DATA_API_KEY", "").strip()
MARKET_DATA_API_HEADER = os.getenv(
    "MARKET_DATA_API_HEADER", "Authorization"
).strip()
MARKET_DATA_API_PREFIX = os.getenv(
    "MARKET_DATA_API_PREFIX", "Bearer"
).strip()
AKD_SYMBOL_PARAM = os.getenv("AKD_SYMBOL_PARAM", "symbol").strip()
AKD_PROVIDER_NAME = os.getenv("AKD_PROVIDER_NAME", "Lisanslı veri sağlayıcı").strip()
AKD_CACHE_SECONDS = max(1, int(os.getenv("AKD_CACHE_SECONDS", "5")))
VIRMAN_MIN_TRANSFER_LOT = max(
    1, int(os.getenv("VIRMAN_MIN_TRANSFER_LOT", "100000"))
)

PRO_DATA_CACHE = {}
PRO_DATA_LOCK = threading.Lock()

COLUMNS = [
    "name",
    "description",
    "close",
    "change",
    "volume",
    "relative_volume_10d_calc",
    "RSI",
    "MACD.macd",
    "MACD.signal",
    "EMA20",
    "EMA50",
    "SMA20",
    "Recommend.All",
    "market_cap_basic"
]


def puanla(s):
    score = 0

    rsi = s.get("rsi")
    rv = s.get("relative_volume")
    price = s.get("price")
    ema20 = s.get("ema20")
    ema50 = s.get("ema50")
    sma20 = s.get("sma20")
    macd = s.get("macd")
    macd_signal = s.get("macd_signal")
    rec = s.get("recommend")
    change = s.get("change")

    if rsi is not None:
        if 55 <= rsi <= 70:
            score += 30
        elif 50 <= rsi < 55:
            score += 18
        elif 70 < rsi <= 75:
            score += 8

    if rv is not None:
        if rv >= 2:
            score += 30
        elif rv >= 1.5:
            score += 25
        elif rv >= 1.2:
            score += 12

    if price and ema20 and ema50:
        if price > ema20 > ema50:
            score += 25
        elif price > ema20:
            score += 12

    if macd is not None and macd_signal is not None and macd > macd_signal:
        score += 20

    if rec is not None:
        if rec >= 0.5:
            score += 20
        elif rec >= 0.2:
            score += 10

    if change is not None:
        if 0.5 <= change <= 6:
            score += 15
        elif 0 < change < 0.5:
            score += 7
        elif 6 < change < 9.9:
            score += 8

    if price and sma20 and price > sma20:
        score += 10

    return min(score, 150)


def sinyal_adi(score):
    if score >= 120:
        return "ÇOK GÜÇLÜ"
    if score >= 110:
        return "GÜÇLÜ"
    if score >= 90:
        return "İZLE"
    if score >= 70:
        return "ORTA"
    return "ZAYIF"


def sayi(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def fiyat_yuvarla(value):
    """Fiyatı BIST ekranı için okunur hassasiyette yuvarlar."""
    value = sayi(value)
    if value < 1:
        return round(value, 4)
    if value < 10:
        return round(value, 3)
    return round(value, 2)


def teknik_profil(stock):
    """Mevcut ücretsiz fiyat/hacim alanlarından şeffaf teknik profil üretir."""
    price = sayi(stock.get("price"))
    change = sayi(stock.get("change"))
    rsi = sayi(stock.get("rsi"))
    relative_volume = sayi(stock.get("relative_volume"))
    ema20 = sayi(stock.get("ema20"))
    ema50 = sayi(stock.get("ema50"))
    macd = sayi(stock.get("macd"))
    macd_signal = sayi(stock.get("macd_signal"))
    recommend = sayi(stock.get("recommend"))

    trend_up = price > 0 and ema20 > 0 and ema50 > 0 and price > ema20 > ema50
    above_ema20 = price > 0 and ema20 > 0 and price > ema20
    macd_positive = macd > macd_signal
    rsi_momentum = 54 <= rsi <= 72
    volume_supported = relative_volume >= 1.2
    volume_burst = relative_volume >= 2 and above_ema20

    score = 0
    reasons = []

    if trend_up:
        score += 30
        reasons.append("Fiyat EMA20 ve EMA50 üzerinde")
    elif above_ema20:
        score += 16
        reasons.append("Fiyat EMA20 üzerinde")

    if rsi_momentum:
        score += 22
        reasons.append("RSI pozitif momentum bölgesinde")
    elif 48 <= rsi < 54:
        score += 12
        reasons.append("RSI toparlanma bölgesinde")
    elif rsi > 72:
        score += 5
        reasons.append("RSI güçlü ama yüksek bölgede")

    if relative_volume >= 2.5:
        score += 24
        reasons.append("Göreli hacim güçlü")
    elif volume_supported:
        score += 15
        reasons.append("Hacim ortalama üzerinde")

    if macd_positive:
        score += 18
        reasons.append("MACD alım yönünü destekliyor")

    if 0.25 <= change <= 6:
        score += 10
        reasons.append("Günlük hareket dengeli pozitif")
    elif 0 < change < 0.25:
        score += 4
    elif change < -4:
        score -= 8

    if recommend >= 0.2:
        score += 6

    score = max(0, min(100, int(round(score))))
    breakout = (
        trend_up
        and relative_volume >= 1.25
        and macd_positive
        and 0.25 <= change <= 8
    )
    momentum = trend_up and rsi_momentum and macd_positive and score >= 65
    pullback = (
        above_ema20
        and ema20 > 0
        and price <= ema20 * 1.035
        and 44 <= rsi <= 60
        and macd_positive
    )

    if breakout:
        label = "HACİMLİ KIRILIM"
    elif momentum:
        label = "POZİTİF MOMENTUM"
    elif pullback:
        label = "TOPARLANMA İZLE"
    elif volume_burst:
        label = "HACİM HAREKETİ"
    else:
        label = "NÖTR İZLE"

    if abs(change) >= 6 or relative_volume >= 4 or rsi >= 74:
        risk_level = "YÜKSEK"
    elif abs(change) >= 3 or relative_volume >= 2:
        risk_level = "ORTA"
    else:
        risk_level = "DENGELİ"

    return {
        "score": score,
        "label": label,
        "trend": "YUKARI" if trend_up else ("EMA20 ÜSTÜ" if above_ema20 else "ZAYIF"),
        "breakout": breakout,
        "momentum": momentum,
        "pullback": pullback,
        "volume_burst": volume_burst,
        "risk_level": risk_level,
        "reasons": reasons[:4]
    }


def sepet_puani(stock):
    """Günlük teknik izleme sepetini sıralamak için kullanılan şeffaf puan."""
    technical = stock.get("technical") or teknik_profil(stock)
    price = sayi(stock.get("price"))
    volume = sayi(stock.get("volume"))
    relative_volume = sayi(stock.get("relative_volume"))
    change = sayi(stock.get("change"))

    score = technical.get("score", 0) + min(12, int(max(0, relative_volume - 1) * 6))
    if technical.get("breakout"):
        score += 10
    if technical.get("momentum"):
        score += 6
    if stock.get("score", 0) >= 110:
        score += 6
    if price * volume >= 100_000_000:
        score += 5
    if not 0 <= change <= 9:
        score -= 12

    return max(0, min(150, int(round(score))))


def gunluk_teknik_sepet(stocks, limit=5):
    """Yatırım tavsiyesi olmayan, gün içi takip için otomatik teknik sepet."""
    eligible = []

    for stock in stocks:
        technical = stock.get("technical") or teknik_profil(stock)
        price = sayi(stock.get("price"))
        change = sayi(stock.get("change"))

        if price <= 0 or change < -5.5 or change > 9.5:
            continue
        if technical.get("score", 0) < 62:
            continue
        if not (
            technical.get("breakout")
            or technical.get("momentum")
            or technical.get("pullback")
        ):
            continue

        item = dict(stock)
        item["technical"] = technical
        item["basket_score"] = sepet_puani(item)
        eligible.append(item)

    # Piyasa zayıfsa sepet boş kalmasın; daha yumuşak teknik eşiğe düşer.
    if len(eligible) < limit:
        eligible = []
        for stock in stocks:
            technical = stock.get("technical") or teknik_profil(stock)
            price = sayi(stock.get("price"))
            change = sayi(stock.get("change"))
            if price <= 0 or change < -6 or change > 9.5:
                continue
            if technical.get("score", 0) < 50:
                continue
            item = dict(stock)
            item["technical"] = technical
            item["basket_score"] = sepet_puani(item)
            eligible.append(item)

    eligible.sort(
        key=lambda stock: (
            stock.get("basket_score", 0),
            (stock.get("technical") or {}).get("score", 0),
            sayi(stock.get("relative_volume")),
            sayi(stock.get("market_cap"))
        ),
        reverse=True
    )

    basket = []
    for rank, stock in enumerate(eligible[:limit], start=1):
        price = sayi(stock.get("price"))
        technical = stock.get("technical") or {}
        risk = technical.get("risk_level", "DENGELİ")
        band = 0.006 if risk == "DENGELİ" else (0.009 if risk == "ORTA" else 0.013)
        stop_rate = 0.975 if risk == "DENGELİ" else (0.965 if risk == "ORTA" else 0.95)

        basket.append({
            "rank": rank,
            "symbol": stock.get("symbol"),
            "description": stock.get("description", ""),
            "price": stock.get("price"),
            "change": stock.get("change"),
            "relative_volume": stock.get("relative_volume"),
            "score": stock.get("score"),
            "technical_score": technical.get("score", 0),
            "basket_score": stock.get("basket_score", 0),
            "label": technical.get("label", "NÖTR İZLE"),
            "trend": technical.get("trend", "-"),
            "risk_level": risk,
            "reasons": technical.get("reasons", []),
            "watch_band_low": fiyat_yuvarla(price * (1 - band)),
            "watch_band_high": fiyat_yuvarla(price * (1 + band)),
            "risk_stop": fiyat_yuvarla(price * stop_rate),
            "watch_target_1": fiyat_yuvarla(price * (1 + (1 - stop_rate))),
            "watch_target_2": fiyat_yuvarla(price * (1 + (1 - stop_rate) * 2))
        })

    return basket


def piyasa_genisligi(stocks):
    total = len(stocks)
    rising = sum(1 for stock in stocks if sayi(stock.get("change")) > 0)
    falling = sum(1 for stock in stocks if sayi(stock.get("change")) < 0)
    flat = max(0, total - rising - falling)
    momentum = sum(
        1 for stock in stocks
        if (stock.get("technical") or {}).get("momentum")
    )
    breakouts = sum(
        1 for stock in stocks
        if (stock.get("technical") or {}).get("breakout")
    )
    volume_bursts = sum(
        1 for stock in stocks
        if (stock.get("technical") or {}).get("volume_burst")
    )
    ratio = round(rising / max(1, falling), 2)

    if ratio >= 1.35:
        status = "POZİTİF GENİŞLİK"
    elif ratio <= 0.75:
        status = "ZAYIF GENİŞLİK"
    else:
        status = "DENGELİ PİYASA"

    return {
        "total": total,
        "rising": rising,
        "falling": falling,
        "flat": flat,
        "advance_decline_ratio": ratio,
        "momentum": momentum,
        "breakouts": breakouts,
        "volume_bursts": volume_bursts,
        "status": status
    }


def virman_analizi(s):
    """Fiyat/hacim verisinden olasi kurumsal hareket skoru uretir."""
    price = sayi(s.get("price"))
    volume = sayi(s.get("volume"))
    relative_volume = sayi(s.get("relative_volume"))
    change = sayi(s.get("change"))
    ema20 = sayi(s.get("ema20"))
    macd = sayi(s.get("macd"))
    macd_signal = sayi(s.get("macd_signal"))

    transaction_value = max(0.0, price * volume)
    normal_volume = volume / relative_volume if relative_volume > 0 else volume
    abnormal_volume = max(0.0, volume - normal_volume)
    abnormal_value = max(0.0, price * abnormal_volume)

    score = 0
    reasons = []

    if relative_volume >= 6:
        score += 35
        reasons.append("Göreli hacim 6x ve üzerinde")
    elif relative_volume >= 4:
        score += 32
        reasons.append("Göreli hacim 4x ve üzerinde")
    elif relative_volume >= 3:
        score += 28
        reasons.append("Göreli hacim 3x ve üzerinde")
    elif relative_volume >= VIRMAN_MIN_RELATIVE_VOLUME:
        score += 24
        reasons.append("Göreli hacim 2,5x ve üzerinde")

    if abnormal_value >= 1_000_000_000:
        score += 30
        reasons.append("Normal üstü hacim 1 milyar TL üzerinde")
    elif abnormal_value >= 500_000_000:
        score += 27
        reasons.append("Normal üstü hacim 500 milyon TL üzerinde")
    elif abnormal_value >= 200_000_000:
        score += 23
        reasons.append("Normal üstü hacim 200 milyon TL üzerinde")
    elif abnormal_value >= 100_000_000:
        score += 20
        reasons.append("Normal üstü hacim 100 milyon TL üzerinde")
    elif abnormal_value >= 50_000_000:
        score += 16
        reasons.append("Normal üstü hacim 50 milyon TL üzerinde")
    elif abnormal_value >= VIRMAN_MIN_ABNORMAL_TL:
        score += 12
        reasons.append("Normal üstü hacim 25 milyon TL üzerinde")

    if transaction_value >= 2_000_000_000:
        score += 15
        reasons.append("İşlem büyüklüğü 2 milyar TL üzerinde")
    elif transaction_value >= 1_000_000_000:
        score += 13
        reasons.append("İşlem büyüklüğü 1 milyar TL üzerinde")
    elif transaction_value >= 500_000_000:
        score += 11
        reasons.append("İşlem büyüklüğü 500 milyon TL üzerinde")
    elif transaction_value >= 200_000_000:
        score += 9
        reasons.append("İşlem büyüklüğü 200 milyon TL üzerinde")
    elif transaction_value >= VIRMAN_MIN_TRANSACTION_TL:
        score += 7
        reasons.append("İşlem büyüklüğü 50 milyon TL üzerinde")

    bullish = (
        change >= 0.25
        and price > 0
        and ema20 > 0
        and price > ema20
        and macd > macd_signal
    )
    bearish = (
        change <= -0.25
        and price > 0
        and ema20 > 0
        and price < ema20
        and macd < macd_signal
    )
    quiet_big_volume = abs(change) <= 0.60 and relative_volume >= 3

    if bullish:
        score += 20
        direction = "ALIM YÖNLÜ OLASI TOPLAMA"
        reasons.append("Fiyat, EMA20 ve MACD alım yönünü destekliyor")
    elif bearish:
        score += 20
        direction = "SATIŞ YÖNLÜ OLASI DAĞITIM"
        reasons.append("Fiyat, EMA20 ve MACD satış yönünü destekliyor")
    elif quiet_big_volume:
        score += 16
        direction = "YÖNÜ BELİRSİZ BÜYÜK HACİM"
        reasons.append("Yüksek hacme rağmen fiyat hareketi sınırlı")
    elif change > 0:
        score += 10
        direction = "ALIM YÖNLÜ İZLE"
    elif change < 0:
        score += 10
        direction = "SATIŞ YÖNLÜ İZLE"
    else:
        score += 7
        direction = "YÖN BELİRSİZ"

    score = max(0, min(100, int(round(score))))
    candidate = (
        relative_volume >= VIRMAN_MIN_RELATIVE_VOLUME
        and transaction_value >= VIRMAN_MIN_TRANSACTION_TL
        and abnormal_value >= VIRMAN_MIN_ABNORMAL_TL
        and score >= VIRMAN_MIN_SCORE
        and abs(change) <= 6
    )

    if score >= 85:
        level = "ÇOK GÜÇLÜ ADAY"
    elif score >= VIRMAN_MIN_SCORE:
        level = "GÜÇLÜ ADAY"
    elif score >= 55:
        level = "İZLE"
    else:
        level = "NORMAL"

    return {
        "candidate": candidate,
        "score": score,
        "level": level,
        "direction": direction,
        "transaction_value": int(transaction_value),
        "normal_volume": int(max(0.0, normal_volume)),
        "abnormal_volume": int(abnormal_volume),
        "abnormal_value": int(abnormal_value),
        "reasons": reasons
    }


def fetch_market():
    """Bütün BIST hisselerini tek toplu tarama isteğiyle yeniler."""
    with MARKET_FETCH_LOCK:
        payload = {
            "filter": [
                {"left": "exchange", "operation": "equal", "right": "BIST"}
            ],
            "options": {"lang": "tr"},
            "markets": ["turkey"],
            "symbols": {"query": {"types": []}, "tickers": []},
            "columns": COLUMNS,
            "sort": {"sortBy": "volume", "sortOrder": "desc"},
            "range": [0, 9999]
        }

        r = requests.post(
            TV_URL,
            headers=HEADERS,
            json=payload,
            timeout=30
        )
        r.raise_for_status()

        raw = r.json()
        stocks = []

        for row in raw.get("data", []):
            d = row.get("d", [])

            if len(d) < len(COLUMNS):
                continue

            stock = {
                "symbol": d[0],
                "description": d[1] or "",
                "price": d[2],
                "change": d[3],
                "volume": d[4],
                "relative_volume": d[5],
                "rsi": d[6],
                "macd": d[7],
                "macd_signal": d[8],
                "ema20": d[9],
                "ema50": d[10],
                "sma20": d[11],
                "recommend": d[12],
                "market_cap": d[13]
            }

            stock["score"] = puanla(stock)
            stock["signal"] = sinyal_adi(stock["score"])
            stock["technical"] = teknik_profil(stock)
            stock["institutional"] = virman_analizi(stock)

            stocks.append(stock)

        # Sağlayıcı geçici olarak boş/bozuk yanıt dönerse ekrandaki son
        # başarılı listeyi silmeyelim.
        if not stocks:
            raise RuntimeError("BIST taramasından geçerli hisse listesi gelmedi")

        with CACHE_LOCK:
            CACHE["stocks"] = stocks
            CACHE["updated"] = int(time.time())
            CACHE["last_error"] = None

        return stocks


def get_market():
    with CACHE_LOCK:
        stocks = CACHE["stocks"]
        updated = CACHE["updated"]

    stale = not stocks or time.time() - updated > MARKET_REFRESH_SECONDS
    if not stale:
        return stocks

    # Arka plan yenilemesi sürerken kullanıcıya son başarılı listeyi hemen
    # döndür; sayfanın 30 saniyelik sağlayıcı zaman aşımını beklemesini önle.
    if stocks and MARKET_FETCH_LOCK.locked():
        return stocks

    try:
        return fetch_market()
    except Exception as e:
        print("Veri hatası:", e)
        with CACHE_LOCK:
            CACHE["last_error"] = type(e).__name__
            return CACHE["stocks"]


def ilk_deger(row, names, default=None):
    if not isinstance(row, dict):
        return default
    lowered = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
        value = lowered.get(str(name).lower())
        if value is not None:
            return value
    return default


def veri_satirlari(payload, depth=0):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict) or depth > 3:
        return []

    for key in (
        "institutions", "brokers", "rows", "items", "data",
        "result", "results", "records", "takas", "akd"
    ):
        value = ilk_deger(payload, [key])
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = veri_satirlari(value, depth + 1)
            if nested:
                return nested
    return []


def kurum_adi(row):
    value = ilk_deger(row, [
        "institution", "institution_name", "broker", "broker_name",
        "name", "kurum", "kurum_adi", "kurumAdi", "araci_kurum"
    ], "")
    return str(value or "").strip()


def akd_normalize(payload):
    rows = []
    for raw in veri_satirlari(payload):
        name = kurum_adi(raw)
        if not name:
            continue

        buy = sayi(ilk_deger(raw, [
            "buy", "buy_lot", "buy_lots", "buyLot", "alis", "alış",
            "alis_lot", "alisLot"
        ]))
        sell = sayi(ilk_deger(raw, [
            "sell", "sell_lot", "sell_lots", "sellLot", "satis", "satış",
            "satis_lot", "satisLot"
        ]))
        net_raw = ilk_deger(raw, [
            "net", "net_lot", "net_lots", "netLot", "net_adet", "netAdet"
        ])
        net = sayi(net_raw, buy - sell) if net_raw is not None else buy - sell
        net_tl = sayi(ilk_deger(raw, [
            "net_tl", "net_value", "netValue", "net_tutar", "netTutar"
        ]))
        avg = sayi(ilk_deger(raw, [
            "average", "average_price", "avg_price", "avgPrice",
            "ortalama", "ortalama_fiyat"
        ]))

        rows.append({
            "institution": name,
            "buy": int(buy),
            "sell": int(sell),
            "net": int(net),
            "net_tl": int(net_tl),
            "average": avg
        })

    rows.sort(key=lambda x: abs(x["net"]), reverse=True)
    return rows


def takas_normalize(payload):
    rows = []
    for raw in veri_satirlari(payload):
        name = kurum_adi(raw)
        if not name:
            continue

        holding = sayi(ilk_deger(raw, [
            "holding", "balance", "quantity", "lot", "lots", "adet",
            "bakiye", "saklama"
        ]))
        change = sayi(ilk_deger(raw, [
            "change", "change_lot", "changeLot", "difference", "diff",
            "degisim", "değişim", "fark", "net_change"
        ]))
        percent = sayi(ilk_deger(raw, [
            "percent", "percentage", "share", "ratio", "oran", "pay"
        ]))

        rows.append({
            "institution": name,
            "holding": int(holding),
            "change": int(change),
            "percent": percent
        })

    rows.sort(key=lambda x: abs(x["change"]), reverse=True)
    return rows


def provider_headers():
    headers = {
        "Accept": "application/json",
        "User-Agent": "BIST-Veri-Terminali/6.0"
    }
    if MARKET_DATA_API_KEY:
        value = MARKET_DATA_API_KEY
        if MARKET_DATA_API_PREFIX:
            value = f"{MARKET_DATA_API_PREFIX} {value}"
        headers[MARKET_DATA_API_HEADER] = value
    return headers


def professional_data(kind, symbol, force=False):
    symbol = symbol.upper().strip()
    url_template = AKD_API_URL if kind == "akd" else TAKAS_API_URL
    cache_key = f"{kind}:{symbol}"

    if not url_template:
        return {
            "ok": False,
            "configured": False,
            "rows": [],
            "error": "Lisanslı veri bağlantısı henüz tanımlanmadı."
        }

    with PRO_DATA_LOCK:
        old = PRO_DATA_CACHE.get(cache_key)
        if (
            not force
            and old
            and time.time() - old.get("cached_at", 0) < AKD_CACHE_SECONDS
        ):
            return old

    try:
        params = None
        if "{symbol}" in url_template:
            url = url_template.replace("{symbol}", symbol)
        else:
            url = url_template
            params = {AKD_SYMBOL_PARAM: symbol}

        response = requests.get(
            url,
            params=params,
            headers=provider_headers(),
            timeout=12
        )
        response.raise_for_status()
        payload = response.json()
        rows = akd_normalize(payload) if kind == "akd" else takas_normalize(payload)

        result = {
            "ok": True,
            "configured": True,
            "provider": AKD_PROVIDER_NAME,
            "symbol": symbol,
            "rows": rows,
            "updated": int(time.time()),
            "cached_at": time.time()
        }
    except Exception as exc:
        result = {
            "ok": False,
            "configured": True,
            "provider": AKD_PROVIDER_NAME,
            "symbol": symbol,
            "rows": [],
            "error": f"Veri sağlayıcı hatası: {type(exc).__name__}",
            "updated": int(time.time()),
            "cached_at": time.time()
        }

    with PRO_DATA_LOCK:
        PRO_DATA_CACHE[cache_key] = result
    return result


def kurum_key(name):
    return "".join(ch for ch in str(name).upper() if ch.isalnum())


def gercek_veriden_virman_eslestir(akd_rows, takas_rows):
    """AKD ile açıklanamayan, birbirine yakın takas giriş/çıkışlarını eşler."""
    akd_net = {kurum_key(x["institution"]): sayi(x.get("net")) for x in akd_rows}
    incoming = [x for x in takas_rows if sayi(x.get("change")) >= VIRMAN_MIN_TRANSFER_LOT]
    outgoing = [x for x in takas_rows if sayi(x.get("change")) <= -VIRMAN_MIN_TRANSFER_LOT]
    matches = []

    for source in outgoing:
        out_lot = abs(sayi(source.get("change")))
        for target in incoming:
            in_lot = abs(sayi(target.get("change")))
            biggest = max(out_lot, in_lot)
            if biggest <= 0:
                continue

            difference_pct = abs(out_lot - in_lot) / biggest * 100
            if difference_pct > 3:
                continue

            source_akd = abs(akd_net.get(kurum_key(source["institution"]), 0))
            target_akd = abs(akd_net.get(kurum_key(target["institution"]), 0))
            explained_by_market = (
                source_akd >= out_lot * 0.50
                or target_akd >= in_lot * 0.50
            )

            score = 50
            score += max(0, int(20 - difference_pct * 5))
            if biggest >= 5_000_000:
                score += 15
            elif biggest >= 1_000_000:
                score += 11
            elif biggest >= 500_000:
                score += 7
            else:
                score += 4
            if not explained_by_market:
                score += 15

            score = min(100, score)
            matches.append({
                "from": source["institution"],
                "to": target["institution"],
                "lot": int((out_lot + in_lot) / 2),
                "difference_percent": round(difference_pct, 2),
                "score": score,
                "market_explained": explained_by_market,
                "label": "OLASI VİRMAN" if score >= 75 else "KONTROL ET"
            })

    matches.sort(key=lambda x: (x["score"], x["lot"]), reverse=True)
    return matches[:10]


def tv_sid(prefix):
    return prefix + "_" + "".join(
        random.choice(string.ascii_lowercase) for _ in range(12)
    )


def tv_frame(method, params):
    body = json.dumps(
        {"m": method, "p": params},
        separators=(",", ":")
    )
    return f"~m~{len(body)}~m~{body}"


def fetch_live_quote(symbol, wait_seconds=4):
    symbol = symbol.upper().strip()
    full_symbol = f"BIST:{symbol}"

    qs = tv_sid("qs")

    ws = None

    try:
        ws = websocket.create_connection(
            "wss://data.tradingview.com/socket.io/websocket",
            origin="https://data.tradingview.com",
            timeout=5
        )

        ws.send(tv_frame(
            "set_auth_token",
            ["unauthorized_user_token"]
        ))

        ws.send(tv_frame(
            "quote_create_session",
            [qs]
        ))

        ws.send(tv_frame(
            "quote_set_fields",
            [
                qs,
                "short_name",
                "description",
                "lp",
                "bid",
                "ask",
                "bid_size",
                "ask_size",
                "ch",
                "chp",
                "volume"
            ]
        ))

        ws.send(tv_frame(
            "quote_add_symbols",
            [qs, full_symbol]
        ))

        result = {
            "symbol": symbol,
            "last": None,
            "bid": None,
            "ask": None,
            "bid_size": None,
            "ask_size": None,
            "change_percent": None,
            "volume": None,
            "updated": int(time.time())
        }

        end = time.time() + wait_seconds

        while time.time() < end:
            try:
                raw = ws.recv()

                if "~h~" in raw:
                    ws.send(raw)
                    continue

                parts = raw.split("~m~")

                for part in parts:
                    part = part.strip()

                    if not part.startswith("{"):
                        continue

                    try:
                        j = json.loads(part)
                    except:
                        continue

                    if j.get("m") != "qsd":
                        continue

                    pp = j.get("p", [])

                    if len(pp) < 2:
                        continue

                    obj = pp[1]
                    v = obj.get("v", {})

                    if v.get("lp") is not None:
                        result["last"] = v.get("lp")

                    if v.get("bid") is not None:
                        result["bid"] = v.get("bid")

                    if v.get("ask") is not None:
                        result["ask"] = v.get("ask")

                    if v.get("bid_size") is not None:
                        result["bid_size"] = v.get("bid_size")

                    if v.get("ask_size") is not None:
                        result["ask_size"] = v.get("ask_size")

                    if v.get("chp") is not None:
                        result["change_percent"] = v.get("chp")

                    if v.get("volume") is not None:
                        result["volume"] = v.get("volume")

                    if result["bid"] is not None and result["ask"] is not None:
                        with LIVE_LOCK:
                            LIVE_CACHE[symbol] = result

                        return result

            except websocket.WebSocketTimeoutException:
                continue

        with LIVE_LOCK:
            old = LIVE_CACHE.get(symbol, {})

            for k, v in old.items():
                if result.get(k) is None:
                    result[k] = v

            LIVE_CACHE[symbol] = result

        return result

    except Exception as e:
        print("Canlı veri hatası:", symbol, e)

        with LIVE_LOCK:
            return LIVE_CACHE.get(symbol, {
                "symbol": symbol,
                "last": None,
                "bid": None,
                "ask": None,
                "bid_size": None,
                "ask_size": None,
                "change_percent": None,
                "volume": None,
                "updated": int(time.time())
            })

    finally:
        if ws:
            try:
                ws.close()
            except:
                pass


def tl_yaz(value):
    value = sayi(value)
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f} milyar TL"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f} milyon TL"
    return f"{value:,.0f} TL"


def virman_telegram_alarmlari(stocks):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return []

    now = time.time()
    candidates = sorted(
        [
            s for s in stocks
            if s.get("institutional", {}).get("candidate")
        ],
        key=lambda s: (
            s.get("institutional", {}).get("score", 0),
            s.get("institutional", {}).get("abnormal_value", 0)
        ),
        reverse=True
    )

    with VIRMAN_ALERT_LOCK:
        fresh = [
            s for s in candidates
            if now - VIRMAN_LAST_SENT.get(s.get("symbol", ""), 0)
            >= VIRMAN_ALERT_COOLDOWN
        ][:VIRMAN_MAX_TELEGRAM]

    if not fresh:
        return []

    lines = [
        "⚠️ OLASI KURUMSAL / VİRMAN RADARI",
        ""
    ]
    for stock in fresh:
        info = stock["institutional"]
        lines.extend([
            f"📌 {stock['symbol']} — {info['score']}/100",
            f"{info['direction']}",
            f"Fiyat: {sayi(stock.get('price')):.2f} TL | "
            f"Değişim: %{sayi(stock.get('change')):+.2f}",
            f"Göreli hacim: {sayi(stock.get('relative_volume')):.2f}x",
            f"İşlem: {tl_yaz(info['transaction_value'])}",
            f"Normal üstü: {tl_yaz(info['abnormal_value'])}",
            ""
        ])

    lines.extend([
        "Bu bildirim halka açık piyasa verisinden üretilen tahmindir.",
        "Gerçek virman teyidi için lisanslı AKD + T+2 takas gerekir.",
        "Yatırım tavsiyesi değildir."
    ])

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": "\n".join(lines)},
            timeout=15
        )
        response.raise_for_status()
    except Exception as exc:
        print("Virman Telegram hatası:", type(exc).__name__)
        return []

    sent = []
    with VIRMAN_ALERT_LOCK:
        for stock in fresh:
            symbol = stock.get("symbol", "")
            if symbol:
                VIRMAN_LAST_SENT[symbol] = now
                sent.append(symbol)
    return sent


def gunluk_sepet_telegram_raporu(stocks):
    """İsteğe bağlı, günde en fazla bir kez teknik sepet özeti gönderir."""
    if not (
        TELEGRAM_DAILY_BASKET_ENABLED
        and TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    ):
        return False

    now = datetime.now(TURKEY_TZ)
    today = now.strftime("%Y-%m-%d")
    if now.weekday() > 4 or now.hour < TELEGRAM_DAILY_BASKET_HOUR:
        return False

    with DAILY_BASKET_LOCK:
        if DAILY_BASKET_LAST_SENT.get("date") == today:
            return False

    basket = gunluk_teknik_sepet(stocks)
    if not basket:
        return False

    lines = [
        "📊 GÜNLÜK TEKNİK İZLEME SEPETİ",
        f"{now.strftime('%d.%m.%Y %H:%M')} • {len(basket)} hisse",
        ""
    ]
    for item in basket:
        lines.extend([
            f"{item['rank']}. {item['symbol']} — {item['label']}",
            f"Fiyat: {sayi(item['price']):.2f} TL | %{sayi(item['change']):+.2f}",
            f"Teknik: {item['technical_score']}/100 | Hacim: {sayi(item['relative_volume']):.2f}x",
            ""
        ])

    lines.extend([
        "Bu sepet otomatik teknik takip listesidir.",
        "Yatırım tavsiyesi değildir."
    ])

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": "\n".join(lines)},
            timeout=15
        )
        response.raise_for_status()
    except Exception as exc:
        print("Sepet Telegram hatası:", type(exc).__name__)
        return False

    with DAILY_BASKET_LOCK:
        DAILY_BASKET_LAST_SENT["date"] = today
    return True

def background_loop():
    while True:
        try:
            stocks = fetch_market()
            print(
                "BIST canlı tarama güncellendi:",
                len(stocks),
                "hisse /",
                MARKET_REFRESH_SECONDS,
                "sn"
            )
            virman_telegram_alarmlari(stocks)
            gunluk_sepet_telegram_raporu(stocks)
        except Exception as e:
            print("Arka plan veri hatası:", e)
            with CACHE_LOCK:
                CACHE["last_error"] = type(e).__name__

        time.sleep(MARKET_REFRESH_SECONDS)


def start_market_worker():
    """Gunicorn altında da tek bir canlı tarama iş parçacığı başlatır."""
    global MARKET_WORKER_STARTED

    if not MARKET_BACKGROUND_ENABLED:
        return

    with MARKET_WORKER_LOCK:
        if MARKET_WORKER_STARTED:
            return

        threading.Thread(
            target=background_loop,
            daemon=True,
            name="bist-canli-tarama"
        ).start()
        MARKET_WORKER_STARTED = True


# Render'da uygulama Gunicorn ile içe aktarıldığı için __main__ bloğu
# çalışmaz. İş parçacığını burada başlatmak canlı yenilemenin çalışması için
# gereklidir; fonksiyon kendi sürecinde yalnızca bir kez başlar.
start_market_worker()


@app.route("/api/market")
def api_market():
    stocks = get_market()
    daily_basket = gunluk_teknik_sepet(stocks)
    breadth = piyasa_genisligi(stocks)
    with CACHE_LOCK:
        updated = CACHE["updated"]
        last_error = CACHE["last_error"]

    response = jsonify({
        "ok": True,
        "count": len(stocks),
        "virman_count": sum(
            1 for s in stocks if s.get("institutional", {}).get("candidate")
        ),
        "stocks": stocks,
        "updated": updated,
        "refresh_seconds": MARKET_REFRESH_SECONDS,
        "last_error": last_error,
        "source": "Toplu BIST canlı tarama",
        "daily_basket": daily_basket,
        "market_breadth": breadth,
        "telegram_daily_basket_enabled": bool(
            TELEGRAM_DAILY_BASKET_ENABLED
            and TELEGRAM_BOT_TOKEN
            and TELEGRAM_CHAT_ID
        )
    })
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.route("/api/basket")
def api_basket():
    stocks = get_market()
    with CACHE_LOCK:
        updated = CACHE["updated"]

    response = jsonify({
        "ok": True,
        "title": "Günlük Teknik İzleme Sepeti",
        "basket": gunluk_teknik_sepet(stocks),
        "market_breadth": piyasa_genisligi(stocks),
        "updated": updated,
        "refresh_seconds": MARKET_REFRESH_SECONDS,
        "note": (
            "Sepet; trend, RSI, MACD, göreli hacim ve günlük hareketten "
            "oluşan kural tabanlı izleme listesidir; alım-satım tavsiyesi değildir."
        )
    })
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.route("/api/virman")
def api_virman():
    candidates = [
        s for s in get_market()
        if s.get("institutional", {}).get("candidate")
    ]
    candidates.sort(
        key=lambda s: (
            s.get("institutional", {}).get("score", 0),
            s.get("institutional", {}).get("abnormal_value", 0)
        ),
        reverse=True
    )
    return jsonify({
        "ok": True,
        "count": len(candidates),
        "candidates": candidates,
        "real_virman": False,
        "note": (
            "Halka açık fiyat ve hacim verisinden üretilen olası kurumsal "
            "hareket tahminidir; gerçek MKK virman kaydı değildir."
        ),
        "updated": CACHE["updated"]
    })


def public_provider_result(result):
    return {k: v for k, v in result.items() if k != "cached_at"}


@app.route("/api/pro-status")
def api_pro_status():
    return jsonify({
        "ok": True,
        "akd_configured": bool(AKD_API_URL),
        "takas_configured": bool(TAKAS_API_URL),
        "provider": AKD_PROVIDER_NAME if (AKD_API_URL or TAKAS_API_URL) else None,
        "estimated_radar": True,
        "real_virman_requires_both": True
    })


@app.route("/api/akd/<symbol>")
def api_akd(symbol):
    result = public_provider_result(professional_data("akd", symbol))
    result["real_akd"] = bool(result.get("ok") and result.get("rows"))
    return jsonify(result)


@app.route("/api/takas/<symbol>")
def api_takas(symbol):
    result = public_provider_result(professional_data("takas", symbol))
    result["real_takas"] = bool(result.get("ok") and result.get("rows"))
    return jsonify(result)


@app.route("/api/virman-check/<symbol>")
def api_virman_check(symbol):
    akd = professional_data("akd", symbol)
    takas = professional_data("takas", symbol)

    if not akd.get("ok") or not takas.get("ok"):
        return jsonify({
            "ok": False,
            "configured": bool(AKD_API_URL and TAKAS_API_URL),
            "symbol": symbol.upper().strip(),
            "matches": [],
            "akd_error": akd.get("error"),
            "takas_error": takas.get("error"),
            "note": "Gerçek karşılaştırma için hem AKD hem takas bağlantısı gerekir."
        })

    matches = gercek_veriden_virman_eslestir(
        akd.get("rows", []), takas.get("rows", [])
    )
    return jsonify({
        "ok": True,
        "configured": True,
        "symbol": symbol.upper().strip(),
        "matches": matches,
        "count": len(matches),
        "provider": AKD_PROVIDER_NAME,
        "note": (
            "AKD ile açıklanamayan, birbirine yakın takas giriş ve çıkışları "
            "eşleştirilmiştir. Sonuç olası virman işaretidir; kesin yatırımcı "
            "kimliği göstermez."
        ),
        "updated": int(time.time())
    })


@app.route("/api/stock/<symbol>")
def api_stock(symbol):
    symbol = symbol.upper().strip()

    for s in get_market():
        if s["symbol"].upper() == symbol:
            return jsonify({
                "ok": True,
                "stock": s,
                "realtime_depth": False,
                "real_akd": False,
                "real_takas": False,
                "real_virman": False,
                "estimated_institutional_movement": True
            })

    return jsonify({
        "ok": False,
        "error": "Hisse bulunamadı"
    }), 404


KUR_CACHE = {"data": None, "updated": 0}
MARKET_TICKER_CACHE = {"data": None, "updated": 0}
MARKET_TICKER_LOCK = threading.Lock()

def fetch_kurlar():
    if KUR_CACHE["data"] and time.time() - KUR_CACHE["updated"] < 30:
        return KUR_CACHE["data"]

    out = {
        "usd": None,
        "eur": None,
        "gram": None,
        "ceyrek": None,
        "updated": int(time.time())
    }

    try:
        r = requests.get(
            "https://dolartoday.org/api/rates?symbols=USD,EUR,GA",
            timeout=10
        )
        r.raise_for_status()
        j = r.json()
        rates = j.get("rates", {})

        usd = rates.get("USD") or rates.get("usd")
        eur = rates.get("EUR") or rates.get("eur")
        ga  = rates.get("GA") or rates.get("ga")

        out["usd"] = usd
        out["eur"] = eur
        out["gram"] = ga

    except Exception as e:
        print("Kur API hatası:", e)

    # Çeyrek altın için anahtarsız public endpoint denemesi.
    try:
        r = requests.get(
            "https://api.apinoktam.erenozdemir.com.tr/public/v1/altin",
            timeout=10
        )
        if r.ok:
            j = r.json()
            items = (
                j.get("data", {}).get("kalemler")
                or j.get("data")
                or j.get("kalemler")
                or []
            )

            if isinstance(items, list):
                for x in items:
                    name = str(
                        x.get("sembol")
                        or x.get("tur")
                        or x.get("name")
                        or x.get("isim")
                        or ""
                    ).lower()

                    if "ceyrek" in name or "çeyrek" in name or name=="cey":
                        out["ceyrek"] = x
                        break
    except Exception as e:
        print("Çeyrek API hatası:", e)

    KUR_CACHE["data"] = out
    KUR_CACHE["updated"] = int(time.time())
    return out


@app.route("/api/kurlar")
def api_kurlar():
    return jsonify({
        "ok": True,
        "data": fetch_kurlar()
    })


def ticker_sayi(value):
    """Farklı ücretsiz kur uç noktalarından ortak fiyat alanını ayıklar."""
    if isinstance(value, dict):
        value = ilk_deger(
            value,
            (
                "sell", "satis", "satış", "Selling", "price", "last",
                "close", "value", "rate", "TL"
            )
        )
    return sayi(value, None)


def fetch_bist100_quote():
    """BIST 100 endeksini hisse taramasından bağımsız ve güvenli çeker."""
    payload = {
        "symbols": {"tickers": ["BIST:XU100"], "query": {"types": []}},
        "columns": ["name", "description", "close", "change"]
    }
    try:
        response = requests.post(
            TV_URL,
            headers=HEADERS,
            json=payload,
            timeout=12
        )
        response.raise_for_status()
        rows = response.json().get("data", [])
        if not rows:
            return {"price": None, "change": None}
        values = rows[0].get("d") or []
        return {
            "price": values[2] if len(values) > 2 else None,
            "change": values[3] if len(values) > 3 else None
        }
    except Exception as exc:
        print("BIST 100 bant verisi hatası:", type(exc).__name__)
        return {"price": None, "change": None}


def fetch_market_ticker():
    """Üstteki kayan bant için küçük ve önbellekli piyasa özeti."""
    with MARKET_TICKER_LOCK:
        cached = MARKET_TICKER_CACHE.get("data")
        if cached and time.time() - MARKET_TICKER_CACHE["updated"] < 10:
            return cached

        rates = fetch_kurlar()
        bist100 = fetch_bist100_quote()
        data = {
            "updated": int(time.time()),
            "items": [
                {
                    "key": "bist100",
                    "label": "BIST 100",
                    "price": bist100.get("price"),
                    "change": bist100.get("change"),
                    "digits": 0
                },
                {
                    "key": "usd",
                    "label": "DOLAR/TL",
                    "price": ticker_sayi(rates.get("usd")),
                    "change": None,
                    "digits": 4
                },
                {
                    "key": "eur",
                    "label": "EURO/TL",
                    "price": ticker_sayi(rates.get("eur")),
                    "change": None,
                    "digits": 4
                },
                {
                    "key": "gram",
                    "label": "GRAM ALTIN",
                    "price": ticker_sayi(rates.get("gram")),
                    "change": None,
                    "digits": 2
                },
                {
                    "key": "ceyrek",
                    "label": "ÇEYREK ALTIN",
                    "price": ticker_sayi(rates.get("ceyrek")),
                    "change": None,
                    "digits": 2
                }
            ]
        }
        MARKET_TICKER_CACHE["data"] = data
        MARKET_TICKER_CACHE["updated"] = data["updated"]
        return data


@app.route("/api/ticker")
def api_ticker():
    response = jsonify({"ok": True, "data": fetch_market_ticker()})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


# =========================
# PIYASA TERMINALI API
# =========================

def _tr_num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    x = str(v).strip().replace(" ", "")
    if "," in x and "." in x:
        if x.rfind(",") > x.rfind("."):
            x = x.replace(".", "").replace(",", ".")
        else:
            x = x.replace(",", "")
    elif "," in x:
        x = x.replace(",", ".")
    try:
        return float(x)
    except:
        return None


def fetch_fx_gold_full():
    import requests

    r=requests.get(
        "https://finans.truncgil.com/v3/today.json",
        timeout=15,
        headers={"User-Agent":"Mozilla/5.0"}
    )
    r.raise_for_status()
    j=r.json()

    doviz=[]
    altin=[]

    fx_symbols={
        "USD":"USD","EUR":"EUR","GBP":"GBP","CHF":"CHF",
        "CAD":"CAD","AUD":"AUD","RUB":"RUB","AED":"AED",
        "DKK":"DKK","SEK":"SEK","NOK":"NOK","JPY":"JPY"
    }

    gold_symbols={
        "gram-has-altin":"GA",
        "ceyrek-altin":"Ç",
        "yarim-altin":"Y",
        "tam-altin":"TA",
        "cumhuriyet-altini":"CA",
        "ata-altin":"ATA",
        "14-ayar-altin":"14A",
        "18-ayar-altin":"18A",
        "22-ayar-bilezik":"22A",
        "ikibucuk-altin":"2.5",
        "besli-altin":"5L",
        "gremse-altin":"GR",
        "resat-altin":"RŞ",
        "hamit-altin":"HM",
        "gumus":"XAG",
        "gram-platin":"XPT",
        "gram-paladyum":"XPD"
    }

    def val(x,*names):
        for n in names:
            if n in x and x[n] not in (None,""):
                return _tr_num(x[n])
        return None

    for key,x in j.items():
        if not isinstance(x,dict):
            continue

        lk=str(key).lower()

        buy=val(x,"Buying","buying","Alış","alis")
        sell=val(x,"Selling","selling","Satış","satis")
        chg=val(
            x,
            "Change","change",
            "ChangePercent","changePercent",
            "ChangeRate","changeRate",
            "Rate","rate",
            "Değişim","degisim"
        )

        if key in fx_symbols:
            doviz.append({
                "symbol":fx_symbols[key],
                "name":x.get("Name") or key,
                "buy":buy,
                "sell":sell,
                "change_pct":chg
            })
            continue

        for gkey,sym in gold_symbols.items():
            if lk == gkey:
                altin.append({
                    "symbol":sym,
                    "name":x.get("Name") or key.replace("-"," ").upper(),
                    "buy":buy,
                    "sell":sell,
                    "change_pct":chg
                })
                break

    return doviz,altin


def fetch_crypto_full():
    import requests

    tickers=[
        "BINANCE:BTCUSDT","BINANCE:ETHUSDT",
        "BINANCE:BNBUSDT","BINANCE:SOLUSDT",
        "BINANCE:XRPUSDT","BINANCE:ADAUSDT",
        "BINANCE:DOGEUSDT","BINANCE:AVAXUSDT",
        "BINANCE:LINKUSDT","BINANCE:TRXUSDT",
        "BINANCE:DOTUSDT","BINANCE:SHIBUSDT"
    ]

    payload={
        "symbols":{"tickers":tickers,"query":{"types":[]}},
        "columns":["name","description","close","change","volume"]
    }

    r=requests.post(
        "https://scanner.tradingview.com/crypto/scan",
        json=payload,
        timeout=15,
        headers={
            "User-Agent":"Mozilla/5.0",
            "Content-Type":"text/plain;charset=UTF-8",
            "Origin":"https://www.tradingview.com"
        }
    )
    r.raise_for_status()

    out=[]

    for item in r.json().get("data",[]):
        d=item.get("d") or []
        if len(d)<5:
            continue

        sym=str(d[0]).replace("USDT","")

        out.append({
            "symbol":sym,
            "name":d[1] or sym,
            "price":d[2],
            "change_pct":d[3],
            "volume":d[4]
        })

    return out


def fetch_bist_full():
    import requests

    tickers=[
        "BIST:THYAO","BIST:ASELS","BIST:TUPRS","BIST:EREGL",
        "BIST:KCHOL","BIST:SISE","BIST:AKBNK","BIST:GARAN",
        "BIST:YKBNK","BIST:ISCTR","BIST:SAHOL","BIST:FROTO",
        "BIST:TOASO","BIST:BIMAS","BIST:TCELL","BIST:ENKAI",
        "BIST:PETKM","BIST:HEKTS","BIST:SASA","BIST:ASTOR",
        "BIST:ENJSA","BIST:MGROS","BIST:ULKER","BIST:PGSUS",
        "BIST:ARCLK","BIST:KOZAL","BIST:KRDMD","BIST:TTKOM",
        "BIST:OYAKC","BIST:VAKBN"
    ]

    payload={
        "symbols":{"tickers":tickers,"query":{"types":[]}},
        "columns":["name","description","close","change","volume"]
    }

    r=requests.post(
        "https://scanner.tradingview.com/turkey/scan",
        json=payload,
        timeout=15,
        headers={"User-Agent":"Mozilla/5.0"}
    )
    r.raise_for_status()

    out=[]

    for item in r.json().get("data",[]):
        d=item.get("d") or []
        if len(d)<5:
            continue

        out.append({
            "symbol":d[0],
            "name":d[1] or d[0],
            "price":d[2],
            "change_pct":d[3],
            "volume":d[4]
        })

    return out


@app.route("/api/piyasa")
def api_piyasa():
    import time

    result = {
        "ok": True,
        "updated": int(time.time()),
        "doviz": [],
        "altin": [],
        "kripto": [],
        "borsa": [],
        "errors": {}
    }

    try:
        doviz, altin = fetch_fx_gold_full()
        result["doviz"] = doviz
        result["altin"] = altin
    except Exception as e:
        result["errors"]["doviz_altin"] = str(e)

    try:
        result["kripto"] = fetch_crypto_full()
    except Exception as e:
        result["errors"]["kripto"] = str(e)

    try:
        result["borsa"] = fetch_bist_full()
    except Exception as e:
        result["errors"]["borsa"] = str(e)

    resp = jsonify(result)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-store"
    return resp


HTML = r'''
<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport"
content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">

<title>BIST PRO Radar</title>

<script src="https://telegram.org/js/telegram-web-app.js"></script>
<!-- AKD ekran görüntüsünü telefonda, sunucuya göndermeden okumak için. -->
<script src="https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js"></script>

<style>
*{
    box-sizing:border-box
}

body{
    margin:0;
    background:#070b12;
    color:#eef3ff;
    font-family:Arial,Helvetica,sans-serif;
}

.app{
    max-width:900px;
    margin:auto;
    padding-bottom:85px;
}

.top{
    position:sticky;
    top:0;
    z-index:20;
    background:rgba(7,11,18,.96);
    backdrop-filter:blur(12px);
    padding:15px;
    border-bottom:1px solid #182131;
}

.title{
    font-size:22px;
    font-weight:800;
}

.sub{
    margin-top:4px;
    color:#8290a8;
    font-size:12px;
}

.liveStatus{
    display:flex;
    align-items:center;
    gap:7px;
    margin-top:9px;
    color:#9baac0;
    font-size:11px;
    font-weight:700;
}

.liveDot{
    width:8px;
    height:8px;
    flex:0 0 8px;
    border-radius:50%;
    background:#20d391;
    box-shadow:0 0 0 0 rgba(32,211,145,.55);
    animation:livePulse 1.6s infinite;
}

.liveStatus.warningLive{color:#f5ca61}
.liveStatus.warningLive .liveDot{
    background:#f5ca61;
    animation:none;
}

@keyframes livePulse{
    70%{box-shadow:0 0 0 7px rgba(32,211,145,0)}
    100%{box-shadow:0 0 0 0 rgba(32,211,145,0)}
}

.search{
    width:100%;
    border:none;
    outline:none;
    margin-top:13px;
    padding:14px 16px;
    border-radius:14px;
    background:#111925;
    color:white;
    font-size:16px;
}

.stats{
    display:grid;
    grid-template-columns:repeat(4,1fr);
    gap:8px;
    padding:12px 14px 5px;
}

.stat{
    background:#0e1520;
    border:1px solid #1a2433;
    border-radius:13px;
    padding:11px;
}

.stat b{
    display:block;
    font-size:18px;
}

.stat span{
    color:#8190aa;
    font-size:11px;
}

.tabs{
    display:flex;
    overflow:auto;
    gap:8px;
    padding:10px 14px;
}

.tabs button{
    white-space:nowrap;
    background:#101824;
    color:#9aa8bd;
    border:1px solid #1c2838;
    padding:9px 13px;
    border-radius:12px;
}

.tabs button.active{
    background:#1769ff;
    color:white;
    border-color:#1769ff;
}

.list{
    padding:5px 12px;
}

.stock{
    padding:14px;
    margin:8px 0;
    background:#0d141e;
    border:1px solid #192434;
    border-radius:15px;
    cursor:pointer;
}

.stock.virmanCandidate{
    border-color:#7b5b18;
    box-shadow:0 0 0 1px rgba(245,187,61,.08);
}

.virmanTag{
    margin-top:10px;
    padding:8px 10px;
    border-radius:9px;
    background:#2b210d;
    border:1px solid #6d5015;
    color:#ffd36d;
    font-size:11px;
    font-weight:800;
}

.virmanTag.buy{
    background:#0e2a20;
    border-color:#175b44;
    color:#39dfa4;
}

.virmanTag.sell{
    background:#2b1117;
    border-color:#6b2635;
    color:#ff7487;
}

.providerBadge{
    display:inline-block;
    margin-bottom:10px;
    padding:6px 9px;
    border-radius:8px;
    background:#102947;
    border:1px solid #1f5590;
    color:#82baff;
    font-size:11px;
    font-weight:800;
}

.dataTableWrap{
    overflow:auto;
    border-radius:12px;
    border:1px solid #1a2636;
}

.dataTable{
    width:100%;
    min-width:560px;
    border-collapse:collapse;
    background:#0d141e;
}

.dataTable th,
.dataTable td{
    padding:10px 8px;
    border-bottom:1px solid #1a2636;
    text-align:right;
    font-size:12px;
}

.dataTable th:first-child,
.dataTable td:first-child{
    text-align:left;
}

.dataTable th{
    color:#8290a8;
    font-size:10px;
    position:sticky;
    top:0;
    background:#111a26;
}

.stockTop{
    display:flex;
    justify-content:space-between;
    gap:10px;
}

.symbol{
    font-weight:800;
    font-size:17px;
}

.desc{
    color:#8190a5;
    font-size:12px;
    margin-top:3px;
}

.price{
    font-weight:800;
    text-align:right;
}

.price.tickUp{
    color:#26dfa0;
    animation:priceUp .95s ease-out;
}

.price.tickDown{
    color:#ff7180;
    animation:priceDown .95s ease-out;
}

@keyframes priceUp{
    0%{background:rgba(38,223,160,.34);transform:translateY(-2px)}
    100%{background:transparent;transform:translateY(0)}
}

@keyframes priceDown{
    0%{background:rgba(255,113,128,.28);transform:translateY(2px)}
    100%{background:transparent;transform:translateY(0)}
}

.showMore{
    display:block;
    width:calc(100% - 24px);
    margin:12px auto 6px;
    padding:12px;
    border:1px solid #275b9f;
    border-radius:12px;
    background:#11284a;
    color:#8fc1ff;
    font-weight:800;
}

.green{color:#29d391}
.red{color:#ff5c6c}

.smallgrid{
    display:grid;
    grid-template-columns:repeat(4,1fr);
    gap:6px;
    margin-top:12px;
}

.small{
    background:#111a26;
    padding:8px;
    border-radius:9px;
    text-align:center;
}

.small span{
    color:#78869a;
    display:block;
    font-size:10px;
}

.small b{
    font-size:12px;
}

.detail{
    display:none;
    min-height:100vh;
    background:#070b12;
}

.detailHeader{
    padding:15px;
    border-bottom:1px solid #192333;
}

.back{
    background:#111a27;
    color:#fff;
    border:0;
    padding:10px 14px;
    border-radius:10px;
}

.hero{
    padding:20px 15px;
}

.heroSymbol{
    font-size:26px;
    font-weight:900;
}

.heroName{
    color:#8190a8;
    margin-top:4px;
}

.heroPrice{
    font-size:31px;
    font-weight:900;
    margin-top:16px;
}

.detailTabs{
    display:flex;
    gap:7px;
    overflow:auto;
    padding:8px 13px 15px;
}

.detailTabs button{
    border:0;
    background:#111a27;
    color:#91a0b6;
    padding:10px 13px;
    border-radius:11px;
    white-space:nowrap;
}

.detailTabs button.active{
    background:#1d6cff;
    color:white;
}

.panel{
    padding:4px 13px 30px;
}

.card{
    background:#0e1621;
    border:1px solid #1a2636;
    border-radius:15px;
    padding:14px;
    margin-bottom:11px;
}

.card h3{
    margin:0 0 12px;
}

.rows{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:8px;
}

.row{
    background:#111b28;
    padding:11px;
    border-radius:11px;
}

.row span{
    color:#8190a5;
    display:block;
    font-size:11px;
}

.row b{
    font-size:14px;
}

.warning{
    padding:14px;
    background:#241d0b;
    border:1px solid #4c3b10;
    border-radius:13px;
    color:#f5ca61;
    line-height:1.5;
}

.score{
    font-size:45px;
    font-weight:900;
}

.bar{
    height:8px;
    background:#192333;
    border-radius:20px;
    overflow:hidden;
    margin-top:9px;
}

.bar div{
    height:100%;
    background:#2774ff;
}

.loading{
    text-align:center;
    padding:50px;
    color:#8190a5;
}

.bottom{
    position:fixed;
    left:0;
    right:0;
    bottom:0;
    background:#0c121c;
    border-top:1px solid #1b2635;
    padding:11px;
    text-align:center;
    font-size:12px;
    color:#77859b;
}

/* ===== V3 PROFESSIONAL TERMINAL ===== */

.detail{
    background:#080b10;
}

.detailHeader{
    background:#090d13;
    padding:14px 16px;
    border-bottom:1px solid #171d27;
}

.back{
    font-weight:700;
    font-size:15px;
    background:#101722;
}

.hero{
    background:
      linear-gradient(110deg,rgba(90,16,43,.62),rgba(21,12,24,.80));
    border-bottom:3px solid #cf294e;
    padding:18px 20px 16px;
}

.heroSymbol{
    font-size:27px;
    letter-spacing:.4px;
}

.heroName{
    color:#898b99;
    font-size:14px;
}

.heroPrice{
    font-size:36px;
    margin-top:14px;
}

.proTabs{
    background:#0b0f15;
    border-bottom:1px solid #1a202a;
    padding:0;
    gap:0;
}

.proTabs button{
    border-radius:0;
    background:#0b0f15;
    padding:15px 17px;
    font-weight:700;
    font-size:13px;
}

.proTabs button.active{
    background:#191936;
    color:#7d78ff;
    border-bottom:2px solid #6861ff;
}

.marketStrip{
    display:grid;
    grid-template-columns:repeat(5,1fr);
    background:#121720;
    border:1px solid #1e2530;
    border-radius:16px;
    overflow:hidden;
    margin-bottom:10px;
}

.marketStrip div{
    padding:10px 4px;
    text-align:center;
    border-right:1px solid #202632;
}

.marketStrip div:last-child{
    border-right:0;
}

.marketStrip span{
    display:block;
    color:#737887;
    font-size:10px;
    font-weight:700;
}

.marketStrip b{
    font-size:13px;
}

.depthCard{
    background:#090c11;
    border:1px solid #20242c;
    border-radius:16px;
    overflow:hidden;
}

.depthHead,
.depthRow{
    display:grid;
    grid-template-columns:.55fr 1.15fr 1fr 1fr 1.15fr .55fr;
    align-items:center;
}

.depthHead{
    padding:9px 6px;
    color:#757986;
    font-size:11px;
    font-weight:700;
    border-bottom:1px solid #20242c;
}

.depthRow{
    position:relative;
    min-height:45px;
    border-bottom:1px solid #171b22;
    font-size:15px;
}

.depthRow > div{
    z-index:2;
    padding:8px 5px;
}

.depthBid{
    color:#19d8a0;
}

.depthAsk{
    color:#ff5367;
}

.depthBuyBg{
    position:absolute;
    left:0;
    top:0;
    bottom:0;
    background:rgba(14,165,132,.16);
    z-index:1;
}

.depthSellBg{
    position:absolute;
    right:0;
    top:0;
    bottom:0;
    background:rgba(229,45,71,.13);
    z-index:1;
}

.depthBalance{
    padding:13px;
}

.balanceBar{
    display:flex;
    height:8px;
    gap:3px;
    margin-bottom:9px;
}

.balanceBuy{
    background:#16c997;
    border-radius:6px 0 0 6px;
}

.balanceSell{
    background:#ec324d;
    border-radius:0 6px 6px 0;
}

.balanceText{
    display:flex;
    justify-content:space-between;
    font-weight:800;
}

.balanceText .buy{
    color:#25d9a8;
}

.balanceText .sell{
    color:#ff5367;
}

.sectionTitle{
    font-size:15px;
    font-weight:800;
    color:#9297a4;
    margin:15px 3px 8px;
}

.tradeTable{
    width:100%;
    border-collapse:collapse;
    background:#090c11;
    border-radius:14px;
    overflow:hidden;
}

.tradeTable th{
    color:#747987;
    font-size:10px;
    padding:9px 5px;
    border-bottom:1px solid #20242c;
}

.tradeTable td{
    padding:9px 5px;
    border-bottom:1px solid #161a20;
    font-size:12px;
}

.locked{
    padding:18px;
    background:#10151d;
    border:1px dashed #353d49;
    border-radius:15px;
    color:#9aa0ad;
    line-height:1.6;
}

.signalBox{
    background:#101722;
    border:1px solid #202c3a;
    border-radius:15px;
    padding:16px;
    margin-bottom:10px;
}

.signalBig{
    font-size:42px;
    font-weight:900;
}

.akdImportCard{
    background:linear-gradient(145deg,#111a29,#0e151f);
    border:1px solid #315480;
}

.akdImportCard h3{
    color:#dcecff;
}

.akdImportSteps{
    margin:0 0 13px;
    color:#9eacc0;
    font-size:12px;
    line-height:1.65;
}

.filePicker{
    display:flex;
    align-items:center;
    justify-content:center;
    min-height:48px;
    width:100%;
    border:1px dashed #4b83c9;
    border-radius:12px;
    color:#9bc7ff;
    background:#0b1522;
    font-weight:800;
    cursor:pointer;
}

.filePicker input{
    display:none;
}

.ocrProgress{
    min-height:20px;
    margin:10px 2px 0;
    color:#8da0b8;
    font-size:11px;
    line-height:1.45;
}

.draftLabel{
    display:block;
    margin:14px 0 7px;
    color:#b7c4d7;
    font-size:11px;
    font-weight:800;
}

.draftArea{
    display:block;
    width:100%;
    min-height:176px;
    resize:vertical;
    border:1px solid #293b52;
    border-radius:12px;
    padding:11px;
    background:#09111c;
    color:#d8e4f5;
    font:12px/1.55 monospace;
}

.akdActions{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:8px;
    margin-top:10px;
}

.actionButton{
    border:0;
    border-radius:11px;
    padding:12px 9px;
    background:#1f70ef;
    color:#fff;
    font-size:12px;
    font-weight:800;
    cursor:pointer;
}

.actionButton.secondary{
    background:#172435;
    border:1px solid #30445e;
    color:#bcd1ec;
}

.manualBadge{
    background:#192836;
    border-color:#496886;
    color:#b7d8fc;
}

.akdPrivacy{
    margin-top:11px;
    color:#7f91a7;
    font-size:10px;
    line-height:1.5;
}


.kurFlashUp{
    animation:kurUpFlash .8s ease;
}
.kurFlashDown{
    animation:kurDownFlash .8s ease;
}

@keyframes kurUpFlash{
    0%{
        box-shadow:0 0 0 1px rgba(32,211,145,.95),
                   0 0 22px rgba(32,211,145,.45);
        border-color:#20d391;
    }
    100%{
        box-shadow:none;
    }
}

@keyframes kurDownFlash{
    0%{
        box-shadow:0 0 0 1px rgba(255,92,108,.95),
                   0 0 22px rgba(255,92,108,.45);
        border-color:#ff5c6c;
    }
    100%{
        box-shadow:none;
    }
}

/* ===== V7 PRO TERMINAL DASHBOARD ===== */
html,body{
    width:100%;
    max-width:100%;
    overflow-x:hidden;
}

.top{
    background:linear-gradient(155deg,rgba(10,18,31,.98),rgba(7,11,18,.97));
}

.marketTape{
    width:100%;
    overflow:hidden;
    margin:11px 0 1px;
    border:1px solid #1c2b3e;
    border-radius:12px;
    background:linear-gradient(90deg,#0c1522,#0b1019);
}

.tapeTrack{
    display:flex;
    width:max-content;
    min-width:100%;
    animation:tapeMove 32s linear infinite;
}

.tapeTrack.paused{animation-play-state:paused}
.marketTape:active .tapeTrack{animation-play-state:paused}

.tapeSet{
    display:flex;
    align-items:stretch;
}

.tapeItem{
    display:flex;
    align-items:center;
    gap:8px;
    min-width:152px;
    padding:9px 12px;
    border-right:1px solid #1d2b3d;
}

.tapeLabel{
    color:#8291a8;
    font-size:10px;
    font-weight:900;
    white-space:nowrap;
}

.tapePrice{
    color:#e7effc;
    font-size:12px;
    font-weight:900;
    white-space:nowrap;
}

.tapeChange{
    font-size:10px;
    font-weight:900;
    white-space:nowrap;
}

.tapeChange.flat{color:#718198}

@keyframes tapeMove{
    from{transform:translateX(0)}
    to{transform:translateX(-50%)}
}

.terminalDashboard{
    padding:12px 14px 3px;
}

.dashboardHero{
    position:relative;
    overflow:hidden;
    min-height:132px;
    padding:16px;
    border:1px solid #294569;
    border-radius:19px;
    background:
        radial-gradient(circle at 90% 10%,rgba(53,126,255,.30),transparent 33%),
        linear-gradient(135deg,#101f36,#0d141f 62%,#12111e);
}

.dashboardHero:after{
    content:"";
    position:absolute;
    width:180px;
    height:180px;
    right:-77px;
    bottom:-105px;
    border:1px solid rgba(98,158,255,.22);
    border-radius:50%;
    box-shadow:0 0 0 23px rgba(98,158,255,.03),0 0 0 47px rgba(98,158,255,.025);
}

.dashEyebrow{
    color:#81b7ff;
    font-size:10px;
    font-weight:900;
    letter-spacing:.8px;
}

.dashTitle{
    margin-top:5px;
    max-width:78%;
    color:#f1f5fd;
    font-size:19px;
    font-weight:900;
    line-height:1.15;
}

.dashText{
    max-width:80%;
    margin-top:7px;
    color:#9aa9be;
    font-size:11px;
    line-height:1.42;
}

.dashButton{
    position:relative;
    z-index:1;
    margin-top:12px;
    padding:9px 12px;
    border:1px solid #448eff;
    border-radius:10px;
    background:#1d69df;
    color:white;
    font-size:11px;
    font-weight:900;
}

.breadthGrid{
    display:grid;
    grid-template-columns:repeat(4,1fr);
    gap:7px;
    margin-top:9px;
}

.breadthCard{
    min-width:0;
    padding:10px 7px;
    border:1px solid #1b2a3d;
    border-radius:12px;
    background:#0d1623;
    text-align:center;
}

.breadthCard span{
    display:block;
    overflow:hidden;
    color:#74839a;
    font-size:9px;
    font-weight:800;
    text-overflow:ellipsis;
    white-space:nowrap;
}

.breadthCard b{
    display:block;
    margin-top:4px;
    color:#eaf1fc;
    font-size:15px;
}

.proQuickTools{
    display:grid;
    grid-template-columns:repeat(3,1fr);
    gap:8px;
    padding:11px 14px 4px;
}

.quickTool{
    min-height:72px;
    padding:10px 7px;
    border:1px solid #1c2c40;
    border-radius:14px;
    background:linear-gradient(145deg,#101a28,#0d141e);
    color:#bdcadb;
    font-size:10px;
    font-weight:800;
    line-height:1.25;
}

.quickTool b{
    display:block;
    margin-bottom:5px;
    color:#78adff;
    font-size:18px;
    line-height:19px;
}

.quickTool.featured{
    border-color:#3d6ca6;
    background:linear-gradient(145deg,#163155,#101c2d);
    color:#e5efff;
}

.quickTool.featured b{color:#63d8ff}
.quickTool.orange b{color:#ffc35f}
.quickTool.greenTool b{color:#45dea7}
.quickTool.pink b{color:#ff8ba4}

#kurBar{display:none!important}

.stockTitleLine{
    display:flex;
    align-items:center;
    gap:7px;
}

.favoriteButton{
    width:28px;
    height:28px;
    padding:0;
    border:1px solid #27364a;
    border-radius:9px;
    background:#111d2b;
    color:#8293a9;
    font-size:15px;
}

.favoriteButton.active{
    border-color:#c28a27;
    background:#33250e;
    color:#ffd067;
}

.stockSetup{
    display:inline-block;
    margin-top:9px;
    padding:5px 8px;
    border:1px solid #24537b;
    border-radius:8px;
    background:#0d263c;
    color:#87c5ff;
    font-size:10px;
    font-weight:900;
}

.toolSheet{
    display:none;
    position:fixed;
    inset:0;
    z-index:10001;
    overflow-y:auto;
    overscroll-behavior:contain;
    padding:0 0 86px;
    background:#070b12;
}

.toolSheet.open{display:block}

.toolHead{
    position:sticky;
    top:0;
    z-index:2;
    display:flex;
    align-items:center;
    gap:10px;
    padding:13px 14px;
    border-bottom:1px solid #1e2a3b;
    background:rgba(8,13,21,.97);
    backdrop-filter:blur(12px);
}

.toolHead button{
    border:1px solid #2b3e56;
    border-radius:10px;
    padding:9px 11px;
    background:#101b29;
    color:#e3ecfa;
    font-weight:800;
}

.toolHeadTitle{
    overflow:hidden;
    color:#eff5ff;
    font-size:16px;
    font-weight:900;
    text-overflow:ellipsis;
    white-space:nowrap;
}

.toolPanel{padding:13px}

.sheetHero{
    position:relative;
    overflow:hidden;
    padding:16px;
    border:1px solid #274c74;
    border-radius:17px;
    background:linear-gradient(135deg,#112b4c,#0c1724 72%);
}

.sheetHero h2{
    margin:4px 0 6px;
    color:#f4f7fd;
    font-size:20px;
}

.sheetHero p{
    margin:0;
    color:#a8bad0;
    font-size:12px;
    line-height:1.5;
}

.toolSectionTitle{
    margin:16px 2px 8px;
    color:#aab9cd;
    font-size:12px;
    font-weight:900;
    letter-spacing:.3px;
}

.basketCard,.portfolioCard,.alertCard,.riskCard{
    margin:9px 0;
    padding:14px;
    border:1px solid #1d2d42;
    border-radius:15px;
    background:#0d1622;
}

.basketTop,.portfolioRowTop{
    display:flex;
    align-items:flex-start;
    justify-content:space-between;
    gap:10px;
}

.basketRank{
    display:inline-flex;
    align-items:center;
    justify-content:center;
    min-width:28px;
    height:28px;
    margin-right:8px;
    border-radius:9px;
    background:#174378;
    color:#dcecff;
    font-size:12px;
    font-weight:900;
}

.basketSymbol{font-size:17px;font-weight:900}
.basketName{margin-top:3px;color:#8291a7;font-size:11px}
.basketPrice{text-align:right;font-size:17px;font-weight:900}
.basketMeta{margin-top:10px;color:#9dacc0;font-size:11px;line-height:1.55}
.basketReasons{margin:10px 0 0;padding:0;list-style:none;color:#b6c5d7;font-size:11px;line-height:1.6}
.basketReasons li:before{content:"• ";color:#58bbff}

.levelGrid{
    display:grid;
    grid-template-columns:repeat(3,1fr);
    gap:6px;
    margin-top:11px;
}

.levelCell{
    padding:8px 5px;
    border-radius:9px;
    background:#121f2e;
    text-align:center;
}

.levelCell span{display:block;color:#8090a5;font-size:9px}
.levelCell b{display:block;margin-top:3px;color:#e8effa;font-size:11px}

.sheetActions{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:8px;
    margin-top:12px;
}

.sheetButton{
    border:1px solid #2e629f;
    border-radius:10px;
    padding:10px 8px;
    background:#155cc1;
    color:white;
    font-size:11px;
    font-weight:900;
}

.sheetButton.secondary{
    border-color:#31445d;
    background:#162233;
    color:#c0d0e5;
}

.formGrid{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:8px;
}

.formGrid .wide{grid-column:1/-1}

.formInput,.formSelect{
    width:100%;
    min-height:42px;
    border:1px solid #2a3a50;
    border-radius:10px;
    padding:10px;
    outline:0;
    background:#09121d;
    color:#edf3fb;
    font-size:12px;
}

.formLabel{display:block;margin:11px 1px 6px;color:#97a8bf;font-size:10px;font-weight:800}

.portfolioTotals{
    display:grid;
    grid-template-columns:repeat(3,1fr);
    gap:7px;
    margin-top:10px;
}

.portfolioTotals div{padding:10px 7px;border-radius:10px;background:#111d2b;text-align:center}
.portfolioTotals span{display:block;color:#7f90a7;font-size:9px}
.portfolioTotals b{display:block;margin-top:4px;font-size:12px}
.portfolioList{margin-top:12px}
.portfolioItem{padding:12px 0;border-bottom:1px solid #1a2737}
.portfolioItem:last-child{border-bottom:0}
.portfolioInfo{margin-top:8px;color:#90a0b4;font-size:11px;line-height:1.55}

.iconTextButton{
    border:0;
    border-radius:8px;
    padding:7px 8px;
    background:#172538;
    color:#bcd2ee;
    font-size:10px;
    font-weight:800;
}

.iconTextButton.danger{background:#351720;color:#ff9ba8}

.riskResult{
    margin-top:12px;
    padding:13px;
    border:1px solid #265a8c;
    border-radius:12px;
    background:#0c2033;
}

.riskResultGrid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.riskResultGrid div{padding:9px;border-radius:9px;background:#101d2a}
.riskResultGrid span{display:block;color:#8293aa;font-size:10px}
.riskResultGrid b{display:block;margin-top:4px;color:#edf4fd;font-size:13px}

.alertList{margin-top:11px}
.alertItem{padding:11px 0;border-bottom:1px solid #1b2939}
.alertItem:last-child{border-bottom:0}
.alertDescription{margin-top:4px;color:#9dacc0;font-size:11px;line-height:1.4}

.toast{
    display:none;
    position:fixed;
    left:12px;
    right:12px;
    bottom:79px;
    z-index:10050;
    padding:13px 14px;
    border:1px solid #316da9;
    border-radius:13px;
    background:#10243b;
    color:#edf6ff;
    font-size:12px;
    font-weight:800;
    box-shadow:0 12px 30px rgba(0,0,0,.35);
}

.toast.show{display:block;animation:toastIn .25s ease-out}
@keyframes toastIn{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}

.portraitGuard{display:none}
@media (orientation:landscape) and (max-height:620px){
    .portraitGuard{
        display:flex;
        position:fixed;
        inset:0;
        z-index:20000;
        align-items:center;
        justify-content:center;
        padding:28px;
        background:#070b12;
        color:#dfeafa;
        text-align:center;
        font-weight:800;
        line-height:1.6;
    }
}

@media (prefers-reduced-motion:reduce){
    .tapeTrack{animation:none}
}

</style>

<style>
/* ===== V7.1 MOBILE RADAR: REFERANS VİDEO DÜZENİ ===== */
body{
    background:#05080e;
}

.app{
    max-width:680px;
    padding-bottom:108px;
}

#home{
    min-height:100vh;
    background:
        radial-gradient(circle at 92% 12%,rgba(53,92,201,.18),transparent 25%),
        radial-gradient(circle at 6% 38%,rgba(29,168,174,.08),transparent 24%),
        #05080e;
}

.top{
    position:relative;
    top:auto;
    overflow:hidden;
    padding:12px 14px 9px;
    border:0;
    background:
        radial-gradient(ellipse at 89% 30%,rgba(72,104,243,.54),transparent 37%),
        radial-gradient(ellipse at 10% 88%,rgba(21,183,183,.22),transparent 26%),
        linear-gradient(135deg,#080d21 0%,#111d55 50%,#0b1232 100%);
}

.top:before{
    content:"";
    position:absolute;
    inset:0;
    opacity:.32;
    pointer-events:none;
    background:
        linear-gradient(104deg,transparent 31%,rgba(139,173,255,.25) 38%,transparent 48%),
        linear-gradient(166deg,transparent 42%,rgba(120,245,232,.16) 54%,transparent 65%);
}

.terminalControlBar,.referenceTitle,.liveStatus,.searchRow,.marketTape{
    position:relative;
    z-index:1;
}

.terminalControlBar{
    display:grid;
    grid-template-columns:43px minmax(0,1fr) 43px;
    align-items:center;
    gap:9px;
}

.terminalRoundButton{
    width:43px;
    height:43px;
    border:1px solid rgba(196,219,255,.17);
    border-radius:14px;
    background:rgba(5,10,22,.50);
    color:#e8f0ff;
    font-size:21px;
    font-weight:900;
}

.marketPill{
    display:flex;
    align-items:center;
    justify-content:center;
    gap:7px;
    min-width:0;
    height:39px;
    padding:0 10px;
    border:1px solid rgba(132,154,222,.30);
    border-radius:999px;
    background:rgba(2,7,14,.72);
    color:#e8effe;
    font-size:12px;
    font-weight:900;
    white-space:nowrap;
}

.marketPill .liveDot{
    width:7px;
    height:7px;
    flex:0 0 7px;
}

.marketOpenText{color:#39e29d}
.marketClosedText{color:#f5c763}
.marketPillSep{color:#6f7d99}

.title.referenceTitle{
    display:flex;
    align-items:center;
    gap:7px;
    margin-top:11px;
    color:#f4f7ff;
    font-size:19px;
    letter-spacing:-.3px;
}

.title.referenceTitle span{
    padding:4px 7px;
    border:1px solid rgba(92,218,255,.35);
    border-radius:7px;
    background:rgba(12,96,161,.33);
    color:#86dcff!important;
    font-size:9px!important;
    letter-spacing:.5px;
}

.sub{display:none}

.liveStatus{
    margin-top:5px;
    color:#b2c2db;
    font-size:10px;
}

.liveStatus>.liveDot{display:none}

.searchRow{
    display:grid;
    grid-template-columns:minmax(0,1fr) 48px;
    gap:9px;
    margin-top:12px;
}

.search{
    min-width:0;
    margin:0;
    padding:13px 14px;
    border:1px solid rgba(166,193,255,.16);
    border-radius:15px;
    background:rgba(5,10,19,.78);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.035);
    color:#f4f7fb;
    font-size:14px;
}

.searchToolButton{
    border:1px solid rgba(169,195,255,.23);
    border-radius:15px;
    background:rgba(7,13,27,.68);
    color:#d9e4f7;
    font-size:21px;
}

.marketTape{
    margin:11px -14px -9px;
    border:0;
    border-radius:0;
    background:rgba(1,5,10,.69);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.04);
}

.tapeTrack,.tapeTrack.paused{
    min-width:max-content;
    animation:tapeMove 23s linear infinite!important;
    animation-play-state:running!important;
    will-change:transform;
}

.tapeItem{
    min-width:164px;
    padding:8px 12px;
    border-right:1px solid rgba(151,176,229,.12);
}

.tapeLabel{color:#9eafca;font-size:9px}
.tapePrice{color:#eff5ff;font-size:12px}
.tapeChange{font-size:10px}

.terminalDashboard{
    padding:11px 14px 2px;
}

.dashboardHero{
    min-height:0;
    padding:13px 14px;
    border:1px solid rgba(53,133,175,.42);
    border-radius:17px;
    background:
        radial-gradient(circle at 100% 0,rgba(51,210,195,.18),transparent 34%),
        linear-gradient(125deg,#0a2632,#0b1420 64%);
}

.dashboardHero:after{display:none}
.dashEyebrow{font-size:9px;color:#5fe6d2}
.dashTitle{max-width:100%;font-size:16px;line-height:1.2}
.dashText{max-width:100%;font-size:10px;line-height:1.45}
.dashButton{margin-top:10px;padding:8px 10px;border-color:#2bb9af;border-radius:10px;background:#087c78;font-size:10px}

.breadthGrid{gap:6px;margin-top:7px}
.breadthCard{padding:8px 5px;border-color:#162c39;border-radius:10px;background:#09121a}
.breadthCard span{font-size:8px}.breadthCard b{margin-top:3px;font-size:13px}

.stats{display:none}

.tabs{
    gap:7px;
    padding:10px 14px 4px;
    scrollbar-width:none;
}

.tabs::-webkit-scrollbar,.proQuickTools::-webkit-scrollbar{display:none}

.tabs button{
    border-color:#203447;
    border-radius:999px;
    padding:8px 11px;
    background:#0c1721;
    color:#a7b8cc;
    font-size:10px;
    font-weight:800;
}

.tabs button.active{
    border-color:#3bd3c8;
    background:#0a7776;
    color:#f3ffff;
}

.proQuickTools{
    display:flex;
    gap:7px;
    overflow-x:auto;
    padding:6px 14px 8px;
    scrollbar-width:none;
}

.quickTool{
    display:flex;
    flex:0 0 auto;
    align-items:center;
    gap:5px;
    min-height:35px;
    padding:8px 10px;
    border-color:#1d3242;
    border-radius:11px;
    background:#0d1721;
    color:#b1c2d7;
    font-size:10px;
}

.quickTool b,.quickTool.featured b,.quickTool.orange b,.quickTool.greenTool b,.quickTool.pink b{
    display:inline;
    margin:0;
    color:#62e2d5;
    font-size:13px;
    line-height:1;
}

.quickTool.featured{border-color:#236d78;background:#0b3340;color:#dffcf8}
.quickTool.orange b{color:#ffcb72}.quickTool.pink b{color:#ff90aa}

.commandGrid{display:grid;grid-template-columns:repeat(2,1fr);gap:9px;margin-top:12px}
.commandCard{
    min-height:112px;
    padding:13px;
    border:1px solid #1d3545;
    border-radius:16px;
    background:linear-gradient(145deg,#0d1a25,#09121b);
    color:#eef6ff;
    text-align:left;
}
.commandCard b{display:block;margin-top:8px;font-size:13px}
.commandCard span{display:block;margin-top:5px;color:#92a7bd;font-size:10px;line-height:1.35}
.commandIcon{display:flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:10px;background:#123c45;color:#66e4d5;font-size:17px;font-weight:900}
.commandCard.warningCard .commandIcon{background:#392b13;color:#ffce77}
.commandCard.pinkCard .commandIcon{background:#381a2c;color:#ff99b6}

.watchlistHeader{
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:8px;
    margin:0 14px;
    padding:11px 2px 8px;
    border-top:1px solid rgba(121,151,182,.10);
}

.watchlistTitleWrap{display:flex;align-items:center;gap:7px;min-width:0}
.watchlistTitle{color:#f0f5fd;font-size:16px;font-weight:900;white-space:nowrap}
.watchlistCount{min-width:23px;padding:3px 7px;border-radius:999px;background:#1a2737;color:#9fb5cd;font-size:10px;font-weight:900;text-align:center}
.watchlistActions{display:flex;align-items:center;gap:6px}
.watchlistAction{border:0;border-radius:10px;padding:7px 9px;background:transparent;color:#8295ab;font-size:10px;font-weight:800}
.watchlistAction.alert{border:1px solid #1e7473;background:#0b3236;color:#65eadb}

.list{padding:0 14px 12px}

.stock{
    margin:0;
    padding:12px 1px;
    border:0;
    border-bottom:1px solid rgba(124,148,177,.13);
    border-radius:0;
    background:transparent;
    box-shadow:none;
}

.stock.virmanCandidate{border-color:#755a1c;box-shadow:none}

.stockTop{
    display:grid;
    grid-template-columns:46px minmax(0,1fr) max-content;
    align-items:center;
    gap:10px;
}

.stockAvatar{
    display:flex;
    align-items:center;
    justify-content:center;
    width:45px;
    height:45px;
    border:1px solid rgba(223,240,255,.18);
    border-radius:14px;
    background:linear-gradient(145deg,hsl(var(--avatar-hue),55%,42%),hsl(var(--avatar-hue),48%,22%));
    box-shadow:inset 0 1px 0 rgba(255,255,255,.15);
    color:#effaff;
    font-size:11px;
    font-weight:900;
    letter-spacing:-.4px;
}

.stockIdentity{min-width:0}
.stockTitleLine{gap:5px}
.symbol{font-size:16px;letter-spacing:.1px}
.desc{overflow:hidden;margin-top:2px;color:#78899c;font-size:9px;text-overflow:ellipsis;white-space:nowrap}
.favoriteButton{width:24px;height:24px;border-radius:8px;font-size:13px}

.stockQuote{display:flex;flex-direction:column;align-items:flex-end;gap:4px;min-width:80px}
.price{font-size:16px;line-height:1}
.changeBadge{padding:4px 7px;border-radius:7px;background:#182c2a;font-size:10px;font-weight:900;line-height:1}
.changeBadge.red{background:#2e1620}.changeBadge.green{background:#12352d}

.stockMetaLine{
    display:flex;
    gap:7px;
    margin:7px 0 0 56px;
    overflow:hidden;
    color:#7e90a6;
    font-size:9px;
    white-space:nowrap;
}

.stockMetaLine span{overflow:hidden;text-overflow:ellipsis}
.stockMetaLine strong{color:#a7b7ca;font-weight:800}
.stockSetup{margin:7px 0 0 56px;padding:4px 7px;border-radius:7px;border-color:#1e5665;background:#0a2933;color:#5cdccf;font-size:9px}
.smallgrid{display:none}
.stock .virmanTag{margin:8px 0 0 56px;padding:6px 8px;border-radius:8px;font-size:9px}

.showMore{width:100%;margin:14px 0 2px;border-radius:12px;background:#0a2c3a;border-color:#207987;color:#67e6db;font-size:11px}

.bottom{display:none!important}
.toast{bottom:97px}

@media (prefers-reduced-motion:reduce){
    .tapeTrack,.tapeTrack.paused{animation:tapeMove 48s linear infinite!important}
}

.mobileBottomNav{
    position:fixed;
    left:50%;
    right:auto;
    bottom:max(10px,env(safe-area-inset-bottom));
    z-index:10000;
    width:calc(100% - 22px);
    max-width:560px;
    height:70px;
    padding:6px;
    transform:translateX(-50%);
    border:1px solid rgba(142,169,204,.22);
    border-radius:23px;
    background:rgba(11,18,29,.94);
    box-shadow:0 12px 32px rgba(0,0,0,.43),inset 0 1px 0 rgba(255,255,255,.05);
    backdrop-filter:blur(18px);
    display:grid;
    grid-template-columns:repeat(4,1fr);
    gap:4px;
}
.mobileBottomNav button{
    border:0;
    background:transparent;
    color:#8797aa;
    font-size:10px;
    font-weight:800;
    border-radius:17px;
    padding:5px 2px 4px;
}
.mobileBottomNav button b{
    display:block;
    color:#d7e1ee;
    font-size:19px;
    line-height:22px;
    margin-bottom:3px;
}
.mobileBottomNav button.active{
    color:#ecfffd;
    background:linear-gradient(145deg,rgba(44,194,186,.30),rgba(21,72,91,.50));
}
.mobileBottomNav button.active b{color:#68f1df}
body{padding-bottom:106px!important;}
</style>

</head>

<body>

<div class="app">

<div id="home">

<div class="top">
<div class="terminalControlBar">
<button class="terminalRoundButton" onclick="refreshHome()" aria-label="Piyasayı yenile">↻</button>
<div class="marketPill">
<span class="liveDot"></span>
<span id="marketSessionText" class="marketOpenText">Piyasa hazırlanıyor</span>
<span class="marketPillSep">•</span>
<span id="terminalClock">--:--:--</span>
</div>
<button class="terminalRoundButton" onclick="openProTool('tools')" aria-label="Araçlar">☷</button>
</div>
<div class="title referenceTitle">BIST PRO RADAR <span>CANLI</span></div>
<div id="liveStatus" class="liveStatus">
<span class="liveDot"></span>
<span id="liveStatusText">Tüm BIST canlı taramaya hazırlanıyor...</span>
</div>

<div class="searchRow">
<input
id="search"
class="search"
placeholder="🔎 Hisse ara: ASELS, THYAO, TUPRS..."
oninput="renderStocks()">
<button class="searchToolButton" onclick="openProTool('tools')" aria-label="Araçlar">⚙</button>
</div>

<div class="marketTape" aria-label="Canlı piyasa bandı">
<div id="tapeTrack" class="tapeTrack">
<div class="tapeSet">
<div class="tapeItem"><span class="tapeLabel">BIST 100</span><span class="tapePrice">Yükleniyor</span><span class="tapeChange flat">CANLI</span></div>
<div class="tapeItem"><span class="tapeLabel">DOLAR/TL</span><span class="tapePrice">Yükleniyor</span><span class="tapeChange flat">CANLI</span></div>
<div class="tapeItem"><span class="tapeLabel">EURO/TL</span><span class="tapePrice">Yükleniyor</span><span class="tapeChange flat">CANLI</span></div>
<div class="tapeItem"><span class="tapeLabel">GRAM ALTIN</span><span class="tapePrice">Yükleniyor</span><span class="tapeChange flat">CANLI</span></div>
<div class="tapeItem"><span class="tapeLabel">ÇEYREK ALTIN</span><span class="tapePrice">Yükleniyor</span><span class="tapeChange flat">CANLI</span></div>
</div>
<div class="tapeSet" aria-hidden="true">
<div class="tapeItem"><span class="tapeLabel">BIST 100</span><span class="tapePrice">Yükleniyor</span><span class="tapeChange flat">CANLI</span></div>
<div class="tapeItem"><span class="tapeLabel">DOLAR/TL</span><span class="tapePrice">Yükleniyor</span><span class="tapeChange flat">CANLI</span></div>
<div class="tapeItem"><span class="tapeLabel">EURO/TL</span><span class="tapePrice">Yükleniyor</span><span class="tapeChange flat">CANLI</span></div>
<div class="tapeItem"><span class="tapeLabel">GRAM ALTIN</span><span class="tapePrice">Yükleniyor</span><span class="tapeChange flat">CANLI</span></div>
<div class="tapeItem"><span class="tapeLabel">ÇEYREK ALTIN</span><span class="tapePrice">Yükleniyor</span><span class="tapeChange flat">CANLI</span></div>
</div>
</div>
</div>
</div>

<div id="terminalDashboard" class="terminalDashboard">
<div class="dashboardHero">
<div class="dashEyebrow">BIST PRO RADAR</div>
<div class="dashTitle">Günlük teknik görünüm hazırlanıyor</div>
<div class="dashText">Tüm BIST taranıyor; güçlü teknik koşullar oluşunca burada özetlenecek.</div>
</div>
</div>

<div id="kurBar" style="
    margin:12px 14px 4px;
    display:grid;
    grid-template-columns:repeat(2,1fr);
    gap:8px">

    <div class="stat">
        <span>DOLAR / TL</span>
        <b id="usdKur">-</b>
        <small id="usdAlt" style="color:#7f8ca0">yükleniyor...</small>
<div id="usdDeg" style="font-size:12px;margin-top:4px">—</div>
    </div>

    <div class="stat">
        <span>EURO / TL</span>
        <b id="eurKur">-</b>
        <small id="eurAlt" style="color:#7f8ca0">yükleniyor...</small>
<div id="eurDeg" style="font-size:12px;margin-top:4px">—</div>
    </div>

    <div class="stat">
        <span>GRAM ALTIN</span>
        <b id="gramKur">-</b>
        <small id="gramAlt" style="color:#7f8ca0">yükleniyor...</small>
<div id="gramDeg" style="font-size:12px;margin-top:4px">—</div>
    </div>

    <div class="stat">
        <span>ÇEYREK ALTIN</span>
        <b id="ceyrekKur">-</b>
        <small id="ceyrekAlt" style="color:#7f8ca0">yükleniyor...</small>
<div id="ceyrekDeg" style="font-size:12px;margin-top:4px">—</div>
    </div>
</div>

<div class="stats">
<div class="stat">
<b id="total">-</b>
<span>Taranan</span>
</div>

<div class="stat">
<b id="up">-</b>
<span>Yükselen</span>
</div>

<div class="stat">
<b id="strong">-</b>
<span>Güçlü Sinyal</span>
</div>

<div class="stat">
<b id="virmanCount">-</b>
<span>Virman Adayı</span>
</div>
</div>

<div class="tabs">
<button class="active" onclick="setFilter('all',this)">Tüm BIST</button>
<button onclick="setFilter('strong',this)">Güçlü</button>
<button onclick="setFilter('up',this)">Yükselen</button>
<button onclick="setFilter('down',this)">Düşen</button>
<button onclick="setFilter('volume',this)">Hacim</button>
<button onclick="setFilter('breakout',this)">Kırılım</button>
<button onclick="setFilter('momentum',this)">Momentum</button>
<button onclick="setFilter('pullback',this)">Toparlanma</button>
<button onclick="setFilter('favorites',this)">★ Favori</button>
<button onclick="setFilter('virman',this)">Virman</button>
</div>

<div class="proQuickTools" aria-label="Profesyonel araçlar">
<button class="quickTool featured" onclick="openProTool('basket')"><b>◈</b>Günlük Sepet</button>
<button class="quickTool greenTool" onclick="openProTool('portfolio')"><b>▣</b>Portföy</button>
<button class="quickTool orange" onclick="openProTool('alerts')"><b>♧</b>Alarmlar</button>
<button class="quickTool" onclick="applyQuickFilter('breakout')"><b>↗</b>Kırılım</button>
<button class="quickTool" onclick="applyQuickFilter('momentum')"><b>⚡</b>Momentum</button>
<button class="quickTool" onclick="applyQuickFilter('pullback')"><b>⌁</b>Toparlanma</button>
<button class="quickTool pink" onclick="applyQuickFilter('volume')"><b>▥</b>Hacim Radar</button>
<button class="quickTool" onclick="applyQuickFilter('favorites')"><b>★</b>Favoriler</button>
<button class="quickTool orange" onclick="openProTool('risk')"><b>◫</b>Risk Hesabı</button>
</div>

<div class="watchlistHeader">
<div class="watchlistTitleWrap">
<button class="watchlistAction" onclick="applyQuickFilter('favorites')">Favorilerim⌄</button>
<span id="watchlistTitle" class="watchlistTitle">Piyasa Radarı</span>
<span id="watchlistCount" class="watchlistCount">0</span>
</div>
<div class="watchlistActions">
<button class="watchlistAction" onclick="openProTool('tools')">Düzenle</button>
<button class="watchlistAction alert" onclick="openProTool('alerts')">♧ Alarmlar</button>
</div>
</div>

<div id="list" class="list">
<div class="loading">BIST verileri yükleniyor...</div>
</div>

</div>


<div id="detail" class="detail">

<div class="detailHeader">
<button class="back" onclick="closeDetail()">← Geri</button>
</div>

<div class="hero">
<div class="heroSymbol" id="dSymbol"></div>
<div class="heroName" id="dName"></div>
<div class="heroPrice" id="dPrice"></div>
<div id="dChange"></div>
</div>

<div class="detailTabs proTabs">
<button class="active" onclick="detailTab('summary',this)">ÖZET</button>
<button onclick="detailTab('chart',this)">GRAFİK</button>
<button onclick="detailTab('depth',this)">DERİNLİK</button>
<button onclick="detailTab('akd',this)">AKD</button>
<button onclick="detailTab('kademe',this)">KADEME</button>
<button onclick="detailTab('takas',this)">TAKAS</button>
<button onclick="detailTab('virman',this)">VİRMAN</button>
<button onclick="detailTab('signals',this)">SİNYALLER</button>
</div>

<div id="panel" class="panel"></div>

</div>

<div id="toolSheet" class="toolSheet" aria-hidden="true">
<div class="toolHead">
<button onclick="closeProTool()">← Geri</button>
<div id="toolHeadTitle" class="toolHeadTitle">Pro Araçlar</div>
</div>
<div id="toolPanel" class="toolPanel"></div>
</div>

</div>

<div id="toast" class="toast" role="status"></div>
<div class="portraitGuard">Uygulamayı rahat kullanmak için telefonu dik konuma çevir.</div>

<script>

let allStocks = []
let selected = null
let filter = "all"
let marketLoading = false
let marketDisplayLimit = 150
let lastPrices = new Map()
let priceMoves = new Map()
let marketMeta = {updated:0, refreshSeconds:10, lastError:null}
let dailyBasket = []
let marketBreadth = {}
let telegramBasketEnabled = false
let proCurrentTool = "basket"
let portfolioEditingSymbol = ""
let portfolioDraft = null
let toastTimer = null

function loadLocal(key,fallback){
    try{
        const parsed=JSON.parse(localStorage.getItem(key)||"")
        return parsed===null ? fallback : parsed
    }catch(e){
        return fallback
    }
}

function saveLocal(key,value){
    try{localStorage.setItem(key,JSON.stringify(value))}catch(e){}
}

let favorites = Array.from(new Set(
    (loadLocal("bist_pro_favorites",[])||[])
        .map(x=>String(x||"").trim().toUpperCase())
        .filter(Boolean)
))
let portfolio = (loadLocal("bist_pro_portfolio",[])||[])
    .filter(x=>x&&x.symbol)
let alerts = (loadLocal("bist_pro_alerts",[])||[])
    .filter(x=>x&&x.id)
let alertHistory = (loadLocal("bist_pro_alert_history",[])||[])
const savedMarket=loadLocal("bist_pro_last_market",null)

if(savedMarket && Array.isArray(savedMarket.stocks) && savedMarket.stocks.length){
    allStocks=savedMarket.stocks
    dailyBasket=Array.isArray(savedMarket.dailyBasket)?savedMarket.dailyBasket:[]
    marketBreadth=savedMarket.marketBreadth||{}
    telegramBasketEnabled=Boolean(savedMarket.telegramBasketEnabled)
    marketMeta={
        updated:Number(savedMarket.updated)||0,
        refreshSeconds:Number(savedMarket.refreshSeconds)||10,
        lastError:"cached"
    }
    setTimeout(()=>applyMarketUi(false),0)
}

function trNumber(value,defaultValue=0){
    if(value===null || value===undefined || value==="") return defaultValue
    if(typeof value==="number") return Number.isFinite(value)?value:defaultValue
    let raw=String(value).trim().replace(/\s/g,"")
    if(raw.includes(",") && raw.includes(".")){
        raw=raw.lastIndexOf(",")>raw.lastIndexOf(".")
            ? raw.replaceAll(".","").replace(",",".")
            : raw.replaceAll(",","")
    }else if(raw.includes(",")){
        raw=raw.replace(",",".")
    }
    const number=Number(raw)
    return Number.isFinite(number)?number:defaultValue
}

function isFavorite(symbol){
    return favorites.includes(String(symbol||"").toUpperCase())
}

function toggleFavorite(symbol){
    symbol=String(symbol||"").trim().toUpperCase()
    if(!symbol) return
    favorites=isFavorite(symbol)
        ? favorites.filter(x=>x!==symbol)
        : [...favorites,symbol]
    saveLocal("bist_pro_favorites",favorites)
    renderStocks()
    if(document.getElementById("toolSheet").classList.contains("open") && proCurrentTool==="portfolio"){
        renderProTool()
    }
}

function applyQuickFilter(nextFilter){
    filter=nextFilter
    marketDisplayLimit=150
    document.querySelectorAll(".tabs button").forEach(button=>{
        button.classList.toggle("active",button.getAttribute("onclick")===`setFilter('${nextFilter}',this)`)
    })
    renderStocks()
    window.scrollTo({top:0,behavior:"smooth"})
}

function showToast(message){
    const toast=document.getElementById("toast")
    if(!toast) return
    toast.textContent=message
    toast.classList.add("show")
    clearTimeout(toastTimer)
    toastTimer=setTimeout(()=>toast.classList.remove("show"),5200)
}

async function fetchJsonWithTimeout(url,options={},timeoutMs=8500){
    const controller=typeof AbortController!=="undefined" ? new AbortController() : null
    const timeout=controller
        ? setTimeout(()=>controller.abort(),timeoutMs)
        : null
    try{
        const response=await fetch(
            url,
            {...options,...(controller?{signal:controller.signal}:{})}
        )
        if(!response.ok) throw new Error("Bağlantı isteği başarısız")
        return await response.json()
    }finally{
        if(timeout) clearTimeout(timeout)
    }
}

function updateTerminalClock(){
    const clock=document.getElementById("terminalClock")
    const session=document.getElementById("marketSessionText")
    const now=new Date()

    try{
        if(clock){
            clock.textContent=new Intl.DateTimeFormat("tr-TR",{
                timeZone:"Europe/Istanbul",
                hour:"2-digit",
                minute:"2-digit",
                second:"2-digit",
                hour12:false
            }).format(now)
        }

        if(session){
            const parts=new Intl.DateTimeFormat("en-US",{
                timeZone:"Europe/Istanbul",
                weekday:"short",
                hour:"2-digit",
                minute:"2-digit",
                hour12:false
            }).formatToParts(now)
            const get=type=>parts.find(item=>item.type===type)?.value||""
            const weekday=get("weekday")
            const hour=Number(get("hour"))
            const minute=Number(get("minute"))
            const totalMinute=hour*60+minute
            const weekdayOpen=["Mon","Tue","Wed","Thu","Fri"].includes(weekday)
            const marketOpen=weekdayOpen && totalMinute>=580 && totalMinute<1090
            const online=allStocks.length>0 && !marketMeta.lastError

            session.textContent=marketOpen
                ? "Piyasa Açık"
                : (online ? "Piyasa Kapalı" : "Bağlanıyor")
            session.classList.toggle("marketOpenText",marketOpen)
            session.classList.toggle("marketClosedText",!marketOpen)
        }
    }catch(e){
        if(clock) clock.textContent="Canlı"
    }
}

function refreshHome(){
    showToast("Piyasa verisi yenileniyor...")
    load()
    loadMarketTicker()
    loadKurlar()
    updateTerminalClock()
}

function tryPortraitLock(){
    try{
        if(screen.orientation&&screen.orientation.lock){
            screen.orientation.lock("portrait").catch(()=>{})
        }
    }catch(e){}
}

tryPortraitLock()

if(window.Telegram && Telegram.WebApp){
    Telegram.WebApp.ready()
    Telegram.WebApp.expand()
}

function n(v,d=2){
    if(v===null || v===undefined) return "-"
    return Number(v).toLocaleString("tr-TR",{
        maximumFractionDigits:d
    })
}

function money(v){
    if(v===null || v===undefined) return "-"
    const x=Number(v)
    if(x>=1e9) return "₺"+(x/1e9).toFixed(2)+" Mr"
    if(x>=1e6) return "₺"+(x/1e6).toFixed(1)+" Mn"
    return "₺"+n(x,0)
}

function esc(v){
    return String(v??"")
        .replaceAll("&","&amp;")
        .replaceAll("<","&lt;")
        .replaceAll(">","&gt;")
        .replaceAll('"',"&quot;")
        .replaceAll("'","&#039;")
}

function tapePrice(item){
    if(!item || item.price===null || item.price===undefined || item.price==="") return "—"
    const price=Number(item&&item.price)
    if(!Number.isFinite(price)) return "—"
    const digits=Number(item.digits)
    const display=n(price,Number.isFinite(digits)?digits:2)
    return item.key==="bist100" ? display : "₺"+display
}

function renderMarketTape(items){
    const track=document.getElementById("tapeTrack")
    if(!track || !Array.isArray(items) || !items.length) return

    const content=items.map(item=>{
        const change=Number(item.change)
        const hasChange=Number.isFinite(change)
        const className=hasChange
            ? (change>0?"green":change<0?"red":"flat")
            : "flat"
        const changeText=hasChange
            ? "%"+(change>0?"+":"")+n(change,2)
            : "CANLI"
        return `<div class="tapeItem">
            <span class="tapeLabel">${esc(item.label)}</span>
            <span class="tapePrice">${tapePrice(item)}</span>
            <span class="tapeChange ${className}">${changeText}</span>
        </div>`
    }).join("")

    track.innerHTML=`<div class="tapeSet">${content}</div><div class="tapeSet" aria-hidden="true">${content}</div>`
    track.classList.remove("paused")
}

async function loadMarketTicker(){
    try{
        const data=await fetchJsonWithTimeout(
            "/api/ticker?ts="+Date.now(),
            {cache:"no-store"},
            7500
        )
        renderMarketTape((data.data||{}).items||[])
    }catch(e){
        console.log("Piyasa bandı hatası",e)
    }
}

function renderTerminalDashboard(){
    const host=document.getElementById("terminalDashboard")
    if(!host) return

    const breadth=marketBreadth||{}
    const lead=dailyBasket[0]
    const status=breadth.status||"PİYASA TARANIYOR"
    const ratio=Number(breadth.advance_decline_ratio)
    const ratioText=Number.isFinite(ratio)?ratio.toLocaleString("tr-TR",{maximumFractionDigits:2}):"—"
    const headline=lead
        ? `${lead.symbol} günlük sepetin ilk sırasında`
        : "Günlük teknik sepet hazırlanıyor"
    const detail=lead
        ? `${lead.label} • Teknik ${lead.technical_score}/100 • Rel. hacim ${n(lead.relative_volume,2)}x`
        : "Trend, RSI, MACD ve hacim koşulları birlikte kontrol ediliyor."

    host.innerHTML=`
    <div class="dashboardHero">
        <div class="dashEyebrow">${esc(status)}</div>
        <div class="dashTitle">${esc(headline)}</div>
        <div class="dashText">${esc(detail)}</div>
        <button class="dashButton" onclick="openProTool('basket')">Günlük 5’li Sepeti Aç →</button>
    </div>
    <div class="breadthGrid">
        <div class="breadthCard"><span>YÜKSELEN</span><b class="green">${breadth.rising??"—"}</b></div>
        <div class="breadthCard"><span>DÜŞEN</span><b class="red">${breadth.falling??"—"}</b></div>
        <div class="breadthCard"><span>Y/D ORANI</span><b>${ratioText}</b></div>
        <div class="breadthCard"><span>KIRILIM</span><b>${breadth.breakouts??"—"}</b></div>
    </div>`
}



const kurYuzdeOnceki = {
    usd:null,
    eur:null,
    gram:null,
    ceyrek:null
}

const oncekiKur = {
    usd:null,
    eur:null,
    gram:null,
    ceyrek:null
}


function kurDegisimYaz(key, yeni){
    const eski = kurYuzdeOnceki[key]
    kurYuzdeOnceki[key] = Number(yeni)

    const el = document.getElementById(key+"Deg")
    if(!el) return

    if(eski===null || yeni===null || yeni===undefined){
        el.innerHTML='<span style="color:#7f8ca0">—</span>'
        return
    }

    const fark = Number(yeni)-Number(eski)
    const yuzde = eski ? (fark/Number(eski))*100 : 0

    if(fark>0){
        el.innerHTML=
          '<span style="color:#20d391;font-weight:700">▲ '+
          n(fark,4)+' (%'+n(yuzde,2)+')</span>'
    }else if(fark<0){
        el.innerHTML=
          '<span style="color:#ff5c6c;font-weight:700">▼ '+
          n(fark,4)+' (%'+n(yuzde,2)+')</span>'
    }else{
        el.innerHTML=
          '<span style="color:#7f8ca0">■ 0,00 (%0,00)</span>'
    }
}

function yonOku(key, yeni, kart){
    const eski = oncekiKur[key]
    oncekiKur[key] = yeni

    if(eski===null || yeni===null || yeni===undefined){
        return '<span style="color:#7f8ca0;font-size:18px">—</span>'
    }

    if(Number(yeni) > Number(eski)){
        if(kart){
            kart.classList.remove("kurFlashDown")
            kart.classList.add("kurFlashUp")
            setTimeout(()=>kart.classList.remove("kurFlashUp"),850)
        }
        return '<span style="color:#20d391;font-size:20px;font-weight:900">↑</span>'
    }

    if(Number(yeni) < Number(eski)){
        if(kart){
            kart.classList.remove("kurFlashUp")
            kart.classList.add("kurFlashDown")
            setTimeout(()=>kart.classList.remove("kurFlashDown"),850)
        }
        return '<span style="color:#ff5c6c;font-size:20px;font-weight:900">↓</span>'
    }

    return '<span style="color:#7f8ca0;font-size:18px">—</span>'
}

function kurObj(x){
    if(!x) return {buy:null,sell:null}

    return {
        buy: x["buy"] ?? x["alis"] ?? null,
        sell: x["sell"] ?? x["satis"] ?? null
    }
}

async function loadKurlar(){
    try{
        const j=await fetchJsonWithTimeout(
            "/api/kurlar",
            {cache:"no-store"},
            7500
        )
        const d=j.data || {}

        function val(x,...keys){
            for(const k of keys){
                if(x && x[k]!==null && x[k]!==undefined){
                    return Number(x[k])
                }
            }
            return null
        }

        function yaz(id,altId,x,digit){
            if(!x) return

            const sell=val(x,"sell","satis","satış")
            const buy=val(x,"buy","alis","alış")
            const el=document.getElementById(id)
            const alt=document.getElementById(altId)

            if(sell!==null){
                const yeni="₺"+n(sell,digit)
                const eski=el.dataset.price

                if(eski!==String(sell)){
                    let ok='—'
                    let renk='#7f8ca0'

                    if(eski!==undefined && eski!==""){
                        if(sell>Number(eski)){
                            ok='▲'
                            renk='#20d391'
                        }else if(sell<Number(eski)){
                            ok='▼'
                            renk='#ff5c6c'
                        }
                    }

                    el.innerHTML=
                        '<span>'+yeni+'</span> '+
                        '<span style="color:'+renk+
                        ';font-size:13px;font-weight:800;margin-left:6px">'+
                        ok+
                        '</span>'

                    el.dataset.price=String(sell)
                }
            }

            if(buy!==null){
                const yeniAlt="Alış ₺"+n(buy,digit)
                if(alt.textContent!==yeniAlt){
                    alt.textContent=yeniAlt
                }
            }
        }

        yaz("usdKur","usdAlt",d.usd,4)
        yaz("eurKur","eurAlt",d.eur,4)
        yaz("gramKur","gramAlt",d.gram,2)
        yaz("ceyrekKur","ceyrekAlt",d.ceyrek,2)

    }catch(e){
        console.log("Kur hatası",e)
    }
}

function updateLiveStatus(){
    const status=document.getElementById("liveStatus")
    const text=document.getElementById("liveStatusText")
    if(!status || !text) return

    const now=Math.floor(Date.now()/1000)
    const age=marketMeta.updated ? Math.max(0,now-marketMeta.updated) : null
    const interval=Math.max(10,Number(marketMeta.refreshSeconds)||10)

    status.classList.toggle("warningLive",Boolean(marketMeta.lastError))

    if(age===null){
        text.textContent="Tüm BIST canlı tarama hazırlanıyor..."
        return
    }

    const prefix=marketMeta.lastError
        ? "Son başarılı BIST verisi"
        : "Canlı toplu tarama"
    const errorText=marketMeta.lastError
        ? (marketMeta.lastError==="cached"
            ? " • bağlantı kuruluyor"
            : " • sağlayıcı yeniden deneniyor")
        : ""

    text.textContent=
        `${prefix} • ${allStocks.length} hisse • ${age} sn önce • ${interval} sn yenileme${errorText}`
}

function preparePriceMoves(stocks){
    const nextPrices=new Map()
    priceMoves=new Map()

    stocks.forEach(stock=>{
        const symbol=stock.symbol||""
        const price=Number(stock.price)
        if(!symbol || !Number.isFinite(price)) return

        const old=lastPrices.get(symbol)
        if(old!==undefined && old!==price){
            priceMoves.set(symbol,price>old ? "tickUp" : "tickDown")
        }
        nextPrices.set(symbol,price)
    })

    lastPrices=nextPrices
}

function applyMarketUi(checkAlerts=true){
    const total=document.getElementById("total")
    const up=document.getElementById("up")
    const strong=document.getElementById("strong")
    const virmanCount=document.getElementById("virmanCount")

    if(total) total.textContent=allStocks.length
    if(up) up.textContent=allStocks.filter(x=>(x.change||0)>0).length
    if(strong) strong.textContent=allStocks.filter(x=>x.score>=110).length
    if(virmanCount){
        virmanCount.textContent=allStocks.filter(
            x=>x.institutional&&x.institutional.candidate
        ).length
    }

    updateLiveStatus()
    renderTerminalDashboard()
    renderStocks()
    if(checkAlerts) evaluateAlerts(allStocks)

    const sheet=document.getElementById("toolSheet")
    if(sheet && sheet.classList.contains("open") && proCurrentTool==="basket"){
        renderProTool()
    }
}

async function load(){
    if(marketLoading) return
    marketLoading=true

    try{
        const j=await fetchJsonWithTimeout(
            "/api/market?ts="+Date.now(),
            {cache:"no-store"},
            9000
        )
        const stocks=j.stocks || []

        preparePriceMoves(stocks)
        allStocks=stocks
        dailyBasket=Array.isArray(j.daily_basket)?j.daily_basket:[]
        marketBreadth=j.market_breadth||{}
        telegramBasketEnabled=Boolean(j.telegram_daily_basket_enabled)
        marketMeta={
            updated:Number(j.updated)||0,
            refreshSeconds:Number(j.refresh_seconds)||10,
            lastError:j.last_error||null
        }
        saveLocal("bist_pro_last_market",{
            stocks:allStocks,
            dailyBasket,
            marketBreadth,
            telegramBasketEnabled,
            updated:marketMeta.updated,
            refreshSeconds:marketMeta.refreshSeconds,
            cachedAt:Date.now()
        })
        applyMarketUi(true)
    }catch(e){
        marketMeta.lastError="connection"
        updateLiveStatus()

        if(!allStocks.length){
            document.getElementById("list").innerHTML=
                '<div class="warning">Veriler alınamadı. Uygulama otomatik olarak yeniden deniyor.</div>'
        }
    }finally{
        marketLoading=false
    }
}

function showMoreStocks(){
    marketDisplayLimit+=150
    renderStocks()
}

function setFilter(f,el){
    filter=f
    marketDisplayLimit=150

    document.querySelectorAll(".tabs button")
        .forEach(b=>b.classList.remove("active"))

    el.classList.add("active")
    renderStocks()
}

function stockHue(symbol){
    let hash=0
    for(const letter of String(symbol||"")){
        hash=((hash<<5)-hash)+letter.charCodeAt(0)
        hash|=0
    }
    return 170+Math.abs(hash%150)
}

function marketUpdateTime(){
    const updated=Number(marketMeta.updated)
    if(!updated) return "--:--"
    try{
        return new Date(updated*1000).toLocaleTimeString("tr-TR",{
            timeZone:"Europe/Istanbul",
            hour:"2-digit",
            minute:"2-digit"
        })
    }catch(e){
        return "--:--"
    }
}

function updateListHeader(total){
    const titles={
        all:"Piyasa Radarı",
        strong:"Güçlü Sinyaller",
        up:"Yükselenler",
        down:"Düşenler",
        volume:"Hacim Radarı",
        breakout:"Kırılım Radarı",
        momentum:"Momentum",
        pullback:"Toparlanma",
        favorites:"Favorilerim",
        virman:"Kurumsal Radar"
    }
    const title=document.getElementById("watchlistTitle")
    const count=document.getElementById("watchlistCount")
    if(title) title.textContent=titles[filter]||"Piyasa Radarı"
    if(count) count.textContent=Number(total||0).toLocaleString("tr-TR")
}

function renderStocks(){
    let q=document.getElementById("search").value
        .trim()
        .toUpperCase()

    let arr=[...allStocks]

    if(q){
        arr=arr.filter(x=>
            (x.symbol||"").toUpperCase().includes(q) ||
            (x.description||"").toUpperCase().includes(q)
        )
    }

    if(filter==="strong")
        arr=arr.filter(x=>x.score>=110)

    if(filter==="up")
        arr=arr.filter(x=>(x.change||0)>0)

    if(filter==="down")
        arr=arr.filter(x=>(x.change||0)<0)

    if(filter==="volume")
        arr.sort((a,b)=>(b.relative_volume||0)-(a.relative_volume||0))

    if(filter==="breakout")
        arr=arr
            .filter(x=>x.technical&&x.technical.breakout)
            .sort((a,b)=>(b.technical.score||0)-(a.technical.score||0))

    if(filter==="momentum")
        arr=arr
            .filter(x=>x.technical&&x.technical.momentum)
            .sort((a,b)=>(b.technical.score||0)-(a.technical.score||0))

    if(filter==="pullback")
        arr=arr
            .filter(x=>x.technical&&x.technical.pullback)
            .sort((a,b)=>(b.technical.score||0)-(a.technical.score||0))

    if(filter==="favorites")
        arr=arr.filter(x=>isFavorite(x.symbol))

    if(filter==="virman")
        arr=arr
            .filter(x=>x.institutional&&x.institutional.candidate)
            .sort((a,b)=>(b.institutional.score||0)-(a.institutional.score||0))

    const totalMatched=arr.length
    updateListHeader(totalMatched)

    // Telefonu yüzlerce kartla ilk açılışta yormadan tüm hisseleri bellekte
    // tutuyoruz. Arama tüm BIST listesini tarar; aşağıdaki düğmeyle listenin
    // tamamı parça parça açılır.
    if(!q && filter==="all")
        arr=arr.slice(0,marketDisplayLimit)

    const list=document.getElementById("list")

    if(!arr.length){
        list.innerHTML=filter==="virman"
            ? '<div class="warning">Şu anda belirlenen güçlü eşikleri geçen virman/kurumsal hareket adayı yok.</div>'
            : filter==="favorites"
                ? '<div class="warning">Henüz favori eklemedin. Hisse kartındaki yıldız düğmesine basarak liste oluşturabilirsin.</div>'
            : '<div class="loading">Hisse bulunamadı.</div>'
        return
    }

    list.innerHTML=arr.map(s=>{
        const change=Number(s.change)
        const cls=Number.isFinite(change) && change>=0 ? "green":"red"
        const v=s.institutional||{}
        const technical=s.technical||{}
        const vClass=(v.direction||"").includes("SATIŞ") ? "sell":"buy"
        const candidateClass=v.candidate ? " virmanCandidate":""
        const favoriteClass=isFavorite(s.symbol)?" active":""
        const symbol=String(s.symbol||"").toUpperCase()
        const avatar=symbol.slice(0,2)||"BI"
        const transactionValue=Number(s.price||0)*Number(s.volume||0)
        const setup=technical.label&&technical.label!=="NÖTR İZLE"
            ? `<div class="stockSetup">${esc(technical.label)} • ${technical.score||0}/100</div>`
            : ""

        return `
        <div class="stock${candidateClass}" onclick='openDetail(${JSON.stringify(s.symbol)})'>
            <div class="stockTop">
                <div class="stockAvatar" style="--avatar-hue:${stockHue(symbol)}">${esc(avatar)}</div>
                <div class="stockIdentity">
                    <div class="stockTitleLine">
                        <div class="symbol">${esc(symbol)}</div>
                        <button class="favoriteButton${favoriteClass}" aria-label="Favoriye ekle" onclick='event.stopPropagation();toggleFavorite(${JSON.stringify(s.symbol)})'>${isFavorite(s.symbol)?"★":"☆"}</button>
                    </div>
                    <div class="desc">${esc(s.description||"")}</div>
                </div>
                <div class="stockQuote">
                    <div class="price ${priceMoves.get(s.symbol)||""}">₺${n(s.price)}</div>
                    <div class="changeBadge ${cls}">%${n(s.change)}</div>
                </div>
            </div>
            <div class="stockMetaLine">
                <span><strong>İşlem</strong> ${money(transactionValue)}</span>
                <span><strong>Hacim</strong> ${n(s.relative_volume,2)}x</span>
                <span><strong>RSI</strong> ${n(s.rsi,1)}</span>
                <span>${marketUpdateTime()}</span>
            </div>
            ${v.candidate ? `<div class="virmanTag ${vClass}">⚠ ${esc(v.direction||"Kurumsal hareket")} • ${v.score||0}/100</div>` : ""}
            ${setup}
        </div>`
    }).join("")

    if(!q && filter==="all" && arr.length<totalMatched){
        const remaining=totalMatched-arr.length
        list.innerHTML+=
            `<button class="showMore" onclick="showMoreStocks()">${remaining} hisse daha göster</button>`
    }
}

function openProTool(tool){
    proCurrentTool=tool
    const titles={
        basket:"Günlük Teknik Sepet",
        portfolio:"Portföy & Maliyet",
        alerts:"Akıllı Alarmlar",
        risk:"Risk Hesaplayıcı",
        tools:"Komuta Merkezi"
    }
    document.getElementById("toolHeadTitle").textContent=titles[tool]||"Pro Araçlar"
    const sheet=document.getElementById("toolSheet")
    sheet.classList.add("open")
    sheet.setAttribute("aria-hidden","false")
    tryPortraitLock()
    renderProTool()
    window.scrollTo(0,0)
}

function closeProTool(){
    const sheet=document.getElementById("toolSheet")
    sheet.classList.remove("open")
    sheet.setAttribute("aria-hidden","true")
    portfolioEditingSymbol=""
    portfolioDraft=null
}

function renderProTool(){
    const panel=document.getElementById("toolPanel")
    if(!panel) return
    if(proCurrentTool==="basket") panel.innerHTML=renderBasketTool()
    else if(proCurrentTool==="portfolio") panel.innerHTML=renderPortfolioTool()
    else if(proCurrentTool==="alerts") panel.innerHTML=renderAlertsTool()
    else if(proCurrentTool==="risk") panel.innerHTML=renderRiskTool()
    else if(proCurrentTool==="tools") panel.innerHTML=renderToolsTool()
    else panel.innerHTML='<div class="warning">Araç bulunamadı.</div>'
}

function renderToolsTool(){
    const cards=[
        ["◈","Günlük Sepet","Bugünün 5 teknik takip adayı","openProTool('basket')",""],
        ["▣","Portföy","Maliyet ve anlık kâr/zarar","openProTool('portfolio')",""],
        ["♧","Alarmlar","Fiyat, yüzde ve hacim uyarıları","openProTool('alerts')",""],
        ["◫","Risk Hesabı","Lot ve risk/ödül hesaplayıcı","openProTool('risk')","warningCard"],
        ["↗","Kırılım Radar","Hacimli teknik kırılım adayları","closeProTool();applyQuickFilter('breakout')",""],
        ["⚡","Momentum","Güçlü teknik momentum filtresi","closeProTool();applyQuickFilter('momentum')",""],
        ["⌁","Toparlanma","EMA20 çevresi toparlanma adayları","closeProTool();applyQuickFilter('pullback')",""],
        ["▥","Kurumsal Radar","Olası kurumsal hareket adayları","closeProTool();applyQuickFilter('virman')","pinkCard"],
        ["★","Favorilerim","Kendi seçtiğin hisseler","closeProTool();applyQuickFilter('favorites')",""],
        ["◉","Hacim Radar","Göreli hacme göre sıralama","closeProTool();applyQuickFilter('volume')",""]
    ]

    const cardsHtml=cards.map(([icon,title,description,action,extra])=>`
        <button class="commandCard ${extra}" onclick="${action}">
            <span class="commandIcon">${icon}</span>
            <b>${title}</b>
            <span>${description}</span>
        </button>`).join("")

    return `
        <div class="sheetHero">
            <div class="dashEyebrow">BIST PRO RADAR</div>
            <h2>Tüm araçlar tek yerde</h2>
            <p>Tarama, teknik sepet, alarm, portföy ve kurumsal radar ekranlarını buradan açabilirsin.</p>
        </div>
        <div class="commandGrid">${cardsHtml}</div>
        <div class="warning" style="margin-top:12px">Kurumsal radar ve teknik sepet, halka açık fiyat/hacim verisinden kural tabanlı üretilir. Gerçek AKD veya kesin virman kaydı değildir.</div>
    `
}

function riskClass(level){
    if(level==="YÜKSEK") return "red"
    if(level==="ORTA") return "green"
    return "green"
}

function renderBasketTool(){
    const breadth=marketBreadth||{}
    const basketRows=dailyBasket.map(item=>{
        const reasons=(item.reasons||[]).map(reason=>`<li>${esc(reason)}</li>`).join("")
        return `<div class="basketCard">
            <div class="basketTop">
                <div>
                    <span class="basketRank">${item.rank||"—"}</span><span class="basketSymbol">${esc(item.symbol)}</span>
                    <div class="basketName">${esc(item.description||"")}</div>
                </div>
                <div>
                    <div class="basketPrice">₺${n(item.price)}</div>
                    <div class="${Number(item.change)>=0?"green":"red"}" style="text-align:right">%${n(item.change)}</div>
                </div>
            </div>
            <div class="basketMeta">
                <b>${esc(item.label||"TEKNİK İZLE")}</b> • Teknik ${item.technical_score||0}/100 • Rel. Hacim ${n(item.relative_volume,2)}x • Risk <span class="${riskClass(item.risk_level)}">${esc(item.risk_level||"-")}</span>
            </div>
            ${reasons?`<ul class="basketReasons">${reasons}</ul>`:""}
            <div class="levelGrid">
                <div class="levelCell"><span>TAKİP BANDI</span><b>₺${n(item.watch_band_low)}–${n(item.watch_band_high)}</b></div>
                <div class="levelCell"><span>RİSK SINIRI</span><b>₺${n(item.risk_stop)}</b></div>
                <div class="levelCell"><span>İZLEME HEDEFİ</span><b>₺${n(item.watch_target_1)}</b></div>
            </div>
            <div class="sheetActions">
                <button class="sheetButton secondary" onclick="openStockFromSheet('${esc(item.symbol)}')">Detayı Aç</button>
                <button class="sheetButton" onclick="prefillPortfolio('${esc(item.symbol)}')">Portföye Ekle</button>
            </div>
        </div>`
    }).join("")

    return `
        <div class="sheetHero">
            <div class="dashEyebrow">OTOMATİK GÜNLÜK RADAR</div>
            <h2>Günlük 5’li Teknik Sepet</h2>
            <p>Trend, RSI, MACD, göreli hacim ve günlük fiyat hareketinin birlikte uyduğu hisseler sıralanır. Bu bir takip aracıdır; emir veya yatırım tavsiyesi değildir.</p>
        </div>
        <div class="breadthGrid" style="margin-top:10px">
            <div class="breadthCard"><span>YÜKSELEN</span><b class="green">${breadth.rising??"—"}</b></div>
            <div class="breadthCard"><span>DÜŞEN</span><b class="red">${breadth.falling??"—"}</b></div>
            <div class="breadthCard"><span>MOMENTUM</span><b>${breadth.momentum??"—"}</b></div>
            <div class="breadthCard"><span>HACİM+</span><b>${breadth.volume_bursts??"—"}</b></div>
        </div>
        <div class="toolSectionTitle">BUGÜNÜN TEKNİK İZLEME LİSTESİ</div>
        ${basketRows||'<div class="warning">Şu an güçlü teknik eşikleri geçen sepet adayı bulunamadı. Piyasa yenilendikçe otomatik tekrar hesaplanır.</div>'}
        <div class="warning" style="margin-top:12px">Takip bandı, risk sınırı ve hedef alanları algoritmik izleme seviyeleridir. Kendi riskine göre karar ver; kesin getiri veya al-sat önerisi değildir.</div>
        <div class="toolSectionTitle">TELEGRAM RAPORU</div>
        <div class="portfolioCard">${telegramBasketEnabled?'✅ Telegram günlük sepet raporu aktif. Her iş günü belirlenen saatte en fazla bir rapor gönderilir.':'ℹ️ Telegram günlük sepet raporu şu an kapalı. İstersen Render ortam değişkeninden ayrıca açılabilir.'}</div>
    `
}

function openStockFromSheet(symbol){
    closeProTool()
    openDetail(String(symbol).toUpperCase())
}

function prefillPortfolio(symbol){
    symbol=String(symbol||"").toUpperCase()
    const stock=allStocks.find(item=>String(item.symbol).toUpperCase()===symbol)
    const existing=portfolio.find(item=>String(item.symbol).toUpperCase()===symbol)
    portfolioDraft=existing||{
        symbol,
        quantity:"",
        cost:stock?stock.price:"",
        target:"",
        stop:""
    }
    portfolioEditingSymbol=symbol
    proCurrentTool="portfolio"
    document.getElementById("toolHeadTitle").textContent="Portföy & Maliyet"
    renderProTool()
}

function currentStock(symbol){
    const key=String(symbol||"").toUpperCase()
    return allStocks.find(item=>String(item.symbol||"").toUpperCase()===key)||null
}

function positionMetrics(position){
    const quantity=trNumber(position.quantity,0)
    const cost=trNumber(position.cost,0)
    const stock=currentStock(position.symbol)
    const price=stock?trNumber(stock.price,null):null
    const totalCost=quantity*cost
    const currentValue=price===null?null:quantity*price
    const pnl=currentValue===null?null:currentValue-totalCost
    const pnlPct=totalCost>0&&pnl!==null?(pnl/totalCost)*100:null
    return {quantity,cost,stock,price,totalCost,currentValue,pnl,pnlPct}
}

function renderPortfolioTool(){
    const draft=portfolioDraft
        || portfolio.find(item=>String(item.symbol).toUpperCase()===portfolioEditingSymbol)
        || {symbol:"",quantity:"",cost:"",target:"",stop:""}
    const rows=portfolio.map(position=>{
        const data=positionMetrics(position)
        const pnlClass=(data.pnl||0)>=0?"green":"red"
        const priceText=data.price===null?"Veri yok":"₺"+n(data.price)
        const pnlText=data.pnl===null?"—":`${data.pnl>=0?"+":""}${money(data.pnl)} (%${n(data.pnlPct)})`
        return `<div class="portfolioItem">
            <div class="portfolioRowTop">
                <div>
                    <div class="basketSymbol">${esc(position.symbol)}</div>
                    <div class="portfolioInfo">${n(data.quantity,0)} lot • Maliyet ₺${n(data.cost)} • Son ${priceText}</div>
                </div>
                <div style="text-align:right">
                    <b class="${pnlClass}">${pnlText}</b>
                    <div class="portfolioInfo">${position.target?`Hedef ₺${n(position.target)}`:"Hedef yok"}${position.stop?` • Stop ₺${n(position.stop)}`:""}</div>
                </div>
            </div>
            <div class="sheetActions">
                <button class="iconTextButton" onclick="editPortfolio('${esc(position.symbol)}')">Düzenle</button>
                <button class="iconTextButton danger" onclick="deletePortfolio('${esc(position.symbol)}')">Sil</button>
            </div>
        </div>`
    }).join("")

    const totals=portfolio.reduce((acc,position)=>{
        const data=positionMetrics(position)
        acc.cost+=data.totalCost
        if(data.currentValue!==null) acc.value+=data.currentValue
        return acc
    },{cost:0,value:0})
    const totalPnl=totals.value-totals.cost
    const totalPnlPct=totals.cost>0?(totalPnl/totals.cost)*100:0

    return `
        <div class="sheetHero">
            <div class="dashEyebrow">KİŞİSEL PORTFÖY</div>
            <h2>Maliyet ve Anlık Kâr/Zarar</h2>
            <p>Veriler yalnızca bu telefonun tarayıcısında saklanır. Hisse, lot, maliyet, hedef ve stop seviyeni istediğin zaman güncelleyebilirsin.</p>
        </div>
        <div class="portfolioTotals">
            <div><span>TOPLAM MALİYET</span><b>${money(totals.cost)}</b></div>
            <div><span>GÜNCEL DEĞER</span><b>${portfolio.length?money(totals.value):"—"}</b></div>
            <div><span>KÂR / ZARAR</span><b class="${totalPnl>=0?"green":"red"}">${portfolio.length?`${totalPnl>=0?"+":""}${money(totalPnl)} (%${n(totalPnlPct)})`:"—"}</b></div>
        </div>
        <div class="toolSectionTitle">POZİSYON EKLE / GÜNCELLE</div>
        <div class="portfolioCard">
            <label class="formLabel">HİSSE KODU</label>
            <input id="pSymbol" class="formInput" value="${esc(draft.symbol||"")}" placeholder="Örn. ASELS" autocapitalize="characters">
            <div class="formGrid">
                <div><label class="formLabel">LOT ADEDİ</label><input id="pQuantity" class="formInput" inputmode="decimal" value="${esc(draft.quantity??"")}" placeholder="Örn. 250"></div>
                <div><label class="formLabel">ORT. MALİYET</label><input id="pCost" class="formInput" inputmode="decimal" value="${esc(draft.cost??"")}" placeholder="Örn. 36,42"></div>
                <div><label class="formLabel">HEDEF FİYAT (ops.)</label><input id="pTarget" class="formInput" inputmode="decimal" value="${esc(draft.target??"")}" placeholder="Örn. 38,00"></div>
                <div><label class="formLabel">STOP FİYAT (ops.)</label><input id="pStop" class="formInput" inputmode="decimal" value="${esc(draft.stop??"")}" placeholder="Örn. 35,40"></div>
            </div>
            <div class="sheetActions"><button class="sheetButton" onclick="savePortfolio()">${portfolioEditingSymbol?"Pozisyonu Güncelle":"Portföye Kaydet"}</button><button class="sheetButton secondary" onclick="clearPortfolioDraft()">Temizle</button></div>
        </div>
        <div class="toolSectionTitle">AÇIK POZİSYONLAR</div>
        <div class="portfolioCard portfolioList">${rows||'<div class="warning">Henüz portföyüne hisse eklemedin. Yukarıdan maliyet ve lot girerek başlayabilirsin.</div>'}</div>
    `
}

function clearPortfolioDraft(){
    portfolioEditingSymbol=""
    portfolioDraft=null
    renderProTool()
}

function savePortfolio(){
    const symbol=String(document.getElementById("pSymbol").value||"").trim().toUpperCase()
    const quantity=trNumber(document.getElementById("pQuantity").value,0)
    const cost=trNumber(document.getElementById("pCost").value,0)
    const target=trNumber(document.getElementById("pTarget").value,0)
    const stop=trNumber(document.getElementById("pStop").value,0)

    if(!symbol || quantity<=0 || cost<=0){
        showToast("Hisse kodu, lot ve maliyet alanlarını doğru doldur.")
        return
    }
    if(!currentStock(symbol)){
        showToast("Bu kod mevcut BIST listesinde bulunamadı. Hisse kodunu kontrol et.")
        return
    }

    portfolio=portfolio.filter(item=>String(item.symbol).toUpperCase()!==symbol)
    portfolio.push({
        symbol,
        quantity,
        cost,
        target:target>0?target:"",
        stop:stop>0?stop:"",
        updatedAt:Date.now()
    })
    portfolio.sort((a,b)=>String(a.symbol).localeCompare(String(b.symbol),"tr"))
    saveLocal("bist_pro_portfolio",portfolio)
    portfolioEditingSymbol=""
    portfolioDraft=null
    renderProTool()
    showToast(symbol+" portföye kaydedildi.")
}

function editPortfolio(symbol){
    const item=portfolio.find(position=>String(position.symbol).toUpperCase()===String(symbol).toUpperCase())
    if(!item) return
    portfolioEditingSymbol=String(symbol).toUpperCase()
    portfolioDraft={...item}
    renderProTool()
}

function deletePortfolio(symbol){
    portfolio=portfolio.filter(position=>String(position.symbol).toUpperCase()!==String(symbol).toUpperCase())
    saveLocal("bist_pro_portfolio",portfolio)
    renderProTool()
    showToast(String(symbol).toUpperCase()+" portföyden silindi.")
}

function renderRiskTool(){
    const draft=loadLocal("bist_pro_risk_draft",{capital:"",riskPercent:"1",entry:"",stop:"",target:""})
    return `
        <div class="sheetHero">
            <div class="dashEyebrow">POZİSYON BOYUTU</div>
            <h2>Risk Hesaplayıcı</h2>
            <p>Sermayenin ne kadarını riske edeceğini, giriş–stop farkına göre maksimum lotu ve hedefteki olası sonucu hesaplar.</p>
        </div>
        <div class="riskCard">
            <div class="formGrid">
                <div><label class="formLabel">SERMAYE (TL)</label><input id="rCapital" class="formInput" inputmode="decimal" value="${esc(draft.capital??"")}" placeholder="Örn. 30000"></div>
                <div><label class="formLabel">RİSK %</label><input id="rRisk" class="formInput" inputmode="decimal" value="${esc(draft.riskPercent??"1")}" placeholder="Örn. 1"></div>
                <div><label class="formLabel">GİRİŞ FİYATI</label><input id="rEntry" class="formInput" inputmode="decimal" value="${esc(draft.entry??"")}" placeholder="Örn. 36,42"></div>
                <div><label class="formLabel">STOP FİYATI</label><input id="rStop" class="formInput" inputmode="decimal" value="${esc(draft.stop??"")}" placeholder="Örn. 35,40"></div>
                <div class="wide"><label class="formLabel">HEDEF FİYAT</label><input id="rTarget" class="formInput" inputmode="decimal" value="${esc(draft.target??"")}" placeholder="Örn. 38,00"></div>
            </div>
            <div class="sheetActions"><button class="sheetButton" onclick="calculateRisk()">Hesapla</button><button class="sheetButton secondary" onclick="useSelectedForRisk()">Seçili Hisseyi Kullan</button></div>
            <div id="riskResult" class="riskResult">Değerleri girip <b>Hesapla</b> düğmesine bas.</div>
        </div>
        <div class="warning">Bu hesap yalnızca matematiksel risk/ödül aracıdır. Komisyon, kayma, tavan–taban ve likidite riski içermez.</div>
    `
}

function useSelectedForRisk(){
    if(!selected){
        showToast("Önce bir hissenin detayını aç veya giriş fiyatını kendin gir.")
        return
    }
    document.getElementById("rEntry").value=selected.price??""
    document.getElementById("rStop").value=Number(selected.price)*0.97
    document.getElementById("rTarget").value=Number(selected.price)*1.03
}

function calculateRisk(){
    const capital=trNumber(document.getElementById("rCapital").value,0)
    const riskPercent=trNumber(document.getElementById("rRisk").value,0)
    const entry=trNumber(document.getElementById("rEntry").value,0)
    const stop=trNumber(document.getElementById("rStop").value,0)
    const target=trNumber(document.getElementById("rTarget").value,0)
    const result=document.getElementById("riskResult")
    saveLocal("bist_pro_risk_draft",{capital,riskPercent,entry,stop,target})

    if(capital<=0 || riskPercent<=0 || entry<=0 || stop<=0 || target<=0 || stop>=entry || target<=entry){
        result.innerHTML='<span class="red">Sermaye ve fiyatları gir. Uzun pozisyon hesabında stop girişten düşük, hedef girişten yüksek olmalı.</span>'
        return
    }

    const maxRisk=capital*(riskPercent/100)
    const riskPerLot=entry-stop
    const lotsByRisk=Math.floor(maxRisk/riskPerLot)
    const lotsByCash=Math.floor(capital/entry)
    const lots=Math.max(0,Math.min(lotsByRisk,lotsByCash))
    const positionValue=lots*entry
    const possibleLoss=lots*riskPerLot
    const possibleProfit=lots*(target-entry)
    const rr=possibleLoss>0?possibleProfit/possibleLoss:0

    result.innerHTML=`<div class="riskResultGrid">
        <div><span>MAKS. LOT</span><b>${n(lots,0)}</b></div>
        <div><span>POZİSYON TUTARI</span><b>${money(positionValue)}</b></div>
        <div><span>STOPTA RİSK</span><b class="red">${money(possibleLoss)}</b></div>
        <div><span>HEDEFTE SONUÇ</span><b class="green">${money(possibleProfit)}</b></div>
        <div><span>RİSK / ÖDÜL</span><b>1 : ${n(rr,2)}</b></div>
        <div><span>SERMAYE KULLANIMI</span><b>%${n(capital?positionValue/capital*100:0,1)}</b></div>
    </div>`
}

function alertTypeLabel(type){
    const labels={
        price_above:"Fiyat üstüne çıkınca",
        price_below:"Fiyat altına inince",
        change_above:"Günlük değişim % üstüne çıkınca",
        relative_volume:"Göreli hacim x üstüne çıkınca"
    }
    return labels[type]||"Alarm"
}

function alertDescription(alert){
    const symbol=alert.symbol==="ALL"?"TÜM BIST":alert.symbol
    const suffix=alert.type==="relative_volume"?"x":alert.type==="change_above"?"%":" TL"
    return `${symbol} • ${alertTypeLabel(alert.type)} ${n(alert.value,2)}${suffix}`
}

function renderAlertsTool(){
    const rows=[...alerts].sort((a,b)=>(b.createdAt||0)-(a.createdAt||0)).map(alert=>{
        const last=alert.lastTriggeredAt
            ? new Date(alert.lastTriggeredAt).toLocaleString("tr-TR",{hour:"2-digit",minute:"2-digit",day:"2-digit",month:"2-digit"})
            : "Henüz tetiklenmedi"
        return `<div class="alertItem">
            <div class="portfolioRowTop">
                <div><b>${esc(alert.symbol==="ALL"?"TÜM BIST":alert.symbol)}</b><div class="alertDescription">${esc(alertDescription(alert))}</div></div>
                <button class="iconTextButton danger" onclick="deleteAlert('${esc(alert.id)}')">Sil</button>
            </div>
            <div class="portfolioInfo">Son durum: ${esc(last)} • Aynı alarm 30 dakika beklemeden tekrar çalmaz.</div>
        </div>`
    }).join("")
    const history=[...alertHistory].slice(0,3).map(item=>
        `<div class="alertDescription">• ${esc(item.text)} <span style="color:#71839b">(${new Date(item.at).toLocaleTimeString("tr-TR",{hour:"2-digit",minute:"2-digit"})})</span></div>`
    ).join("")

    return `
        <div class="sheetHero">
            <div class="dashEyebrow">AÇIK SAYFA UYARILARI</div>
            <h2>Akıllı Alarm Merkezi</h2>
            <p>Fiyat, günlük değişim ve göreli hacim koşullarını kaydet. Uygulama açıkken her canlı yenilemede kontrol edilir; uygun olursa ekranda ve izin verirsen telefonda uyarı çıkar.</p>
        </div>
        <div class="alertCard">
            <label class="formLabel">HİSSE KODU</label>
            <input id="aSymbol" class="formInput" placeholder="Örn. ASELS veya TÜM BIST" autocapitalize="characters">
            <div class="formGrid">
                <div><label class="formLabel">KOŞUL</label>
                    <select id="aType" class="formSelect">
                        <option value="price_above">Fiyat üstüne çıkınca</option>
                        <option value="price_below">Fiyat altına inince</option>
                        <option value="change_above">Günlük değişim % üstü</option>
                        <option value="relative_volume">Göreli hacim x üstü</option>
                    </select>
                </div>
                <div><label class="formLabel">SEVİYE</label><input id="aValue" class="formInput" inputmode="decimal" placeholder="Örn. 1,50"></div>
            </div>
            <div class="sheetActions"><button class="sheetButton" onclick="saveAlert()">Alarmı Kaydet</button><button class="sheetButton secondary" onclick="requestBrowserNotifications()">Telefon Bildirimi</button></div>
        </div>
        <div class="warning">“TÜM BIST” seçeneğini sadece günlük değişim veya göreli hacim alarmında kullan. Tarayıcı/uygulama kapalıyken bu yerel alarmlar çalışmaz; sunucu tarafı Telegram günlük sepet raporu ayrı özelliktir.</div>
        <div class="toolSectionTitle">KAYITLI ALARMLAR</div>
        <div class="alertCard alertList">${rows||'<div class="portfolioInfo">Henüz alarm eklemedin.</div>'}</div>
        <div class="toolSectionTitle">SON UYARILAR</div>
        <div class="alertCard">${history||'<div class="portfolioInfo">Henüz tetiklenen alarm yok.</div>'}</div>
    `
}

function saveAlert(){
    let symbol=String(document.getElementById("aSymbol").value||"").trim().toUpperCase()
    const type=document.getElementById("aType").value
    const value=trNumber(document.getElementById("aValue").value,0)
    if(["TÜM BIST","TUM BIST","ALL","*"].includes(symbol)) symbol="ALL"

    if(!symbol || value<=0){
        showToast("Hisse kodu ve alarm seviyesini gir.")
        return
    }
    if(symbol==="ALL" && ["price_above","price_below"].includes(type)){
        showToast("TÜM BIST için fiyat alarmı yerine yüzde veya hacim alarmını kullan.")
        return
    }
    if(symbol!=="ALL" && !currentStock(symbol)){
        showToast("Bu hisse kodu mevcut BIST listesinde bulunamadı.")
        return
    }

    alerts.push({
        id:"alert_"+Date.now()+"_"+Math.random().toString(16).slice(2),
        symbol,
        type,
        value,
        createdAt:Date.now(),
        lastTriggeredAt:0
    })
    saveLocal("bist_pro_alerts",alerts)
    renderProTool()
    showToast("Alarm kaydedildi: "+alertDescription(alerts[alerts.length-1]))
}

function deleteAlert(id){
    alerts=alerts.filter(alert=>alert.id!==id)
    saveLocal("bist_pro_alerts",alerts)
    renderProTool()
}

function beepAlert(){
    try{
        const Audio=window.AudioContext||window.webkitAudioContext
        if(!Audio) return
        const context=new Audio()
        const oscillator=context.createOscillator()
        const gain=context.createGain()
        oscillator.connect(gain)
        gain.connect(context.destination)
        oscillator.frequency.value=880
        gain.gain.setValueAtTime(.045,context.currentTime)
        gain.gain.exponentialRampToValueAtTime(.001,context.currentTime+.28)
        oscillator.start()
        oscillator.stop(context.currentTime+.3)
    }catch(e){}
}

function triggerAlert(alert,stock){
    const price=stock?" • ₺"+n(stock.price):""
    const text=`🔔 ${alertDescription(alert)}${stock?` (${stock.symbol}${price})`:""}`
    alertHistory=[{text,at:Date.now()},...alertHistory].slice(0,25)
    saveLocal("bist_pro_alert_history",alertHistory)
    showToast(text)
    beepAlert()

    try{
        if("Notification" in window && Notification.permission==="granted"){
            new Notification("BIST Terminal Alarmı",{body:text})
        }
    }catch(e){}
}

function evaluateAlerts(stocks){
    if(!alerts.length || !Array.isArray(stocks) || !stocks.length) return
    const now=Date.now()
    let changed=false

    alerts.forEach(alert=>{
        if(now-(alert.lastTriggeredAt||0)<30*60*1000) return
        const candidates=alert.symbol==="ALL"
            ? stocks
            : stocks.filter(stock=>String(stock.symbol).toUpperCase()===alert.symbol)
        const hit=candidates.find(stock=>{
            const price=trNumber(stock.price,0)
            const change=trNumber(stock.change,0)
            const volume=trNumber(stock.relative_volume,0)
            if(alert.type==="price_above") return price>=alert.value
            if(alert.type==="price_below") return price>0&&price<=alert.value
            if(alert.type==="change_above") return change>=alert.value
            if(alert.type==="relative_volume") return volume>=alert.value
            return false
        })
        if(hit){
            alert.lastTriggeredAt=now
            triggerAlert(alert,hit)
            changed=true
        }
    })

    if(changed) saveLocal("bist_pro_alerts",alerts)
}

function requestBrowserNotifications(){
    if(!("Notification" in window)){
        showToast("Bu tarayıcı bildirim iznini desteklemiyor.")
        return
    }
    Notification.requestPermission().then(permission=>{
        showToast(permission==="granted"?"Telefon bildirim izni açıldı.":"Bildirim izni verilmedi.")
    }).catch(()=>showToast("Bildirim izni alınamadı."))
}

async function openDetail(symbol){

    const r=await fetch("/api/stock/"+symbol)
    const j=await r.json()

    if(!j.ok) return

    selected=j.stock

    document.getElementById("home").style.display="none"
    document.getElementById("detail").style.display="block"

    document.getElementById("dSymbol").textContent=selected.symbol
    document.getElementById("dName").textContent=selected.description||""
    document.getElementById("dPrice").textContent="₺"+n(selected.price)

    const ch=document.getElementById("dChange")
    ch.textContent="%"+n(selected.change)

    ch.className=(selected.change||0)>=0?"green":"red"

    detailTab(
        "summary",
        document.querySelector(".detailTabs button")
    )

    window.scrollTo(0,0)
}


let depthTimer = null;

async function loadDepth(symbol,silent=false){

    const p=document.getElementById("panel")

    if(!silent){
        p.innerHTML=`
        <div class="loading">
        Derinlik verisi alınıyor...
        </div>
        `
    }

    try{

        const r=await fetch("/api/live/"+symbol)
        const j=await r.json()

        if(!j.ok) throw new Error("Veri alınamadı")

        const x=j.live

        const bidLot = Number(x.bid_size || 0)
        const askLot = Number(x.ask_size || 0)

        const total = bidLot + askLot

        let buyPct = total ? (bidLot/total)*100 : 50
        let sellPct = 100-buyPct

        p.innerHTML=`

        <div class="marketStrip">

            <div>
                <span>SON</span>
                <b>₺${n(selected.price)}</b>
            </div>

            <div>
                <span>DEĞİŞİM</span>
                <b>%${n(selected.change)}</b>
            </div>

            <div>
                <span>RSI</span>
                <b>${n(selected.rsi,1)}</b>
            </div>

            <div>
                <span>REL.HACİM</span>
                <b>${n(selected.relative_volume,2)}x</b>
            </div>

            <div>
                <span>SKOR</span>
                <b>${selected.score}</b>
            </div>

        </div>


        <div class="depthCard">

            <div class="depthHead">
                <div>EMİR</div>
                <div>LOT</div>
                <div>ALIŞ</div>
                <div>SATIŞ</div>
                <div>LOT</div>
                <div>EMİR</div>
            </div>


            <div class="depthRow">

                <div class="depthBuyBg"
                     style="width:${buyPct/2}%"></div>

                <div class="depthSellBg"
                     style="width:${sellPct/2}%"></div>

                <div>-</div>

                <div class="depthBid">
                    ${bidLot ? n(bidLot,0) : "-"}
                </div>

                <div class="depthBid">
                    ${x.bid!==null ? n(x.bid) : "-"}
                </div>

                <div class="depthAsk">
                    ${x.ask!==null ? n(x.ask) : "-"}
                </div>

                <div class="depthAsk">
                    ${askLot ? n(askLot,0) : "-"}
                </div>

                <div>-</div>

            </div>


            <div class="depthBalance">

                <div class="balanceBar">

                    <div class="balanceBuy"
                         style="width:${buyPct}%"></div>

                    <div class="balanceSell"
                         style="width:${sellPct}%"></div>

                </div>

                <div class="balanceText">

                    <div class="buy">
                        Alış %${n(buyPct,0)}
                    </div>

                    <div class="sell">
                        Satış %${n(sellPct,0)}
                    </div>

                </div>

            </div>

        </div>


        <div class="sectionTitle">
        SON İŞLEMLER
        </div>

        <div class="locked">
        Saat • fiyat • lot • alıcı kurum • satıcı kurum tablosu
        için işlem tarafı verisi gerekiyor.

        <br><br>

        Şu anda gerçek kaynaktan yalnızca mevcut
        alış/satış ve alış/satış lotları gösteriliyor.
        Sahte kademe veya kurum adı üretilmiyor.
        </div>
        `

    }catch(e){

        if(!silent){
            p.innerHTML=`
            <div class="warning">
            Derinlik verisi şu anda alınamadı.
            </div>
            `
        }
    }
}

async function loadLive(symbol){
    const p=document.getElementById("panel")

    p.innerHTML=`
    <div class="card">
    <h3>Canlı Alış / Satış</h3>
    <div class="loading">Alış-satış verisi alınıyor...</div>
    </div>
    `

    try{
        const r=await fetch("/api/live/"+symbol)
        const j=await r.json()

        if(!j.ok){
            throw new Error("Veri alınamadı")
        }

        const x=j.live

        p.innerHTML=`
        <div class="card">
        <h3>Canlı Alış / Satış</h3>

        <div class="rows">

        <div class="row">
        <span>ALIŞ</span>
        <b>${x.bid===null ? "-" : "₺"+n(x.bid)}</b>
        </div>

        <div class="row">
        <span>SATIŞ</span>
        <b>${x.ask===null ? "-" : "₺"+n(x.ask)}</b>
        </div>

        <div class="row">
        <span>ALIŞ LOT</span>
        <b>${x.bid_size===null ? "-" : n(x.bid_size,0)}</b>
        </div>

        <div class="row">
        <span>SATIŞ LOT</span>
        <b>${x.ask_size===null ? "-" : n(x.ask_size,0)}</b>
        </div>

        <div class="row">
        <span>SPREAD</span>
        <b>${x.spread===null ? "-" : "₺"+n(x.spread,4)}</b>
        </div>

        <div class="row">
        <span>SPREAD %</span>
        <b>${x.spread_pct===null ? "-" : "%"+n(x.spread_pct,3)}</b>
        </div>

        <div class="row">
        <span>SON</span>
        <b>${x.last===null ? "-" : "₺"+n(x.last)}</b>
        </div>

        <div class="row">
        <span>HACİM</span>
        <b>${x.volume===null ? "-" : n(x.volume,0)}</b>
        </div>

        </div>
        </div>

        <div class="warning">
        Bu veri WebSocket üzerinden alınır. BIST tarafında gecikmeli olabilir.
        Bu ekran gerçek gelen değerleri gösterir; sahte alış/satış üretmez.
        </div>
        `

    }catch(e){
        p.innerHTML=`
        <div class="warning">
        Canlı alış/satış verisi şu anda alınamadı.
        </div>
        `
    }
}


/* ===== ÜCRETSİZ AKD FOTOĞRAF AKTAR =====
   Fotoğraf yalnızca telefondaki tarayıcıda OCR edilir. Sunucuya görsel,
   Borsa Robotu girişi veya şifre gönderilmez; sonuç aynı telefonda saklanır. */
const MANUAL_AKD_STORAGE_PREFIX="bist_manual_akd_v1_"
const MANUAL_AKD_STALE_MS=12*60*60*1000

function manualAkdKey(symbol){
    return MANUAL_AKD_STORAGE_PREFIX+String(symbol||"").toUpperCase().replace(/[^A-Z0-9]/g,"")
}

function foldTr(value){
    return String(value||"")
        .toLocaleUpperCase("tr-TR")
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g,"")
}

function parseTrNumber(value){
    let raw=String(value??"").replace(/[^\d,.-]/g,"")
    if(!raw || raw==="-" || raw===".") return null

    const comma=raw.lastIndexOf(",")
    const dot=raw.lastIndexOf(".")

    if(comma>=0 && dot>=0){
        if(comma>dot) raw=raw.replace(/\./g,"").replace(",",".")
        else raw=raw.replace(/,/g,"")
    }else if(comma>=0){
        const tail=raw.length-comma-1
        raw=tail<=2 ? raw.replace(",",".") : raw.replace(/,/g,"")
    }else if(dot>=0){
        const tail=raw.length-dot-1
        if(tail===3) raw=raw.replace(/\./g,"")
    }

    const number=Number(raw)
    return Number.isFinite(number) ? number : null
}

function canonicalAkdSide(value){
    const text=foldTr(value)
    if(text.includes("ALIC") || text.includes("ALIS") || text.includes("BUY")) return "buy"
    if(text.includes("SATIC") || text.includes("SATIS") || text.includes("SELL")) return "sell"
    return ""
}

function cleanInstitution(value){
    return String(value||"")
        .replace(/^\s*\d+\s*[.)-]?\s*/,"")
        .replace(/[|;]/g," ")
        .replace(/\s+/g," ")
        .trim()
        .slice(0,56)
}

function normalizeManualRows(rows){
    const byKey=new Map()

    ;(rows||[]).forEach(raw=>{
        const side=canonicalAkdSide(raw.side||raw.direction)
        const institution=cleanInstitution(raw.institution||raw.name||raw.kurum)
        const lot=parseTrNumber(raw.lot??raw.quantity??raw.net)
        const average=parseTrNumber(raw.average??raw.avg??raw.cost??raw.maliyet)
        const percent=parseTrNumber(raw.percent??raw.ratio??raw.oran)

        if(!side || !institution || !lot || lot<=0) return

        const row={
            side,
            institution,
            lot:Math.round(Math.min(lot,999999999)),
            average:average&&average>0 ? average : null,
            percent:percent!==null&&percent>=0&&percent<=100 ? percent : null
        }
        const key=side+"|"+foldTr(institution)
        const old=byKey.get(key)
        if(!old || row.lot>old.lot) byKey.set(key,row)
    })

    return [...byKey.values()]
        .sort((a,b)=>b.lot-a.lot)
        .slice(0,40)
}

function getManualAkd(symbol){
    try{
        const snapshot=JSON.parse(localStorage.getItem(manualAkdKey(symbol))||"null")
        if(!snapshot || !Array.isArray(snapshot.rows)) return null
        snapshot.rows=normalizeManualRows(snapshot.rows)
        if(!snapshot.rows.length) return null
        snapshot.symbol=String(snapshot.symbol||symbol).toUpperCase()
        snapshot.capturedAt=Number(snapshot.capturedAt||0)
        snapshot.stale=!!(snapshot.capturedAt && Date.now()-snapshot.capturedAt>MANUAL_AKD_STALE_MS)
        return snapshot
    }catch(e){
        return null
    }
}

function manualAkdRows(snapshot){
    return normalizeManualRows(snapshot?.rows||[]).map(row=>({
        institution:row.institution,
        buy:row.side==="buy" ? row.lot : 0,
        sell:row.side==="sell" ? row.lot : 0,
        net:row.side==="buy" ? row.lot : -row.lot,
        average:row.average,
        percent:row.percent,
        side:row.side
    })).sort((a,b)=>Math.abs(b.net)-Math.abs(a.net))
}

function manualAkdStats(snapshot){
    const rows=manualAkdRows(snapshot)
    const buyers=rows.filter(x=>x.net>0).sort((a,b)=>b.net-a.net)
    const sellers=rows.filter(x=>x.net<0).sort((a,b)=>a.net-b.net)
    const totalBuy=buyers.reduce((sum,x)=>sum+x.net,0)
    const totalSell=Math.abs(sellers.reduce((sum,x)=>sum+x.net,0))
    const ratio=(totalBuy-totalSell)/Math.max(totalBuy+totalSell,1)
    let direction="DENGE"
    if(ratio>=.08) direction="ALIM YOĞUNLUĞU"
    if(ratio<=-.08) direction="SATIŞ YOĞUNLUĞU"
    return {rows,buyers,sellers,totalBuy,totalSell,ratio,direction}
}

function manualAkdTime(snapshot){
    if(!snapshot?.capturedAt) return "tarih bilinmiyor"
    return new Date(snapshot.capturedAt).toLocaleString("tr-TR",{
        day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"
    })
}

function manualRowsToDraft(rows){
    return normalizeManualRows(rows).map(row=>[
        row.side==="buy" ? "ALIS" : "SATIS",
        row.institution,
        row.lot,
        row.average===null ? "" : String(row.average).replace(".",","),
        row.percent===null ? "" : String(row.percent).replace(".",",")
    ].join(";")).join("\n")
}

function parseManualAkdDraft(text){
    const rows=[]
    String(text||"").split(/\r?\n/).forEach(line=>{
        const fields=line.split(/[;|]/).map(x=>x.trim())
        if(fields.length<3) return
        rows.push({
            side:fields[0],
            institution:fields[1],
            lot:fields[2],
            average:fields[3],
            percent:fields[4]
        })
    })
    return normalizeManualRows(rows)
}

function groupOcrWords(words){
    const valid=(words||[]).filter(word=>{
        const box=word?.bbox||{}
        return word?.text && Number.isFinite(box.x0) && Number.isFinite(box.y0)
    }).map(word=>({
        text:String(word.text).trim(),
        x0:word.bbox.x0,
        x1:word.bbox.x1,
        y0:word.bbox.y0,
        y1:word.bbox.y1,
        confidence:Number(word.confidence||0)
    })).filter(word=>word.text)

    const maxX=Math.max(...valid.map(word=>word.x1),1)
    const lines=[]
    valid.sort((a,b)=>a.y0-b.y0||a.x0-b.x0).forEach(word=>{
        const center=(word.y0+word.y1)/2
        const height=Math.max(8,word.y1-word.y0)
        let line=null
        for(let index=lines.length-1;index>=0;index--){
            const item=lines[index]
            if(Math.abs(item.center-center)<=Math.max(13,(item.height+height)*.58)){
                line=item
                break
            }
        }
        if(!line){
            line={center,height,words:[]}
            lines.push(line)
        }
        line.words.push(word)
        line.center=(line.center*(line.words.length-1)+center)/line.words.length
        line.height=Math.max(line.height,height)
    })

    return lines.map(line=>{
        const lineWords=line.words.sort((a,b)=>a.x0-b.x0).map(word=>({
            ...word,
            x:((word.x0+word.x1)/2)/maxX
        }))
        return {words:lineWords,text:lineWords.map(word=>word.text).join(" ")}
    })
}

function ocrLineToManualRow(line,side){
    const folded=foldTr(line.text)
    if(!side || !line?.words?.length) return null
    if(/ALIC|SATIC|TUMUNU|GOSTER|TOPLAM|HACIM|ISLEM|MALIYET|ORAN|PGC/.test(folded)) return null

    const numeric=line.words.map(word=>({
        ...word,
        value:parseTrNumber(word.text)
    })).filter(word=>word.value!==null && /\d/.test(word.text))

    let lotWord=numeric.find(word=>word.x>=.36 && word.x<.69 && word.value>=10)
    if(!lotWord){
        lotWord=[...numeric].filter(word=>word.value>=10)
            .sort((a,b)=>b.value-a.value)[0]
    }
    if(!lotWord || lotWord.value<10) return null

    const name=cleanInstitution(line.words
        .filter(word=>word.x<.46 && !/\d/.test(word.text))
        .map(word=>word.text).join(" "))
    if(!name || /^(DIGER|TOPLAM|TUMUNU)$/i.test(foldTr(name))) return null

    const avgWord=numeric.find(word=>word.x>=.62 && word.x<.86 && word.value>0 && word.value<1000000)
    const pctWord=numeric.find(word=>word.x>=.80 && word.value>=0 && word.value<=100)

    return {
        side,
        institution:name,
        lot:lotWord.value,
        average:avgWord?.value,
        percent:pctWord?.value
    }
}

function extractManualAkdRows(ocrData){
    let side=""
    const rows=[]
    const lines=groupOcrWords(ocrData?.words||[])

    lines.forEach(line=>{
        const folded=foldTr(line.text)
        if(folded.includes("ALICILAR") || folded.includes("ALANLAR")){
            side="buy"
            return
        }
        if(folded.includes("SATICILAR") || folded.includes("SATANLAR")){
            side="sell"
            return
        }
        const row=ocrLineToManualRow(line,side)
        if(row) rows.push(row)
    })

    return normalizeManualRows(rows)
}

function manualAkdUploadHtml(symbol){
    const snapshot=getManualAkd(symbol)
    const previous=snapshot
        ? `<div class="ocrProgress">Bu hissede ${manualAkdTime(snapshot)} tarihli kayıt var. Yeni fotoğraf kaydedilirse üzerine yazılır.</div>`
        : ""

    return `
    <div class="card akdImportCard">
        <h3>📷 AKD Fotoğraf Aktar — ${esc(symbol)}</h3>
        <div class="akdImportSteps">
            Borsa Robotu’nda aynı hissenin <b>AKD</b> ekranını aç. Alıcılar ve Satıcılar görünürken ekran görüntüsünü seç.
            İlk okumada 15–30 saniye sürebilir; alttaki satırları kontrol edip gerekirse düzelt.
        </div>
        <label class="filePicker">
            📷 EKRAN GÖRÜNTÜSÜ SEÇ
            <input id="akdImageInput" type="file" accept="image/png,image/jpeg,image/webp" onchange="processAkdImage(event,'${esc(symbol)}')">
        </label>
        <div id="ocrStatus" class="ocrProgress">Henüz fotoğraf seçilmedi.</div>
        ${previous}
        <label class="draftLabel">OKUNAN SATIRLAR — format: ALIS;KURUM;LOT;MALIYET;ORAN</label>
        <textarea id="akdDraft" class="draftArea" spellcheck="false" placeholder="ALIS;Yapı Kr.;222103;111,96;37,23\nSATIS;Info;221605;111,48;37,15"></textarea>
        <div class="akdActions">
            <button class="actionButton" onclick="saveManualAkd('${esc(symbol)}')">AKD'Yİ KAYDET</button>
            <button class="actionButton secondary" onclick="loadAkd('${esc(symbol)}')">VAZGEÇ</button>
        </div>
        <div class="akdPrivacy">Görsel sunucuya yüklenmez. OCR telefonundaki tarayıcıda çalışır; sadece senin cihazındaki özet satırlar kaydedilir.</div>
    </div>`
}

function manualAkdTableHtml(snapshot){
    const stats=manualAkdStats(snapshot)
    const rows=stats.rows
    const table=rows.map(x=>`
        <tr>
            <td>${esc(x.institution)}</td>
            <td class="green">${x.buy?n(x.buy,0):'-'}</td>
            <td class="red">${x.sell?n(x.sell,0):'-'}</td>
            <td class="${x.net>=0?'green':'red'}">${x.net>=0?'+':''}${n(x.net,0)}</td>
            <td>${x.average?('₺'+n(x.average,3)):'-'}</td>
            <td>${x.percent===null?'—':'%'+n(x.percent,2)}</td>
        </tr>`).join("")

    const directionClass=stats.direction.includes("SATIŞ") ? "red" : (stats.direction.includes("ALIM") ? "green" : "")
    const staleNote=snapshot.stale
        ? '<div class="warning" style="margin-top:10px">Bu AKD görüntüsü 12 saatten eski. Yeni ekran görüntüsü aktararak güncelle.</div>'
        : ''

    return `
    <div class="providerBadge manualBadge">● AKD FOTOĞRAF AKTAR • ${manualAkdTime(snapshot)}</div>
    <div class="marketStrip">
        <div><span>KURUM</span><b>${rows.length}</b></div>
        <div><span>ALIŞ LOT</span><b class="green">${n(stats.totalBuy,0)}</b></div>
        <div><span>SATIŞ LOT</span><b class="red">${n(stats.totalSell,0)}</b></div>
        <div><span>EKRAN YÖNÜ</span><b class="${directionClass}">${stats.direction}</b></div>
        <div><span>KAYNAK</span><b>FOTOĞRAF</b></div>
    </div>
    <div class="dataTableWrap">
        <table class="dataTable">
            <thead><tr><th>KURUM</th><th>ALIŞ LOT</th><th>SATIŞ LOT</th><th>NET LOT</th><th>ORT.</th><th>ORAN</th></tr></thead>
            <tbody>${table}</tbody>
        </table>
    </div>
    ${staleNote}
    <div class="akdActions">
        <button class="actionButton" onclick="openManualAkdImport('${esc(snapshot.symbol)}')">YENİ FOTOĞRAF AKTAR</button>
        <button class="actionButton secondary" onclick="clearManualAkd('${esc(snapshot.symbol)}')">KAYDI SİL</button>
    </div>
    <div class="akdPrivacy">Bu, seçtiğin ekran görüntüsünün zaman damgalı AKD özetidir. Gerçek zamanlı API değildir ve tek başına virman kanıtı sayılmaz.</div>`
}

function openManualAkdImport(symbol){
    if(depthTimer){
        clearInterval(depthTimer)
        depthTimer=null
    }
    const p=document.getElementById("panel")
    p.innerHTML=manualAkdUploadHtml(symbol)
    window.scrollTo({top:0,behavior:"smooth"})
}

async function processAkdImage(event,symbol){
    const file=event?.target?.files?.[0]
    const status=document.getElementById("ocrStatus")
    if(!file || !status) return
    if(file.size>12*1024*1024){
        status.textContent="Fotoğraf 12 MB'dan küçük olmalı. Ekran görüntüsünü seç."
        return
    }
    if(!window.Tesseract){
        status.textContent="OCR kütüphanesi henüz yüklenemedi. İnterneti kontrol edip tekrar dene veya satırları elle gir."
        return
    }

    status.textContent="Fotoğraf telefonda okunuyor..."
    try{
        const result=await window.Tesseract.recognize(file,"tur+eng",{
            logger:message=>{
                if(!message?.status) return
                const percentage=typeof message.progress==="number" ? " %"+Math.round(message.progress*100) : ""
                status.textContent="OCR: "+message.status+percentage
            }
        })
        const rows=extractManualAkdRows(result?.data||{})
        const draft=document.getElementById("akdDraft")
        if(draft && rows.length) draft.value=manualRowsToDraft(rows)
        status.textContent=rows.length
            ? rows.length+" kurum satırı bulundu. Kaydetmeden önce alttaki lot ve kurumları kontrol et."
            : "Satırlar otomatik okunamadı. Alttaki kutuya örnekteki formatla elle gir; fotoğraf telefondan çıkmadı."
    }catch(error){
        status.textContent="OCR okunamadı. Aynı bilgileri alttaki kutuya elle yazabilirsin."
    }
}

function saveManualAkd(symbol){
    const draft=document.getElementById("akdDraft")
    const status=document.getElementById("ocrStatus")
    const rows=parseManualAkdDraft(draft?.value||"")
    if(!rows.length){
        if(status) status.textContent="Kaydedilecek satır bulunamadı. Her satır ALIS;KURUM;LOT;MALIYET;ORAN şeklinde olmalı."
        return
    }

    const snapshot={
        version:1,
        symbol:String(symbol||"").toUpperCase(),
        capturedAt:Date.now(),
        source:"AKD ekran görüntüsü",
        rows
    }
    try{
        localStorage.setItem(manualAkdKey(symbol),JSON.stringify(snapshot))
    }catch(error){
        if(status) status.textContent="Telefon bu kaydı saklayamadı. Tarayıcı depolama alanını kontrol et."
        return
    }

    const akdTab=[...document.querySelectorAll(".detailTabs button")]
        .find(button=>foldTr(button.textContent).includes("AKD"))
    if(akdTab) detailTab("akd",akdTab)
    else loadAkd(symbol)
}

function clearManualAkd(symbol){
    if(!confirm(String(symbol).toUpperCase()+" için kaydedilen AKD görüntü özetini silmek istiyor musun?")) return
    localStorage.removeItem(manualAkdKey(symbol))
    loadAkd(symbol)
}

function manualAkdVirmanHtml(symbol){
    const snapshot=getManualAkd(symbol)
    if(!snapshot){
        return `
        <div class="card akdImportCard">
            <h3>📷 Gerçek AKD'yi Virman Radarına Kat</h3>
            <div class="desc" style="line-height:1.6">Borsa Robotu'ndaki aynı hissenin AKD ekran görüntüsünü aktar. En güçlü alıcı ve satıcılar, hacim radarının yanında gösterilsin.</div>
            <div class="akdActions" style="grid-template-columns:1fr;margin-top:12px">
                <button class="actionButton" onclick="openManualAkdImport('${esc(symbol)}')">AKD FOTOĞRAF AKTAR</button>
            </div>
        </div>`
    }

    const stats=manualAkdStats(snapshot)
    const topBuy=stats.buyers[0]
    const topSell=stats.sellers[0]
    const radar=selected?.institutional?.direction||"YÖN BELİRSİZ"
    const snapshotSell=stats.direction.includes("SATIŞ")
    const snapshotBuy=stats.direction.includes("ALIM")
    const aligned=(radar.includes("SATIŞ")&&snapshotSell)||(radar.includes("ALIM")&&snapshotBuy)
    const alignment=aligned ? "Radar yönüyle UYUMLU" : (stats.direction==="DENGE" ? "Ekran dengeli" : "Radar yönünü TEYİT ETMİYOR")
    const alignmentClass=aligned ? "green" : "red"

    return `
    <div class="card akdImportCard">
        <div class="providerBadge manualBadge">● YÜKLENEN AKD • ${manualAkdTime(snapshot)}</div>
        <h3>AKD + Virman Radar Kontrolü</h3>
        <div class="rows">
            <div class="row"><span>EN GÜÇLÜ ALICI</span><b class="green">${topBuy?esc(topBuy.institution):'—'}</b><div class="desc">${topBuy?'+'+n(topBuy.net,0)+' lot':'—'}</div></div>
            <div class="row"><span>EN GÜÇLÜ SATICI</span><b class="red">${topSell?esc(topSell.institution):'—'}</b><div class="desc">${topSell?n(Math.abs(topSell.net),0)+' lot':'—'}</div></div>
            <div class="row"><span>GÖRÜNTÜ YÖNÜ</span><b>${stats.direction}</b><div class="desc">Yüklenen kurum satırları</div></div>
            <div class="row"><span>RADAR UYUMU</span><b class="${alignmentClass}">${alignment}</b><div class="desc">${esc(radar)}</div></div>
        </div>
        <div class="akdActions">
            <button class="actionButton secondary" onclick="openManualAkdImport('${esc(symbol)}')">FOTOĞRAFI GÜNCELLE</button>
            <button class="actionButton secondary" onclick="loadAkd('${esc(symbol)}')">AKD TABLOSUNU AÇ</button>
        </div>
    </div>`
}

async function loadAkd(symbol,silent=false){
    const p=document.getElementById("panel")
    const manualSnapshot=getManualAkd(symbol)
    if(!silent) p.innerHTML='<div class="loading">AKD verisi kontrol ediliyor...</div>'

    try{
        const r=await fetch("/api/akd/"+encodeURIComponent(symbol),{cache:"no-store"})
        const j=await r.json()

        if(!j.configured){
            p.innerHTML=manualSnapshot ? manualAkdTableHtml(manualSnapshot) : manualAkdUploadHtml(symbol)
            return
        }

        if(!j.ok) throw new Error(j.error||"AKD alınamadı")
        const rows=j.rows||[]
        if(!rows.length){
            p.innerHTML=manualSnapshot
                ? manualAkdTableHtml(manualSnapshot)
                : '<div class="warning">Sağlayıcı bağlantısı açık fakat bu hisse için AKD satırı dönmedi.</div>'
            return
        }

        const netBuy=rows.filter(x=>(x.net||0)>0).reduce((a,x)=>a+Number(x.net||0),0)
        const netSell=Math.abs(rows.filter(x=>(x.net||0)<0).reduce((a,x)=>a+Number(x.net||0),0))
        const table=rows.slice(0,30).map(x=>`
            <tr>
                <td>${esc(x.institution)}</td>
                <td class="green">${n(x.buy,0)}</td>
                <td class="red">${n(x.sell,0)}</td>
                <td class="${Number(x.net)>=0?'green':'red'}">${Number(x.net)>=0?'+':''}${n(x.net,0)}</td>
                <td>${money(x.net_tl)}</td>
                <td>${x.average?('₺'+n(x.average,3)):'-'}</td>
            </tr>`).join("")

        p.innerHTML=`
        <div class="providerBadge">● CANLI AKD • ${esc(j.provider||'Lisanslı sağlayıcı')}</div>
        <div class="marketStrip">
            <div><span>KURUM</span><b>${rows.length}</b></div>
            <div><span>NET ALIŞ</span><b class="green">${n(netBuy,0)}</b></div>
            <div><span>NET SATIŞ</span><b class="red">${n(netSell,0)}</b></div>
        </div>
        <div class="dataTableWrap">
            <table class="dataTable">
                <thead><tr><th>KURUM</th><th>ALIŞ LOT</th><th>SATIŞ LOT</th><th>NET LOT</th><th>NET TL</th><th>ORT.</th></tr></thead>
                <tbody>${table}</tbody>
            </table>
        </div>
        <div class="warning" style="margin-top:10px">AKD seans içi aracı kurum işlemlerini gösterir; tek başına virman kanıtı değildir.</div>`
    }catch(e){
        if(!silent) p.innerHTML=manualSnapshot
            ? manualAkdTableHtml(manualSnapshot)
            : '<div class="warning">Canlı AKD alınamadı: '+esc(e.message)+'</div>'
    }
}

async function loadTakas(symbol,silent=false){
    const p=document.getElementById("panel")
    if(!silent) p.innerHTML='<div class="loading">Takas verisi alınıyor...</div>'

    try{
        const r=await fetch("/api/takas/"+encodeURIComponent(symbol),{cache:"no-store"})
        const j=await r.json()

        if(!j.configured){
            p.innerHTML=`
            <div class="card"><h3>Takas / Saklama</h3></div>
            <div class="locked">
            Takas ekranı hazır; gerçek kurum saklama ve değişim verisi için
            lisanslı takas API bağlantısı tanımlanmalıdır.
            </div>`
            return
        }

        if(!j.ok) throw new Error(j.error||"Takas alınamadı")
        const rows=j.rows||[]
        if(!rows.length){
            p.innerHTML='<div class="warning">Sağlayıcı bağlantısı açık fakat bu hisse için takas satırı dönmedi.</div>'
            return
        }

        const table=rows.slice(0,40).map(x=>`
            <tr>
                <td>${esc(x.institution)}</td>
                <td>${n(x.holding,0)}</td>
                <td class="${Number(x.change)>=0?'green':'red'}">${Number(x.change)>=0?'+':''}${n(x.change,0)}</td>
                <td>%${n(x.percent,2)}</td>
            </tr>`).join("")

        p.innerHTML=`
        <div class="providerBadge">● TAKAS VERİSİ • ${esc(j.provider||'Lisanslı sağlayıcı')}</div>
        <div class="dataTableWrap">
            <table class="dataTable">
                <thead><tr><th>KURUM</th><th>SAKLAMA LOT</th><th>DEĞİŞİM</th><th>PAY</th></tr></thead>
                <tbody>${table}</tbody>
            </table>
        </div>
        <div class="warning" style="margin-top:10px">Kesinleşmiş takas verisi işlem gününe göre gecikmeli olabilir; sağlayıcının tarih bilgisini esas al.</div>`
    }catch(e){
        if(!silent) p.innerHTML='<div class="warning">Takas verisi alınamadı: '+esc(e.message)+'</div>'
    }
}

function estimatedVirmanHtml(s){
    const v=s.institutional||{}
    const directionClass=(v.direction||"").includes("SATIŞ")?'red':'green'
    const reasons=(v.reasons&&v.reasons.length)
        ? v.reasons.map(x=>'✓ '+esc(x)).join('<br>')
        : 'Belirgin kurumsal hareket ölçütü yok.'

    return `
    <div class="signalBox">
        <div>TAHMİNİ KURUMSAL / VİRMAN SKORU</div>
        <div class="signalBig">${v.score||0}/100</div>
        <b class="${directionClass}">${esc(v.direction||'YÖN BELİRSİZ')}</b>
        <div style="margin-top:7px;color:#8e9aae">${esc(v.level||'NORMAL')}</div>
    </div>
    <div class="card">
        <div class="rows">
            <div class="row"><span>GÖRELİ HACİM</span><b>${n(s.relative_volume,2)}x</b></div>
            <div class="row"><span>TOPLAM İŞLEM</span><b>${money(v.transaction_value)}</b></div>
            <div class="row"><span>NORMAL ÜSTÜ LOT</span><b>${n(v.abnormal_volume,0)}</b></div>
            <div class="row"><span>NORMAL ÜSTÜ TL</span><b>${money(v.abnormal_value)}</b></div>
        </div>
    </div>
    <div class="card"><h3>Skorun Nedenleri</h3><div class="desc" style="line-height:1.7">${reasons}</div></div>`
}

async function loadVirman(symbol,silent=false){
    const p=document.getElementById("panel")
    const estimate=estimatedVirmanHtml(selected)
    const manualAkd=manualAkdVirmanHtml(symbol)
    if(!silent) p.innerHTML=estimate+manualAkd+'<div class="loading">AKD ve takas eşleşmesi kontrol ediliyor...</div>'

    try{
        const r=await fetch("/api/virman-check/"+encodeURIComponent(symbol),{cache:"no-store"})
        const j=await r.json()

        if(!j.configured){
            p.innerHTML=estimate+manualAkd+`
            <div class="locked">
            Yukarıdaki skor fiyat ve hacimden üretilen tahmindir. Yüklediğin AKD ekran görüntüsü
            kurum yoğunluğunu bu tahminle birlikte gösterir. Kesin virman eşleşmesi için ayrıca
            canlı takas verisi gerekir.
            </div>`
            return
        }

        if(!j.ok) throw new Error(j.note||j.akd_error||j.takas_error||"Karşılaştırma yapılamadı")
        const matches=j.matches||[]
        if(!matches.length){
            p.innerHTML=estimate+manualAkd+`
            <div class="card"><h3>AKD + Takas Kontrolü</h3>
            <div class="green">Bu hissede eşik geçen kurumdan kuruma lot eşleşmesi bulunmadı.</div></div>
            <div class="warning">Bu sonuç virman olmadığı garantisi vermez.</div>`
            return
        }

        const table=matches.map(x=>`
            <tr>
                <td>${esc(x.from)}</td>
                <td>${esc(x.to)}</td>
                <td>${n(x.lot,0)}</td>
                <td>%${n(x.difference_percent,2)}</td>
                <td>${x.score}/100</td>
                <td class="${x.score>=75?'green':''}">${esc(x.label)}</td>
            </tr>`).join("")

        p.innerHTML=estimate+manualAkd+`
        <div class="providerBadge">● AKD + TAKAS EŞLEŞTİRMESİ • ${esc(j.provider||'Lisanslı sağlayıcı')}</div>
        <div class="dataTableWrap">
            <table class="dataTable">
                <thead><tr><th>ÇIKAN KURUM</th><th>GİREN KURUM</th><th>LOT</th><th>FARK</th><th>SKOR</th><th>DURUM</th></tr></thead>
                <tbody>${table}</tbody>
            </table>
        </div>
        <div class="warning" style="margin-top:10px">Eşleşme olası virmanı gösterir; kesin yatırımcı kimliği veya kesin işlem türü değildir.</div>`
    }catch(e){
        if(!silent) p.innerHTML=estimate+manualAkd+'<div class="warning">AKD–takas karşılaştırması yapılamadı: '+esc(e.message)+'</div>'
    }
}

function closeDetail(){
    if(depthTimer){
        clearInterval(depthTimer)
        depthTimer=null
    }
    document.getElementById("detail").style.display="none"
    document.getElementById("home").style.display="block"
}

function detailTab(tab,el){

    if(depthTimer){
        clearInterval(depthTimer)
        depthTimer=null
    }

    document.querySelectorAll(".detailTabs button")
        .forEach(b=>b.classList.remove("active"))

    el.classList.add("active")

    const s=selected
    const p=document.getElementById("panel")

    if(tab==="depth"){
        loadDepth(s.symbol);

        depthTimer = setInterval(()=>{
            if(selected&&selected.symbol===s.symbol) loadDepth(s.symbol,true);
        },3000);

        return
    }

    if(tab==="kademe"){
        loadDepth(s.symbol);

        depthTimer = setInterval(()=>{
            if(selected&&selected.symbol===s.symbol) loadDepth(s.symbol,true);
        },3000);

        return
    }

    if(tab==="signals"){

        let durum = s.signal || "-"
        let rsiText = "-"
        const technical=s.technical||{}
        const technicalReasons=(technical.reasons||[])
            .map(reason=>`<li>${esc(reason)}</li>`)
            .join("")

        if(s.rsi!==null){
            if(s.rsi>=70) rsiText="Aşırı güçlü / dikkat"
            else if(s.rsi>=55) rsiText="Pozitif momentum"
            else if(s.rsi>=45) rsiText="Nötr"
            else rsiText="Zayıf momentum"
        }

        p.innerHTML=`

        <div class="signalBox">
            <div>TEKNİK SKOR</div>
            <div class="signalBig">${s.score}/150</div>
            <b>${durum}</b>
        </div>

        <div class="card">

            <div class="rows">

                <div class="row">
                <span>RSI</span>
                <b>${n(s.rsi,1)}</b>
                </div>

                <div class="row">
                <span>RSI DURUM</span>
                <b>${rsiText}</b>
                </div>

                <div class="row">
                <span>MACD</span>
                <b>${n(s.macd,3)}</b>
                </div>

                <div class="row">
                <span>MACD SIGNAL</span>
                <b>${n(s.macd_signal,3)}</b>
                </div>

                <div class="row">
                <span>EMA20</span>
                <b>${n(s.ema20)}</b>
                </div>

                <div class="row">
                <span>EMA50</span>
                <b>${n(s.ema50)}</b>
                </div>

                <div class="row">
                <span>REL. HACİM</span>
                <b>${n(s.relative_volume,2)}x</b>
                </div>

                <div class="row">
                <span>GÜNLÜK</span>
                <b>%${n(s.change)}</b>
                </div>

            </div>

        </div>
        `

        p.innerHTML+=`
        <div class="card">
        <h3>Pro Teknik Profil</h3>
        <div class="rows">
            <div class="row"><span>KURGU</span><b>${esc(technical.label||"NÖTR İZLE")}</b></div>
            <div class="row"><span>PRO SKOR</span><b>${technical.score||0}/100</b></div>
            <div class="row"><span>TREND</span><b>${esc(technical.trend||"-")}</b></div>
            <div class="row"><span>RİSK</span><b class="${riskClass(technical.risk_level)}">${esc(technical.risk_level||"-")}</b></div>
        </div>
        ${technicalReasons?`<ul class="basketReasons">${technicalReasons}</ul>`:""}
        <div class="sheetActions"><button class="sheetButton" onclick="prefillPortfolio('${esc(s.symbol)}')">Portföye Ekle</button><button class="sheetButton secondary" onclick="openProTool('risk')">Risk Hesabı</button></div>
        </div>`

        return
    }

    if(tab==="summary"){
        p.innerHTML=`

        <div class="marketStrip">

        <div>
        <span>FİYAT</span>
        <b>₺${n(s.price)}</b>
        </div>

        <div>
        <span>DEĞİŞİM</span>
        <b>%${n(s.change)}</b>
        </div>

        <div>
        <span>RSI</span>
        <b>${n(s.rsi,1)}</b>
        </div>

        <div>
        <span>REL.HACİM</span>
        <b>${n(s.relative_volume,2)}x</b>
        </div>

        <div>
        <span>SKOR</span>
        <b>${s.score}</b>
        </div>

        </div>

        <div class="card">
        <h3>Piyasa Özeti</h3>

        <div class="rows">

        <div class="row">
        <span>Son Fiyat</span>
        <b>₺${n(s.price)}</b>
        </div>

        <div class="row">
        <span>Günlük Değişim</span>
        <b>%${n(s.change)}</b>
        </div>

        <div class="row">
        <span>Hacim</span>
        <b>${money(s.volume)}</b>
        </div>

        <div class="row">
        <span>Piyasa Değeri</span>
        <b>${money(s.market_cap)}</b>
        </div>

        <div class="row">
        <span>Relative Volume</span>
        <b>${n(s.relative_volume)}x</b>
        </div>

        <div class="row">
        <span>Teknik Sinyal</span>
        <b>${s.signal}</b>
        </div>

        </div>
        </div>
        `
    }

    if(tab==="live"){
        loadLive(s.symbol)
    }

    if(tab==="chart"){
        p.innerHTML=`
        <div class="card">
        <h3>${s.symbol} • Canlı Grafik</h3>
        <div style="height:430px;border-radius:12px;overflow:hidden;background:#0b111a">
        <iframe
          title="${s.symbol} TradingView grafiği"
          src="https://s.tradingview.com/widgetembed/?symbol=BIST%3A${encodeURIComponent(s.symbol)}&interval=15&hidesidetoolbar=1&symboledit=0&saveimage=0&toolbarbg=f1f3f6&studies=[]&theme=dark&style=1&timezone=Europe%2FIstanbul&withdateranges=1&hide_top_toolbar=1&hide_legend=0&locale=tr"
          style="width:100%;height:100%;border:0"
          loading="lazy"></iframe>
        </div>
        </div>
        <div class="warning">Grafik ücretsiz sağlayıcıdan yüklenir; seans dışı veya sağlayıcı gecikmesinde son güncellemeyi gösterebilir.</div>
        `
        return
    }

    if(tab==="technical"){
        p.innerHTML=`
        <div class="card">
        <h3>Teknik Göstergeler</h3>

        <div class="rows">

        <div class="row">
        <span>RSI</span>
        <b>${n(s.rsi,1)}</b>
        </div>

        <div class="row">
        <span>MACD</span>
        <b>${n(s.macd,3)}</b>
        </div>

        <div class="row">
        <span>MACD Signal</span>
        <b>${n(s.macd_signal,3)}</b>
        </div>

        <div class="row">
        <span>EMA20</span>
        <b>₺${n(s.ema20)}</b>
        </div>

        <div class="row">
        <span>EMA50</span>
        <b>₺${n(s.ema50)}</b>
        </div>

        <div class="row">
        <span>SMA20</span>
        <b>₺${n(s.sma20)}</b>
        </div>

        </div>
        </div>
        `
    }

    if(tab==="akd"){
        loadAkd(s.symbol)
        depthTimer=setInterval(()=>{
            if(selected&&selected.symbol===s.symbol) loadAkd(s.symbol,true)
        },5000)
        return
    }

    if(tab==="takas"){
        loadTakas(s.symbol)
        depthTimer=setInterval(()=>{
            if(selected&&selected.symbol===s.symbol) loadTakas(s.symbol,true)
        },60000)
        return
    }

    if(tab==="virman"){
        loadVirman(s.symbol)
        depthTimer=setInterval(()=>{
            if(selected&&selected.symbol===s.symbol) loadVirman(s.symbol,true)
        },60000)
        return
    }

    if(tab==="kap"){
        p.innerHTML=`
        <div class="card">
        <h3>KAP Haberleri</h3>
        <div class="warning">
        ${s.symbol} için güncel KAP bildirimleri bu bölüme bağlanacak.
        </div>
        </div>
        `
    }

    if(tab==="ai"){
        const w=Math.min(100,(s.score/150)*100)

        p.innerHTML=`
        <div class="card">

        <h3>AI / Teknik Skor</h3>

        <div class="score">${s.score}</div>
        <div>${s.signal}</div>

        <div class="bar">
        <div style="width:${w}%"></div>
        </div>

        </div>

        <div class="warning">
        Bu skor şu anda RSI, hacim, trend, MACD ve teknik göstergelerden
        oluşturulan kural tabanlı analiz skorudur. Kesin yükseliş tahmini
        veya yatırım tavsiyesi değildir.
        </div>
        `
    }
}

load()
loadKurlar()
loadMarketTicker()
updateTerminalClock()
setInterval(load,10000)
setInterval(loadMarketTicker,10000)
setInterval(updateLiveStatus,1000)
setInterval(updateTerminalClock,1000)
setInterval(loadKurlar,10000)

</script>


<div class="mobileBottomNav" id="mobileBottomNav">
 <button class="active" onclick="bottomGo('all',this)"><b>⌂</b>Ana Sayfa</button>
 <button onclick="bottomGo('virman',this)"><b>▥</b>Kurumsal</button>
 <button onclick="bottomOpen('portfolio',this)"><b>◴</b>Portföy</button>
 <button onclick="bottomOpen('tools',this)"><b>•••</b>Diğer</button>
</div>

<script>
function bottomSetActive(el){
  document.querySelectorAll('#mobileBottomNav button').forEach(x=>x.classList.remove('active'));
  if(el) el.classList.add('active');
}

function bottomGo(type,el){
  bottomSetActive(el);
  applyQuickFilter(type||'all');
}

function bottomOpen(tool,el){
  bottomSetActive(el);
  openProTool(tool);
}
</script>

</body>
</html>
'''


@app.route("/api/live/<symbol>")
def api_live(symbol):
    symbol = symbol.upper().strip()

    with LIVE_LOCK:
        cached = LIVE_CACHE.get(symbol)

    if cached and time.time() - cached.get("updated", 0) < 8:
        data = cached
    else:
        data = fetch_live_quote(symbol)

    bid = data.get("bid")
    ask = data.get("ask")

    spread = None
    spread_pct = None

    if bid is not None and ask is not None:
        spread = ask - bid

        if bid:
            spread_pct = (spread / bid) * 100

    data["spread"] = spread
    data["spread_pct"] = spread_pct

    return jsonify({
        "ok": True,
        "live": data,
        "note": "TradingView WebSocket verisi. BIST verisi gecikmeli olabilir."
    })



@app.route("/")
def home():
    return render_template_string(HTML)


@app.route("/api/health")
def api_health():
    with CACHE_LOCK:
        market_count = len(CACHE.get("stocks", []))
        market_updated = CACHE.get("updated", 0)
        market_error = CACHE.get("last_error")

    return jsonify({
        "ok": True,
        "version": APP_VERSION,
        "market_count": market_count,
        "market_updated": market_updated,
        "market_refresh_seconds": MARKET_REFRESH_SECONDS,
        "market_last_error": market_error,
        "akd_configured": bool(AKD_API_URL),
        "takas_configured": bool(TAKAS_API_URL),
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "telegram_daily_basket_enabled": bool(
            TELEGRAM_DAILY_BASKET_ENABLED
            and TELEGRAM_BOT_TOKEN
            and TELEGRAM_CHAT_ID
        )
    })


if __name__ == "__main__":
    start_market_worker()

    port = int(os.environ.get("PORT", 5000))

    print("BIST PRO RADAR V7.1 CANLI TARAMA AKTİF")
    print("http://127.0.0.1:%s" % port)

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        threaded=True
    )
