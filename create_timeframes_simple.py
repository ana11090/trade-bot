"""
Simple script to create OHLCV timeframes from tick data.
Processes year by year to save memory.

Usage:
    python create_timeframes_simple.py [SYMBOL]

Default symbol is XAUUSD if not provided. Tick data is expected at
<script_dir>/<symbol_lower>/ticks/<year>/*.csv and OHLCV is written
to <script_dir>/data/.
"""

import pandas as pd
import os
import sys

# WHY (Phase 46 Fix 1): Old code used relative paths 'xauusd/ticks/'
#      and 'data/' which broke when run from any directory other than
#      the project root. Anchor to the script's own directory.
# CHANGED: April 2026 — Phase 46 Fix 1 — anchored paths
#          (audit Part D HIGH #49)
# WHY (Phase 46 Fix 2): SYMBOL was hardcoded XAUUSD. Accept it from
#      sys.argv[1] so the script works for any instrument with tick
#      data in <symbol_lower>/ticks/.
# CHANGED: April 2026 — Phase 46 Fix 2 — CLI symbol
#          (audit Part D HIGH #50)
_HERE = os.path.dirname(os.path.abspath(__file__))
SYMBOL = sys.argv[1].upper() if len(sys.argv) > 1 else 'XAUUSD'
# WHY (Phase 53 Fix 1): Old code parsed timestamps as tz-naive and
#      resampled into tz-naive buckets. If the input ticks are in
#      broker time (EET, EDT) the resulting candles are shifted
#      from UTC and the user's backtest may match against the
#      wrong sessions. Accept an optional second CLI arg specifying
#      the source timezone; convert to UTC before resampling. If
#      not provided, log the assumption (UTC) clearly.
# CHANGED: April 2026 — Phase 53 Fix 1 — timezone-aware resample
#          (audit Part D MED #54)
SOURCE_TZ = sys.argv[2] if len(sys.argv) > 2 else None  # None = assume UTC
TICK_DATA_PATH = os.path.join(_HERE, SYMBOL.lower(), 'ticks')
OUTPUT_PATH    = os.path.join(_HERE, 'data')

print(f"Symbol: {SYMBOL}")
print(f"Tick path: {TICK_DATA_PATH}")
print(f"Output: {OUTPUT_PATH}")
if SOURCE_TZ:
    print(f"Source timezone: {SOURCE_TZ} (will convert to UTC)")
else:
    print(f"Source timezone: ASSUMED UTC (pass tz as 2nd arg if different, e.g. 'EET')")

# Timeframes
TIMEFRAMES = {
    'M5': '5min',
    'M15': '15min',
    'H1': '1H',
    'H4': '4H',
    'D1': '1D',
    'W1': '1W',
    # WHY (Phase 46 Fix 3): '1ME' is pandas 2.x only and breaks
    #      pandas 1.x with KeyError at resample. '1M' works in both
    #      (deprecated in 2.x with warning, still functional).
    # CHANGED: April 2026 — Phase 46 Fix 3 — cross-version freq
    #          (audit Part D HIGH #51)
    'MN': '1M',
}

def process_year_to_timeframe(year, timeframe_name, resample_rule):
    """Process one year of tick data into a specific timeframe."""
    year_path = os.path.join(TICK_DATA_PATH, str(year))

    if not os.path.isdir(year_path):
        return None

    # Get all tick files for this year
    tick_files = sorted([f for f in os.listdir(year_path) if f.endswith('.csv')])

    if not tick_files:
        return None

    print(f"  Processing {year} ({len(tick_files)} files)...", flush=True)

    # Load all months for this year
    dfs = []
    for file in tick_files:
        file_path = os.path.join(year_path, file)
        df = pd.read_csv(file_path)
        # Handle malformed timestamps
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
        df = df.dropna(subset=['timestamp'])
        dfs.append(df)

    # Combine all months
    year_ticks = pd.concat(dfs, ignore_index=True)
    # Phase 53 Fix 1b: convert to UTC if source tz was specified
    if SOURCE_TZ and 'timestamp' in year_ticks.columns:
        try:
            if year_ticks['timestamp'].dt.tz is None:
                year_ticks['timestamp'] = year_ticks['timestamp'].dt.tz_localize(SOURCE_TZ).dt.tz_convert('UTC').dt.tz_localize(None)
        except Exception as _e:
            print(f"  WARNING: timezone conversion failed: {_e}")
    # WHY (Phase 46 Fix 5): Overlapping tick files at month boundaries
    #      (e.g., last tick of January present in both Jan and Feb files)
    #      produced duplicate timestamps. Resampling then double-counted
    #      volume on the overlap candles.
    # CHANGED: April 2026 — Phase 46 Fix 5 — dedupe ticks
    #          (audit Part D HIGH #53)
    _before = len(year_ticks)
    year_ticks = year_ticks.drop_duplicates(subset=['timestamp'], keep='first')
    if len(year_ticks) < _before:
        print(f"  Dropped {_before - len(year_ticks)} duplicate ticks "
              f"({(_before - len(year_ticks)) / _before * 100:.1f}%)")
    year_ticks = year_ticks.sort_values('timestamp').set_index('timestamp')

    # WHY (Phase 46 Fix 4): Old code assumed a 'mid' column. Tick CSVs
    #      that only carry bid/ask crashed with KeyError. Fall back to
    #      (bid+ask)/2 if mid is absent.
    # CHANGED: April 2026 — Phase 46 Fix 4 — mid fallback
    #          (audit Part D HIGH #52)
    if 'mid' in year_ticks.columns:
        year_ticks['price'] = year_ticks['mid']
    elif 'bid' in year_ticks.columns and 'ask' in year_ticks.columns:
        year_ticks['price'] = (year_ticks['bid'] + year_ticks['ask']) / 2.0
    elif 'price' in year_ticks.columns:
        pass  # already named correctly
    else:
        print(f"  ERROR: tick data has no 'mid', 'bid'/'ask', or 'price' "
              f"columns. Available: {list(year_ticks.columns)}")
        return None

    # WHY (Phase 53 Fix 2): Old code called .resample() five separate
    #      times. Each call rebuilt the resample grouper from scratch
    #      (pandas overhead). Use a single .resample().agg(dict) call
    #      that builds the grouper once and computes all five aggs
    #      in one pass. ~4x faster on large tick datasets.
    # CHANGED: April 2026 — Phase 53 Fix 2 — single resample call
    #          (audit Part D MED #55)
    _grouper = year_ticks[['price', 'volume']].resample(resample_rule)
    ohlcv = _grouper.agg({
        'price':  ['first', 'max', 'min', 'last'],
        'volume': 'sum',
    })
    # Flatten the MultiIndex columns
    ohlcv.columns = ['open', 'high', 'low', 'close', 'volume']

    ohlcv = ohlcv.reset_index()
    ohlcv = ohlcv.dropna()

    return ohlcv

