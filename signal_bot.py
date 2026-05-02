import os
import time
import traceback
from datetime import datetime
import ccxt
import pandas as pd
import numpy as np
import requests

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "malick-crypto-signaux")
MAX_PRICE = float(os.environ.get("MAX_PRICE", "1.0"))
MIN_VOLUME = float(os.environ.get("MIN_VOLUME", "5000000"))
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "300"))
MIN_SCORE = int(os.environ.get("MIN_SCORE", "4"))
TIMEFRAME = os.environ.get("TIMEFRAME", "15m")

def notify(title, message, priority="default", tags=""):
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": tags}, timeout=10)
    except Exception as e:
        print(f"[ntfy] {e}")

def compute_rsi(series, period=14):
    delta=series.diff()
    gain=delta.clip(lower=0)
    loss=-delta.clip(upper=0)
    ag=gain.ewm(com=period-1,min_periods=period).mean()
    al=loss.ewm(com=period-1,min_periods=period).mean()
    rs=ag/al.replace(0,np.nan)
    return 100-(100/(1+rs))

def compute_macd(series,fast=12,slow=26,signal=9):
    ef=series.ewm(span=fast,adjust=False).mean()
    es=series.ewm(span=slow,adjust=False).mean()
    ml=ef-es
    sl2=ml.ewm(span=signal,adjust=False).mean()
    return ml,sl2,ml-sl2

def compute_bollinger(series,period=20,std_dev=2):
    sma=series.rolling(period).mean()
    std=series.rolling(period).std()
    return sma+std_dev*std,sma,sma-std_dev*std

def compute_atr(high,low,close,period=14):
    tr=pd.concat([high-low,(high-close.shift()).abs(),(low-close.shift()).abs()],axis=1).max(axis=1)
    return tr.ewm(com=period-1,min_periods=period).mean()

def analyze(symbol,df,min_score=4):
    if len(df)<50: return None,0,[],0
    close,high,low,vol=df["close"],df["high"],df["low"],df["volume"]
    rsi=compute_rsi(close)
    ema9=close.ewm(span=9,adjust=False).mean()
    ema21=close.ewm(span=21,adjust=False).mean()
    ema50=close.ewm(span=50,adjust=False).mean()
    ml,ms,mh=compute_macd(close)
    bu,bm,bl=compute_bollinger(close)
    atr=compute_atr(high,low,close)
    rn,rp=rsi.iloc[-1],rsi.iloc[-2]
    e9,e21,e50=ema9.iloc[-1],ema21.iloc[-1],ema50.iloc[-1]
    mn,msg=ml.iloc[-1],ms.iloc[-1]
    mhn,mhp=mh.iloc[-1],mh.iloc[-2]
    price=close.iloc[-1]
    bun,bln,bmn=bu.iloc[-1],bl.iloc[-1],bm.iloc[-1]
    atrn=atr.iloc[-1]
    vr=vol.iloc[-1]/vol.rolling(20).mean().iloc[-1]
    ls,ss,lr,sr=0,0,[],[]
    if rn<35 and rn>rp: ls+=2;lr.append(f"RSI survendu {rn:.0f}")
    elif rn<50 and rn>rp: ls+=1;lr.append(f"RSI haussier {rn:.0f}")
    if e9>e21>e50: ls+=2;lr.append("EMA haussier 9>21>50")
    elif e9>e21: ls+=1;lr.append("EMA9>EMA21")
    if mn>msg and mhn>0 and mhp<=0: ls+=2;lr.append("Croisement MACD haussier")
    elif mn>msg and mhn>mhp: ls+=1;lr.append("MACD momentum haussier")
    if price<=bln*1.005: ls+=2;lr.append("Prix sur BB basse")
    elif price<bmn: ls+=1;lr.append("Prix sous BB mediane")
    if vr>=1.5: ls+=1;lr.append(f"Volume fort {vr:.1f}x")
    if rn>65 and rn<rp: ss+=2;sr.append(f"RSI surachet {rn:.0f}")
    elif rn>50 and rn<rp: ss+=1;sr.append(f"RSI baissier {rn:.0f}")
    if e9<e21<e50: ss+=2;sr.append("EMA baissier 9<21<50")
    elif e9<e21: ss+=1;sr.append("EMA9<EMA21")
    if mn<msg and mhn<0 and mhp>=0: ss+=2;sr.append("Croisement MACD baissier")
    elif mn<msg and mhn<mhp: ss+=1;sr.append("MACD momentum baissier")
    if price>=bun*0.995: ss+=2;sr.append("Prix sur BB haute")
    elif price>bmn: ss+=1;sr.append("Prix dessus BB mediane")
    if vr>=1.5 and ss>0: ss+=1;sr.append(f"Volume fort {vr:.1f}x")
    sl2,tp2=2.0,3.5
    if ls>=min_score and ls>ss: return "LONG",ls,lr,price,price-sl2*atrn,price+tp2*atrn,atrn
    if ss>=min_score and ss>ls: return "SHORT",ss,sr,price,price+sl2*atrn,price-tp2*atrn,atrn
    return None,0,[],price,0,0,atrn

