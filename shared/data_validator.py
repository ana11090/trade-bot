"""
Data Validator — checks candle data quality and cross-references with trade prices.
"""

import os
import pandas as pd
from datetime import datetime, timedelta

# WHY: Hardcoded magic numbers scattered through validation logic make it hard
#      to tune sensitivity without hunting for every occurrence.
# CHANGED: April 2026 — centralized validation thresholds
WEEKEND_GAP_MULTIPLIER = 3     # Expected delta × 3 for weekend/holiday gaps
LARGE_GAP_MULTIPLIER = 5       # Expected delta × 5 for backtest large gaps
MAX_GAPS_TO_DISPLAY = 10       # Keep first N gaps in reports
ZERO_VOLUME_THRESHOLD_VALIDATE = 0.10  # Warn if >10% zero-volume candles (validation)
ZERO_VOLUME_THRESHOLD_BACKTEST = 0.20  # Warn if >20% zero-volume candles (backtest)

# WHY: Hardcoded XAUUSD price range ($200-$5000) fails for other instruments.
#      EURUSD realistic range is ~0.80-1.60, USDJPY is ~75-160, etc.
# CHANGED: April 2026 — per-symbol price validation ranges
SYMBOL_PRICE_RANGES = {
    'XAUUSD': (200, 5000),      # Gold: $250 (2001) to $2700+ (2024)
    'EURUSD': (0.80, 1.60),     # Euro: 0.83 (2000) to 1.60 (2008)
    'GBPUSD': (1.00, 2.20),     # Pound: 1.04 (1985) to 2.11 (2007)
    'USDJPY': (75, 160),        # Yen: 75.56 (2011) to 160 (1990)
    'BTCUSD': (100, 100000),    # Bitcoin: ~$100 (2013) to $69k (2021)
    'ETHUSD': (10, 10000),      # Ethereum: ~$10 (2015) to $4.8k (2021)
}


