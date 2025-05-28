import requests
import time
import json
import os
from datetime import datetime
import pandas as pd
from flask import Flask
import threading

# === Flask setup biar Render gak spin-down ===
app = Flask(__name__)

@app.route('/')
def index():
    return "✅ Binance breakout bot is running."

def run_flask():
    app.run(host='0.0.0.0', port=10000)

# === Bot logic di bawah sini ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
BASE_URL = "https://api.binance.com"
LOG_FILE = "alert_log.json"

# Load log alert harian
if os.path.exists(LOG_FILE):
    with open(LOG_FILE, "r") as f:
        alert_log = json.load(f)
else:
    alert_log = {}

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"❌ Telegram error: {e}")

def analyze_pair(symbol):
    print(f"🔍 {symbol}", flush=True)
    try:
        url = f"{BASE_URL}/api/v3/klines"
        params = {
            'symbol': symbol,
            'interval': '4h',
            'limit': 30
        }
        res = requests.get(url, params=params, timeout=10)
        data = res.json()

        if not isinstance(data, list):
            print(f"⚠️ Data kosong / error: {symbol}")
            return

        df = pd.DataFrame(data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_volume', 'taker_buy_quote_volume', 'ignore'
        ])
        df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)

        if df[['open', 'high', 'low', 'close', 'volume']].isnull().values.any():
            print(f"⚠️ Data null ditemukan di {symbol}, dilewati.")
            return

        df['ma7'] = df['close'].rolling(7).mean()
        df['ma25'] = df['close'].rolling(25).mean()
        df['volume_ma'] = df['volume'].rolling(20).mean()

        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss
        df['rsi14'] = 100 - (100 / (1 + rs))

        last = df.iloc[-1]
        prev = df.iloc[-2]

        vol_url = f"{BASE_URL}/api/v3/ticker/24hr?symbol={symbol}"
        vol_data = requests.get(vol_url).json()
        volume_usd = float(vol_data['quoteVolume'])

        if volume_usd < 500_000:
            print(f"⚠️ Skip {symbol} (volume 24h cuma ${volume_usd:,.0f})")
            return

        base_high = df['high'].iloc[-6:-1].max()
        base_low = df['low'].iloc[-6:-1].min()
        base_range_pct = (base_high - base_low) / base_low * 100

        today = datetime.now().strftime('%Y-%m-%d')
        if alert_log.get(symbol) == today:
            return

        body_pct = (last['close'] - last['open']) / last['open'] * 100
        body_vs_wick = (last['close'] - last['open']) > (last['high'] - last['low']) * 0.25
        strong_breakout = body_pct > 1.2 and body_vs_wick

        breakout_valid = (
            prev['rsi14'] <= 60 and
            last['rsi14'] > 60 and last['rsi14'] < 70 and
            last['volume'] > 1.5 * last['volume_ma'] and
            last['ma7'] > last['ma25'] and
            base_range_pct < 5
        )

        if breakout_valid and strong_breakout:
            msg = f"🔥 *STRONG BREAKOUT*\n\n*Pair:* `{symbol}`\n*Close:* `{last['close']}`\n*RSI:* `{last['rsi14']:.2f}`\n*Volume:* `{last['volume']:.2f}`\n*Base Range:* `{base_range_pct:.2f}%`\n*Candle Body:* `{body_pct:.2f}%`"
            print(msg)
            send_telegram(msg)
            alert_log[symbol] = today

        elif breakout_valid:
            msg = f"⚠️ *NORMAL BREAKOUT*\n\n*Pair:* `{symbol}`\n*Close:* `{last['close']}`\n*RSI:* `{last['rsi14']:.2f}`\n*Volume:* `{last['volume']:.2f}`\n*Base Range:* `{base_range_pct:.2f}%`\n*Candle Body:* `{body_pct:.2f}%`"
            print(msg)
            send_telegram(msg)
            alert_log[symbol] = today

    except Exception as e:
        print(f"⚠️ Error fetch {symbol}: {e}")

def get_usdt_pairs():
    try:
        res = requests.get(f"{BASE_URL}/api/v3/exchangeInfo", timeout=10)
        data = res.json()
        pairs = [
            s['symbol'] for s in data['symbols']
            if s['quoteAsset'] == 'USDT' and s['status'] == 'TRADING' and s['isSpotTradingAllowed']
        ]
        return pairs
    except Exception as e:
        print(f"⚠️ Gagal ambil daftar pair: {e}")
        return []

# === LOOP TIAP 15 MENIT ===
def run_bot():
    while True:
        symbols = get_usdt_pairs()
        print(f"\n📊 Mulai scan {len(symbols)} pair... ⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
        for symbol in symbols:
            analyze_pair(symbol)
            time.sleep(0.1)

        with open(LOG_FILE, "w") as f:
            json.dump(alert_log, f)

        print("⏳ Tunggu 15 menit...\n", flush=True)
        time.sleep(15 * 60)

# ⬇️ INI DI LUAR, bukan di dalam run_bot
threading.Thread(target=run_flask).start()
threading.Thread(target=run_bot).start()
