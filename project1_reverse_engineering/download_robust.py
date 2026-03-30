"""
Robust XAUUSD data downloader with retry logic and error handling
Downloads from 2020 to present (proven data availability)
"""

import os
import sys
import requests
import pandas as pd
from datetime import datetime, timedelta
import struct
import lzma
import time

# Output paths
BASE_FOLDER = r'D:\traiding data\xauusd'
TICK_FOLDER = os.path.join(BASE_FOLDER, 'ticks')
TIMEFRAME_FOLDER = os.path.join(BASE_FOLDER, 'timeframes')

# Create folders
os.makedirs(TICK_FOLDER, exist_ok=True)
os.makedirs(TIMEFRAME_FOLDER, exist_ok=True)

def log(msg):
    """Print with timestamp"""
    timestamp = datetime.now().strftime('%H:%M:%S')
    print(f"[{timestamp}] {msg}")
    sys.stdout.flush()

def download_dukascopy_ticks(symbol, year, month, day, hour, max_retries=3):
    """Download 1 hour of tick data with retry logic"""
    base_url = "https://datafeed.dukascopy.com/datafeed"
    url = f"{base_url}/{symbol}/{year}/{month:02d}/{day:02d}/{hour:02d}h_ticks.bi5"

    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=60)  # Increased timeout
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

                ask_price = ask / 100000.0
                bid_price = bid / 100000.0
                mid_price = (ask_price + bid_price) / 2

                ticks.append({
                    'timestamp': tick_time,
                    'ask': ask_price,
                    'bid': bid_price,
                    'mid': mid_price,
                    'spread': ask_price - bid_price,
                    'volume': ask_vol + bid_vol
                })

            if ticks:
                return pd.DataFrame(ticks)
            return None

        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                log(f"  Timeout on {year}-{month+1:02d}-{day:02d} {hour:02d}h, retry {attempt+1}/{max_retries}")
                time.sleep(2)  # Wait before retry
                continue
            else:
                return None
        except Exception as e:
            return None

    return None

def save_tick_data(tick_df, year, month):
    """Save raw tick data to monthly files"""
    if tick_df is None or len(tick_df) == 0:
        return

    year_folder = os.path.join(TICK_FOLDER, str(year))
    os.makedirs(year_folder, exist_ok=True)

    filename = f"ticks_{year}_{month:02d}.csv"
    filepath = os.path.join(year_folder, filename)

    if os.path.exists(filepath):
        existing = pd.read_csv(filepath)
        combined = pd.concat([existing, tick_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=['timestamp'])
        combined = combined.sort_values('timestamp')
        combined.to_csv(filepath, index=False)
    else:
        tick_df.to_csv(filepath, index=False)

def resample_ticks(tick_df, freq):
    """Resample tick data to OHLCV"""
    if tick_df is None or len(tick_df) == 0:
        return None

    df = tick_df.copy()
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)

    ohlc = df['mid'].resample(freq).ohlc()
    volume = df['volume'].resample(freq).sum()

    result = pd.DataFrame({
        'timestamp': ohlc.index,
        'open': ohlc['open'],
        'high': ohlc['high'],
        'low': ohlc['low'],
        'close': ohlc['close'],
        'volume': volume.values
    })

    return result.dropna()

def download_year_month(symbol, year, month, freq, timeframe_name):
    """Download one month of data"""
    log(f"Downloading {year}-{month:02d} ({timeframe_name})...")

    month_ticks = []
    month_resampled = []

    # Get number of days in month
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)

    current_month = datetime(year, month, 1)
    days_in_month = (next_month - current_month).days

    successful_hours = 0
    failed_hours = 0

    for day in range(1, days_in_month + 1):
        for hour in range(24):
            tick_df = download_dukascopy_ticks(symbol, year, month - 1, day, hour)

            if tick_df is not None and len(tick_df) > 0:
                month_ticks.append(tick_df)
                successful_hours += 1

                # Resample
                ohlc = resample_ticks(tick_df, freq)
                if ohlc is not None and len(ohlc) > 0:
                    month_resampled.append(ohlc)
            else:
                failed_hours += 1

    # Save ticks for this month
    if month_ticks:
        monthly_tick_df = pd.concat(month_ticks, ignore_index=True)
        save_tick_data(monthly_tick_df, year, month)
        log(f"  Saved {year}-{month:02d} ticks: {len(monthly_tick_df):,} ticks ({successful_hours} hours OK, {failed_hours} failed)")
    else:
        log(f"  No data for {year}-{month:02d}")
        return None

    # Combine resampled data
    if month_resampled:
        return pd.concat(month_resampled, ignore_index=True)
    return None

