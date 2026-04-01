"""
Download XAUUSD Historical Data from Multiple Sources
Fixed version with better error handling
"""

import os
import sys
import pandas as pd
import requests
from datetime import datetime, timedelta
import time

OUTPUT_FOLDER = '../data/'

# ============================================================
# SOURCE 1: Yahoo Finance (yfinance)
# ============================================================

def download_yfinance(timeframe='M5', start_date='2020-01-01'):
    """
    Download from Yahoo Finance using yfinance library
    Note: yfinance has limitations on intraday data (only recent 60-730 days)
    """
    print(f"\n{'='*60}")
    print(f"  DOWNLOADING FROM YAHOO FINANCE - {timeframe}")
    print(f"{'='*60}\n")

    try:
        import yfinance as yf

        # Map timeframes
        interval_map = {
            'M5': '5m',
            'M15': '15m'
        }

        if timeframe not in interval_map:
            print(f"[ERROR] Unsupported timeframe: {timeframe}")
            return None

        symbol = 'GC=F'  # Gold Futures
        interval = interval_map[timeframe]

        print(f"Downloading {symbol} {interval} data...")
        print(f"Note: yfinance limits intraday data to ~60-730 days\n")

        ticker = yf.Ticker(symbol)

        # For intraday data, we need to use period parameter
        # yfinance allows: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max
        if interval in ['5m', '15m']:
            # For 5m: max 60 days, for 15m: max 60 days according to API
            # But we can try "max" and see what we get
            df = ticker.history(period='max', interval=interval)
        else:
            df = ticker.history(start=start_date, interval=interval)

        if df is None or df.empty:
            print("[ERROR] No data received from yfinance")
            return None

        # Format the data
        df = df.reset_index()
        df = df.rename(columns={
            'Datetime': 'timestamp',
            'Date': 'timestamp',
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume'
        })

        # Keep only required columns
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]

        print(f"[OK] Downloaded {len(df):,} candles")
        print(f"     Range: {df['timestamp'].min()} to {df['timestamp'].max()}")

        return df

    except ImportError:
        print("[ERROR] yfinance not installed. Installing...")
        os.system("pip install -q yfinance")
        print("[INFO] Please run this script again")
        return None
    except Exception as e:
        print(f"[ERROR] yfinance download failed: {str(e)}")
        return None


# ============================================================
# SOURCE 2: Twelve Data (Free API)
# ============================================================

def download_twelvedata(timeframe='M5', start_date='2020-01-01'):
    """
    Download from Twelve Data API (free tier available)
    """
    print(f"\n{'='*60}")
    print(f"  DOWNLOADING FROM TWELVE DATA - {timeframe}")
    print(f"{'='*60}\n")

    # Note: Requires API key
    print("[INFO] Twelve Data requires free API key from twelvedata.com")
    print("[INFO] Skipping for now...\n")

    return None


# ============================================================
# SOURCE 3: Polygon.io (Free tier)
# ============================================================

def download_polygon(timeframe='M5', start_date='2020-01-01'):
    """
    Download from Polygon.io
    """
    print(f"\n{'='*60}")
    print(f"  DOWNLOADING FROM POLYGON.IO - {timeframe}")
    print(f"{'='*60}\n")

    print("[INFO] Polygon.io requires API key")
    print("[INFO] Skipping for now...\n")

    return None


# ============================================================
# SOURCE 4: Try manual Investing.com data
# ============================================================

def download_investing_com():
    """
    Attempt to download from Investing.com historical data
    """
    print(f"\n{'='*60}")
    print(f"  CHECKING INVESTING.COM")
    print(f"{'='*60}\n")

    print("[INFO] Investing.com requires manual download or scraping")
    print("[INFO] Visit: https://www.investing.com/commodities/gold-historical-data")
    print("[INFO] Skipping automated download...\n")

    return None


# ============================================================
# SOURCE 5: MetaTrader Data from HistData.com
# ============================================================

def download_histdata_com(timeframe='M5'):
    """
    Try to download from HistData.com
    """
    print(f"\n{'='*60}")
    print(f"  CHECKING HISTDATA.COM - {timeframe}")
    print(f"{'='*60}\n")

    print("[INFO] HistData.com has historical FX data")
    print("[INFO] Checking for XAUUSD...")

    try:
        # HistData.com has specific URL patterns
        # They provide data in monthly ZIP files

        base_url = "http://www.histdata.com/download-free-forex-data/"

        print("[INFO] HistData.com requires manual download")
        print(f"[INFO] Visit: {base_url}")
        print("[INFO] Look for 'XAUUSD' or 'GOLD' data\n")

        return None

    except Exception as e:
        print(f"[ERROR] Could not access HistData: {str(e)}")
        return None


