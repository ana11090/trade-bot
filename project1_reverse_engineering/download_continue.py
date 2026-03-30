"""Continue from 2013-06 onwards"""
import os, sys, requests, pandas as pd
from datetime import datetime, timedelta
import struct, lzma, time

BASE = r'D:\traiding data\xauusd'
TICK = os.path.join(BASE, 'ticks')
os.makedirs(TICK, exist_ok=True)

def log(m):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)

def dl_hour(sym, y, m, d, h, retry=5):
    url = f"https://datafeed.dukascopy.com/datafeed/{sym}/{y}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5"
    for a in range(retry):
        try:
            r = requests.get(url, timeout=60)
            if r.status_code != 200: return None
            data = lzma.decompress(r.content)
            ticks = []
            for i in range(0, len(data), 20):
                if i + 20 > len(data): break
                chunk = data[i:i+20]
                ts_ms, ask, bid, av, bv = struct.unpack('>IIIff', chunk)
                bt = datetime(y, m + 1, d, h)
                tt = bt + timedelta(milliseconds=ts_ms)
                ticks.append({'timestamp': tt, 'ask': ask/100000.0, 'bid': bid/100000.0,
                             'mid': (ask+bid)/200000.0, 'spread': (ask-bid)/100000.0, 'volume': av+bv})
            return pd.DataFrame(ticks) if ticks else None
        except:
            if a < retry-1: time.sleep(3); continue
            return None
    return None

def exists(y, m):
    return os.path.exists(os.path.join(TICK, str(y), f"ticks_{y}_{m:02d}.csv"))

def dl_month(sym, y, m):
    if exists(y, m):
        log(f"SKIP {y}-{m:02d}")
        return True
    log(f"Downloading {y}-{m:02d}...")
    nm = datetime(y+1,1,1) if m==12 else datetime(y,m+1,1)
    days = (nm - datetime(y,m,1)).days
    all_t, ok, fail, cf = [], 0, 0, 0
    for d in range(1, days+1):
        dt = []
        for h in range(24):
            t = dl_hour(sym, y, m-1, d, h)
            if t is not None and len(t)>0: dt.append(t); ok+=1; cf=0
            else: fail+=1; cf+=1
            if cf>50: log("  Rate limit, wait 5min"); time.sleep(300); cf=0
        if dt: all_t.extend(dt)
        if d%5==0 or d==days: log(f"  Day {d}/{days} (OK:{ok}, Fail:{fail})")
    if all_t:
        df = pd.concat(all_t, ignore_index=True)
        yf = os.path.join(TICK, str(y))
        os.makedirs(yf, exist_ok=True)
        fp = os.path.join(yf, f"ticks_{y}_{m:02d}.csv")
        df.to_csv(fp, index=False)
        sz = os.path.getsize(fp)/(1024*1024)
        log(f"SAVED {y}-{m:02d}: {len(df):,} ticks, {sz:.1f}MB")
        time.sleep(2)
        return True
    log(f"FAILED {y}-{m:02d}")
    return False

log("="*60)
log("CONTINUING FROM 2013-06")
log("="*60)
cur = datetime(2013, 6, 1)
end = datetime.now()
cnt = 0
while cur <= end:
    dl_month('XAUUSD', cur.year, cur.month)
    cnt += 1
    if cur.month == 12: cur = datetime(cur.year+1, 1, 1)
    else: cur = datetime(cur.year, cur.month+1, 1)
    if cnt % 12 == 0: log(f"*** {cnt} MONTHS ({cur.year}) ***")
log("="*60)
log("COMPLETE!")
log("="*60)
