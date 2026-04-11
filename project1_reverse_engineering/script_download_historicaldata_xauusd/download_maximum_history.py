"""
MAXIMUM HISTORY DOWNLOAD
Downloads ALL available XAUUSD data: 2005-2026
Every tick + all timeframes
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
        log(f"SAVED {year}-{month:02d}: {len(month_df):,} ticks, {size_mb:.1f} MB (OK: {ok_count}, Fail: {fail_count})")
        return month_df

    log(f"SKIPPED {year}-{month:02d}: No data")
    return None

def create_timeframes_from_ticks():
    """Create all timeframes from saved tick data"""
    log("="*60)
    log("CREATING TIMEFRAMES FROM TICK DATA")
    log("="*60)

    # Find all tick files
    tick_files = []
    for year_folder in sorted(os.listdir(TICK_FOLDER)):
        year_path = os.path.join(TICK_FOLDER, year_folder)
        if os.path.isdir(year_path):
            for filename in sorted(os.listdir(year_path)):
                if filename.endswith('.csv'):
                    tick_files.append(os.path.join(year_path, filename))

    log(f"Found {len(tick_files)} tick files")

    # Load all ticks
    log("Loading all tick data...")
    all_ticks = []
    for i, filepath in enumerate(tick_files):
        if i % 10 == 0:
            log(f"  Loading file {i+1}/{len(tick_files)}...")
        df = pd.read_csv(filepath)
        all_ticks.append(df)

    ticks_df = pd.concat(all_ticks, ignore_index=True)
    ticks_df['timestamp'] = pd.to_datetime(ticks_df['timestamp'])
    ticks_df = ticks_df.sort_values('timestamp')
    ticks_df.set_index('timestamp', inplace=True)

    log(f"Total ticks loaded: {len(ticks_df):,}")

    # Create timeframes
    timeframes = {
        'M1': '1min',
        'M5': '5min',
        'M15': '15min',
        'H1': '1H',
        'H4': '4H',
        'D1': 'D',
        'W1': 'W',
        'MN': 'M'
    }

    for tf_name, freq in timeframes.items():
        log(f"Creating {tf_name}...")

        ohlc = ticks_df['mid'].resample(freq).ohlc()
        volume = ticks_df['volume'].resample(freq).sum()

        tf_df = pd.DataFrame({
            'timestamp': ohlc.index,
            'open': ohlc['open'],
            'high': ohlc['high'],
            'low': ohlc['low'],
            'close': ohlc['close'],
            'volume': volume.values
        }).dropna()

        output_file = os.path.join(TIMEFRAME_FOLDER, f'xauusd_{tf_name}.csv')
        tf_df.to_csv(output_file, index=False)

        size_mb = os.path.getsize(output_file) / (1024 * 1024)
        log(f"  SAVED: xauusd_{tf_name}.csv ({len(tf_df):,} candles, {size_mb:.1f} MB)")

def main():
    log("="*60)
    log("MAXIMUM HISTORY DOWNLOAD - XAUUSD")
    log("="*60)
    log("Period: 2005-2026 (21 years)")
    log("Tick data -> Every price movement")
    log("Timeframes -> M1, M5, M15, H1, H4, D1, W1, MN")
    log(f"Output -> {BASE_FOLDER}")
    log("Estimated: 20-30 GB, 8-12 hours")
    log("="*60)

    symbol = 'XAUUSD'
    start_year = 2005
    end_date = datetime.now()

    # Download all months from 2005 to now
    current = datetime(start_year, 1, 1)
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
            log(f"*** COMPLETED {total_months} MONTHS ({year}) ***")

    log("="*60)
    log("TICK DOWNLOAD COMPLETE!")
    log("="*60)

    # Create timeframes
    create_timeframes_from_ticks()

    log("="*60)
    log("ALL DOWNLOADS COMPLETE!")
    log(f"Data location: {BASE_FOLDER}")
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
