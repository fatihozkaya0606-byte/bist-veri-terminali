from flask import Flask, jsonify, render_template_string
import requests
import threading
import time
import os
import websocket
import json
import random
import string

app = Flask(__name__)

TV_URL = "https://scanner.tradingview.com/turkey/scan"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/json",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/"
}

CACHE = {
    "stocks": [],
    "updated": 0
}

LIVE_CACHE = {}
LIVE_LOCK = threading.Lock()

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


def fetch_market():
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

        stocks.append(stock)

    CACHE["stocks"] = stocks
    CACHE["updated"] = int(time.time())

    return stocks


def get_market():
    if not CACHE["stocks"] or time.time() - CACHE["updated"] > 60:
        try:
            return fetch_market()
        except Exception as e:
            print("Veri hatası:", e)

    return CACHE["stocks"]


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

def background_loop():
    while True:
        try:
            fetch_market()
            print("BIST verileri güncellendi:", len(CACHE["stocks"]))
        except Exception as e:
            print("Arka plan veri hatası:", e)

        time.sleep(60)


@app.route("/api/market")
def api_market():
    return jsonify({
        "ok": True,
        "count": len(get_market()),
        "stocks": get_market(),
        "updated": CACHE["updated"]
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
                "real_takas": False
            })

    return jsonify({
        "ok": False,
        "error": "Hisse bulunamadı"
    }), 404


KUR_CACHE = {"data": None, "updated": 0}

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

