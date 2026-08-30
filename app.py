from flask import Flask, jsonify, render_template_string, request
import requests, time, json, os, threading
from datetime import datetime

app = Flask(__name__)

TV_URL = "https://scanner.tradingview.com/turkey/scan"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android) AppleWebKit/537.36 Chrome/140 Mobile Safari/537.36",
    "Content-Type": "application/json",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
}

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

STATE_FILE = os.path.expanduser("~/borsa/sent_signals.json")

SCAN_INTERVAL = 15 * 60
MIN_SCORE = 110
MAX_TELEGRAM = 8
COOLDOWN = 3 * 60 * 60

_cache = {"ts": 0, "stocks": [], "error": None}
_lock = threading.Lock()


def fnum(x, default=0.0):
    try:
        return float(x) if x is not None else default
    except Exception:
        return default


def score_stock(price, change, relvol, rsi, macd, macdsig, ema20, ema50, sma20, rec):
    score = 0
    reasons = []

    if 55 <= rsi <= 70:
        score += 30
        reasons.append("RSI güçlü bölgede")
    elif 50 <= rsi < 55:
        score += 18
        reasons.append("RSI toparlanıyor")
    elif 70 < rsi <= 75:
        score += 8
        reasons.append("RSI yüksek")

    if relvol >= 2:
        score += 30
        reasons.append("Göreli hacim 2x+")
    elif relvol >= 1.5:
        score += 25
        reasons.append("Göreli hacim 1.5x+")
    elif relvol >= 1.2:
        score += 12
        reasons.append("Hacim artıyor")

    if price > ema20 > ema50 > 0:
        score += 25
        reasons.append("EMA trend güçlü")
    elif price > ema20 > 0:
        score += 12
        reasons.append("EMA20 üzerinde")

    if macd > macdsig:
        score += 20
        reasons.append("MACD pozitif")

    if rec >= 0.5:
        score += 20
        reasons.append("Teknik güç yüksek")
    elif rec >= 0.2:
        score += 10
        reasons.append("Teknik görünüm pozitif")

    if 0.5 <= change <= 6:
        score += 15
        reasons.append("Momentum güçlü")
    elif 0 < change < 0.5:
        score += 7
    elif 6 < change <= 9.9:
        score += 8
        reasons.append("Hızlı yükseliş")

    if sma20 > 0 and price > sma20:
        score += 10
        reasons.append("SMA20 üzerinde")

    score = min(int(score), 150)

    if score >= 120:
        signal, emoji = "ÇOK GÜÇLÜ", "🚀"
    elif score >= 110:
        signal, emoji = "GÜÇLÜ", "🔥"
    elif score >= 90:
        signal, emoji = "İZLE", "👀"
    elif score >= 70:
        signal, emoji = "ORTA", "⚡"
    else:
        signal, emoji = "ZAYIF", "➖"

    return score, signal, emoji, reasons


def fetch_bist():
    payload = {
        "filter": [
            {"left": "exchange", "operation": "equal", "right": "BIST"},
            {"left": "type", "operation": "equal", "right": "stock"},
        ],
        "options": {"lang": "tr"},
        "markets": ["turkey"],
        "symbols": {
            "query": {"types": []},
            "tickers": []
        },
        "columns": [
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
        ],
        "sort": {
            "sortBy": "volume",
            "sortOrder": "desc"
        },
        "range": [0, 1000]
    }

    r = requests.post(
        TV_URL,
        headers=HEADERS,
        json=payload,
        timeout=30
    )

    r.raise_for_status()

    data = r.json().get("data", [])

    out = []

    for item in data:
        d = item.get("d", [])
        sym = item.get("s", "").replace("BIST:", "")

        if not sym or len(d) < 14:
            continue

        name = d[1] or sym

        price = fnum(d[2])
        change = fnum(d[3])
        volume = fnum(d[4])
        relvol = fnum(d[5])
        rsi = fnum(d[6])
        macd = fnum(d[7])
        macdsig = fnum(d[8])
        ema20 = fnum(d[9])
        ema50 = fnum(d[10])
        sma20 = fnum(d[11])
        rec = fnum(d[12])
        mcap = fnum(d[13])

        if price <= 0:
            continue

        score, signal, emoji, reasons = score_stock(
            price,
            change,
            relvol,
            rsi,
            macd,
            macdsig,
            ema20,
            ema50,
            sma20,
            rec
        )

        out.append({
            "symbol": sym,
            "name": str(name),
            "price": round(price, 2),
            "change": round(change, 2),
            "volume": int(volume),
            "relative_volume": round(relvol, 2),
            "rsi": round(rsi, 1),
            "macd": round(macd, 3),
            "ema20": round(ema20, 2),
            "ema50": round(ema50, 2),
            "recommend": round(rec, 2),
            "market_cap": mcap,
            "score": score,
            "signal": signal,
            "emoji": emoji,
            "reasons": reasons,
        })

    out.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return out