def create_timeframe_file(timeframe_name, resample_rule):
    """Create complete OHLCV file for a timeframe."""
    print(f"\nCreating {timeframe_name} ({resample_rule})...", flush=True)

    # Get all year directories
    years = sorted([d for d in os.listdir(TICK_DATA_PATH)
                   if os.path.isdir(os.path.join(TICK_DATA_PATH, d)) and d.isdigit()])

    # WHY (Phase 61 Fix 6): Old code processed each year separately then
    #      concatenated OHLCV. Candles that span Dec 31 / Jan 1 (H4, D1,
    #      W1, MN) were split into two partial bars — one at year-end and
    #      one at year-start — with wrong OHLC values on both halves.
    #      Fix: load all years' ticks together, deduplicate, then resample
    #      once across the full range so boundary candles are computed
    #      correctly.
    # CHANGED: April 2026 — Phase 61 Fix 6 — single-pass cross-year resample
    #          (audit Part D MEDIUM #56)
    all_ticks = []
    for year in years:
        year_path = os.path.join(TICK_DATA_PATH, str(year))
        dfs = []
        for fn in sorted(os.listdir(year_path)):
            if fn.endswith('.csv'):
                try:
                    _df = pd.read_csv(os.path.join(year_path, fn))
                    dfs.append(_df)
                except Exception:
                    pass
        if dfs:
            all_ticks.append(pd.concat(dfs, ignore_index=True))

    if not all_ticks:
        return

    all_tick_df = pd.concat(all_ticks, ignore_index=True)
    all_tick_df['timestamp'] = pd.to_datetime(all_tick_df['timestamp'], errors='coerce')
    all_tick_df = all_tick_df.dropna(subset=['timestamp'])
    all_tick_df = all_tick_df.drop_duplicates(subset=['timestamp'], keep='first')
    all_tick_df = all_tick_df.sort_values('timestamp').set_index('timestamp')

    # Resolve price column (same logic as process_year_to_timeframe)
    if 'mid' in all_tick_df.columns:
        all_tick_df['price'] = all_tick_df['mid']
    elif 'bid' in all_tick_df.columns and 'ask' in all_tick_df.columns:
        all_tick_df['price'] = (all_tick_df['bid'] + all_tick_df['ask']) / 2
    if 'volume' not in all_tick_df.columns:
        all_tick_df['volume'] = 1

    _resampled = all_tick_df.resample(resample_rule).agg({
        'price':  ['first', 'max', 'min', 'last'],
        'volume': 'sum',
    })
    # Flatten MultiIndex columns
    _resampled.columns = ['open', 'high', 'low', 'close', 'volume']
    combined = _resampled.dropna(subset=['open']).reset_index()
    print(f"  Cross-year resample: {len(combined):,} candles from {len(years)} years", flush=True)

    # Save
    output_file = os.path.join(OUTPUT_PATH, f'{SYMBOL.lower()}_{timeframe_name}.csv')
    combined.to_csv(output_file, index=False)

    size_mb = os.path.getsize(output_file) / (1024 * 1024)
    print(f"  Saved {len(combined):,} candles ({size_mb:.1f} MB)", flush=True)
    print(f"  Date range: {combined['timestamp'].min()} to {combined['timestamp'].max()}", flush=True)

def main():
    print("=" * 60, flush=True)
    print("CREATING TIMEFRAME FILES", flush=True)
    print("=" * 60, flush=True)

    os.makedirs(OUTPUT_PATH, exist_ok=True)

    for tf_name, resample_rule in TIMEFRAMES.items():
        try:
            create_timeframe_file(tf_name, resample_rule)
        except Exception as e:
            print(f"ERROR processing {tf_name}: {e}", flush=True)
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60, flush=True)
    print("DONE!", flush=True)
    print("=" * 60, flush=True)

if __name__ == '__main__':
    main()