def validate_candle_file(csv_path, symbol="XAUUSD", drop_duplicates=False):
    """
    Validate a candle CSV file. Returns a dict with:
    - file: path
    - rows: count (after optional dedup)
    - date_range: (start, end)
    - price_range: (min, max)
    - price_ok: bool (is it in realistic range for the symbol?)
    - gaps: list of detected gaps (missing periods)
    - issues: list of issue descriptions
    - duplicates_removed: int (count removed when drop_duplicates=True, else 0)

    WHY: Old version only REPORTED duplicates as an issue but left them
         in the file. Upstream feature computation then processed the
         same bar twice, corrupting rolling-window indicator values.
         When drop_duplicates=True, removes them (keeping the last
         occurrence of each timestamp, matching cross_check_trades_vs_candles
         convention) and reports the count. Default False preserves
         current behavior for read-only callers.
    CHANGED: April 2026 — add drop_duplicates option (audit MED)
    """
    result = {
        "file": csv_path,
        "rows": 0,
        "date_range": None,
        "price_range": None,
        "price_ok": False,
        "gaps": [],
        "issues": [],
        "duplicates_removed": 0,
    }

    if not os.path.exists(csv_path):
        result["issues"].append("File not found")
        return result

    try:
        df = pd.read_csv(csv_path)
    except Exception as e:
        result["issues"].append(f"Failed to read CSV: {e}")
        return result

    result["rows"] = len(df)

    # Check required columns
    required_cols = ["timestamp", "open", "high", "low", "close", "volume"]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        result["issues"].append(f"Missing columns: {', '.join(missing_cols)}")
        return result

    # Parse timestamps
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df = df.sort_values("timestamp")

    if len(df) == 0:
        result["issues"].append("No valid timestamps")
        return result

    # Date range
    result["date_range"] = (str(df["timestamp"].iloc[0]), str(df["timestamp"].iloc[-1]))

    # Price range
    all_prices = pd.concat([df["open"], df["high"], df["low"], df["close"]])
    min_price = all_prices.min()
    max_price = all_prices.max()
    result["price_range"] = (round(min_price, 2), round(max_price, 2))

    # Check if prices are in realistic range (symbol-specific validation)
    symbol_upper = symbol.upper()
    if symbol_upper in SYMBOL_PRICE_RANGES:
        min_valid, max_valid = SYMBOL_PRICE_RANGES[symbol_upper]
        if min_price < min_valid or max_price > max_valid:
            result["price_ok"] = False
            result["issues"].append(
                f"Price range {min_price:.5f}-{max_price:.5f} outside realistic "
                f"{symbol_upper} range ({min_valid}-{max_valid})")
        else:
            result["price_ok"] = True
    else:
        result["price_ok"] = True  # Unknown symbol — skip validation

    # OHLC sanity checks
    invalid_ohlc = df[(df["high"] < df["low"]) |
                      (df["high"] < df["open"]) |
                      (df["high"] < df["close"]) |
                      (df["low"] > df["open"]) |
                      (df["low"] > df["close"])]

    if len(invalid_ohlc) > 0:
        result["issues"].append(f"{len(invalid_ohlc)} candles with invalid OHLC relationships")

    # Check for duplicates
    # WHY: Detection alone isn't enough — upstream feature computation
    #      still processes the duplicate bars and corrupts rolling-window
    #      indicators. If caller opts in via drop_duplicates=True, remove
    #      them here and report the count.
    # CHANGED: April 2026 — optional dedup (audit MED)
    duplicates = df[df.duplicated(subset=["timestamp"], keep=False)]
    n_dup_rows = len(duplicates)
    if n_dup_rows > 0:
        n_unique_timestamps = df["timestamp"].nunique()
        n_to_remove = len(df) - n_unique_timestamps
        if drop_duplicates:
            df = df.drop_duplicates(subset=["timestamp"], keep="last")
            result["duplicates_removed"] = int(n_to_remove)
            result["rows"] = len(df)
            result["issues"].append(
                f"{n_to_remove} duplicate timestamps removed (kept last occurrence)"
            )
        else:
            result["issues"].append(
                f"{n_dup_rows} duplicate timestamp rows detected "
                f"({n_to_remove} to remove). Call with drop_duplicates=True to clean."
            )

    # Check for zero-volume candles (might indicate data issues)
    zero_vol = df[df["volume"] == 0]
    if len(zero_vol) > len(df) * ZERO_VOLUME_THRESHOLD_VALIDATE:
        result["issues"].append(f"{len(zero_vol)} zero-volume candles ({len(zero_vol)/len(df)*100:.1f}%)")

    # Gap detection (simplified - just check for large time jumps)
    # Extract timeframe from filename (e.g., xauusd_H1.csv -> H1)
    filename = os.path.basename(csv_path)
    tf_map = {
        "M1": timedelta(minutes=1),
        "M5": timedelta(minutes=5),
        "M15": timedelta(minutes=15),
        "H1": timedelta(hours=1),
        "H4": timedelta(hours=4),
        "D1": timedelta(days=1),
        "W1": timedelta(weeks=1),
        "MN": timedelta(days=31)  # Approximate
    }

    tf = None
    for tf_name in tf_map:
        if tf_name in filename:
            tf = tf_name
            break

    if tf and tf in tf_map:
        expected_delta = tf_map[tf]
        # Check gaps (allow multiplier × expected delta for weekends/holidays)
        df["time_diff"] = df["timestamp"].diff()
        large_gaps = df[df["time_diff"] > expected_delta * WEEKEND_GAP_MULTIPLIER]

        # WHY: Large gaps flagged for Christmas, New Year, Good Friday, etc.
        #      are expected market closures, not data quality issues.
        # CHANGED: April 2026 — skip known holiday gaps
        # NOTE: Dates are (month, day) tuples for common forex/stock holidays
        known_holidays = [
            (12, 24), (12, 25), (12, 26),  # Christmas Eve, Christmas, Boxing Day
            (12, 31), (1, 1),               # New Year's Eve, New Year's Day
            (7, 4),                         # US Independence Day
            # Add more as needed (Good Friday varies, so not included)
        ]

        # Filter out weekend gaps + holiday gaps
        weekday_gaps = []
        for idx, row in large_gaps.iterrows():
            prev_idx = df.index[df.index.get_loc(idx) - 1]
            prev_row = df.loc[prev_idx]

            # Check if gap is over a weekend (Friday to Monday)
            if prev_row["timestamp"].weekday() == 4 and row["timestamp"].weekday() == 0:
                continue  # Weekend gap is expected

            # Check if gap overlaps a known holiday
            gap_start = prev_row["timestamp"]
            gap_end = row["timestamp"]
            is_holiday_gap = False
            for month, day in known_holidays:
                # Check if holiday falls between gap_start and gap_end
                try:
                    holiday_date = pd.Timestamp(year=gap_start.year, month=month, day=day)
                    if gap_start <= holiday_date <= gap_end:
                        is_holiday_gap = True
                        break
                except ValueError:
                    continue  # Invalid date (e.g., Feb 30)

            if is_holiday_gap:
                continue  # Holiday gap is expected

            weekday_gaps.append((str(prev_row["timestamp"]), str(row["timestamp"]), str(row["time_diff"])))

        if len(weekday_gaps) > 0:
            result["gaps"] = weekday_gaps[:MAX_GAPS_TO_DISPLAY]
            if len(weekday_gaps) > MAX_GAPS_TO_DISPLAY:
                result["issues"].append(f"{len(weekday_gaps)} large gaps detected (showing first {MAX_GAPS_TO_DISPLAY})")
            else:
                result["issues"].append(f"{len(weekday_gaps)} large gaps detected")

    return result