def state_load():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def state_save(s):
    os.makedirs(
        os.path.dirname(STATE_FILE),
        exist_ok=True
    )

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            s,
            f,
            ensure_ascii=False,
            indent=2
        )


def telegram_send(stock):
    if not BOT_TOKEN or not CHAT_ID:
        return False

    text = "\n".join([
        "🚨 BIST RADAR SİNYALİ",
        "",
        f"📈 {stock['symbol']}",
        f"💰 Fiyat: {stock['price']:.2f} TL",
        f"📊 Değişim: {stock['change']:+.2f}%",
        f"🧠 Teknik Skor: {stock['score']}/150",
        f"RSI: {stock['rsi']}",
        f"🔥 Göreli Hacim: {stock['relative_volume']}x",
        "",
        *[
            f"✅ {r}"
            for r in stock.get("reasons", [])[:6]
        ],
        "",
        f"{stock['emoji']} {stock['signal']}",
        "",
        "⚠️ Teknik taramadır, yatırım tavsiyesi değildir."
    ])

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendMessage"
    )

    r = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": text
        },
        timeout=15
    )

    return r.ok


def send_new_signals(stocks):
    now = time.time()
    state = state_load()
    sent = []

    candidates = [
        x
        for x in stocks
        if x["score"] >= MIN_SCORE
    ][:MAX_TELEGRAM]

    for s in candidates:
        old = state.get(
            s["symbol"],
            {}
        )

        last_t = fnum(
            old.get("time")
        )

        last_score = fnum(
            old.get("score")
        )

        last_price = fnum(
            old.get("price")
        )

        due = (
            now - last_t
            >= COOLDOWN
        )

        stronger = (
            s["score"]
            >= last_score + 10
        )

        moved = (
            last_price > 0
            and abs(
                (
                    s["price"]
                    / last_price
                    - 1
                )
                * 100
            )
            >= 2
        )

        if (
            s["symbol"] not in state
            or due
            or stronger
            or moved
        ):
            if telegram_send(s):
                state[s["symbol"]] = {
                    "time": now,
                    "score": s["score"],
                    "price": s["price"]
                }

                sent.append(
                    s["symbol"]
                )

                time.sleep(0.3)

    state_save(state)

    return sent


def refresh_cache(send_tg=False):
    stocks = fetch_bist()

    sent = (
        send_new_signals(stocks)
        if send_tg
        else []
    )

    with _lock:
        _cache["ts"] = time.time()
        _cache["stocks"] = stocks
        _cache["error"] = None

    return stocks, sent


def background_loop():
    while True:
        try:
            now = datetime.now()

            weekday = now.weekday()

            minutes = (
                now.hour * 60
                + now.minute
            )

            in_session = (
                weekday < 5
                and 600 <= minutes <= 1090
            )

            if in_session:
                refresh_cache(
                    send_tg=True
                )

        except Exception as e:
            print(
                "Otomatik tarama hatası:",
                e
            )

        time.sleep(
            SCAN_INTERVAL
        )