STABLECOINS={"USDT","USDC","BUSD","DAI","TUSD","FDUSD"}
BAD_SUFFIXES=("UP","DOWN","BULL","BEAR","3L","3S")

def is_eligible(symbol,price,vol24h):
    if price<=0 or price>MAX_PRICE: return False
    if vol24h<MIN_VOLUME: return False
    base=symbol.split("/")[0].split(":")[0]
    if base.upper() in STABLECOINS: return False
    if any(base.upper().endswith(s) for s in BAD_SUFFIXES): return False
    return True

def ts(): return datetime.now().strftime("%H:%M:%S")

def main():
    print(f"[{ts()}] Bot demarre | ntfy: {NTFY_TOPIC}")
    notify("Bot demarre",f"Actif! Cryptos<${MAX_PRICE} | {TIMEFRAME} | {SCAN_INTERVAL//60}min",tags="white_check_mark")
    exchange=ccxt.binance({"options":{"defaultType":"future"}})
    exchange.load_markets()
    print(f"[{ts()}] {len(exchange.markets)} marches charges")
    last_signal={}
    cycle=0
    while True:
        cycle+=1
        print(f"\n[{ts()}] Cycle #{cycle}")
        try:
            tickers=exchange.fetch_tickers()
            eligible=[(s,t["last"] or 0,t.get("quoteVolume") or 0) for s,t in tickers.items() if is_eligible(s,t.get("last") or 0,t.get("quoteVolume") or 0)]
            print(f"[{ts()}] {len(eligible)} paires eligibles")
            sent=0
            for sym,price,vol24h in eligible:
                try:
                    now=time.time()
                    if sym in last_signal and (now-last_signal[sym])<3600: continue
                    raw=exchange.fetch_ohlcv(sym,TIMEFRAME,limit=100)
                    if len(raw)<50: continue
                    df=pd.DataFrame(raw,columns=["ts","open","high","low","close","volume"])
                    df.set_index("ts",inplace=True)
                    result=analyze(sym,df,min_score=MIN_SCORE)
                    signal=result[0]
                    if signal:
                        _,score,reasons,pnow,sl,tp,atr=result
                        base=sym.split("/")[0]
                        sl_pct=abs(pnow-sl)/pnow*100
                        tp_pct=abs(tp-pnow)/pnow*100
                        tag="chart_with_upwards_trend" if signal=="LONG" else "chart_with_downwards_trend"
                        notify(f"{signal} {base} Score {score}/8",f"Prix: ${pnow:.8g}\nSL: ${sl:.8g} (-{sl_pct:.1f}%)\nTP: ${tp:.8g} (+{tp_pct:.1f}%)\nVol: ${vol24h/1e6:.1f}M\n{chr(44).join(reasons[:3])}",priority="high",tags=tag)
                        last_signal[sym]=now
                        sent+=1
                        print(f"[{ts()}] {signal} {sym} @ ${pnow:.8g} score={score}")
                    time.sleep(0.3)
                except Exception: continue
            print(f"[{ts()}] {sent} signal(s).")
        except Exception as e:
            print(f"[{ts()}] Erreur: {e}");traceback.print_exc()
        print(f"[{ts()}] Prochain scan {SCAN_INTERVAL}s...")
        time.sleep(SCAN_INTERVAL)

if __name__=="__main__": main()
