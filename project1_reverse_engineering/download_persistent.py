"""Persistent download - will retry with long waits until complete"""
import os, sys, requests, pandas as pd
from datetime import datetime, timedelta
import struct, lzma, time

BASE = r'D:\traiding data\xauusd'
TICK = os.path.join(BASE, 'ticks')
os.makedirs(TICK, exist_ok=True)

def log(m):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {m}", flush=True)

def dl_hour(sym, y, m, d, h):
    url = f"https://datafeed.dukascopy.com/datafeed/{sym}/{y}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5"
    try:
        r = requests.get(url, timeout=60)
        if r.status_code != 200: return None
        data = lzma.decompress(r.content)
        ticks = []
        for i in range(0, len(data), 20):
            if i + 20 > len(data): break
            ts_ms, ask, bid, av, bv = struct.unpack('>IIIff', data[i:i+20])
            bt = datetime(y, m + 1, d, h)
            ticks.append({'timestamp': bt + timedelta(milliseconds=ts_ms),
                         'ask': ask/100000.0, 'bid': bid/100000.0,
                         'mid': (ask+bid)/200000.0, 'spread': (ask-bid)/100000.0,
                         'volume': av+bv})
        return pd.DataFrame(ticks) if ticks else None
    except: return None

def exists(y, m):
    return os.path.exists(os.path.join(TICK, str(y), f"ticks_{y}_{m:02d}.csv"))

def dl_month(sym, y, m):
    if exists(y, m): return True
    log(f"Download {y}-{m:02d}")
    nm = datetime(y+1,1,1) if m==12 else datetime(y,m+1,1)
    days, all_t, ok, fail = (nm - datetime(y,m,1)).days, [], 0, 0
    for d in range(1, days+1):
        dt = []
        for h in range(24):
            t = dl_hour(sym, y, m-1, d, h)
            if t is not None and len(t)>0: dt.append(t); ok+=1
            else: fail+=1
        if dt: all_t.extend(dt)
        if d%10==0: log(f"  Day {d}/{days} OK:{ok} Fail:{fail}")
    if all_t:
        df = pd.concat(all_t, ignore_index=True)
        yf = os.path.join(TICK, str(y))
        os.makedirs(yf, exist_ok=True)
        df.to_csv(os.path.join(yf, f"ticks_{y}_{m:02d}.csv"), index=False)
        sz = os.path.getsize(os.path.join(yf, f"ticks_{y}_{m:02d}.csv"))/(1024*1024)
        log(f"SAVED {y}-{m:02d}: {len(df):,} ticks {sz:.1f}MB")
        return True
    return False

def main():
    log("="*60)
    log("PERSISTENT DOWNLOAD - Will retry until complete")
    log("Waiting 2 hours for rate limit reset...")
    log("="*60)
    time.sleep(7200)  # Wait 2 hours

    log("Starting download...")
    cur = datetime(2013, 6, 1)
    end = datetime.now()
    failed_run = False

    while cur <= end:
        success = dl_month('XAUUSD', cur.year, cur.month)
        if not success and not exists(cur.year, cur.month):
            log(f"Failed {cur.year}-{cur.month:02d}, will retry later")
            failed_run = True
            break  # Stop this run, will retry

        if cur.month == 12: cur = datetime(cur.year+1, 1, 1)
        else: cur = datetime(cur.year, cur.month+1, 1)
        time.sleep(1)  # Small delay between months

    if failed_run or cur <= end:
        log("Session ended, waiting 4 hours then retrying...")
        time.sleep(14400)  # Wait 4 hours
        log("Retrying...")
        main()  # Recursive retry
    else:
        log("="*60)
        log("ALL DOWNLOADS COMPLETE!")
        log("="*60)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log("Cancelled")
        sys.exit(0)
    except Exception as e:
        log(f"ERROR: {e}")
        log("Waiting 1 hour then retrying...")
        time.sleep(3600)
        main()  # Retry on error
