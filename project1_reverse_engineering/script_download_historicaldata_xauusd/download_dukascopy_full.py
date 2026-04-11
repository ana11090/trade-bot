"""
Download ALL available XAUUSD data from Dukascopy
Tick data (every price movement) and resampled timeframes
Period: 2020-01-01 to present
"""

import os
import sys
import requests
import pandas as pd
from datetime import datetime, timedelta
import struct
import lzma

# CHANGED: April 2026 — portable default (Phase 19c)
_BASE = os.environ.get(
    'TICK_ROOT',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'xauusd')
)
OUTPUT_FOLDER = os.path.join(_BASE, 'timeframes')
TICK_FOLDER = os.path.join(_BASE, 'ticks')

def log(msg):
    """Print with timestamp"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    sys.stdout.flush()

def download_dukascopy_ticks(symbol, year, month, day, hour):
    """Download 1 hour of tick data"""
    base_url = "https://datafeed.dukascopy.com/datafeed"
    url = f"{base_url}/{symbol}/{year}/{month:02d}/{day:02d}/{hour:02d}h_ticks.bi5"

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

    except Exception as e:
        return None

def save_tick_data(tick_df, year, month):
    """Save raw tick data to monthly files"""
    if tick_df is None or len(tick_df) == 0:
        return

    # Create folder structure: D:\traiding data\xauusd\ticks\2020\
    year_folder = os.path.join(TICK_FOLDER, str(year))
    os.makedirs(year_folder, exist_ok=True)

    # Save as: ticks_2020_01.csv
    filename = f"ticks_{year}_{month:02d}.csv"
    filepath = os.path.join(year_folder, filename)

    # Append to existing file or create new
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

def download_timeframe(symbol, start_date, end_date, timeframe, freq):
    """Download tick data, save it, and resample to timeframe"""
    log(f"Starting {timeframe} download ({start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')})")

    all_resampled = []
    all_ticks_by_month = {}

    current_date = start_date
    total_days = (end_date - start_date).days
    days_done = 0
    last_progress = 0

    while current_date <= end_date:
        month_key = (current_date.year, current_date.month)

        for hour in range(24):
            tick_df = download_dukascopy_ticks(
                symbol,
                current_date.year,
                current_date.month - 1,
                current_date.day,
                hour
            )

            if tick_df is not None and len(tick_df) > 0:
                # Collect ticks for this month
                if month_key not in all_ticks_by_month:
                    all_ticks_by_month[month_key] = []
                all_ticks_by_month[month_key].append(tick_df)

                # Resample for timeframe
                ohlc = resample_ticks(tick_df, freq)
                if ohlc is not None and len(ohlc) > 0:
                    all_resampled.append(ohlc)

        days_done += 1
        progress = int((days_done / total_days) * 100)

        # Save ticks monthly
        if current_date.day == 1 or current_date == end_date:
            for (year, month), tick_dfs in all_ticks_by_month.items():
                if tick_dfs:
                    monthly_ticks = pd.concat(tick_dfs, ignore_index=True)
                    save_tick_data(monthly_ticks, year, month)
                    size_mb = len(monthly_ticks) * 0.0001  # rough estimate
                    log(f"  Saved {year}-{month:02d} ticks: {len(monthly_ticks):,} ticks (~{size_mb:.1f}MB)")
            all_ticks_by_month.clear()

        if progress >= last_progress + 5:
            log(f"  {timeframe}: {progress}% complete ({current_date.strftime('%Y-%m-%d')})")
            last_progress = progress

        current_date += timedelta(days=1)

    if not all_resampled:
        log(f"  {timeframe}: No data downloaded")
        return None

    combined = pd.concat(all_resampled, ignore_index=True)
    combined = combined.sort_values('timestamp').drop_duplicates(subset=['timestamp'])

    log(f"  {timeframe}: {len(combined):,} candles ({combined['timestamp'].min()} to {combined['timestamp'].max()})")
    return combined

def download_daily_weekly_monthly(symbol, start_date, end_date):
    """Download H1 and create D1, W1, M1"""
    log(f"Downloading H1 data for daily/weekly/monthly resampling...")

    h1_data = download_timeframe(symbol, start_date, end_date, 'H1', '1H')

    if h1_data is None:
        return None, None, None

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

    # Monthly
    m1 = h1_data['close'].resample('M').ohlc()
    m1_vol = h1_data['volume'].resample('M').sum()
    m1_df = pd.DataFrame({
        'timestamp': m1.index,
        'open': m1['open'],
        'high': m1['high'],
        'low': m1['low'],
        'close': m1['close'],
        'volume': m1_vol.values
    }).dropna()

    log(f"  D1: {len(d1_df):,} candles")
    log(f"  W1: {len(w1_df):,} candles")
    log(f"  M1: {len(m1_df):,} candles")

    return d1_df, w1_df, m1_df

def main():
    log("=" * 60)
    log("DUKASCOPY FULL HISTORICAL DATA DOWNLOAD")
    log("=" * 60)
    log("Symbol: XAUUSD")
    log("Period: 2015-01-01 to present (11 years)")
    log(f"Tick data -> {TICK_FOLDER}")
    log(f"Resampled data -> {OUTPUT_FOLDER}")
    log("Timeframes: M5, M15, H1, H4, D1, W1, M1")
    log("This will take 4-10 hours depending on connection")
    log("=" * 60)

    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    os.makedirs(TICK_FOLDER, exist_ok=True)

    symbol = 'XAUUSD'
    start_date = datetime(2015, 1, 1)  # 11 years of historical data
    end_date = datetime.now()

    timeframes = {
        'M5': '5min',
        'M15': '15min',
        'H4': '4H'
    }

    results = {}

    for tf_name, freq in timeframes.items():
        log(f"\n{'='*60}")
        log(f"DOWNLOADING {tf_name}")
        log(f"{'='*60}")

        df = download_timeframe(symbol, start_date, end_date, tf_name, freq)

        if df is not None:
            output_file = os.path.join(OUTPUT_FOLDER, f'xauusd_{tf_name}_dukascopy.csv')
            df.to_csv(output_file, index=False)

            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            log(f"  SAVED: {output_file} ({size_mb:.2f} MB)")
            results[tf_name] = len(df)
        else:
            log(f"  FAILED to download {tf_name}")
            results[tf_name] = 0

    # Download D1, W1, M1
    log(f"\n{'='*60}")
    log(f"DOWNLOADING H1, D1, W1, M1")
    log(f"{'='*60}")

    d1_df, w1_df, m1_df = download_daily_weekly_monthly(symbol, start_date, end_date)

    if d1_df is not None:
        d1_df.to_csv(os.path.join(OUTPUT_FOLDER, 'xauusd_D1_dukascopy.csv'), index=False)
        log(f"  SAVED: xauusd_D1_dukascopy.csv")

    if w1_df is not None:
        w1_df.to_csv(os.path.join(OUTPUT_FOLDER, 'xauusd_W1_dukascopy.csv'), index=False)
        log(f"  SAVED: xauusd_W1_dukascopy.csv")

    if m1_df is not None:
        m1_df.to_csv(os.path.join(OUTPUT_FOLDER, 'xauusd_M1_dukascopy.csv'), index=False)
        log(f"  SAVED: xauusd_M1_dukascopy.csv")

    # Final summary
    log(f"\n{'='*60}")
    log("DOWNLOAD COMPLETE!")
    log(f"{'='*60}")
    log(f"\nResampled data:")
    for tf, count in results.items():
        status = "OK" if count > 0 else "FAILED"
        log(f"  {tf}: {count:,} candles [{status}]")

    log(f"\nTick data saved to: {TICK_FOLDER}")
    log(f"Resampled data saved to: {os.path.abspath(OUTPUT_FOLDER)}")
    log("=" * 60)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log("\n\nCancelled by user")
        sys.exit(0)
    except Exception as e:
        log(f"\n\nERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
