"""
Candle Labeler — labels every candle in history as WIN or LOSS.

For each candle: "if you entered at the next candle's open with
SL of X pips and TP of Y pips, would the trade have been profitable?"

This creates a massive labeled dataset (130K+ rows) for ML training.
Much more data than the 1,106 trades from any single robot.
"""

import os
import numpy as np
import pandas as pd
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(_HERE, 'outputs')


def label_candles(
    candles_path,
    sl_pips=150,
    tp_pips=300,
    pip_size=0.01,
    direction="BUY",         # "BUY", "SELL", or "BOTH"
    max_hold_candles=50,     # max candles to hold before forced exit
    spread_pips=2.5,         # deduct spread from entry
    cache=True,
    progress_callback=None,
):
    """
    Label every candle as WIN or LOSS.

    Returns DataFrame with columns:
        timestamp, direction, label (1=WIN, 0=LOSS),
        pips_result, hold_candles, exit_reason

    Cached to outputs/candle_labels_{direction}_{sl}_{tp}.csv
    """
    # Check cache
    cache_name = f"candle_labels_{direction}_{sl_pips}_{tp_pips}.csv"
    cache_path = os.path.join(OUTPUT_DIR, cache_name)

    if cache and os.path.exists(cache_path):
        cached = pd.read_csv(cache_path)
        if len(cached) > 1000:
            return cached

    # Load candles (utf-8-sig automatically strips BOM)
    candles = pd.read_csv(candles_path, encoding='utf-8-sig')

    # Auto-detect timestamp column — don't assume the name
    ts_col = None
    for col in candles.columns:
        cl = col.lower().strip()
        if cl in ('timestamp', 'time', 'date', 'datetime', 'open_time', 'open time', 'opentime'):
            ts_col = col
            break
    if ts_col is None:
        # Fallback: use first column
        ts_col = candles.columns[0]

    candles['timestamp'] = pd.to_datetime(candles[ts_col], errors='coerce')
    candles = candles.dropna(subset=['timestamp'])

    # Find OHLC columns (different CSVs use different column names)
    col_map = {}
    for col in candles.columns:
        cl = col.lower().strip()
        if ('open' in cl or cl == 'o') and 'time' not in cl:
            col_map['open'] = col
        elif 'high' in cl or cl == 'h':
            col_map['high'] = col
        elif 'low' in cl or cl == 'l':
            col_map['low'] = col
        elif ('close' in cl or cl == 'c') and 'time' not in cl:
            col_map['close'] = col

    opens      = candles[col_map['open']].values.astype(float)
    highs      = candles[col_map['high']].values.astype(float)
    lows       = candles[col_map['low']].values.astype(float)
    closes     = candles[col_map['close']].values.astype(float)
    timestamps = candles['timestamp'].values

    n = len(candles)
    results = []

    directions_to_test = []
    if direction in ("BUY", "BOTH"):
        directions_to_test.append("BUY")
    if direction in ("SELL", "BOTH"):
        directions_to_test.append("SELL")

    total_work = len(directions_to_test) * (n - max_hold_candles - 1)
    work_done  = 0

    for dir_name in directions_to_test:
        for i in range(n - max_hold_candles - 1):
            work_done += 1
            if progress_callback and work_done % 5000 == 0:
                pct = work_done / total_work * 100
                progress_callback(work_done, total_work,
                                  f"Labeling {dir_name}: {pct:.0f}%")

            # Entry at next candle's open
            entry_price = opens[i + 1]

            # Apply spread
            if dir_name == "BUY":
                entry_price += spread_pips * pip_size
                sl_price    = entry_price - sl_pips * pip_size
                tp_price    = entry_price + tp_pips * pip_size
            else:
                entry_price -= spread_pips * pip_size
                sl_price    = entry_price + sl_pips * pip_size
                tp_price    = entry_price - tp_pips * pip_size

            label       = 0
            pips_result = 0
            hold        = 0
            exit_reason = "MAX_HOLD"

            for j in range(i + 1, min(i + 1 + max_hold_candles, n)):
                hold        = j - i
                candle_high = highs[j]
                candle_low  = lows[j]
                candle_open = opens[j]

                if dir_name == "BUY":
                    sl_hit = candle_low  <= sl_price
                    tp_hit = candle_high >= tp_price

                    if sl_hit and tp_hit:
                        if abs(candle_open - sl_price) < abs(candle_open - tp_price):
                            label       = 0
                            pips_result = (min(candle_open, sl_price) - entry_price) / pip_size
                            exit_reason = "STOP_LOSS"
                        else:
                            label       = 1
                            pips_result = (max(candle_open, tp_price) - entry_price) / pip_size
                            exit_reason = "TAKE_PROFIT"
                        break
                    elif sl_hit:
                        label       = 0
                        pips_result = (min(candle_open, sl_price) - entry_price) / pip_size
                        exit_reason = "STOP_LOSS"
                        break
                    elif tp_hit:
                        label       = 1
                        pips_result = (max(candle_open, tp_price) - entry_price) / pip_size
                        exit_reason = "TAKE_PROFIT"
                        break

                else:  # SELL
                    sl_hit = candle_high >= sl_price
                    tp_hit = candle_low  <= tp_price

                    if sl_hit and tp_hit:
                        if abs(candle_open - sl_price) < abs(candle_open - tp_price):
                            label       = 0
                            pips_result = (entry_price - max(candle_open, sl_price)) / pip_size
                            exit_reason = "STOP_LOSS"
                        else:
                            label       = 1
                            pips_result = (entry_price - min(candle_open, tp_price)) / pip_size
                            exit_reason = "TAKE_PROFIT"
                        break
                    elif sl_hit:
                        label       = 0
                        pips_result = (entry_price - max(candle_open, sl_price)) / pip_size
                        exit_reason = "STOP_LOSS"
                        break
                    elif tp_hit:
                        label       = 1
                        pips_result = (entry_price - min(candle_open, tp_price)) / pip_size
                        exit_reason = "TAKE_PROFIT"
                        break
            else:
                # Max hold reached — use close of last candle
                last_idx = min(i + max_hold_candles, n - 1)
                if dir_name == "BUY":
                    pips_result = (closes[last_idx] - entry_price) / pip_size
                else:
                    pips_result = (entry_price - closes[last_idx]) / pip_size
                label = 1 if pips_result > 0 else 0

            results.append({
                'timestamp':   str(timestamps[i]),
                'direction':   dir_name,
                'label':       label,
                'pips_result': round(pips_result, 1),
                'hold_candles': hold,
                'exit_reason': exit_reason,
            })

    df = pd.DataFrame(results)

    if cache:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        df.to_csv(cache_path, index=False)

    win_rate = df['label'].mean() if len(df) > 0 else 0
    avg_pips = df['pips_result'].mean() if len(df) > 0 else 0

    if progress_callback:
        progress_callback(total_work, total_work,
                          f"Done! {len(df)} candles labeled. "
                          f"WR: {win_rate:.1%}, avg: {avg_pips:+.0f} pips")

    return df
