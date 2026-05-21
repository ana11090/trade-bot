"""
M1 vs H4 SL-detection diagnostic.

Run from the repo root:
    python diagnose_m1.py

What this proves:
- Whether the M1 loader returns data for a real trade timestamp
- Whether M1 LOW reaches SL where MT5 says SL was hit
- Whether H4 LOW reaches SL for the same candle
- Whether the divergence is data-level (M1 doesn't reach SL either)
  OR aggregation-level (M1 reaches SL but H4 LOW is artificially higher)

No code changes. Just reads files and prints comparisons.
"""

import os
import sys
import pandas as pd

# Make the repo importable
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# These are the cases that diverged between Python (TIME_EXIT win) and
# MT5 (STOP_LOSS fired in seconds-to-minutes). All BUY direction. SL is
# 150 pips × 0.01 pip_size = 1.50 below entry.
DIVERGENT_TRADES = [
    # (entry_time, python_entry, mt5_entry, mt5_sl_price, mt5_sl_fire_ts)
    ('2026-03-05 20:00:00', 5063.08, 5062.12, 5061.57, '20:00:07'),
    ('2026-03-09 16:00:00', 5074.14, 5075.06, 5072.63, '16:08:01'),
    ('2026-03-24 04:00:00', 4322.09, 4321.61, 4320.58, '04:00:06'),
    # Also include one trade Python DID catch (control case)
    ('2026-03-26 12:00:00', 4423.60, 4424.75, 4422.10, '12:01:19'),
]

DATA_DIR = os.path.join(HERE, 'data', 'sources', 'levereged_2026.05.03')
H4_PATH  = os.path.join(DATA_DIR, 'XAUUSD_H4.csv')
M1_PATH  = os.path.join(DATA_DIR, 'XAUUSD_M1.csv')


def load_csv(path):
    print(f"\n  Loading {path}")
    if not os.path.exists(path):
        print(f"  X FILE MISSING")
        return None
    sz = os.path.getsize(path)
    print(f"  Size: {sz:,} bytes")
    if sz < 1000:
        with open(path, 'r') as f:
            first = f.readline()
        print(f"  X Looks like a stub. First line: {first[:80]}")
        return None
    try:
        df = pd.read_csv(path)
        if 'timestamp' not in df.columns:
            for c in df.columns:
                if 'time' in c.lower() or 'date' in c.lower():
                    df = df.rename(columns={c: 'timestamp'})
                    break
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        print(f"  OK Loaded {len(df):,} rows from {df['timestamp'].min()} to {df['timestamp'].max()}")
        print(f"    Columns: {list(df.columns)}")
        return df
    except Exception as e:
        print(f"  X Failed to load: {e}")
        return None


def check_trade(h4_df, m1_df, entry_ts_str, python_entry, mt5_entry, mt5_sl, mt5_sl_fire):
    print(f"\n{'='*78}")
    print(f"TRADE: {entry_ts_str}")
    print(f"  Python entry: {python_entry}  |  SL (entry - 1.50): {python_entry - 1.50:.2f}")
    print(f"  MT5 entry:    {mt5_entry}     |  MT5 SL:            {mt5_sl}")
    print(f"  MT5 SL fired at: {mt5_sl_fire}")
    print(f"{'='*78}")

    entry_ts = pd.Timestamp(entry_ts_str)
    py_sl_price = python_entry - 1.50

    # Look at the H4 candle starting at entry time
    if h4_df is not None:
        h4_row = h4_df[h4_df['timestamp'] == entry_ts]
        if len(h4_row) == 1:
            o = h4_row['open'].iloc[0]
            h = h4_row['high'].iloc[0]
            l = h4_row['low'].iloc[0]
            c = h4_row['close'].iloc[0]
            print(f"\n  H4 candle at {entry_ts}:")
            print(f"    Open  {o:.2f}")
            print(f"    High  {h:.2f}")
            print(f"    Low   {l:.2f}   <-- compare to Python SL {py_sl_price:.2f}")
            print(f"    Close {c:.2f}")
            if l <= py_sl_price:
                print(f"    OK H4 LOW reaches SL: parent_touched = True (Python SHOULD detect SL)")
            else:
                print(f"    X H4 LOW does NOT reach SL -- fast-path returns False, M1 never loaded")
                print(f"       Gap: H4 LOW {l:.2f} is {l - py_sl_price:.2f} ABOVE SL {py_sl_price:.2f}")
        else:
            print(f"  X H4 candle not found in CSV for {entry_ts}")

    # Look at the M1 candles within this H4 window (4 hours)
    if m1_df is not None:
        window_end = entry_ts + pd.Timedelta(hours=4)
        m1_slice = m1_df[(m1_df['timestamp'] >= entry_ts) & (m1_df['timestamp'] < window_end)]
        print(f"\n  M1 candles in H4 window [{entry_ts} -> {window_end}]:")
        print(f"    Count: {len(m1_slice)}")
        if len(m1_slice) > 0:
            m1_min = m1_slice['low'].min()
            m1_min_ts = m1_slice.loc[m1_slice['low'].idxmin(), 'timestamp']
            print(f"    Lowest M1 LOW: {m1_min:.2f} at {m1_min_ts}")
            if m1_min <= py_sl_price:
                print(f"    OK M1 LOW reaches SL {py_sl_price:.2f} -- M1 catches the SL hit!")
                print(f"       This means Python's H4 LOW is missing data that M1 has.")
                print(f"       FIX: don't trust H4 LOW; scan M1 directly.")
            else:
                print(f"    X M1 LOW does NOT reach SL either")
                print(f"       MT5 sees BID dip to {mt5_sl} at {mt5_sl_fire}, but neither")
                print(f"       Python's H4 NOR M1 captures that dip. Data is tick-incomplete.")
                print(f"       FIX: replace data with MT5-exported bars, or use tick data.")
            # Show the first few M1 candles around entry time
            print(f"\n    First 5 M1 candles after entry:")
            print(f"      {'timestamp':<20} {'open':>10} {'high':>10} {'low':>10} {'close':>10}")
            for _, row in m1_slice.head(5).iterrows():
                print(f"      {str(row['timestamp']):<20} {row['open']:>10.2f} {row['high']:>10.2f} {row['low']:>10.2f} {row['close']:>10.2f}")


def main():
    print(f"Data dir: {DATA_DIR}")
    h4_df = load_csv(H4_PATH)
    m1_df = load_csv(M1_PATH)

    if h4_df is None or m1_df is None:
        print("\nX Cannot proceed -- one or both files missing/broken.")
        print("   If files exist but look like stubs, run: git lfs pull")
        sys.exit(1)

    for entry_ts, py_e, mt5_e, mt5_sl, mt5_fire in DIVERGENT_TRADES:
        check_trade(h4_df, m1_df, entry_ts, py_e, mt5_e, mt5_sl, mt5_fire)

    print(f"\n\n{'='*78}")
    print("INTERPRETATION:")
    print(f"{'='*78}")
    print("- All 4 trades show 'M1 LOW reaches SL' -> fix: scan M1 directly,")
    print("  don't trust H4 LOW. I'll write the code change.")
    print("- All 4 trades show 'M1 LOW does NOT reach SL either' -> data is")
    print("  tick-incomplete. Re-export H4/M1 from MT5 terminal.")
    print("- Mixed (some yes, some no) -> likely some MT5 SL hits are sub-minute")
    print("  ticks that even M1 misses. Best fix is to use MT5-exported data")
    print("  or add tick data for the affected dates.")


if __name__ == '__main__':
    main()
