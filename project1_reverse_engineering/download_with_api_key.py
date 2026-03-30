"""
Download Historical Data Using API Keys
Supports multiple providers - you just need a free API key

FREE API KEY SOURCES:
1. Alpha Vantage: https://www.alphavantage.co/support/#api-key (Free, 500 calls/day)
2. Twelve Data: https://twelvedata.com/pricing (Free, 800 calls/day)
3. Polygon.io: https://polygon.io/pricing (Free tier available)
4. FXCM: https://fxcm.com/ (Demo account)

USAGE:
1. Get a free API key from one of the sources above
2. Run: python download_with_api_key.py --provider alphavantage --apikey YOUR_KEY
"""

import os
import sys
import pandas as pd
import requests
from datetime import datetime, timedelta
import argparse

OUTPUT_FOLDER = '../data/'


def download_alphavantage(api_key, timeframe='M5', symbol='XAUUSD'):
    """
    Download from Alpha Vantage
    Free tier: 500 requests/day
    """
    print(f"\n{'='*60}")
    print(f"  ALPHA VANTAGE - {timeframe}")
    print(f"{'='*60}\n")

    interval_map = {
        'M5': '5min',
        'M15': '15min',
        'H1': '60min'
    }

    if timeframe not in interval_map:
        print(f"[ERROR] Unsupported timeframe: {timeframe}")
        return None

    url = "https://www.alphavantage.co/query"

    # Alpha Vantage uses XAU and USD separately
    params = {
        'function': 'FX_INTRADAY',
        'from_symbol': 'XAU',
        'to_symbol': 'USD',
        'interval': interval_map[timeframe],
        'outputsize': 'full',
        'apikey': api_key,
        'datatype': 'csv'
    }

    print(f"Downloading {symbol} {timeframe}...")

    try:
        response = requests.get(url, params=params, timeout=30)

        if response.status_code != 200:
            print(f"[ERROR] HTTP {response.status_code}")
            return None

        # Parse CSV response
        from io import StringIO
        df = pd.read_csv(StringIO(response.text))

        # Check for error messages
        if 'timestamp' not in df.columns:
            print(f"[ERROR] Invalid response: {df.iloc[0, 0] if len(df) > 0 else 'No data'}")
            return None

        # Format data
        df = df.rename(columns={'timestamp': 'timestamp'})
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        # Sort chronologically
        df = df.sort_values('timestamp')

        # Add volume column if missing
        if 'volume' not in df.columns:
            df['volume'] = 0

        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]

        print(f"[OK] Downloaded {len(df):,} candles")
        print(f"     Range: {df['timestamp'].min()} to {df['timestamp'].max()}")

        return df

    except Exception as e:
        print(f"[ERROR] Download failed: {str(e)}")
        return None


def download_twelvedata(api_key, timeframe='M5', symbol='XAUUSD'):
    """
    Download from Twelve Data
    Free tier: 800 requests/day
    """
    print(f"\n{'='*60}")
    print(f"  TWELVE DATA - {timeframe}")
    print(f"{'='*60}\n")

    interval_map = {
        'M5': '5min',
        'M15': '15min',
        'H1': '1h'
    }

    if timeframe not in interval_map:
        print(f"[ERROR] Unsupported timeframe: {timeframe}")
        return None

    # Twelve Data uses XAU/USD format
    url = "https://api.twelvedata.com/time_series"

    params = {
        'symbol': 'XAU/USD',
        'interval': interval_map[timeframe],
        'outputsize': 5000,  # Max per request
        'apikey': api_key,
        'format': 'CSV'
    }

    print(f"Downloading {symbol} {timeframe}...")

    try:
        response = requests.get(url, params=params, timeout=30)

        if response.status_code != 200:
            print(f"[ERROR] HTTP {response.status_code}")
            return None

        # Parse CSV
        from io import StringIO
        df = pd.read_csv(StringIO(response.text), delimiter=';')

        if 'datetime' not in df.columns:
            print(f"[ERROR] Invalid response")
            return None

        # Format data
        df = df.rename(columns={'datetime': 'timestamp'})
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp')

        # Add volume if missing
        if 'volume' not in df.columns:
            df['volume'] = 0

        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]

        print(f"[OK] Downloaded {len(df):,} candles")
        print(f"     Range: {df['timestamp'].min()} to {df['timestamp'].max()}")

        return df

    except Exception as e:
        print(f"[ERROR] Download failed: {str(e)}")
        return None


def merge_with_existing(new_df, timeframe):
    """Merge with existing data"""
    existing_file = os.path.join(OUTPUT_FOLDER, f'xauusd_{timeframe}.csv')

    if os.path.exists(existing_file):
        print(f"\n[INFO] Merging with existing data...")
        existing_df = pd.read_csv(existing_file)
        existing_df['timestamp'] = pd.to_datetime(existing_df['timestamp'])

        # Remove timezone
        if new_df['timestamp'].dt.tz is not None:
            new_df['timestamp'] = new_df['timestamp'].dt.tz_localize(None)
        if existing_df['timestamp'].dt.tz is not None:
            existing_df['timestamp'] = existing_df['timestamp'].dt.tz_localize(None)

        # Combine
        combined_df = pd.concat([new_df, existing_df], ignore_index=True)
        combined_df = combined_df.drop_duplicates(subset=['timestamp'], keep='first')
        combined_df = combined_df.sort_values('timestamp').reset_index(drop=True)

        print(f"     Before: {len(existing_df):,} candles")
        print(f"     After: {len(combined_df):,} candles")

        return combined_df
    else:
        return new_df


def main():
    parser = argparse.ArgumentParser(description='Download historical XAUUSD data using API')
    parser.add_argument('--provider', required=True, choices=['alphavantage', 'twelvedata'],
                       help='Data provider')
    parser.add_argument('--apikey', required=True, help='Your API key')
    parser.add_argument('--timeframe', default='M5', choices=['M5', 'M15', 'H1'],
                       help='Timeframe to download')

    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  DOWNLOADING WITH API KEY")
    print("=" * 60)
    print(f"\nProvider: {args.provider}")
    print(f"Timeframe: {args.timeframe}\n")

    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    # Download data
    if args.provider == 'alphavantage':
        df = download_alphavantage(args.apikey, args.timeframe)
    elif args.provider == 'twelvedata':
        df = download_twelvedata(args.apikey, args.timeframe)
    else:
        print(f"[ERROR] Unknown provider: {args.provider}")
        return

    if df is not None and len(df) > 0:
        # Merge with existing
        df = merge_with_existing(df, args.timeframe)

        # Save
        output_file = os.path.join(OUTPUT_FOLDER, f'xauusd_{args.timeframe}.csv')
        df.to_csv(output_file, index=False)

        file_size_mb = os.path.getsize(output_file) / (1024 * 1024)

        print(f"\n{'='*60}")
        print(f"  SUCCESS!")
        print(f"{'='*60}")
        print(f"\nSaved: {output_file}")
        print(f"Total candles: {len(df):,}")
        print(f"File size: {file_size_mb:.2f} MB")
        print(f"Range: {df['timestamp'].min()} to {df['timestamp'].max()}")
        print(f"{'='*60}\n")
    else:
        print("\n[FAILED] Could not download data\n")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {str(e)}")
        print("\nUsage example:")
        print("  python download_with_api_key.py --provider alphavantage --apikey YOUR_KEY --timeframe M5")
        sys.exit(1)