def validate_all_candles(data_dir):
    """
    Validate all candle files in the data directory.
    Prints a report and returns list of validation results.
    """
    if not os.path.exists(data_dir):
        print(f"ERROR: Data directory not found: {data_dir}")
        return []

    csv_files = [f for f in os.listdir(data_dir) if f.endswith(".csv") and not f.startswith("_")]

    if not csv_files:
        print(f"No CSV files found in {data_dir}")
        return []

    results = []

    for csv_file in sorted(csv_files):
        csv_path = os.path.join(data_dir, csv_file)
        print(f"\nValidating: {csv_file}")
        print("-" * 70)

        result = validate_candle_file(csv_path)
        results.append(result)

        print(f"  Rows:        {result['rows']:,}")
        if result['date_range']:
            print(f"  Date range:  {result['date_range'][0]} to {result['date_range'][1]}")
        if result['price_range']:
            print(f"  Price range: ${result['price_range'][0]:,.2f} - ${result['price_range'][1]:,.2f}")

        if result['price_ok']:
            print(f"  Price check: OK")
        else:
            print(f"  Price check: FAILED")

        if result['issues']:
            print(f"  Issues:")
            for issue in result['issues']:
                print(f"    - {issue}")
        else:
            print(f"  Issues:      None")

        if result['gaps']:
            print(f"  Large gaps (first 3):")
            for gap in result['gaps'][:3]:
                print(f"    {gap[0]} -> {gap[1]} (gap: {gap[2]})")

    # Summary
    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)
    total_files = len(results)
    files_with_issues = sum(1 for r in results if r['issues'])
    files_price_ok = sum(1 for r in results if r['price_ok'])

    print(f"  Total files validated: {total_files}")
    print(f"  Files with issues:     {files_with_issues}")
    print(f"  Files with valid prices: {files_price_ok}")

    if files_with_issues == 0:
        print("\n  All files passed validation!")
    else:
        print(f"\n  {files_with_issues} files have issues that need attention")

    return results


