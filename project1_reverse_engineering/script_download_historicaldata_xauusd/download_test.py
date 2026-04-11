"""Test downloader - downloads 2 months with detailed logging"""

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

os.makedirs(TICK_FOLDER, exist_ok=True)

def log(msg):
    timestamp = datetime.now().strftime('%H:%M:%S')
    print(f"[{timestamp}] {msg}", flush=True)

def download_hour(symbol, year, month, day, hour):
    """Download 1 hour with aggressive timeout"""
    url = f"https://datafeed.dukascopy.com/datafeed/{symbol}/{year}/{month:02d}/{day:02d}/{hour:02d}h_ticks.bi5"

    try:
        response = requests.get(url, timeout=30)
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

    except Exception as e:
        log(f"  ERROR: {year}-{month+1:02d}-{day:02d} {hour:02d}h - {type(e).__name__}")
        return None

def download_month(symbol, year, month):
    """Download one complete month"""
    log(f"Starting {year}-{month:02d}")

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

            # Progress every 6 hours
            if hour % 6 == 0:
                log(f"  Day {day}/{days_in_month}, Hour {hour}/24 (OK: {ok_count}, Fail: {fail_count})")

        if day_ticks:
            all_ticks.extend(day_ticks)

    if all_ticks:
        month_df = pd.concat(all_ticks, ignore_index=True)

        # Save
        year_folder = os.path.join(TICK_FOLDER, str(year))
        os.makedirs(year_folder, exist_ok=True)

        filepath = os.path.join(year_folder, f"ticks_{year}_{month:02d}.csv")
        month_df.to_csv(filepath, index=False)

        log(f"SAVED {year}-{month:02d}: {len(month_df):,} ticks ({ok_count} hours OK, {fail_count} failed)")
        return month_df

    log(f"FAILED {year}-{month:02d}: No data downloaded")
    return None

def main():
    log("="*60)
    log("TEST DOWNLOAD - 2 months only")
    log("="*60)

    # Download just 2 months
    download_month('XAUUSD', 2024, 1)  # Jan 2024
    download_month('XAUUSD', 2024, 2)  # Feb 2024

    log("="*60)
    log("TEST COMPLETE")
    log("="*60)

if __name__ == '__main__':
    main()
