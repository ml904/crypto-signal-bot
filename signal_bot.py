"""
signal_bot.py — Bot de signaux Binance SPOT (cryptos < $1)
"""

import os
import time
import traceback
from datetime import datetime

import ccxt
import pandas as pd
import numpy as np
import requests

NTFY_TOPIC    = os.environ.get("NTFY_TOPIC", "malick-crypto-signaux")
MAX_PRICE     = float(os.environ.get("MAX_PRICE", "1.0"))
MIN_VOLUME    = float(os.environ.get("MIN_VOLUME", "5000000"))
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "300"))
MIN_SCORE     = int(os.environ.get("MIN_SCORE", "4"))
TIMEFRAME     = os.environ.get("TIMEFRAME", "15m")

def notify(title, message, priority="default", tags=""):
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": tags}, timeout=10)
    except Exception as e:
        print(f"[ntfy] Erreur: {e}")

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period-1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period-1, min_periods=period).mean()
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
    return sma + std_dev*std, sma, sma - std_dev*std

def compute_atr(high, low, close, period=14):
    tr = pd.concat([high-low,(high-close.shift()).abs(),(low-close.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(com=period-1, min_periods=period).mean()

def analyze(symbol, df, min_score=4):
    if len(df) < 50: return None,0,[],0,0,0,0
    close=df["close"];high=df["high"];low=df["low"];vol=df["volume"]
    rsi=compute_rsi(close);ema9=close.ewm(span=9,adjust=False).mean()
    ema21=close.ewm(span=21,adjust=False).mean();ema50=close.ewm(span=50,adjust=False).mean()
    macd_l,macd_s,macd_h=compute_macd(close)
    bb_up,bb_mid,bb_low=compute_bollinger(close)
    atr=compute_atr(high,low,close)
    rsi_now=rsi.iloc[-1];rsi_prev=rsi.iloc[-2]
    ema9_now=ema9.iloc[-1];ema21_now=ema21.iloc[-1];ema50_now=ema50.iloc[-1]
    macd_now=macd_l.iloc[-1];macd_sig=macd_s.iloc[-1]
    macd_h_now=macd_h.iloc[-1];macd_h_pre=macd_h.iloc[-2]
    price=close.iloc[-1];bb_up_now=bb_up.iloc[-1];bb_low_now=bb_low.iloc[-1];bb_mid_now=bb_mid.iloc[-1]
    atr_now=atr.iloc[-1]
    vol_avg=vol.rolling(20).mean().iloc[-1]
    vol_ratio=vol.iloc[-1]/vol_avg if vol_avg>0 else 1.0
    ls=0;ss=0;lr=[];sr=[]
    if rsi_now<35 and rsi_now>rsi_prev: ls+=2;lr.append(f"RSI survendu {rsi_now:.0f}")
    elif rsi_now<50 and rsi_now>rsi_prev: ls+=1;lr.append(f"RSI haussier {rsi_now:.0f}")
    if ema9_now>ema21_now>ema50_now: ls+=2;lr.append("EMA 9>21>50")
    elif ema9_now>ema21_now: ls+=1;lr.append("EMA9>EMA21")
    if macd_now>macd_sig and macd_h_now>0 and macd_h_pre<=0: ls+=2;lr.append("Croisement MACD haussier")
    elif macd_now>macd_sig and macd_h_now>macd_h_pre: ls+=1;lr.append("MACD momentum+")
    if price<=bb_low_now*1.005: ls+=2;lr.append("Prix BB basse")
    elif price<bb_mid_now: ls+=1;lr.append("Prix sous BB med")
    if vol_ratio>=1.5: ls+=1;lr.append(f"Volume {vol_ratio:.1f}x")
    if rsi_now>65 and rsi_now<rsi_prev: ss+=2;sr.append(f"RSI surachet {rsi_now:.0f}")
    elif rsi_now>50 and rsi_now<rsi_prev: ss+=1;sr.append(f"RSI baissier {rsi_now:.0f}")
    if ema9_now<ema21_now<ema50_now: ss+=2;sr.append("EMA 9<21<50")
    elif ema9_now<ema21_now: ss+=1;sr.append("EMA9<EMA21")
    if macd_now<macd_sig and macd_h_now<0 and macd_h_pre>=0: ss+=2;sr.append("Croisement MACD baissier")
    elif macd_now<macd_sig and macd_h_now<macd_h_pre: ss+=1;sr.append("MACD momentum-")
    if price>=bb_up_now*0.995: ss+=2;sr.append("Prix BB haute")
    elif price>bb_mid_now: ss+=1;sr.append("Prix au-dessus BB")
    if vol_ratio>=1.5 and ss>0: ss+=1;sr.append(f"Volume {vol_ratio:.1f}x")
    sl_mult=2.0;tp_mult=3.5
    if ls>=min_score and ls>ss:
        return "LONG",ls,lr,price,price-sl_mult*atr_now,price+tp_mult*atr_now,atr_now
    if ss>=min_score and ss>ls:
        return "SHORT",ss,sr,price,price+sl_mult*atr_now,price-tp_mult*atr_now,atr_now
    return None,0,[],price,0,0,atr_now

STABLECOINS={"USDT","USDC","BUSD","DAI","TUSD","FDUSD","USDP","USDD"}
BAD_SUFFIXES=("UP","DOWN","BULL","BEAR","3L","3S")

def is_eligible(symbol,price,vol24h):
    if price<=0 or price>MAX_PRICE: return False
    if vol24h<MIN_VOLUME: return False
    base=symbol.split("/")[0]
    if base.upper() in STABLECOINS: return False
    if any(base.upper().endswith(s) for s in BAD_SUFFIXES): return False
    return True

def ts():
    return datetime.now().strftime("%H:%M:%S")

def main():
    print(f"[{ts()}] Bot signaux SPOT demarré | ntfy:{NTFY_TOPIC} | <${MAX_PRICE} | {TIMEFRAME} | {SCAN_INTERVAL}s")
    notify("Bot demarré", f"Signaux SPOT actifs!\n< ${MAX_PRICE} | {TIMEFRAME}\nScan ttes {SCAN_INTERVAL//60} min", priority="default", tags="white_check_mark")
    exchange = ccxt.binance()
    exchange.load_markets()
    print(f"[{ts()}] {len(exchange.markets)} marchés SPOT chargés")
    last_signal={};COOLDOWN=3600;cycle=0
    while True:
        cycle+=1
        print(f"\n[{ts()}] Cycle #{cycle}")
        try:
            tickers=exchange.fetch_tickers()
            eligible=[(sym,t["last"] or 0,t.get("quoteVolume") or 0)
                for sym,t in tickers.items()
                if sym.endswith("/USDT") and is_eligible(sym,t.get("last") or 0,t.get("quoteVolume") or 0)]
            print(f"[{ts()}] {len(eligible)} paires eligibles")
            signals_sent=0
            for sym,price,vol24h in eligible:
                try:
                    now=time.time()
                    if sym in last_signal and (now-last_signal[sym])<COOLDOWN: continue
                    raw=exchange.fetch_ohlcv(sym,TIMEFRAME,limit=100)
                    if len(raw)<50: continue
                    df=pd.DataFrame(raw,columns=["ts","open","high","low","close","volume"])
                    df.set_index("ts",inplace=True)
                    result=analyze(sym,df,min_score=MIN_SCORE)
                    if result[0]:
                        _,score,reasons,p,sl,tp,atr=result
                        sig=result[0];base=sym.split("/")[0]
                        if sig=="LONG":
                            sl_pct=((p-sl)/p)*100;tp_pct=((tp-p)/p)*100
                            priority="high";tags="chart_with_upwards_trend"
                        else:
                            sl_pct=((sl-p)/p)*100;tp_pct=((p-tp)/p)*100
                            priority="high";tags="chart_with_downwards_trend"
                        title=f"{sig} {base} | Score {score}/8"
                        body=(f"Prix: ${p:.8g}\n"
                              f"SL: ${sl:.8g} (-{sl_pct:.1f}%)\n"
                              f"TP: ${tp:.8g} (+{tp_pct:.1f}%)\n"
                              f"Vol24h: ${vol24h/1e6:.1f}M\n"
                              f"{", ".join(reasons[:3])}")
                        notify(title,body,priority=priority,tags=tags)
                        last_signal[sym]=now;signals_sent+=1
                        print(f"[{ts()}] Signal: {sig} {sym} @ ${p:.8g} (score={score})")
                    time.sleep(0.3)
                except Exception: continue
            msg=f"{signals_sent} signal(s) envoye(s)." if signals_sent else "Aucun signal ce cycle."
            print(f"[{ts()}] {msg}")
        except Exception as e:
            print(f"[{ts()}] Erreur cycle #{cycle}: {e}")
            traceback.print_exc()
        print(f"[{ts()}] Prochain scan dans {SCAN_INTERVAL}s...")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
