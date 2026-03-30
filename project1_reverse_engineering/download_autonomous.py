"""
AUTONOMOUS DOWNLOAD - Complete entire dataset with auto-retry
Will run until all data is downloaded (2005-2026)
"""

import os
import sys
import requests
import pandas as pd
from datetime import datetime, timedelta
import struct
import lzma
import time

BASE_FOLDER = r'D:\traiding data\xauusd'
TICK_FOLDER = os.path.join(BASE_FOLDER, 'ticks')
TIMEFRAME_FOLDER = os.path.join(BASE_FOLDER, 'timeframes')

os.makedirs(TICK_FOLDER, exist_ok=True)
os.makedirs(TIMEFRAME_FOLDER, exist_ok=True)

def log(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {msg}", flush=True)

def download_hour(symbol, year, month, day, hour, retry=5):
    """Download 1 hour with aggressive retry"""
    url = f"https://datafeed.dukascopy.com/datafeed/{symbol}/{year}/{month:02d}/{day:02d}/{hour:02d}h_ticks.bi5"

    for attempt in range(retry):
        try:
            response = requests.get(url, timeout=60)
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
                log(f"    Timeout, retry {attempt+1}/{retry}")
                time.sleep(5)
                continue
        except requests.exceptions.ConnectionError:
            if attempt < retry - 1:
                log(f"    Connection error, retry {attempt+1}/{retry}")
                time.sleep(10)
                continue
        except:
            return None

    return None

def month_exists(year, month):
    """Check if month file already exists"""
    filepath = os.path.join(TICK_FOLDER, str(year), f"ticks_{year}_{month:02d}.csv")
    return os.path.exists(filepath)

def download_month(symbol, year, month):
    """Download complete month"""

    # Skip if already exists
    if month_exists(year, month):
        log(f"SKIP {year}-{month:02d}: Already exists")
        return True

    log(f"Downloading {year}-{month:02d}...")

    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)

    days_in_month = (next_month - datetime(year, month, 1)).days

    all_ticks = []
    ok_count = 0
    fail_count = 0
    consecutive_failures = 0

    for day in range(1, days_in_month + 1):
        day_ticks = []

        for hour in range(24):
            tick_df = download_hour(symbol, year, month - 1, day, hour)

            if tick_df is not None and len(tick_df) > 0:
                day_ticks.append(tick_df)
                ok_count += 1
                consecutive_failures = 0
            else:
                fail_count += 1
                consecutive_failures += 1

            # If too many consecutive failures, might be rate limited
            if consecutive_failures > 50:
                log(f"  WARNING: 50+ consecutive failures, possible rate limit")
                log(f"  Waiting 5 minutes...")
                time.sleep(300)
                consecutive_failures = 0

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

        # Small delay between months to avoid rate limiting
        time.sleep(2)
        return True
    else:
        log(f"FAILED {year}-{month:02d}: No data downloaded")
        return False

def create_timeframes():
    """Create all timeframes from tick data"""
    log("="*70)
    log("CREATING TIMEFRAMES FROM TICK DATA")
    log("="*70)

    # Find all tick files
    tick_files = []
    for year_folder in sorted(os.listdir(TICK_FOLDER)):
        year_path = os.path.join(TICK_FOLDER, year_folder)
        if os.path.isdir(year_path):
            for filename in sorted(os.listdir(year_path)):
                if filename.endswith('.csv') and filename.startswith('ticks_'):
                    tick_files.append(os.path.join(year_path, filename))

    log(f"Found {len(tick_files)} tick files")

    if len(tick_files) == 0:
        log("No tick files found!")
        return

    # Load all ticks
    log("Loading all tick data...")
    all_ticks = []
    for i, filepath in enumerate(tick_files):
        if i % 10 == 0:
            log(f"  Loading file {i+1}/{len(tick_files)}...")
        try:
            df = pd.read_csv(filepath)
            all_ticks.append(df)
        except Exception as e:
            log(f"  Error loading {filepath}: {e}")

    if not all_ticks:
        log("No tick data loaded!")
        return

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
    log("="*70)
    log("AUTONOMOUS DOWNLOAD - XAUUSD MAXIMUM HISTORY")
    log("="*70)
    log("Period: 2005-2026 (21 years)")
    log("Mode: Autonomous - will retry until complete")
    log("="*70)

    # Wait 30 minutes for rate limit to reset
    log("Waiting 30 minutes for rate limit to reset...")
    time.sleep(1800)

    log("Starting download...")

    symbol = 'XAUUSD'
    start_year = 2005
    end_date = datetime.now()

    current = datetime(start_year, 1, 1)
    total_months = 0
    failed_months = []

    # Download all months
    while current <= end_date:
        year = current.year
        month = current.month

        success = download_month(symbol, year, month)

        if not success and not month_exists(year, month):
            failed_months.append(f"{year}-{month:02d}")

        total_months += 1

        # Move to next month
        if month == 12:
            current = datetime(year + 1, 1, 1)
        else:
            current = datetime(year, month + 1, 1)

        # Progress update every 12 months
        if total_months % 12 == 0:
            log(f"*** COMPLETED {total_months} MONTHS ({year}) ***")

    log("="*70)
    log("TICK DOWNLOAD PHASE COMPLETE!")
    log("="*70)

    if failed_months:
        log(f"Failed months: {', '.join(failed_months)}")
        log("Retrying failed months...")

        # Retry failed months
        for month_str in failed_months:
            year, month = map(int, month_str.split('-'))
            log(f"Retrying {month_str}...")
            download_month(symbol, year, month)

    # Create timeframes
    log("\nCreating timeframes...")
    create_timeframes()

    log("="*70)
    log("ALL DOWNLOADS COMPLETE!")
    log(f"Data location: {BASE_FOLDER}")
    log("="*70)

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