def download_timeframe(symbol, start_year, start_month, end_date, timeframe, freq):
    """Download tick data month by month and resample"""
    log(f"=" * 60)
    log(f"DOWNLOADING {timeframe}")
    log(f"=" * 60)

    all_data = []

    current = datetime(start_year, start_month, 1)

    while current <= end_date:
        year = current.year
        month = current.month

        month_data = download_year_month(symbol, year, month, freq, timeframe)

        if month_data is not None:
            all_data.append(month_data)

        # Move to next month
        if month == 12:
            current = datetime(year + 1, 1, 1)
        else:
            current = datetime(year, month + 1, 1)

    if all_data:
        combined = pd.concat(all_data, ignore_index=True)
        combined = combined.sort_values('timestamp').drop_duplicates(subset=['timestamp'])

        output_file = os.path.join(TIMEFRAME_FOLDER, f'xauusd_{timeframe}.csv')
        combined.to_csv(output_file, index=False)

        size_mb = os.path.getsize(output_file) / (1024 * 1024)
        log(f"SAVED: {output_file} ({size_mb:.2f} MB, {len(combined):,} candles)")
        return combined

    return None

def main():
    log("=" * 60)
    log("ROBUST XAUUSD DATA DOWNLOAD")
    log("=" * 60)
    log("Symbol: XAUUSD")
    log("Period: 2020-01-01 to present")
    log(f"Tick data -> {TICK_FOLDER}")
    log(f"Timeframes -> {TIMEFRAME_FOLDER}")
    log("Features: Retry logic, timeout handling, monthly saves")
    log("=" * 60)

    symbol = 'XAUUSD'
    start_year = 2020
    start_month = 1
    end_date = datetime.now()

    timeframes = {
        'M5': '5min',
        'M15': '15min',
        'H1': '1H',
        'H4': '4H',
    }

    results = {}

    for tf_name, freq in timeframes.items():
        df = download_timeframe(symbol, start_year, start_month, end_date, tf_name, freq)
        results[tf_name] = len(df) if df is not None else 0

    # Create D1, W1, MN from H1
    log("\n" + "=" * 60)
    log("CREATING D1, W1, MN from H1 data")
    log("=" * 60)

    h1_file = os.path.join(TIMEFRAME_FOLDER, 'xauusd_H1.csv')
    if os.path.exists(h1_file):
        h1_data = pd.read_csv(h1_file)
        h1_data['timestamp'] = pd.to_datetime(h1_data['timestamp'])
        h1_data.set_index('timestamp', inplace=True)

        # Daily
        d1 = h1_data['close'].resample('D').ohlc()
        d1_vol = h1_data['volume'].resample('D').sum()
        d1_df = pd.DataFrame({
            'timestamp': d1.index,
            'open': d1['open'],
            'high': d1['high'],
            'low': d1['low'],
            'close': d1['close'],
            'volume': d1_vol.values
        }).dropna()
        d1_df.to_csv(os.path.join(TIMEFRAME_FOLDER, 'xauusd_D1.csv'), index=False)
        log(f"SAVED: xauusd_D1.csv ({len(d1_df):,} candles)")

        # Weekly
        w1 = h1_data['close'].resample('W').ohlc()
        w1_vol = h1_data['volume'].resample('W').sum()
        w1_df = pd.DataFrame({
            'timestamp': w1.index,
            'open': w1['open'],
            'high': w1['high'],
            'low': w1['low'],
            'close': w1['close'],
            'volume': w1_vol.values
        }).dropna()
        w1_df.to_csv(os.path.join(TIMEFRAME_FOLDER, 'xauusd_W1.csv'), index=False)
        log(f"SAVED: xauusd_W1.csv ({len(w1_df):,} candles)")

        # Monthly
        mn = h1_data['close'].resample('M').ohlc()
        mn_vol = h1_data['volume'].resample('M').sum()
        mn_df = pd.DataFrame({
            'timestamp': mn.index,
            'open': mn['open'],
            'high': mn['high'],
            'low': mn['low'],
            'close': mn['close'],
            'volume': mn_vol.values
        }).dropna()
        mn_df.to_csv(os.path.join(TIMEFRAME_FOLDER, 'xauusd_MN.csv'), index=False)
        log(f"SAVED: xauusd_MN.csv ({len(mn_df):,} candles)")

    log("\n" + "=" * 60)
    log("DOWNLOAD COMPLETE!")
    log("=" * 60)
    for tf, count in results.items():
        status = "[OK]" if count > 0 else "[FAILED]"
        log(f"{tf}: {count:,} candles {status}")
    log("=" * 60)

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