HTML = r'''
<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport"
content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">

<title>BIST Veri Terminali</title>

<script src="https://telegram.org/js/telegram-web-app.js"></script>

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
    grid-template-columns:repeat(3,1fr);
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

</style>

<style>
.mobileBottomNav{
    position:fixed;
    left:0;
    right:0;
    bottom:0;
    z-index:9999;
    height:64px;
    padding:6px 8px max(6px,env(safe-area-inset-bottom));
    background:#0b111a;
    border-top:1px solid #202c3a;
    display:grid;
    grid-template-columns:repeat(4,1fr);
    gap:5px;
}
.mobileBottomNav button{
    border:0;
    background:transparent;
    color:#8591a3;
    font-size:12px;
    font-weight:600;
    border-radius:10px;
    padding:5px 2px;
}
.mobileBottomNav button b{
    display:block;
    color:#dce5f2;
    font-size:18px;
    line-height:20px;
    margin-bottom:2px;
}
.mobileBottomNav button.active{
    color:#2684ff;
    background:#111c2b;
}
.mobileBottomNav button.active b{color:#2684ff}
body{padding-bottom:76px!important;}
.bottom{bottom:64px!important;}
</style>

</head>

<body>

<div class="app">

<div id="home">

<div class="top">
<div class="title">BIST Veri Terminali</div>
<div class="sub">Piyasa • Teknik • Sinyal • AKD • Takas • AI</div>

<input
id="search"
class="search"
placeholder="🔎 Hisse ara: ASELS, THYAO, TUPRS..."
oninput="renderStocks()">
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
</div>

<div class="tabs">
<button class="active" onclick="setFilter('all',this)">Tüm BIST</button>
<button onclick="setFilter('strong',this)">Güçlü</button>
<button onclick="setFilter('up',this)">Yükselen</button>
<button onclick="setFilter('down',this)">Düşen</button>
<button onclick="setFilter('volume',this)">Hacim</button>
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
<button onclick="detailTab('depth',this)">DERİNLİK</button>
<button onclick="detailTab('akd',this)">AKD</button>
<button onclick="detailTab('kademe',this)">KADEME</button>
<button onclick="detailTab('takas',this)">TAKAS</button>
<button onclick="detailTab('signals',this)">SİNYALLER</button>
</div>

<div id="panel" class="panel"></div>

</div>

</div>

<div class="bottom">
Veriler analiz amaçlıdır • Yatırım tavsiyesi değildir
</div>

<script>

let allStocks = []
let selected = null
let filter = "all"

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
        const r=await fetch("/api/kurlar",{cache:"no-store"})
        const j=await r.json()
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

async function load(){
    try{
        const r=await fetch("/api/market")
        const j=await r.json()

        allStocks=j.stocks || []

        document.getElementById("total").textContent=allStocks.length

        document.getElementById("up").textContent=
            allStocks.filter(x=>(x.change||0)>0).length

        document.getElementById("strong").textContent=
            allStocks.filter(x=>x.score>=110).length

        renderStocks()
    }catch(e){
        document.getElementById("list").innerHTML=
            '<div class="warning">Veriler alınamadı. Biraz sonra yeniden deneyin.</div>'
    }
}

function setFilter(f,el){
    filter=f

    document.querySelectorAll(".tabs button")
        .forEach(b=>b.classList.remove("active"))

    el.classList.add("active")
    renderStocks()
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

    if(!q && filter==="all")
        arr=arr.slice(0,150)

    const list=document.getElementById("list")

    if(!arr.length){
        list.innerHTML='<div class="loading">Hisse bulunamadı.</div>'
        return
    }

    list.innerHTML=arr.map(s=>{

        let cls=(s.change||0)>=0 ? "green":"red"

        return `
        <div class="stock" onclick='openDetail(${JSON.stringify(s.symbol)})'>

        <div class="stockTop">

        <div>
        <div class="symbol">${s.symbol}</div>
        <div class="desc">${s.description||""}</div>
        </div>

        <div>
        <div class="price">₺${n(s.price)}</div>
        <div class="${cls}">%${n(s.change)}</div>
        </div>

        </div>

        <div class="smallgrid">

        <div class="small">
        <span>RSI</span>
        <b>${n(s.rsi,1)}</b>
        </div>

        <div class="small">
        <span>Rel. Hacim</span>
        <b>${n(s.relative_volume,2)}x</b>
        </div>

        <div class="small">
        <span>Skor</span>
        <b>${s.score}/150</b>
        </div>

        <div class="small">
        <span>Sinyal</span>
        <b>${s.signal}</b>
        </div>

        </div>

        </div>
        `
    }).join("")
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

async function loadDepth(symbol){

    const p=document.getElementById("panel")

    p.innerHTML=`
    <div class="loading">
    Derinlik verisi alınıyor...
    </div>
    `

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

        p.innerHTML=`
        <div class="warning">
        Derinlik verisi şu anda alınamadı.
        </div>
        `
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


function closeDetail(){
    document.getElementById("detail").style.display="none"
    document.getElementById("home").style.display="block"
}

function detailTab(tab,el){

    document.querySelectorAll(".detailTabs button")
        .forEach(b=>b.classList.remove("active"))

    el.classList.add("active")

    const s=selected
    const p=document.getElementById("panel")

    if(tab==="depth"){
        loadDepth(s.symbol);

        depthTimer = setInterval(()=>{
            loadDepth(s.symbol);
        },1000);

        return
    }

    if(tab==="kademe"){
        loadDepth(s.symbol);

        depthTimer = setInterval(()=>{
            loadDepth(s.symbol);
        },1000);

        return
    }

    if(tab==="signals"){

        let durum = s.signal || "-"
        let rsiText = "-"

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
        <h3>Grafik</h3>
        <div class="warning">
        Gün içi mum grafik ve geçmiş fiyat serisi sonraki veri
        kaynağı bağlantısıyla burada gösterilecek.
        </div>
        </div>
        `
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
        p.innerHTML=`
        <div class="card">
        <h3>Aracı Kurum Dağılımı — AKD</h3>
        </div>

        <div class="warning">
        Burada kurum bazında
        <b>Alış Lot • Satış Lot • Net Lot • Net TL • En Güçlü Alıcılar • En Güçlü Satıcılar</b>
        gösterilecek.

        <br><br>

        Gerçek AKD verisi mevcut ücretsiz tarama kaynağında bulunmadığı
        için şu anda rakam uydurmuyoruz. Lisanslı AKD kaynağı bağlandığında
        bu sekme otomatik gerçek verilerle dolacak.
        </div>
        `
    }

    if(tab==="takas"){
        p.innerHTML=`
        <div class="card">
        <h3>Takas / Saklama</h3>
        </div>

        <div class="warning">
        Kurumların saklama oranları, takas değişimleri ve yoğunlaşma
        verileri için gerçek takas veri sağlayıcısı bağlanması gerekiyor.
        </div>
        `
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
setInterval(load,60000)
setInterval(loadKurlar, 1000)

</script>


<div class="mobileBottomNav" id="mobileBottomNav">
 <button class="active" onclick="bottomGo('all',this)"><b>⌂</b>Ana Sayfa</button>
 <button onclick="bottomGo('strong',this)"><b>⚡</b>Sinyaller</button>
 <button onclick="bottomGo('strong',this)"><b>★</b>Güçlü</button>
 <button onclick="bottomGo('volume',this)"><b>▥</b>Hacim</button>
</div>

<script>
function bottomGo(type,el){
  document.querySelectorAll('#mobileBottomNav button').forEach(x=>x.classList.remove('active'));
  el.classList.add('active');

  const map={
    all:'all',
    strong:'strong',
    volume:'volume'
  };

  const wanted=map[type] || 'all';

  // Mevcut üst filtre butonunu kullan
  const buttons=[...document.querySelectorAll('button')];
  const names={
    all:'Tüm BIST',
    strong:'Güçlü',
    volume:'Hacim'
  };
  const target=buttons.find(b=>b.textContent.trim()===names[wanted]);
  if(target){
    target.click();
    window.scrollTo({top:0,behavior:'smooth'});
  }
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


if __name__ == "__main__":

    threading.Thread(
        target=background_loop,
        daemon=True
    ).start()

    port = int(os.environ.get("PORT", 5000))

    print("BIST VERİ TERMİNALİ V2 AKTİF")
    print("http://127.0.0.1:%s" % port)

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        threaded=True
    )
