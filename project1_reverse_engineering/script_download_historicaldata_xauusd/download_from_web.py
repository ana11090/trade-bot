"""
Download XAUUSD Historical Data from Web Sources
Tries multiple sources to get M5/M15 data from 2020 onwards
"""

import os
import sys
import pandas as pd
import requests
from datetime import datetime, timedelta
import time

OUTPUT_FOLDER = '../data/'

# ============================================================
# SOURCE 1: Dukascopy (Swiss Bank - Free Historical Data)
# ============================================================

def download_dukascopy(timeframe='M5', start_date='2020-01-01', end_date='2026-03-28'):
    """
    Download from Dukascopy - most reliable free source for historical FX data
    """
    print(f"\n{'='*60}")
    print(f"  DOWNLOADING FROM DUKASCOPY - {timeframe}")
    print(f"{'='*60}\n")

    try:
        # Dukascopy uses a specific URL format
        # We'll use their historical data API
        base_url = "https://datafeed.dukascopy.com/datafeed/"

        # Map timeframes to Dukascopy format
        tf_map = {
            'M5': ('m5', 5),
            'M15': ('m15', 15)
        }

        if timeframe not in tf_map:
            print(f"[ERROR] Unsupported timeframe: {timeframe}")
            return None

        print("Installing required library...")
        os.system("pip install -q dukascopy-historical-data")

        # Try importing the library
        try:
            from dukascopy.historical import get_historical_data

            start = pd.to_datetime(start_date)
            end = pd.to_datetime(end_date)

            print(f"Downloading XAUUSD {timeframe} data from {start_date} to {end_date}...")

            # Download data
            df = get_historical_data(
                instrument='XAUUSD',
                start_date=start,
                end_date=end,
                timeframe=tf_map[timeframe][1],  # timeframe in minutes
                price_type='bid'  # or 'ask'
            )

            if df is not None and len(df) > 0:
                # Rename columns to match our format
                df = df.reset_index()
                df = df.rename(columns={
                    'timestamp': 'timestamp',
                    'open': 'open',
                    'high': 'high',
                    'low': 'low',
                    'close': 'close',
                    'volume': 'volume'
                })

                # Make sure we have all required columns
                if 'volume' not in df.columns:
                    df['volume'] = 0

                df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]

                print(f"[OK] Downloaded {len(df):,} candles")
                print(f"     Range: {df['timestamp'].min()} to {df['timestamp'].max()}")

                return df
            else:
                print("[ERROR] No data received from Dukascopy")
                return None

        except ImportError:
            print("[ERROR] Could not install dukascopy library")
            return None

    except Exception as e:
        print(f"[ERROR] Dukascopy download failed: {str(e)}")
        return None


# ============================================================
# SOURCE 2: Alpha Vantage (Requires free API key)
# ============================================================

def download_alphavantage(timeframe='M5', api_key='demo'):
    """
    Download from Alpha Vantage (requires free API key from alphavantage.co)
    """
    print(f"\n{'='*60}")
    print(f"  DOWNLOADING FROM ALPHA VANTAGE - {timeframe}")
    print(f"{'='*60}\n")

    if api_key == 'demo':
        print("[WARNING] Using demo API key - limited data available")
        print("Get free API key from: https://www.alphavantage.co/support/#api-key\n")

    try:
        # Map timeframes
        interval_map = {
            'M5': '5min',
            'M15': '15min'
        }

        if timeframe not in interval_map:
            print(f"[ERROR] Unsupported timeframe: {timeframe}")
            return None

        url = f"https://www.alphavantage.co/query"
        params = {
            'function': 'FX_INTRADAY',
            'from_symbol': 'XAU',
            'to_symbol': 'USD',
            'interval': interval_map[timeframe],
            'outputsize': 'full',
            'apikey': api_key,
            'datatype': 'json'
        }

        print(f"Requesting data...")
        response = requests.get(url, params=params, timeout=30)

        if response.status_code != 200:
            print(f"[ERROR] HTTP {response.status_code}")
            return None

        data = response.json()

        # Check for errors
        if 'Error Message' in data:
            print(f"[ERROR] {data['Error Message']}")
            return None

        if 'Note' in data:
            print(f"[ERROR] API limit reached: {data['Note']}")
            return None

        # Parse time series data
        ts_key = f'Time Series FX ({interval_map[timeframe]})'
        if ts_key not in data:
            print(f"[ERROR] No time series data in response")
            return None

        ts_data = data[ts_key]

        # Convert to DataFrame
        rows = []
        for timestamp, values in ts_data.items():
            rows.append({
                'timestamp': pd.to_datetime(timestamp),
                'open': float(values['1. open']),
                'high': float(values['2. high']),
                'low': float(values['3. low']),
                'close': float(values['4. close']),
                'volume': 0
            })

        df = pd.DataFrame(rows)
        df = df.sort_values('timestamp')

        print(f"[OK] Downloaded {len(df):,} candles")
        print(f"     Range: {df['timestamp'].min()} to {df['timestamp'].max()}")

        return df

    except Exception as e:
        print(f"[ERROR] Alpha Vantage download failed: {str(e)}")
        return None