HTML = """
<!doctype html>

<html lang="tr">

<head>

<meta charset="utf-8">

<meta
name="viewport"
content="width=device-width,initial-scale=1,maximum-scale=1"
>

<title>BIST Radar AI</title>

<style>

*{
box-sizing:border-box;
}

body{
margin:0;
background:#080b11;
color:#f3f5f8;
font-family:Arial,sans-serif;
padding-bottom:82px;
}

.header{
position:sticky;
top:0;
z-index:10;
padding:16px;
background:#0d1119;
border-bottom:1px solid #202633;
}

.brand{
font-size:22px;
font-weight:900;
}

.sub{
font-size:12px;
color:#8c96a5;
margin-top:4px;
}

.container{
padding:12px;
}

.cards{
display:flex;
gap:9px;
overflow:auto;
}

.card{
min-width:142px;
background:#121722;
border:1px solid #252c39;
border-radius:18px;
padding:14px;
}

.lbl{
font-size:12px;
color:#98a2b1;
}

.num{
font-size:22px;
font-weight:900;
margin-top:7px;
}

.green{
color:#22d69b;
}

.red{
color:#ff5572;
}

.blue{
color:#73a3ff;
}

.search{
display:flex;
align-items:center;
background:#121722;
border:1px solid #252c39;
border-radius:18px;
padding:0 12px;
margin-top:12px;
}

.search input{
width:100%;
padding:14px 8px;
border:0;
outline:0;
background:none;
color:white;
font-size:16px;
}

.tabs{
display:flex;
gap:7px;
overflow:auto;
margin-top:12px;
}

.tab{
border:1px solid #29313f;
background:#11161f;
color:#aab3c0;
border-radius:20px;
padding:10px 15px;
white-space:nowrap;
font-weight:700;
}

.tab.active{
background:#397cf0;
color:#fff;
}

.grid{
display:grid;
grid-template-columns:repeat(3,1fr);
gap:8px;
margin-top:14px;
}

.box{
background:#121722;
border:1px solid #252c39;
border-radius:16px;
padding:13px;
text-align:center;
}

.box .n{
font-size:20px;
font-weight:900;
}

.box .t{
font-size:11px;
color:#98a2b1;
margin-top:4px;
}

.actions{
display:flex;
gap:8px;
margin-top:12px;
}

.btn{
flex:1;
border:0;
border-radius:14px;
padding:13px;
font-weight:800;
color:#fff;
background:#397cf0;
}

.btn.tg{
background:#1b3149;
}

.title{
font-size:13px;
color:#9fa9b7;
font-weight:800;
margin:16px 0 10px;
}

.stock{
background:#121722;
border:1px solid #242b37;
border-radius:16px;
padding:13px;
margin-bottom:8px;
}

.row{
display:flex;
justify-content:space-between;
gap:10px;
align-items:center;
}

.sym{
font-size:17px;
font-weight:900;
}

.name{
font-size:11px;
color:#8d98a8;
margin-top:3px;
max-width:200px;
overflow:hidden;
text-overflow:ellipsis;
white-space:nowrap;
}

.price{
text-align:right;
font-weight:900;
}

.chg{
text-align:right;
font-size:13px;
font-weight:800;
margin-top:3px;
}

.metrics{
display:grid;
grid-template-columns:repeat(4,1fr);
gap:6px;
margin-top:10px;
}

.m{
background:#0c1017;
border-radius:10px;
padding:7px 3px;
text-align:center;
}

.mv{
font-size:13px;
font-weight:800;
}

.ml{
font-size:9px;
color:#7f8998;
margin-top:3px;
}

.sigrow{
display:flex;
justify-content:space-between;
align-items:center;
margin-top:10px;
}

.sig{
font-size:11px;
padding:6px 8px;
border-radius:8px;
background:#202633;
}

.strong{
color:#27dfa3;
background:#123226;
}

.watch{
color:#ffd064;
background:#332b16;
}

.score{
font-size:16px;
font-weight:900;
}

.reason{
font-size:10px;
color:#8d98a8;
line-height:1.5;
margin-top:8px;
}

.fav{
border:0;
background:none;
font-size:20px;
color:#6e7888;
}

.fav.on{
color:#ffc84b;
}

.loading{
text-align:center;
color:#9aa5b5;
padding:30px;
}

.bottom{
position:fixed;
bottom:0;
left:0;
right:0;
height:70px;
background:#0d1119;
border-top:1px solid #252c39;
display:flex;
justify-content:space-around;
}

.nav{
border:0;
background:none;
color:#7e8897;
font-size:11px;
}

.nav span{
display:block;
font-size:21px;
margin-bottom:3px;
}

.nav.active{
color:#73a3ff;
}

</style>

</head>

<body>

<div class="header">

<div class="brand">
🧠 BIST RADAR AI
</div>

<div class="sub">
Tüm BIST • teknik skor • Telegram sinyal
</div>

</div>

<div class="container">

<div class="cards">

<div class="card">
<div class="lbl">
Taranan
</div>
<div
id="total"
class="num"
>
-
</div>
</div>

<div class="card">
<div class="lbl">
Yükselen
</div>
<div
id="up"
class="num green"
>
-
</div>
</div>

<div class="card">
<div class="lbl">
Güçlü Sinyal
</div>
<div
id="strong"
class="num blue"
>
-
</div>
</div>

</div>

<div class="search">

🔎

<input
id="q"
placeholder="Sembol ara... THYAO, GARAN..."
oninput="render()"
>

</div>

<div class="tabs">

<button
class="tab active"
onclick="setMode('all',this)"
>
Ana Sayfa
</button>

<button
class="tab"
onclick="setMode('up',this)"
>
📈 Yükselen
</button>

<button
class="tab"
onclick="setMode('down',this)"
>
📉 Düşen
</button>

<button
class="tab"
onclick="setMode('volume',this)"
>
📊 Hacim
</button>

<button
class="tab"
onclick="setMode('signal',this)"
>
🧠 Sinyaller
</button>

</div>

<div class="grid">

<div class="box">
<div
id="u2"
class="n green"
>
-
</div>
<div class="t">
Yükselen
</div>
</div>

<div class="box">
<div
id="d2"
class="n red"
>
-
</div>
<div class="t">
Düşen
</div>
</div>

<div class="box">
<div
id="f2"
class="n"
>
-
</div>
<div class="t">
Nötr
</div>
</div>

</div>

<div class="actions">

<button
class="btn"
onclick="load(false)"
>
🔄 Piyasayı Tara
</button>

<button
class="btn tg"
onclick="load(true)"
>
📨 Tara + Telegram
</button>

</div>

<div
id="listTitle"
class="title"
>
BIST HİSSELERİ
</div>

<div
id="list"
class="loading"
>
Piyasa verileri yükleniyor...
</div>

</div>

<div class="bottom">

<button
class="nav active"
onclick="nav('all',this)"
>
<span>🏠</span>
Piyasa
</button>

<button
class="nav"
onclick="nav('signal',this)"
>
<span>🔔</span>
Sinyaller
</button>

<button
class="nav"
onclick="nav('volume',this)"
>
<span>🧭</span>
Keşfet
</button>

<button
class="nav"
onclick="favNav(this)"
>
<span>⭐</span>
Favoriler
</button>

<button
class="nav"
onclick="nav('up',this)"
>
<span>📚</span>
Listeler
</button>

</div>

<script>

let stocks = [];

let mode = "all";

let favs =
JSON.parse(
localStorage.getItem(
"bistFavs"
) || "[]"
);


function esc(s){

return String(
s ?? ""
)
.replaceAll("&","&amp;")
.replaceAll("<","&lt;")
.replaceAll(">","&gt;");

}


async function load(tg){

let L =
document.getElementById(
"list"
);

L.className =
"loading";

L.innerText =
tg
? "Telegram ile taranıyor..."
: "BIST taranıyor...";

try{

let r =
await fetch(
"/api/market"
+
(
tg
? "?telegram=1"
: ""
)
);

let j =
await r.json();

if(!r.ok){

throw new Error(
j.error
||
"Veri alınamadı"
);

}

stocks =
j.stocks || [];

summary();

render();

if(tg){

alert(
(j.telegram_sent || []).length
? "Gönderildi: "
+
j.telegram_sent.join(", ")
: "Yeni güçlü Telegram sinyali yok."
);

}

}catch(e){

L.className =
"loading";

L.innerText =
"❌ "
+
e.message;

}

}


function summary(){

let u =
stocks.filter(
x =>
x.change > 0.01
).length;

let d =
stocks.filter(
x =>
x.change < -0.01
).length;

let f =
stocks.length
-
u
-
d;

let s =
stocks.filter(
x =>
x.score >= 110
).length;

document.getElementById(
"total"
).innerText =
stocks.length;

document.getElementById(
"up"
).innerText =
u;

document.getElementById(
"strong"
).innerText =
s;

document.getElementById(
"u2"
).innerText =
u;

document.getElementById(
"d2"
).innerText =
d;

document.getElementById(
"f2"
).innerText =
f;

}


function setMode(m,e){

mode = m;

document.querySelectorAll(
".tab"
).forEach(
x =>
x.classList.remove(
"active"
)
);

e.classList.add(
"active"
);

render();

}


function nav(m,e){

mode = m;

document.querySelectorAll(
".nav"
).forEach(
x =>
x.classList.remove(
"active"
)
);

e.classList.add(
"active"
);

render();

}


function favNav(e){

mode =
"fav";

document.querySelectorAll(
".nav"
).forEach(
x =>
x.classList.remove(
"active"
)
);

e.classList.add(
"active"
);

render();

}


function tf(s){

favs =
favs.includes(s)
?
favs.filter(
x =>
x !== s
)
:
[
...favs,
s
];

localStorage.setItem(
"bistFavs",
JSON.stringify(
favs
)
);

render();

}


function render(){

let a =
[
...stocks
];

let q =
document.getElementById(
"q"
)
.value
.trim()
.toUpperCase();

if(q){

a =
a.filter(
x =>
x.symbol.includes(q)
||
String(
x.name
)
.toUpperCase()
.includes(q)
);

}


if(
mode === "up"
){

a =
a
.filter(
x =>
x.change > 0
)
.sort(
(a,b) =>
b.change
-
a.change
);

document.getElementById(
"listTitle"
).innerText =
"📈 EN ÇOK YÜKSELENLER";

}

else if(
mode === "down"
){

a =
a
.filter(
x =>
x.change < 0
)
.sort(
(a,b) =>
a.change
-
b.change
);

document.getElementById(
"listTitle"
).innerText =
"📉 EN ÇOK DÜŞENLER";

}

else if(
mode === "volume"
){

a.sort(
(a,b) =>
b.relative_volume
-
a.relative_volume
);

document.getElementById(
"listTitle"
).innerText =
"📊 HACİM RADARI";

}

else if(
mode === "signal"
){

a =
a
.filter(
x =>
x.score >= 90
)
.sort(
(a,b) =>
b.score
-
a.score
);

document.getElementById(
"listTitle"
).innerText =
"🧠 AKILLI SİNYALLER";

}

else if(
mode === "fav"
){

a =
a
.filter(
x =>
favs.includes(
x.symbol
)
)
.sort(
(a,b) =>
b.score
-
a.score
);

document.getElementById(
"listTitle"
).innerText =
"⭐ FAVORİLER";

}

else{

a.sort(
(a,b) =>
b.score
-
a.score
);

document.getElementById(
"listTitle"
).innerText =
"BIST HİSSELERİ";

}


let L =
document.getElementById(
"list"
);

L.className = "";

if(!a.length){

L.innerHTML =
'<div class="loading">Sonuç yok.</div>';

return;

}


L.innerHTML =
a.map(
x => `

<div class="stock">

<div class="row">

<div>

<div>

<span class="sym">
${esc(x.symbol)}
</span>

<button
class="fav ${
favs.includes(
x.symbol
)
? "on"
: ""
}"
onclick="tf('${esc(x.symbol)}')"
>

${
favs.includes(
x.symbol
)
? "★"
: "☆"
}

</button>

</div>

<div class="name">
${esc(x.name)}
</div>

</div>

<div>

<div class="price">
${Number(x.price).toFixed(2)} ₺
</div>

<div
class="chg ${
x.change >= 0
? "green"
: "red"
}"
>

${
x.change >= 0
? "+"
: ""
}
${Number(x.change).toFixed(2)}%

</div>

</div>

</div>

<div class="metrics">

<div class="m">
<div class="mv">
${x.rsi}
</div>
<div class="ml">
RSI
</div>
</div>

<div class="m">
<div class="mv">
${x.relative_volume}x
</div>
<div class="ml">
HACİM
</div>
</div>

<div class="m">
<div class="mv">
${x.ema20}
</div>
<div class="ml">
EMA20
</div>
</div>

<div class="m">
<div class="mv">
${x.recommend}
</div>
<div class="ml">
TEKNİK
</div>
</div>

</div>

<div class="sigrow">

<span
class="sig ${
x.score >= 110
? "strong"
:
x.score >= 90
? "watch"
: ""
}"
>

${x.emoji}
${esc(x.signal)}

</span>

<span class="score">
${x.score}/150
</span>

</div>

<div class="reason">

${
(x.reasons || [])
.slice(0,5)
.map(
r =>
"✓ "
+
esc(r)
)
.join(" • ")
}

</div>

</div>

`
).join("");

}


load(false);

</script>

</body>

</html>
"""


@app.route("/")
def index():
    return render_template_string(
        HTML
    )


@app.route("/api/market")
def api_market():
    try:
        stocks, sent = refresh_cache(
            send_tg=(
                request.args.get(
                    "telegram"
                )
                == "1"
            )
        )

        return jsonify({
            "ok": True,
            "count": len(stocks),
            "telegram_sent": sent,
            "stocks": stocks
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route("/api/telegram-test")
def telegram_test():
    if not BOT_TOKEN or not CHAT_ID:
        return jsonify({
            "ok": False,
            "error": "Telegram ayarları yapılmadı"
        }), 400

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendMessage"
    )

    r = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": "✅ BIST RADAR Telegram bağlantısı başarılı."
        },
        timeout=15
    )

    return jsonify({
        "ok": r.ok,
        "response": r.text[:500]
    })


if __name__ == "__main__":
    threading.Thread(
        target=background_loop,
        daemon=True
    ).start()

    print(
        "BIST RADAR AI AKTİF"
    )

    print(
        "http://127.0.0.1:5000"
    )

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        threaded=True
    )
