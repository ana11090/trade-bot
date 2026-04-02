"""
Data Validator — checks candle data quality and cross-references with trade prices.
"""

import os
import pandas as pd
from datetime import datetime, timedelta


def validate_candle_file(csv_path, symbol="XAUUSD"):
    """
    Validate a candle CSV file. Returns a dict with:
    - file: path
    - rows: count
    - date_range: (start, end)
    - price_range: (min, max)
    - price_ok: bool (is it in realistic range for the symbol?)
    - gaps: list of detected gaps (missing periods)
    - issues: list of issue descriptions
    """
    result = {
        "file": csv_path,
        "rows": 0,
        "date_range": None,
        "price_range": None,
        "price_ok": False,
        "gaps": [],
        "issues": []
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

    # Check if prices are in realistic range for XAUUSD
    # Historical range roughly $250 (2001) to $2700+ (2020-2024)
    if symbol.upper() == "XAUUSD":
        if min_price < 200 or max_price > 5000:
            result["price_ok"] = False
            result["issues"].append(f"Price range ${min_price:.2f}-${max_price:.2f} outside realistic XAUUSD range ($200-$5000)")
        else:
            result["price_ok"] = True
    else:
        result["price_ok"] = True  # Don't validate other symbols

    # OHLC sanity checks
    invalid_ohlc = df[(df["high"] < df["low"]) |
                      (df["high"] < df["open"]) |
                      (df["high"] < df["close"]) |
                      (df["low"] > df["open"]) |
                      (df["low"] > df["close"])]

    if len(invalid_ohlc) > 0:
        result["issues"].append(f"{len(invalid_ohlc)} candles with invalid OHLC relationships")

    # Check for duplicates
    duplicates = df[df.duplicated(subset=["timestamp"], keep=False)]
    if len(duplicates) > 0:
        result["issues"].append(f"{len(duplicates)} duplicate timestamps")

    # Check for zero-volume candles (might indicate data issues)
    zero_vol = df[df["volume"] == 0]
    if len(zero_vol) > len(df) * 0.1:  # More than 10% zero volume
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
        # Check gaps (allow 3x expected delta for weekends/holidays)
        df["time_diff"] = df["timestamp"].diff()
        large_gaps = df[df["time_diff"] > expected_delta * 3]

        # Filter out weekend gaps (Friday close to Monday open)
        weekday_gaps = []
        for idx, row in large_gaps.iterrows():
            prev_idx = df.index[df.index.get_loc(idx) - 1]
            prev_row = df.loc[prev_idx]
            # Check if gap is over a weekend (Friday to Monday)
            if prev_row["timestamp"].weekday() == 4 and row["timestamp"].weekday() == 0:
                continue  # Weekend gap is expected
            weekday_gaps.append((str(prev_row["timestamp"]), str(row["timestamp"]), str(row["time_diff"])))

        if len(weekday_gaps) > 0:
            result["gaps"] = weekday_gaps[:10]  # Keep first 10 gaps
            if len(weekday_gaps) > 10:
                result["issues"].append(f"{len(weekday_gaps)} large gaps detected (showing first 10)")
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
        if len(zero_vol) > len(candles_df) * 0.2:  # More than 20%
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
        large_gaps = time_diff[time_diff > expected * 5]  # Allow 5x for weekends
        if len(large_gaps) > 10:
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
