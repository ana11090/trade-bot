"""
Download XAUUSD Price Data from MetaTrader 5
Automatically downloads M5, M15, H1, H4 data and saves to ../data/ folder

REQUIREMENTS:
1. MetaTrader 5 must be installed
2. MT5 must be RUNNING and LOGGED IN to a demo account
3. XAUUSD symbol must be available in Market Watch

USAGE:
    python download_data_mt5.py
"""

import os
import sys
import pandas as pd
from datetime import datetime, timedelta

# Try to import MetaTrader5
try:
    import MetaTrader5 as mt5
except ImportError:
    print("\n" + "=" * 60)
    print("ERROR: MetaTrader5 module not installed")
    print("=" * 60)
    print("\nPlease install it with:")
    print("    pip install MetaTrader5")
    print("\nThen run this script again.")
    print("=" * 60 + "\n")
    sys.exit(1)


# ============================================================
# CONFIGURATION
# ============================================================
SYMBOL = 'XAUUSD'  # Change to 'GOLD' if your broker uses that
OUTPUT_FOLDER = '../data/'
TIMEFRAMES = {
    'M5': (mt5.TIMEFRAME_M5, 50000),    # M5: get 50k bars (~6 months)
    'M15': (mt5.TIMEFRAME_M15, 50000),  # M15: get 50k bars (~18 months)
    'H1': (mt5.TIMEFRAME_H1, datetime(2015, 1, 1)),    # H1: from 2015
    'H4': (mt5.TIMEFRAME_H4, datetime(2010, 1, 1)),    # H4: from 2010 (all data)
}

# Date range - downloading ALL available historical data
# Starting from 2010 to get maximum history available from broker
END_DATE = datetime.now()  # Today
START_DATE = datetime(2010, 1, 1)  # Get all available historical data from 2010