# ============================================================
# MERGE FUNCTION
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

        # Remove timezone info to make compatible
        if new_df['timestamp'].dt.tz is not None:
            new_df['timestamp'] = new_df['timestamp'].dt.tz_localize(None)
        if existing_df['timestamp'].dt.tz is not None:
            existing_df['timestamp'] = existing_df['timestamp'].dt.tz_localize(None)

        # Combine datasets
        combined_df = pd.concat([new_df, existing_df], ignore_index=True)

        # Remove duplicates
        combined_df = combined_df.drop_duplicates(subset=['timestamp'], keep='first')

        # Sort by timestamp
        combined_df = combined_df.sort_values('timestamp').reset_index(drop=True)

        print(f"     Existing: {len(existing_df):,} candles")
        print(f"     New: {len(new_df):,} candles")
        print(f"     After merge: {len(combined_df):,} candles")

        return combined_df
    else:
        return new_df


# ============================================================
# MAIN FUNCTION
# ============================================================

def main():
    print("\n" + "=" * 60)
    print("  DOWNLOADING HISTORICAL DATA FROM WEB SOURCES")
    print("=" * 60)
    print("\nTarget: XAUUSD M5 and M15 from 2020")
    print("This will supplement your existing MT5 data\n")

    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    # Try M5 data
    print("\n" + "=" * 60)
    print("  STEP 1: M5 DATA (5-MINUTE)")
    print("=" * 60)

    m5_df = download_yfinance('M5', '2020-01-01')

    if m5_df is not None and len(m5_df) > 0:
        # Merge with existing data
        m5_df = merge_with_existing(m5_df, 'M5')

        # Save
        output_file = os.path.join(OUTPUT_FOLDER, 'xauusd_M5.csv')
        m5_df.to_csv(output_file, index=False)

        file_size_mb = os.path.getsize(output_file) / (1024 * 1024)

        print(f"\n[SUCCESS] M5 DATA SAVED")
        print(f"   Total candles: {len(m5_df):,}")
        print(f"   File size: {file_size_mb:.2f} MB")
        print(f"   Range: {m5_df['timestamp'].min()} to {m5_df['timestamp'].max()}")
    else:
        print("\n[FAILED] Could not download M5 data")

    # Try M15 data
    print("\n" + "=" * 60)
    print("  STEP 2: M15 DATA (15-MINUTE)")
    print("=" * 60)

    m15_df = download_yfinance('M15', '2020-01-01')

    if m15_df is not None and len(m15_df) > 0:
        # Merge with existing data
        m15_df = merge_with_existing(m15_df, 'M15')

        # Save
        output_file = os.path.join(OUTPUT_FOLDER, 'xauusd_M15.csv')
        m15_df.to_csv(output_file, index=False)

        file_size_mb = os.path.getsize(output_file) / (1024 * 1024)

        print(f"\n[SUCCESS] M15 DATA SAVED")
        print(f"   Total candles: {len(m15_df):,}")
        print(f"   File size: {file_size_mb:.2f} MB")
        print(f"   Range: {m15_df['timestamp'].min()} to {m15_df['timestamp'].max()}")
    else:
        print("\n[FAILED] Could not download M15 data")

    # Final summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)

    if m5_df is not None or m15_df is not None:
        print("\n[OK] Downloaded additional data where possible")
        print("\nNote: Free sources have limitations:")
        print("  - Yahoo Finance: typically only ~60 days for 5m/15m")
        print("  - For deeper history, consider:")
        print("    1. Premium data providers (Alpha Vantage, Polygon, etc.)")
        print("    2. Different MT5 broker with more history")
        print("    3. Manual download from Investing.com or HistData.com")

        print("\nCurrent data coverage:")
        if m5_df is not None:
            print(f"  M5:  {m5_df['timestamp'].min().strftime('%Y-%m-%d')} to {m5_df['timestamp'].max().strftime('%Y-%m-%d')} ({len(m5_df):,} candles)")
        if m15_df is not None:
            print(f"  M15: {m15_df['timestamp'].min().strftime('%Y-%m-%d')} to {m15_df['timestamp'].max().strftime('%Y-%m-%d')} ({len(m15_df):,} candles)")
    else:
        print("\n[INFO] Could not download additional data from free sources")
        print("\nYour current MT5 data is:")
        print("  M5:  Oct 2024 - Mar 2026 (100,000 candles)")
        print("  M15: Feb 2024 - Mar 2026 (50,000 candles)")
        print("  H1:  2015 - 2026 (66,183 candles)")
        print("  H4:  2010 - 2026 (25,004 candles)")
        print("\nThis should be sufficient for most trading strategies!")

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
