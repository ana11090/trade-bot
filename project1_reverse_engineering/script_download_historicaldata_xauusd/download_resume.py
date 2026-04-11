"""
RESUME DOWNLOAD - Continue from October 2007
"""

import os
import sys
import requests
import pandas as pd
from datetime import datetime, timedelta
import struct
import lzma
import time

# CHANGED: April 2026 — portable default (Phase 19c)
BASE_FOLDER = os.environ.get(
    'TICK_ROOT',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'xauusd')
)
TICK_FOLDER = os.path.join(BASE_FOLDER, 'ticks')
TIMEFRAME_FOLDER = os.path.join(BASE_FOLDER, 'timeframes')

os.makedirs(TICK_FOLDER, exist_ok=True)
os.makedirs(TIMEFRAME_FOLDER, exist_ok=True)

def log(msg):
    timestamp = datetime.now().strftime('%H:%M:%S')
    print(f"[{timestamp}] {msg}", flush=True)

def download_hour(symbol, year, month, day, hour, retry=3):
    """Download 1 hour with retry"""
    url = f"https://datafeed.dukascopy.com/datafeed/{symbol}/{year}/{month:02d}/{day:02d}/{hour:02d}h_ticks.bi5"

    for attempt in range(retry):
        try:
            response = requests.get(url, timeout=45)
            if response.status_code != 200:
                return None

            data = lzma.decompress(response.content)
            ticks = []

            for i in range(0, len(data), 20):
                if i + 20 > len(data):
                    break

                chunk = data[i:i+20]
                timestamp_ms, ask, bid, ask_vol, bid_vol = struct.unpack('>IIIff', chunk)

                base_time = datetime(year, month + 1, day, hour)
                tick_time = base_time + timedelta(milliseconds=timestamp_ms)

                ticks.append({
                    'timestamp': tick_time,
                    'ask': ask / 100000.0,
                    'bid': bid / 100000.0,
                    'mid': (ask + bid) / 200000.0,
                    'spread': (ask - bid) / 100000.0,
                    'volume': ask_vol + bid_vol
                })

            return pd.DataFrame(ticks) if ticks else None

        except requests.exceptions.Timeout:
            if attempt < retry - 1:
                time.sleep(2)
                continue
        except:
            return None

    return None

def download_month(symbol, year, month):
    """Download complete month"""
    log(f"Downloading {year}-{month:02d}...")

    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)

    days_in_month = (next_month - datetime(year, month, 1)).days

    all_ticks = []
    ok_count = 0
    fail_count = 0

    for day in range(1, days_in_month + 1):
        day_ticks = []

        for hour in range(24):
            tick_df = download_hour(symbol, year, month - 1, day, hour)

            if tick_df is not None and len(tick_df) > 0:
                day_ticks.append(tick_df)
                ok_count += 1
            else:
                fail_count += 1

        if day_ticks:
            all_ticks.extend(day_ticks)

        # Progress every 5 days
        if day % 5 == 0 or day == days_in_month:
            log(f"  Day {day}/{days_in_month} (OK: {ok_count}, Fail: {fail_count})")

    if all_ticks:
        month_df = pd.concat(all_ticks, ignore_index=True)

        # Save tick data
        year_folder = os.path.join(TICK_FOLDER, str(year))
        os.makedirs(year_folder, exist_ok=True)

        filepath = os.path.join(year_folder, f"ticks_{year}_{month:02d}.csv")
        month_df.to_csv(filepath, index=False)

        size_mb = os.path.getsize(filepath) / (1024 * 1024)
        log(f"SAVED {year}-{month:02d}: {len(month_df):,} ticks, {size_mb:.1f} MB")
        return month_df

    log(f"SKIPPED {year}-{month:02d}: No data")
    return None

def main():
    log("="*60)
    log("RESUMING DOWNLOAD FROM OCTOBER 2007")
    log("="*60)
    log("Continuing: 2007-10 to 2026-03")
    log("="*60)

    symbol = 'XAUUSD'

    # Resume from October 2007
    start_year = 2007
    start_month = 10
    end_date = datetime.now()

    current = datetime(start_year, start_month, 1)
    total_months = 0

    while current <= end_date:
        year = current.year
        month = current.month

        download_month(symbol, year, month)
        total_months += 1

        # Move to next month
        if month == 12:
            current = datetime(year + 1, 1, 1)
        else:
            current = datetime(year, month + 1, 1)

        # Progress update every 12 months
        if total_months % 12 == 0:
            log(f"*** COMPLETED {total_months} MORE MONTHS ({year}) ***")

    log("="*60)
    log("DOWNLOAD COMPLETE!")
    log("="*60)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log("\nCancelled by user")
        sys.exit(0)
    except Exception as e:
        log(f"\nERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