def check_backtest_data_quality(candles_df, timeframe="H1"):
    """
    Quick data quality check for backtesting. Returns list of warnings.
    Called at the start of run_comparison_matrix.

    Args:
        candles_df: DataFrame with columns [timestamp, open, high, low, close, volume]
        timeframe: Timeframe string (M5, H1, H4, D1, etc.)

    Returns:
        List of warning dicts with keys: severity, message
    """
    warnings = []

    if len(candles_df) == 0:
        warnings.append({"severity": "error", "message": "No candles in DataFrame"})
        return warnings

    # Check for duplicate timestamps
    duplicates = candles_df[candles_df.duplicated(subset=["timestamp"], keep=False)]
    if len(duplicates) > 0:
        warnings.append({
            "severity": "warning",
            "message": f"{len(duplicates)} duplicate timestamps found"
        })

    # Check for zero-volume candles
    if "volume" in candles_df.columns:
        zero_vol = candles_df[candles_df["volume"] == 0]
        if len(zero_vol) > len(candles_df) * ZERO_VOLUME_THRESHOLD_BACKTEST:
            warnings.append({
                "severity": "info",
                "message": f"{len(zero_vol)} zero-volume candles ({len(zero_vol)/len(candles_df)*100:.1f}%)"
            })

    # Check history length
    tf_min_candles = {"M5": 10000, "M15": 5000, "H1": 1000, "H4": 500, "D1": 200}
    min_required = tf_min_candles.get(timeframe, 1000)
    if len(candles_df) < min_required:
        warnings.append({
            "severity": "warning",
            "message": f"Short history: {len(candles_df)} candles (recommended: {min_required}+)"
        })

    # Check for time gaps
    tf_delta = {"M5": pd.Timedelta(minutes=5), "M15": pd.Timedelta(minutes=15),
                "H1": pd.Timedelta(hours=1), "H4": pd.Timedelta(hours=4),
                "D1": pd.Timedelta(days=1)}
    if timeframe in tf_delta:
        expected = tf_delta[timeframe]
        time_diff = candles_df["timestamp"].diff()
        large_gaps = time_diff[time_diff > expected * LARGE_GAP_MULTIPLIER]
        if len(large_gaps) > MAX_GAPS_TO_DISPLAY:
            warnings.append({
                "severity": "info",
                "message": f"{len(large_gaps)} large time gaps detected"
            })

    return warnings


def cross_check_trades_vs_candles(trades_csv, candles_csv, tolerance_pct=5.0):
    """
    Check that trade prices fall within the candle price range for matching dates.
    Returns list of mismatches.

    Args:
        trades_csv: Path to trades CSV (must have 'entry_time' and 'entry_price' columns)
        candles_csv: Path to candles CSV
        tolerance_pct: Allow price to be this % outside candle range (default 5%)
    """
    mismatches = []

    if not os.path.exists(trades_csv):
        return [f"Trades file not found: {trades_csv}"]
    if not os.path.exists(candles_csv):
        return [f"Candles file not found: {candles_csv}"]

    try:
        trades = pd.read_csv(trades_csv)
        candles = pd.read_csv(candles_csv)
    except Exception as e:
        return [f"Failed to read files: {e}"]

    # Parse timestamps
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], errors="coerce")
    candles["timestamp"] = pd.to_datetime(candles["timestamp"], errors="coerce")

    trades = trades.dropna(subset=["entry_time", "entry_price"])
    candles = candles.dropna(subset=["timestamp"])

    # WHY: Duplicate timestamps cause set_index to fail with "cannot reindex
    #      from a duplicate axis". Drop duplicates first (keep='last' to
    #      prefer the most recent data if broker sent duplicate candles).
    # CHANGED: April 2026 — drop duplicates before set_index
    candles = candles.drop_duplicates(subset=["timestamp"], keep="last")

    # Set candles timestamp as index for easier lookup
    candles = candles.set_index("timestamp").sort_index()

    for idx, trade in trades.iterrows():
        trade_time = trade["entry_time"]
        trade_price = trade["entry_price"]

        # Find the candle that contains this trade time
        # Use asof to find the most recent candle before or at trade time
        candle = candles.asof(trade_time)

        if candle is None or pd.isna(candle["high"]):
            continue

        # Check if trade price is within candle range (with tolerance)
        tolerance = (candle["high"] - candle["low"]) * (tolerance_pct / 100.0)
        low_bound = candle["low"] - tolerance
        high_bound = candle["high"] + tolerance

        if not (low_bound <= trade_price <= high_bound):
            mismatches.append({
                "trade_time": str(trade_time),
                "trade_price": trade_price,
                "candle_low": candle["low"],
                "candle_high": candle["high"],
                "candle_time": str(candle.name)
            })

    return mismatches
