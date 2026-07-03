"""
Micro-verification of Hot Spot 1a: to_dict('records') pre-conversion.

Directly tests that the modified exit loop (positional loop over pre-built
dict list) produces byte-identical results to the original (iterrows loop)
using tiny synthetic DataFrames — no indicator building, no tick files.

Run with modified code, then with committed code, compare outputs.
OR: run directly — it tests both approaches in the same process.
"""
import sys, os, hashlib, json
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import pandas as pd
import numpy as np

# Simulate what run_backtest does: df + ind with non-contiguous index (after warmup)
N_TOTAL = 300
WARMUP   = 200

rng = np.random.default_rng(42)
timestamps = pd.date_range('2026-01-01', periods=N_TOTAL, freq='1h')
df = pd.DataFrame({
    'timestamp':       timestamps,
    'open':            rng.uniform(1900, 2000, N_TOTAL).round(2),
    'high':            rng.uniform(2000, 2100, N_TOTAL).round(2),
    'low':             rng.uniform(1800, 1900, N_TOTAL).round(2),
    'close':           rng.uniform(1900, 2000, N_TOTAL).round(2),
    'timestamp_utc':   timestamps,  # same for simplicity
}).reset_index(drop=True)

ind = pd.DataFrame({
    'atr': rng.uniform(10, 30, N_TOTAL).round(2),
    'rsi': rng.uniform(30, 70, N_TOTAL).round(2),
}).reset_index(drop=True)

# Apply warmup skip — this makes the index non-contiguous
df  = df.iloc[WARMUP:]
ind = ind.loc[df.index]

# Sanity check
assert df.index[0] == WARMUP, f"Expected first index {WARMUP}, got {df.index[0]}"
assert list(df.index) == list(range(WARMUP, N_TOTAL))


def run_original_iterrows(remaining_df, ind, df, entry_price, pip_size, hard_close_hour=23):
    """Original iterrows approach (exactly as in committed HEAD)."""
    exit_price  = None
    exit_time   = None
    exit_reason = None
    candles_held = 0
    _df_len = len(df)

    for future_idx, future_candle in remaining_df.iterrows():
        if exit_price is not None:
            break
        candles_held += 1
        pnl = (float(future_candle["close"]) - entry_price) / pip_size

        candle_dict = future_candle.to_dict()
        if future_idx in ind.index:
            candle_dict.update(ind.loc[future_idx].to_dict())

        # next_open
        try:
            _np_pos = df.index.get_loc(future_idx) + 1
            if _np_pos < _df_len:
                candle_dict["next_open"] = float(df.iloc[_np_pos]["open"])
        except Exception:
            pass

        # hard close hour
        if hard_close_hour >= 0:
            try:
                _candle_hour = pd.Timestamp(future_candle['timestamp_utc']).hour
                if _candle_hour == hard_close_hour:
                    exit_price  = float(future_candle["open"])
                    exit_time   = future_candle["timestamp"]
                    exit_reason = "HARD_CLOSE_HOUR"
                    break
            except Exception:
                pass

        # simple exit: close > entry * 1.01
        if float(candle_dict["close"]) > entry_price * 1.01:
            exit_price  = float(candle_dict["close"])
            exit_time   = candle_dict["timestamp"]
            exit_reason = "TAKE_PROFIT"
            break
        if float(candle_dict["close"]) < entry_price * 0.99:
            exit_price  = float(candle_dict["close"])
            exit_time   = candle_dict["timestamp"]
            exit_reason = "STOP_LOSS"
            break

    return {
        'exit_price': exit_price, 'exit_time': str(exit_time),
        'exit_reason': exit_reason, 'candles_held': candles_held,
        'next_open_at_exit': candle_dict.get("next_open") if exit_price else None,
        'atr_at_exit': candle_dict.get("atr") if exit_price else None,
    }


def run_modified_to_dict(remaining_df, ind, df, entry_price, pip_size, hard_close_hour=23):
    """Modified to_dict('records') approach (Hot Spot 1a)."""
    exit_price  = None
    exit_time   = None
    exit_reason = None
    candles_held = 0
    _df_len = len(df)

    _rem_dicts = remaining_df.to_dict('records')
    _idx_arr   = remaining_df.index.to_numpy()
    _rem_n     = len(_rem_dicts)

    for _k in range(_rem_n):
        if exit_price is not None:
            break
        future_idx  = _idx_arr[_k]
        candle_dict = _rem_dicts[_k]
        candles_held += 1
        pnl = (float(candle_dict["close"]) - entry_price) / pip_size

        if future_idx in ind.index:
            candle_dict.update(ind.loc[future_idx].to_dict())

        # next_open
        try:
            _np_pos = df.index.get_loc(future_idx) + 1
            if _np_pos < _df_len:
                candle_dict["next_open"] = float(df.iloc[_np_pos]["open"])
        except Exception:
            pass

        # hard close hour
        if hard_close_hour >= 0:
            try:
                _candle_hour = pd.Timestamp(candle_dict['timestamp_utc']).hour
                if _candle_hour == hard_close_hour:
                    exit_price  = float(candle_dict["open"])
                    exit_time   = candle_dict["timestamp"]
                    exit_reason = "HARD_CLOSE_HOUR"
                    break
            except Exception:
                pass

        if float(candle_dict["close"]) > entry_price * 1.01:
            exit_price  = float(candle_dict["close"])
            exit_time   = candle_dict["timestamp"]
            exit_reason = "TAKE_PROFIT"
            break
        if float(candle_dict["close"]) < entry_price * 0.99:
            exit_price  = float(candle_dict["close"])
            exit_time   = candle_dict["timestamp"]
            exit_reason = "STOP_LOSS"
            break

    return {
        'exit_price': exit_price, 'exit_time': str(exit_time),
        'exit_reason': exit_reason, 'candles_held': candles_held,
        'next_open_at_exit': candle_dict.get("next_open") if exit_price else None,
        'atr_at_exit': candle_dict.get("atr") if exit_price else None,
    }


# Run both versions on multiple simulated entry points
entry_points = list(range(WARMUP, N_TOTAL - 10, 5))  # every 5 rows
results_orig = []
results_modif = []
pip_size = 0.01

for ep in entry_points:
    ep_loc = df.index.get_loc(ep)
    remaining_df = df.iloc[ep_loc + 1:]
    entry_price = float(df.iloc[ep_loc]["open"])

    orig  = run_original_iterrows(remaining_df, ind, df, entry_price, pip_size)
    modif = run_modified_to_dict(remaining_df, ind, df, entry_price, pip_size)
    results_orig.append(orig)
    results_modif.append(modif)

# Compare
n_fail = 0
for i, (o, m) in enumerate(zip(results_orig, results_modif)):
    if o != m:
        print(f"MISMATCH at entry_point index {entry_points[i]}:")
        print(f"  ORIG:  {o}")
        print(f"  MODIF: {m}")
        n_fail += 1

if n_fail == 0:
    md5 = hashlib.md5(json.dumps(results_orig, default=str).encode()).hexdigest()
    print(f"PASS: {len(entry_points)} entry points, all identical. md5={md5}")
else:
    print(f"FAIL: {n_fail}/{len(entry_points)} entry points differ")
    sys.exit(1)
