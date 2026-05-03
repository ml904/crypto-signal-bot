"""
signal_bot.py — Bot de signaux crypto (cryptos < $1)
======================================================
- Scanne toutes les cryptos USDT sous $1 sur Gate.io
- Analyse avec RSI, EMA, MACD, Bollinger Bands, Volume
- Envoie les signaux sur ntfy.sh (téléphone)
- Tourne 24h/24 sur Railway (ou n'importe quel serveur)
- Ne trade PAS — lecture seule, zéro risque
"""

import os
import time
import traceback
from datetime import datetime

import ccxt
import pandas as pd
import numpy as np
import requests

# ══════════════════════════════════════════════════════════════════════════════
# Configuration (via variables d'environnement sur Railway)
# ══════════════════════════════════════════════════════════════════════════════

NTFY_TOPIC    = os.environ.get("NTFY_TOPIC", "malick-crypto-signaux")
MAX_PRICE     = float(os.environ.get("MAX_PRICE", "1.0"))
MIN_VOLUME    = float(os.environ.get("MIN_VOLUME", "500000"))
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "300"))
MIN_SCORE     = int(os.environ.get("MIN_SCORE", "3"))
TIMEFRAME     = os.environ.get("TIMEFRAME", "15m")

# ══════════════════════════════════════════════════════════════════════════════
# Notifications ntfy.sh
# ══════════════════════════════════════════════════════════════════════════════