def main():
    print("\n" + "=" * 60)
    print("  DOWNLOADING XAUUSD DATA FROM METATRADER 5")
    print("=" * 60 + "\n")

    # Step 1: Initialize MT5
    print("Step 1: Connecting to MetaTrader 5...")
    if not mt5.initialize():
        print("[ERROR] Failed to initialize MT5")
        print("\nPossible solutions:")
        print("  1. Make sure MetaTrader 5 is RUNNING")
        print("  2. Make sure you're LOGGED IN to a demo account")
        print("  3. Try running this script as Administrator")
        print("  4. Try closing and reopening MT5")
        print(f"\nMT5 error: {mt5.last_error()}")
        sys.exit(1)

    print(f"[OK] Connected to MT5 version {mt5.version()}")

    # Get account info
    account_info = mt5.account_info()
    if account_info is not None:
        print(f"[OK] Logged in as: {account_info.name}")
        print(f"  Account: {account_info.login}")
        print(f"  Server: {account_info.server}")

    # Step 2: Check if symbol exists
    print(f"\nStep 2: Checking symbol '{SYMBOL}'...")

    # Try to get symbol info
    symbol_info = mt5.symbol_info(SYMBOL)

    if symbol_info is None:
        print(f"[ERROR] Symbol '{SYMBOL}' not found")
        print("\nPossible solutions:")
        print("  1. In MT5, right-click Market Watch -> Symbols")
        print("  2. Search for 'XAUUSD' or 'GOLD'")
        print("  3. Click 'Show' to enable the symbol")
        print("\nAlternatively, your broker might use a different name:")
        print("  - Try changing SYMBOL to 'GOLD' in this script")
        print("  - Or check your broker's symbol list")

        # List available symbols
        symbols = mt5.symbols_get()
        gold_symbols = [s.name for s in symbols if 'GOLD' in s.name.upper() or 'XAU' in s.name.upper()]
        if gold_symbols:
            print(f"\nFound these gold-related symbols: {', '.join(gold_symbols[:10])}")
            print(f"You might want to use one of these instead of '{SYMBOL}'")

        mt5.shutdown()
        sys.exit(1)

    print(f"[OK] Symbol '{SYMBOL}' found")
    try:
        print(f"  Description: {symbol_info.description}")
    except:
        print(f"  Description: [Unicode Error]")
    print(f"  Point: {symbol_info.point}")

    # Enable symbol if not enabled
    if not symbol_info.visible:
        print(f"  Enabling symbol in Market Watch...")
        if mt5.symbol_select(SYMBOL, True):
            print(f"  [OK] Symbol enabled")
        else:
            print(f"  [WARNING] Could not enable symbol, but will try to continue")

    # Step 3: Create output folder
    print(f"\nStep 3: Creating output folder...")
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    print(f"[OK] Output folder: {os.path.abspath(OUTPUT_FOLDER)}")

    # Step 4: Download data for each timeframe
    print(f"\nStep 4: Downloading data (optimized ranges per timeframe)...")
    print()

    success_count = 0

    for tf_name, (tf_constant, tf_param) in TIMEFRAMES.items():
        # Use different methods based on parameter type
        if isinstance(tf_param, datetime):
            print(f"  Downloading {tf_name} from {tf_param.strftime('%Y-%m-%d')}...", end=" ", flush=True)
            rates = mt5.copy_rates_range(SYMBOL, tf_constant, tf_param, END_DATE)
        else:  # It's a number of bars
            print(f"  Downloading {tf_name} (last {tf_param:,} bars)...", end=" ", flush=True)
            rates = mt5.copy_rates_from_pos(SYMBOL, tf_constant, 0, tf_param)

        try:

            if rates is None or len(rates) == 0:
                print(f"[ERROR] No data received")
                print(f"     MT5 error: {mt5.last_error()}")
                continue

            # Convert to DataFrame
            df = pd.DataFrame(rates)

            # Convert timestamp to datetime
            df['timestamp'] = pd.to_datetime(df['time'], unit='s')

            # Rename columns to match expected format
            df = df.rename(columns={
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'tick_volume': 'volume'
            })

            # Select and reorder columns
            df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]

            # Save to CSV
            output_file = os.path.join(OUTPUT_FOLDER, f'{SYMBOL.lower()}_{tf_name}.csv')
            df.to_csv(output_file, index=False)

            # Get file size
            file_size = os.path.getsize(output_file)
            file_size_mb = file_size / (1024 * 1024)

            print(f"[OK] {len(df):,} candles ({file_size_mb:.2f} MB)")
            print(f"     Range: {df['timestamp'].min()} to {df['timestamp'].max()}")
            print(f"     Saved: {output_file}")

            success_count += 1

        except Exception as e:
            print(f"[ERROR] Error: {str(e)}")

    # Shutdown MT5
    mt5.shutdown()

    # Final summary
    print("\n" + "=" * 60)
    print("  DOWNLOAD COMPLETE")
    print("=" * 60)
    print(f"\nSuccessfully downloaded: {success_count}/{len(TIMEFRAMES)} timeframes")

    if success_count == len(TIMEFRAMES):
        print("\n[OK] ALL DATA DOWNLOADED SUCCESSFULLY!")
        print("\nNext steps:")
        print("  1. Go to Project 1 -> Configuration")
        print("  2. Click 'Check Data Status'")
        print("  3. Verify: 'Price Data: All 4 timeframes present'")
        print("  4. Run your scenarios!")
    elif success_count > 0:
        print("\n[WARNING] PARTIAL SUCCESS - Some timeframes failed")
        print("\nTry:")
        print("  1. Check your internet connection")
        print("  2. Make sure MT5 is still connected")
        print("  3. Run this script again")
    else:
        print("\n[ERROR] DOWNLOAD FAILED")
        print("\nTroubleshooting:")
        print("  1. Make sure MT5 is running and connected")
        print("  2. Check if symbol name is correct (try 'GOLD' instead)")
        print("  3. Try manual export method (see DOWNLOAD_DATA_MT5.md)")

    print("=" * 60 + "\n")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nDownload cancelled by user.")
        mt5.shutdown()
        sys.exit(0)
    except Exception as e:
        print(f"\n\nUNEXPECTED ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        mt5.shutdown()
        sys.exit(1)
