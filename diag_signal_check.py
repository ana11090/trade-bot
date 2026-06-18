"""
Check why Python's signal mask doesn't fire at 4 bars where MT5 enters.
Uses the exact same indicator build + shift logic as fast_backtest.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import numpy as np

# ── Rule #4 (f76d) conditions ──
CONDITIONS = [
    ("H4_mt5_atr_14", ">",  15.6557),
    ("H4_mt5_adx_21", ">",  27.569),
    ("H4_mfi",         ">",  74.2867),
    ("H4_mt5_adx_21", "<=", 35.447),
]

# All 9 MT5 entry bars
MT5_ENTRIES = [
    "2026-01-14 16:00", "2026-01-21 08:00", "2026-01-21 12:00",
    "2026-01-21 16:00", "2026-01-21 20:00", "2026-01-22 01:00",
    "2026-01-22 12:00", "2026-01-26 08:00", "2026-01-26 12:00",
]

# MT5 DIAG indicator values (from agent log) — for comparison
MT5_DIAG = {
    "2026-01-14 16:00": (31.32, 29.72, 74.32),
    "2026-01-21 08:00": (36.20, 28.09, 89.70),
    "2026-01-21 12:00": (33.36, 29.58, 89.91),
    "2026-01-21 16:00": (33.65, 30.99, 90.05),
    "2026-01-21 20:00": (37.99, 31.29, 78.04),
    "2026-01-22 01:00": (44.00, 30.82, 74.60),
    "2026-01-22 12:00": (50.32, 29.73, 74.97),
    "2026-01-26 08:00": (48.63, 34.34, 80.78),
    "2026-01-26 12:00": (47.33, 35.27, 86.45),
}


def main():
    from project2_backtesting.strategy_backtester import (
        run_comparison_matrix, build_multi_tf_indicators,
        _extract_required_indicators,
    )

    # Find a rule file with f76d
    rules_dir = "project2_backtesting/outputs/rules"
    rule_file = None
    for f in os.listdir(rules_dir):
        if "f76d" in f and f.endswith('.json'):
            rule_file = os.path.join(rules_dir, f)
            break
    if not rule_file:
        print("ERROR: no f76d rule file found")
        return

    rule = json.load(open(rule_file))
    print(f"Rule: {rule_file}")
    ds_id = rule.get('data_source_id')
    print(f"Data source: {ds_id}")

    # Resolve data dir
    data_dir = None
    for candidate in [
        os.path.join("data", "sources", ds_id) if ds_id else None,
        os.path.join("data"),
    ]:
        if candidate and os.path.isdir(candidate):
            h4_files = [f for f in os.listdir(candidate) if 'H4' in f and f.endswith('.csv')]
            if h4_files:
                data_dir = candidate
                break

    if not data_dir:
        print("ERROR: can't find data directory with H4 CSV")
        return

    # Load H4 data
    h4_file = [f for f in os.listdir(data_dir) if 'H4' in f and f.endswith('.csv')][0]
    h4_path = os.path.join(data_dir, h4_file)
    print(f"H4 data: {h4_path}")

    df = pd.read_csv(h4_path)
    if 'timestamp' not in df.columns:
        # Try first column
        df.rename(columns={df.columns[0]: 'timestamp'}, inplace=True)
    df['timestamp'] = pd.to_datetime(df['timestamp'])

    # Build indicators (same as run_comparison_matrix)
    required = {'H4': ['mt5_atr_14', 'mt5_adx_21', 'mfi']}
    try:
        ind = build_multi_tf_indicators(
            data_dir, df['timestamp'],
            required_indicators=required, entry_tf='H4')
    except Exception as e:
        print(f"build_multi_tf_indicators failed: {e}")
        print("Falling back to manual indicator computation...")
        # Manual fallback
        ind = pd.DataFrame(index=df.index)
        # Try loading from cached indicators
        for col in ['H4_mt5_atr_14', 'H4_mt5_adx_21', 'H4_mfi']:
            if col in df.columns:
                ind[col] = df[col]
        if ind.empty:
            print("ERROR: no indicator columns found")
            return

    # Apply shift(1) — same as fast_backtest line 3173
    _to_shift = [c for c in ind.columns if c.startswith('H4_')]
    if _to_shift:
        ind = ind.copy()
        ind[_to_shift] = ind[_to_shift].shift(1)

    print(f"\nData: {len(df)} bars, Indicators: {len(ind)} cols, shifted: {_to_shift[:3]}...")
    print(f"Date range: {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")

    # Check signal at each MT5 entry bar
    print(f"\n{'='*100}")
    print(f"{'Bar':>20}  {'ATR':>10} {'ADX':>10} {'MFI':>10}  {'Signal':>8}  MT5 DIAG (ATR/ADX/MFI)")
    print(f"{'='*100}")

    for entry_ts_str in MT5_ENTRIES:
        ts = pd.Timestamp(entry_ts_str)
        # Find nearest bar (within 1h tolerance for gap bars like 01:05 → 01:00)
        diffs = (df['timestamp'] - ts).abs()
        min_idx = diffs.idxmin()
        min_diff = diffs[min_idx]
        actual_ts = df['timestamp'][min_idx]

        if min_diff > pd.Timedelta(hours=4):
            print(f"  {entry_ts_str:>20}  NOT IN DATA")
            continue

        if min_idx not in ind.index:
            print(f"  {entry_ts_str:>20}  NO INDICATORS")
            continue

        row = ind.loc[min_idx]
        atr_val = row.get('H4_mt5_atr_14', float('nan'))
        adx_val = row.get('H4_mt5_adx_21', float('nan'))
        mfi_val = row.get('H4_mfi', float('nan'))

        # Check all conditions
        fails = []
        for feat, op, thresh in CONDITIONS:
            val = row.get(feat, float('nan'))
            if pd.isna(val):
                fails.append(f"{feat}=NaN")
                continue
            if op == '>' and not (val > thresh):
                fails.append(f"{feat}={val:.2f}{'<' if val < thresh else '='}={thresh:.2f}")
            elif op == '<=' and not (val <= thresh):
                fails.append(f"{feat}={val:.2f}>{thresh:.2f}")

        signal = len(fails) == 0
        mt5_atr, mt5_adx, mt5_mfi = MT5_DIAG.get(entry_ts_str, (0, 0, 0))

        marker = "✓" if signal else "✗"
        fail_str = f"  BLOCKED: {', '.join(fails)}" if fails else ""
        delta_str = ""
        if not pd.isna(atr_val):
            delta_str = f"  Δ ATR={atr_val-mt5_atr:+.2f} ADX={adx_val-mt5_adx:+.2f} MFI={mfi_val-mt5_mfi:+.2f}"

        print(f"  {entry_ts_str:>20}  {atr_val:10.4f} {adx_val:10.4f} {mfi_val:10.4f}  "
              f"{marker:>8}  MT5: {mt5_atr:.2f}/{mt5_adx:.2f}/{mt5_mfi:.2f}{delta_str}{fail_str}")


if __name__ == "__main__":
    main()