def notify(title: str, message: str, priority: str = "default", tags: str = ""):
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": tags,
            },
            timeout=10
        )
    except Exception as e:
        print(f"[ntfy] Erreur: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# Indicateurs techniques
# ══════════════════════════════════════════════════════════════════════════════

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def compute_macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line, macd_line - signal_line

def compute_bollinger(series, period=20, std_dev=2):
    sma = series.rolling(period).mean()
    std = series.rolling(period).std()
    return sma + std_dev * std, sma, sma - std_dev * std

def compute_atr(high, low, close, period=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()

# ══════════════════════════════════════════════════════════════════════════════
# Analyse d'un symbole
# ══════════════════════════════════════════════════════════════════════════════

def analyze(symbol, df, min_score=3):
    if len(df) < 50:
        return None, 0, [], 0, 0, 0, 0

    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    vol   = df["volume"]

    rsi                     = compute_rsi(close)
    ema9                    = close.ewm(span=9,  adjust=False).mean()
    ema21                   = close.ewm(span=21, adjust=False).mean()
    ema50                   = close.ewm(span=50, adjust=False).mean()
    macd_l, macd_s, macd_h = compute_macd(close)
    bb_up, bb_mid, bb_low  = compute_bollinger(close)
    atr                     = compute_atr(high, low, close)

    rsi_now    = rsi.iloc[-1];    rsi_prev   = rsi.iloc[-2]
    ema9_now   = ema9.iloc[-1];   ema21_now  = ema21.iloc[-1]; ema50_now = ema50.iloc[-1]
    macd_now   = macd_l.iloc[-1]; macd_sig   = macd_s.iloc[-1]
    macd_h_now = macd_h.iloc[-1]; macd_h_pre = macd_h.iloc[-2]
    price      = close.iloc[-1]
    bb_up_now  = bb_up.iloc[-1];  bb_low_now = bb_low.iloc[-1]; bb_mid_now = bb_mid.iloc[-1]
    atr_now    = atr.iloc[-1]
    vol_ratio  = vol.iloc[-1] / vol.rolling(20).mean().iloc[-1]

    long_score  = 0
    short_score = 0
    reasons     = []
    short_rsns  = []

    # ── LONG ──────────────────────────────────────────────────────────────────
    if rsi_now < 35 and rsi_now > rsi_prev:
        long_score += 2; reasons.append(f"RSI survendu {rsi_now:.0f}")
    elif rsi_now < 50 and rsi_now > rsi_prev:
        long_score += 1; reasons.append(f"RSI haussier {rsi_now:.0f}")

    if ema9_now > ema21_now > ema50_now:
        long_score += 2; reasons.append("EMA 9>21>50 haussier")
    elif ema9_now > ema21_now:
        long_score += 1; reasons.append("EMA9 > EMA21")

    if macd_now > macd_sig and macd_h_now > 0 and macd_h_pre <= 0:
        long_score += 2; reasons.append("Croisement MACD haussier")
    elif macd_now > macd_sig and macd_h_now > macd_h_pre:
        long_score += 1; reasons.append("MACD momentum haussier")

    if price <= bb_low_now * 1.005:
        long_score += 2; reasons.append("Prix sur BB basse (rebond)")
    elif price < bb_mid_now:
        long_score += 1; reasons.append("Prix sous BB médiane")

    if vol_ratio >= 1.5:
        long_score += 1; reasons.append(f"Volume {vol_ratio:.1f}x")

    # ── SHORT ─────────────────────────────────────────────────────────────────
    if rsi_now > 65 and rsi_now < rsi_prev:
        short_score += 2; short_rsns.append(f"RSI surachet {rsi_now:.0f}")
    elif rsi_now > 50 and rsi_now < rsi_prev:
        short_score += 1; short_rsns.append(f"RSI baissier {rsi_now:.0f}")

    if ema9_now < ema21_now < ema50_now:
        short_score += 2; short_rsns.append("EMA 9<21<50 baissier")
    elif ema9_now < ema21_now:
        short_score += 1; short_rsns.append("EMA9 < EMA21")

    if macd_now < macd_sig and macd_h_now < 0 and macd_h_pre >= 0:
        short_score += 2; short_rsns.append("Croisement MACD baissier")
    elif macd_now < macd_sig and macd_h_now < macd_h_pre:
        short_score += 1; short_rsns.append("MACD momentum baissier")

    if price >= bb_up_now * 0.995:
        short_score += 2; short_rsns.append("Prix sur BB haute (retournement)")
    elif price > bb_mid_now:
        short_score += 1; short_rsns.append("Prix au-dessus BB médiane")

    if vol_ratio >= 1.5 and short_score > 0:
        short_score += 1; short_rsns.append(f"Volume {vol_ratio:.1f}x")

    # ── Décision ──────────────────────────────────────────────────────────────
    sl_mult = 2.0
    tp_mult = 3.5

    if long_score >= min_score and long_score > short_score:
        sl = price - sl_mult * atr_now
        tp = price + tp_mult * atr_now
        return "LONG", long_score, reasons, price, sl, tp, atr_now

    if short_score >= min_score and short_score > long_score:
        sl = price + sl_mult * atr_now
        tp = price - tp_mult * atr_now
        return "SHORT", short_score, short_rsns, price, sl, tp, atr_now

    return None, 0, [], price, 0, 0, atr_now

# ══════════════════════════════════════════════════════════════════════════════
# Filtres symboles
# ══════════════════════════════════════════════════════════════════════════════

STABLECOINS  = {"USDT","USDC","BUSD","DAI","TUSD","FDUSD","USDD","USDP","GUSD"}
BAD_SUFFIXES = ("UP","DOWN","BULL","BEAR","3L","3S","5L","5S")

def is_eligible(symbol, price, vol24h):
    if price <= 0 or price > MAX_PRICE:
        return False
    if vol24h < MIN_VOLUME:
        return False
    base = symbol.split("/")[0]
    if base.upper() in STABLECOINS:
        return False
    if any(base.upper().endswith(s) for s in BAD_SUFFIXES):
        return False
    return True

# ══════════════════════════════════════════════════════════════════════════════
# Bot principal
# ══════════════════════════════════════════════════════════════════════════════

def ts():
    return datetime.now().strftime("%H:%M:%S")

def init_exchange():
    """Initialise Gate.io avec retry automatique."""
    for attempt in range(1, 6):
        try:
            print(f"[{ts()}] Connexion Gate.io (tentative {attempt}/5)...")
            ex = ccxt.gateio({
                "options": {"defaultType": "spot"},
                "timeout": 30000,
            })
            ex.load_markets()
            print(f"[{ts()}] {len(ex.markets)} marchés chargés sur Gate.io OK")
            return ex
        except Exception as e:
            print(f"[{ts()}] Erreur connexion: {e}")
            if attempt < 5:
                wait = 30 * attempt
                print(f"[{ts()}] Retry dans {wait}s...")
                time.sleep(wait)
    raise RuntimeError("Impossible de se connecter a Gate.io apres 5 tentatives")

def main():
    print(f"[{ts()}] Bot de signaux demarre")
    print(f"[{ts()}] Exchange: Gate.io | Cryptos<${MAX_PRICE} | {TIMEFRAME} | Score>={MIN_SCORE}")

    try:
        exchange = init_exchange()
    except RuntimeError as e:
        notify("Bot erreur demarrage", str(e), priority="urgent", tags="x")
        return

    notify(
        "Bot demarre",
        f"Gate.io actif | Cryptos<${MAX_PRICE} | {TIMEFRAME} | Score>={MIN_SCORE}\nScan toutes les {SCAN_INTERVAL//60}min",
        priority="default",
        tags="white_check_mark"
    )

    last_signal = {}
    COOLDOWN    = 3600  # 1h entre deux signaux pour le meme symbole

    cycle = 0
    while True:
        cycle += 1
        print(f"\n[{ts()}] ── Cycle #{cycle} ──────────────────")

        try:
            tickers = exchange.fetch_tickers()
            eligible = [
                (sym, t["last"] or 0, t.get("quoteVolume") or 0)
                for sym, t in tickers.items()
                if sym.endswith("/USDT")
                and is_eligible(sym, t.get("last") or 0, t.get("quoteVolume") or 0)
            ]
            print(f"[{ts()}] {len(eligible)} paires eligibles")

            signals_sent = 0

            for sym, price, vol24h in eligible:
                try:
                    now = time.time()
                    if sym in last_signal and (now - last_signal[sym]) < COOLDOWN:
                        continue

                    raw = exchange.fetch_ohlcv(sym, TIMEFRAME, limit=100)
                    if len(raw) < 50:
                        continue

                    df = pd.DataFrame(raw, columns=["ts","open","high","low","close","volume"])
                    df.set_index("ts", inplace=True)

                    result = analyze(sym, df, min_score=MIN_SCORE)
                    signal = result[0]

                    if signal:
                        _, score, reasons, price_now, sl, tp, atr = result
                        base = sym.split("/")[0]

                        if signal == "LONG":
                            sl_pct   = ((price_now - sl) / price_now) * 100
                            tp_pct   = ((tp - price_now) / price_now) * 100
                            sig_str  = "LONG"
                            priority = "high"
                            tags     = "chart_with_upwards_trend"
                        else:
                            sl_pct   = ((sl - price_now) / price_now) * 100
                            tp_pct   = ((price_now - tp) / price_now) * 100
                            sig_str  = "SHORT"
                            priority = "high"
                            tags     = "chart_with_downwards_trend"

                        title = f"{sig_str} {base} | Score {score}/8"
                        body  = (
                            f"Prix      : ${price_now:.8g}\n"
                            f"Stop-Loss : ${sl:.8g} (-{sl_pct:.1f}%)\n"
                            f"Take-Profit: ${tp:.8g} (+{tp_pct:.1f}%)\n"
                            f"Volume 24h: ${vol24h/1e6:.1f}M\n"
                            f"Signaux: {', '.join(reasons[:3])}"
                        )

                        notify(title, body, priority=priority, tags=tags)
                        last_signal[sym] = now
                        signals_sent += 1
                        print(f"[{ts()}] Signal: {sig_str} {sym} @ ${price_now:.8g} (score={score})")

                    time.sleep(0.2)

                except Exception:
                    continue

            if signals_sent == 0:
                print(f"[{ts()}] Aucun signal fort ce cycle.")
            else:
                print(f"[{ts()}] {signals_sent} signal(s) envoye(s).")

        except Exception as e:
            print(f"[{ts()}] Erreur cycle #{cycle}: {e}")
            traceback.print_exc()
            try:
                print(f"[{ts()}] Reinitialisation exchange...")
                exchange = init_exchange()
            except Exception:
                pass

        print(f"[{ts()}] Prochain scan dans {SCAN_INTERVAL}s...")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
