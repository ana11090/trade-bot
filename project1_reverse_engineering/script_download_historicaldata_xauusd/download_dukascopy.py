"""
Download historical XAUUSD data from Dukascopy (free, 2003-present)
Dukascopy is a Swiss bank that provides free historical tick data
"""

import os
import sys
import requests
import pandas as pd
from datetime import datetime, timedelta
import struct
import lzma

OUTPUT_FOLDER = '../data/'

def download_dukascopy_ticks(symbol, year, month, day, hour):
    """
    Download 1 hour of tick data from Dukascopy
    Returns DataFrame or None
    """
    # Dukascopy URL format
    base_url = "https://datafeed.dukascopy.com/datafeed"

    # Symbol mapping (Dukascopy uses specific format)
    symbol_map = {
        'XAUUSD': 'XAUUSD'
    }

    duka_symbol = symbol_map.get(symbol, symbol)

    # Construct URL: /SYMBOL/YEAR/MONTH-1/DAY/HOUR_ticks.bi5
    url = f"{base_url}/{duka_symbol}/{year}/{month:02d}/{day:02d}/{hour:02d}h_ticks.bi5"

    try:
        response = requests.get(url, timeout=10)

        if response.status_code != 200:
            return None

        # Decompress LZMA
        data = lzma.decompress(response.content)

        # Parse binary data (20 bytes per tick)
        # Format: timestamp(4), ask(4), bid(4), ask_volume(4), bid_volume(4)
        ticks = []
        chunk_size = 20

        for i in range(0, len(data), chunk_size):
            if i + chunk_size > len(data):
                break

            chunk = data[i:i+chunk_size]

            # Unpack binary data
            timestamp_ms, ask, bid, ask_vol, bid_vol = struct.unpack('>IIIff', chunk)

            # Convert timestamp (milliseconds since hour start)
            base_time = datetime(year, month + 1, day, hour)
            tick_time = base_time + timedelta(milliseconds=timestamp_ms)

            # Convert price (stored as integer, divide by 100000 for 5-digit precision)
            ask_price = ask / 100000.0
            bid_price = bid / 100000.0

            ticks.append({
                'timestamp': tick_time,
                'ask': ask_price,
                'bid': bid_price,
                'ask_volume': ask_vol,
                'bid_volume': bid_vol
            })

        if ticks:
            return pd.DataFrame(ticks)
        else:
            return None

    except Exception as e:
        return None


def resample_to_timeframe(tick_df, timeframe='5min'):
    """
    Resample tick data to M5 or M15
    """
    if tick_df is None or len(tick_df) == 0:
        return None

    # Use mid price (average of bid/ask)
    tick_df['price'] = (tick_df['ask'] + tick_df['bid']) / 2
    tick_df['volume'] = tick_df['ask_volume'] + tick_df['bid_volume']

    # Set timestamp as index
    tick_df.set_index('timestamp', inplace=True)

    # Resample to desired timeframe
    ohlc = tick_df['price'].resample(timeframe).ohlc()
    volume = tick_df['volume'].resample(timeframe).sum()

    result = pd.DataFrame({
        'timestamp': ohlc.index,
        'open': ohlc['open'],
        'high': ohlc['high'],
        'low': ohlc['low'],
        'close': ohlc['close'],
        'volume': volume.values
    })

    # Remove NaN rows
    result = result.dropna()

    return result


def download_dukascopy_range(symbol, start_date, end_date, timeframe='M5'):
    """
    Download data for a date range and resample to timeframe
    """
    print(f"\nDownloading {symbol} {timeframe} from Dukascopy...")
    print(f"Range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    print("This may take several minutes...\n")

    tf_map = {
        'M5': '5min',
        'M15': '15min'
    }

    resample_tf = tf_map.get(timeframe, '5min')

    all_data = []
    current_date = start_date
    hours_downloaded = 0
    hours_failed = 0

    while current_date <= end_date:
        # Download each hour
        for hour in range(24):
            tick_df = download_dukascopy_ticks(
                symbol,
                current_date.year,
                current_date.month - 1,  # Dukascopy uses 0-indexed months
                current_date.day,
                hour
            )

            if tick_df is not None:
                # Resample to desired timeframe
                ohlc_df = resample_to_timeframe(tick_df, resample_tf)
                if ohlc_df is not None and len(ohlc_df) > 0:
                    all_data.append(ohlc_df)
                    hours_downloaded += 1
            else:
                hours_failed += 1

            # Progress indicator
            if (hours_downloaded + hours_failed) % 24 == 0:
                print(f"  Progress: {current_date.strftime('%Y-%m-%d')} - Downloaded: {hours_downloaded}h, Failed: {hours_failed}h")

        current_date += timedelta(days=1)

    if not all_data:
        print("\n[ERROR] No data downloaded")
        return None

    # Combine all data
    print(f"\nCombining data...")
    combined_df = pd.concat(all_data, ignore_index=True)
    combined_df = combined_df.sort_values('timestamp')
    combined_df = combined_df.drop_duplicates(subset=['timestamp'])

    print(f"[OK] Downloaded {len(combined_df):,} candles")
    print(f"     Range: {combined_df['timestamp'].min()} to {combined_df['timestamp'].max()}")

    return combined_df


def main():
    print("\n" + "=" * 60)
    print("  DUKASCOPY HISTORICAL DATA DOWNLOAD")
    print("=" * 60)
    print("\nFree historical data from Swiss bank Dukascopy")
    print("Data available from 2003 onwards\n")

    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    # Download M5 data for past 5 years
    start_date = datetime(2020, 1, 1)
    end_date = datetime(2024, 12, 31)

    print("WARNING: Downloading 5 years of tick data takes 1-2 hours!")
    print("We'll download 1 year as a test first.\n")

    # Download 1 year as test
    start_date = datetime(2023, 1, 1)
    end_date = datetime(2023, 12, 31)

    df_m5 = download_dukascopy_range('XAUUSD', start_date, end_date, 'M5')

    if df_m5 is not None:
        output_file = os.path.join(OUTPUT_FOLDER, 'xauusd_M5_dukascopy.csv')
        df_m5.to_csv(output_file, index=False)

        file_size_mb = os.path.getsize(output_file) / (1024 * 1024)

        print(f"\n{'='*60}")
        print(f"  SUCCESS!")
        print(f"{'='*60}")
        print(f"\nSaved: {output_file}")
        print(f"Total candles: {len(df_m5):,}")
        print(f"File size: {file_size_mb:.2f} MB")
        print(f"Range: {df_m5['timestamp'].min()} to {df_m5['timestamp'].max()}")
        print(f"{'='*60}\n")
    else:
        print("\n[FAILED] Could not download from Dukascopy")
        print("\nThis might be because:")
        print("  1. Dukascopy changed their API")
        print("  2. XAUUSD not available on their free tier")
        print("  3. Network/firewall issues")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCancelled by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n\nERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