# ============================================================
# SOURCE 3: Try downloading historical CSV from public sources
# ============================================================

def download_histdata(timeframe='M5'):
    """
    Try downloading from histdata.com or similar sources
    """
    print(f"\n{'='*60}")
    print(f"  TRYING ALTERNATIVE SOURCES - {timeframe}")
    print(f"{'='*60}\n")

    print("[INFO] Checking for downloadable historical data...")

    # This would require scraping or direct CSV downloads
    # For now, return None - can be implemented if other sources fail

    return None


# ============================================================
# MAIN FUNCTION
# ============================================================

def merge_with_existing(new_df, timeframe):
    """
    Merge new data with existing MT5 data to avoid gaps
    """
    existing_file = os.path.join(OUTPUT_FOLDER, f'xauusd_{timeframe}.csv')

    if os.path.exists(existing_file):
        print(f"\n[INFO] Found existing {timeframe} data, merging...")
        existing_df = pd.read_csv(existing_file)
        existing_df['timestamp'] = pd.to_datetime(existing_df['timestamp'])

        # Combine datasets
        combined_df = pd.concat([new_df, existing_df], ignore_index=True)

        # Remove duplicates
        combined_df = combined_df.drop_duplicates(subset=['timestamp'])

        # Sort by timestamp
        combined_df = combined_df.sort_values('timestamp')

        print(f"     Before: {len(existing_df):,} candles")
        print(f"     New: {len(new_df):,} candles")
        print(f"     After merge: {len(combined_df):,} candles")

        return combined_df
    else:
        return new_df


def main():
    print("\n" + "=" * 60)
    print("  DOWNLOADING HISTORICAL DATA FROM WEB SOURCES")
    print("=" * 60)
    print("\nTarget: XAUUSD M5 and M15 from 2020 to 2024")
    print("This will supplement your existing MT5 data\n")

    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    # Download M5 data
    print("\n" + "=" * 60)
    print("  STEP 1: M5 DATA")
    print("=" * 60)

    m5_df = None

    # Try Dukascopy first (best free source)
    m5_df = download_dukascopy('M5', '2020-01-01', '2024-10-25')

    if m5_df is None:
        print("\n[INFO] Dukascopy failed, trying Alpha Vantage...")
        m5_df = download_alphavantage('M5')

    if m5_df is not None:
        # Merge with existing data
        m5_df = merge_with_existing(m5_df, 'M5')

        # Save
        output_file = os.path.join(OUTPUT_FOLDER, 'xauusd_M5.csv')
        m5_df.to_csv(output_file, index=False)

        file_size_mb = os.path.getsize(output_file) / (1024 * 1024)

        print(f"\n✅ M5 DATA SAVED")
        print(f"   Total candles: {len(m5_df):,}")
        print(f"   File size: {file_size_mb:.2f} MB")
        print(f"   Range: {m5_df['timestamp'].min()} to {m5_df['timestamp'].max()}")
    else:
        print("\n❌ Failed to download M5 data from all sources")

    # Download M15 data
    print("\n" + "=" * 60)
    print("  STEP 2: M15 DATA")
    print("=" * 60)

    m15_df = None

    # Try Dukascopy first
    m15_df = download_dukascopy('M15', '2020-01-01', '2024-02-14')

    if m15_df is None:
        print("\n[INFO] Dukascopy failed, trying Alpha Vantage...")
        m15_df = download_alphavantage('M15')

    if m15_df is not None:
        # Merge with existing data
        m15_df = merge_with_existing(m15_df, 'M15')

        # Save
        output_file = os.path.join(OUTPUT_FOLDER, 'xauusd_M15.csv')
        m15_df.to_csv(output_file, index=False)

        file_size_mb = os.path.getsize(output_file) / (1024 * 1024)

        print(f"\n✅ M15 DATA SAVED")
        print(f"   Total candles: {len(m15_df):,}")
        print(f"   File size: {file_size_mb:.2f} MB")
        print(f"   Range: {m15_df['timestamp'].min()} to {m15_df['timestamp'].max()}")
    else:
        print("\n❌ Failed to download M15 data from all sources")

    # Final summary
    print("\n" + "=" * 60)
    print("  DOWNLOAD COMPLETE")
    print("=" * 60)

    if m5_df is not None or m15_df is not None:
        print("\n✅ Successfully downloaded additional historical data!")
        print("\nYour data now covers:")
        if m5_df is not None:
            print(f"  M5:  {m5_df['timestamp'].min().strftime('%Y-%m-%d')} to {m5_df['timestamp'].max().strftime('%Y-%m-%d')}")
        if m15_df is not None:
            print(f"  M15: {m15_df['timestamp'].min().strftime('%Y-%m-%d')} to {m15_df['timestamp'].max().strftime('%Y-%m-%d')}")
    else:
        print("\n❌ Could not download data from web sources")
        print("\nAlternative options:")
        print("  1. Get Alpha Vantage API key (free): https://www.alphavantage.co/support/#api-key")
        print("  2. Use a different MT5 broker with deeper history")
        print("  3. Purchase data from a premium provider")

    print("=" * 60 + "\n")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nDownload cancelled by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n\nUNEXPECTED ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
