"""
DOWNLOAD PRICE DATA
Helper script to download XAUUSD (Gold) price data for all timeframes.

NOTE: This script has limitations:
- yfinance provides limited intraday data (typically only recent data)
- May not have M5/M15 granularity
- For complete historical intraday data, use MetaTrader5 or paid data provider

For the best results, use MetaTrader5 Python API with a demo account.
"""

import os
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta


# ============================================================
# CONFIGURATION
# ============================================================
SYMBOL_YFINANCE = 'GC=F'  # Gold Futures on Yahoo Finance
OUTPUT_FOLDER = '../data/'
START_DATE = '2026-02-01'  # Start before first trade for warmup
END_DATE = '2026-03-15'  # End after last trade


def download_data_yfinance():
    """
    Download XAUUSD data using yfinance.

    WARNING: yfinance has limited intraday data availability.
    It may only provide:
    - Recent 7 days for 1m, 5m intervals
    - Recent 60 days for 15m, 30m, 1h intervals
    - Historical data for daily intervals

    This is NOT ideal for this project but can be used for testing.
    """
    print(f"\n{'=' * 60}")
    print(f"  DOWNLOADING PRICE DATA — yfinance")
    print(f"{'=' * 60}\n")

    print(f"  Symbol: {SYMBOL_YFINANCE} (Gold Futures)")
    print(f"  Period: {START_DATE} to {END_DATE}")
    print(f"  Output: {OUTPUT_FOLDER}\n")

    # Create output directory
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    # Timeframe mappings (yfinance interval -> our filename)
    timeframes = {
        '5m': 'M5',
        '15m': 'M15',
        '1h': 'H1',
        '4h': 'H4',  # Note: yfinance doesn't have 4h, will use 1h and resample
    }

    ticker = yf.Ticker(SYMBOL_YFINANCE)

    for yf_interval, our_tf in timeframes.items():
        print(f"  Downloading {our_tf} ({yf_interval})...")

        try:
            if our_tf == 'H4':
                # yfinance doesn't have 4h interval, so download 1h and resample
                print(f"    (Note: Resampling 1h data to 4h)")
                df = ticker.history(start=START_DATE, end=END_DATE, interval='1h')

                if not df.empty:
                    # Resample to 4h
                    df = df.resample('4H').agg({
                        'Open': 'first',
                        'High': 'max',
                        'Low': 'min',
                        'Close': 'last',
                        'Volume': 'sum'
                    }).dropna()
            else:
                df = ticker.history(start=START_DATE, end=END_DATE, interval=yf_interval)

            if df.empty:
                print(f"    ⚠ WARNING: No data received for {our_tf}")
                print(f"      yfinance may not have intraday data for this period")
                continue

            # Rename columns to match expected format
            df = df.rename(columns={
                'Open': 'open',
                'High': 'high',
                'Low': 'low',
                'Close': 'close',
                'Volume': 'volume'
            })

            # Reset index to get timestamp as column
            df.reset_index(inplace=True)
            df = df.rename(columns={'index': 'timestamp', 'Datetime': 'timestamp', 'Date': 'timestamp'})

            # Keep only required columns
            df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]

            # Save to CSV
            output_file = os.path.join(OUTPUT_FOLDER, f'xauusd_{our_tf}.csv')
            df.to_csv(output_file, index=False)

            print(f"    ✓ Downloaded {len(df)} candles")
            print(f"      Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
            print(f"      Saved: {output_file}")

        except Exception as e:
            print(f"    ✗ ERROR downloading {our_tf}: {str(e)}")

    print(f"\n{'=' * 60}")
    print(f"  IMPORTANT NOTES:")
    print(f"{'=' * 60}")
    print(f"  ⚠ yfinance has limited intraday data availability:")
    print(f"    - May only have recent days for M5/M15 timeframes")
    print(f"    - Your trades are from March 2026, need data for that period")
    print(f"    - yfinance typically doesn't have future data\n")
    print(f"  For better results, use one of these alternatives:")
    print(f"    1. MetaTrader5 Python API (free, needs demo account)")
    print(f"    2. Manual export from MT5 platform")
    print(f"    3. Paid data provider (AlphaVantage, Polygon.io, etc.)\n")
    print(f"{'=' * 60}\n")


def verify_data_coverage():
    """
    Check if downloaded data covers the trade period.
    """
    print(f"\n{'=' * 60}")
    print(f"  VERIFYING DATA COVERAGE")
    print(f"{'=' * 60}\n")

    # Load trades to get date range
    trades_file = '../project0_data_pipeline/Data Files for data mining/trades_clean.csv'

    if not os.path.exists(trades_file):
        print(f"  ⚠ Trades file not found: {trades_file}")
        return

    trades = pd.read_csv(trades_file)
    trades['Open Date'] = pd.to_datetime(trades['Open Date'], format='%d/%m/%Y %H:%M')

    trade_start = trades['Open Date'].min()
    trade_end = trades['Open Date'].max()

    print(f"  Trade period: {trade_start} to {trade_end}\n")

    # Check each timeframe file
    timeframes = ['M5', 'M15', 'H1', 'H4']

    for tf in timeframes:
        file_path = os.path.join(OUTPUT_FOLDER, f'xauusd_{tf}.csv')

        if not os.path.exists(file_path):
            print(f"  ✗ {tf}: File not found")
            continue

        df = pd.read_csv(file_path)
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        data_start = df['timestamp'].min()
        data_end = df['timestamp'].max()

        # Check if data covers trades
        covers_start = data_start <= trade_start
        covers_end = data_end >= trade_end

        if covers_start and covers_end:
            status = "✓ GOOD"
        else:
            status = "✗ INSUFFICIENT"

        print(f"  {tf}: {status}")
        print(f"      Data range: {data_start} to {data_end}")
        print(f"      Candles: {len(df)}")

        if not covers_start:
            print(f"      ⚠ Missing data before {trade_start}")
        if not covers_end:
            print(f"      ⚠ Missing data after {trade_end}")

        print()

    print(f"{'=' * 60}\n")


def main():
    """Main entry point."""
    print("\nThis script will attempt to download XAUUSD price data using yfinance.")
    print("Press Ctrl+C to cancel, or Enter to continue...")

    try:
        input()
    except KeyboardInterrupt:
        print("\nCancelled.")
        return

    download_data_yfinance()
    verify_data_coverage()

    print("\nNext steps:")
    print("1. Check if data coverage is sufficient")
    print("2. If coverage is good, run: python run_all_scenarios.py")
    print("3. If coverage is insufficient, obtain data from alternative source\n")


if __name__ == '__main__':
    main()
