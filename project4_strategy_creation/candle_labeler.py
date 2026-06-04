"""
Candle Labeler — labels every candle in history as WIN or LOSS.

For each candle: "if you entered at the next candle's open with
SL of X pips and TP of Y pips, would the trade have been profitable?"

This creates a massive labeled dataset (130K+ rows) for ML training.
Much more data than the 1,106 trades from any single robot.
"""

import os
import pandas as pd
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(_HERE, 'outputs')


def _default_session(hour):
    """Rough GMT/broker-tz session map. Verify against candle timezone before use.
    WHY: Candle timestamps may be in Europe/Athens (UTC+2/+3); apply a
    UTC_OFFSET correction before calling this if timestamps are not in GMT.
    """
    if 0  <= hour < 8:  return 'asian'
    if 8  <= hour < 13: return 'london'
    if 13 <= hour < 17: return 'london_ny'
    if 17 <= hour < 21: return 'ny'
    return 'late'


def label_candles(
    candles_path,
    sl_pips=150,
    tp_pips=300,
    pip_size=0.01,
    direction="BUY",         # "BUY", "SELL", or "BOTH"
    max_hold_candles=50,     # max candles to hold before forced exit
    spread_pips=2.5,         # deduct spread from entry
    commission_pips=0.0,     # round-trip commission in pips
    slippage_pips=0.0,       # one-way slippage in pips (applied at entry + exit)
    swap_long_pips_per_night=0.0,   # overnight financing cost for BUY (can be negative = credit)
    swap_short_pips_per_night=0.0,  # overnight financing cost for SELL (can be negative = credit)
    candles_per_day=None,    # used to convert hold_candles → nights for swap; auto-detected if None
    min_hold_candles=0,      # CHANGED: June 2026 — constraints (audit D): min candles before exit
    hard_close_hour=None,    # e.g. 23 → no new entries at/after this hour (broker tz)
    allowed_sessions=None,   # e.g. {'london','ny'}; None = all sessions
    max_spread_pips=0.0,     # skip entry if modeled spread exceeds this; 0 = off (no live spread data)
    session_of_hour=None,    # optional callable hour->session; None = use _default_session
    cache=True,
    progress_callback=None,
):
    """
    Label every candle as WIN or LOSS.

    Returns DataFrame with columns:
        timestamp, direction, label (1=WIN, 0=LOSS),
        pips_result, hold_candles, exit_reason

    Cached to outputs/candle_labels_{direction}_{sl}_{tp}.csv

    IMPORTANT — pip_size parameter
    --------------------------------
    pip_size is the monetary value of ONE pip in price units for the instrument.
      XAUUSD  : 0.01  (gold, 1 pip = $0.01 price move)
      EURUSD  : 0.0001 (forex major, 1 pip = 0.0001 price move)
      USDJPY  : 0.01  (JPY pair, 1 pip = 0.01 price move)
      Indices : 1.0   (e.g. SP500, 1 pip = 1.0 price move)
    Using the wrong pip_size shifts all pips_result values by 10–100×.
    sl_pips and tp_pips are in pip units, not price units — they are
    multiplied by pip_size internally.  spread_pips is also in pip units.
    """
    # Check cache
    # WHY: Old cache key only encoded direction/sl/tp. Changing max_hold,
    #      pip_size, or spread_pips produced the same filename, so the
    #      second call silently returned stale labels from the first run
    #      under different parameters. Fix: include every parameter that
    #      affects the output in the cache key.
    # CHANGED: April 2026 — full parameter cache key (audit HIGH #45)
    _sess_key = '_'.join(sorted(allowed_sessions)) if allowed_sessions else 'all'
    cache_name = (
        f"candle_labels_{direction}_sl{sl_pips}_tp{tp_pips}"
        f"_mh{max_hold_candles}_ps{pip_size}_sp{spread_pips}"
        f"_cm{commission_pips}_sl2{slippage_pips}"
        f"_swL{swap_long_pips_per_night}_swS{swap_short_pips_per_night}"
        f"_minh{min_hold_candles}_hch{hard_close_hour or 'off'}_sess{_sess_key}.csv"
    )
    cache_path = os.path.join(OUTPUT_DIR, cache_name)

    if cache and os.path.exists(cache_path):
        cached = pd.read_csv(cache_path)
        # WHY: Fix 5 — validate cached rowcount matches expected rowcount
        #      for the current candles file, not just a minimum threshold.
        #      See audit HIGH #46.
        # CHANGED: April 2026 — strict row count validation (audit HIGH #46)
        # Read current candle file row count to validate the cache
        try:
            _n_candles = sum(1 for _ in open(candles_path, 'r')) - 1  # minus header
            _expected_rows = max(0, _n_candles - max_hold_candles - 1)
            if abs(len(cached) - _expected_rows) < 10:  # tolerate ±10 rows
                return cached
            # Row count mismatch — cache is stale, fall through to regenerate
            print(f"[candle_labeler] Cache has {len(cached)} rows but candles "
                  f"file has {_n_candles} rows (expected ~{_expected_rows}). "
                  f"Regenerating.")
        except Exception as e:
            print(f"[candle_labeler] Cache validation failed: {e}. Regenerating.")

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

    # CHANGED: June 2026 — full cost model (audit gap C)
    # Auto-detect candles_per_day from timeframe implied by median bar gap
    if candles_per_day is None:
        try:
            ts_arr = pd.to_datetime(candles['timestamp']).sort_values()
            _gap_minutes = ts_arr.diff().dropna().dt.total_seconds().median() / 60
            if _gap_minutes > 0:
                candles_per_day = round(24 * 60 / _gap_minutes)
            else:
                candles_per_day = 24  # fallback: assume H1
        except Exception:
            candles_per_day = 24

    # Fixed per-trade cost: round-trip spread + commission + 2× slippage
    _fixed_cost_pips = spread_pips + commission_pips + 2.0 * slippage_pips

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

            # CHANGED: June 2026 — per-candle entry gates (audit D)
            # Entry timestamp is candle i+1 (the candle we enter at open).
            # WHY: hard_close_hour and session gates prevent labeling entries
            # that the EA would never take. Keeping them out of labeled data
            # prevents the ML model from training on "phantom" trades.
            # TIMEZONE NOTE: timestamps may be in broker tz (Europe/Athens,
            # UTC+2/+3). Verify candle tz before relying on hour-based gates.
            _entry_ts = pd.Timestamp(timestamps[i + 1])
            _hr = _entry_ts.hour
            if hard_close_hour is not None and _hr >= hard_close_hour:
                continue
            if allowed_sessions:
                _sess_fn = session_of_hour if session_of_hour else _default_session
                if _sess_fn(_hr) not in allowed_sessions:
                    continue
            # max_spread_pips: no per-candle spread series available in scratch;
            # this gate is a no-op unless a spread column is added in future.

            # Entry at next candle's open
            entry_price = opens[i + 1]

            # Apply spread + entry slippage to get realistic filled price
            _entry_cost = (spread_pips + slippage_pips) * pip_size
            if dir_name == "BUY":
                entry_price += _entry_cost
                sl_price    = entry_price - sl_pips * pip_size
                tp_price    = entry_price + tp_pips * pip_size
            else:
                entry_price -= _entry_cost
                sl_price    = entry_price + sl_pips * pip_size
                tp_price    = entry_price - tp_pips * pip_size

            label       = 0
            pips_result = 0
            hold        = 0
            exit_reason = "MAX_HOLD"

            for j in range(i + 1, min(i + 1 + max_hold_candles, n)):
                hold        = j - i
                # CHANGED: June 2026 — min hold gate (audit D)
                # Honor minimum hold: ignore SL/TP until min_hold_candles elapsed.
                # WHY: models EA behavior where position cannot be closed early.
                if min_hold_candles > 0 and hold < min_hold_candles:
                    continue
                candle_high = highs[j]
                candle_low  = lows[j]
                candle_open = opens[j]

                # CHANGED: June 2026 — full cost model (audit gap C)
                # exit_cost = exit spread + exit slippage + round-trip commission
                _exit_cost = spread_pips + slippage_pips + commission_pips

                if dir_name == "BUY":
                    sl_hit = candle_low  <= sl_price
                    tp_hit = candle_high >= tp_price

                    if sl_hit and tp_hit:
                        # WHY: Both SL and TP hit in the same candle. Without
                        #      sub-candle (M1) data we cannot know the actual
                        #      order. Conservative best practice: always label
                        #      LOSS. The old "closer to open = hit first"
                        #      heuristic is geometrically wrong — distance from
                        #      open does not predict the intra-candle path.
                        # CHANGED: April 2026 — conservative tie-break
                        label       = 0
                        pips_result = (min(candle_open, sl_price) - entry_price) / pip_size - _exit_cost
                        exit_reason = "STOP_LOSS_AMBIGUOUS"
                        break
                    elif sl_hit:
                        label       = 0
                        pips_result = (min(candle_open, sl_price) - entry_price) / pip_size - _exit_cost
                        exit_reason = "STOP_LOSS"
                        break
                    elif tp_hit:
                        label       = 1
                        pips_result = (max(candle_open, tp_price) - entry_price) / pip_size - _exit_cost
                        exit_reason = "TAKE_PROFIT"
                        break

                else:  # SELL
                    sl_hit = candle_high >= sl_price
                    tp_hit = candle_low  <= tp_price

                    if sl_hit and tp_hit:
                        label       = 0
                        pips_result = (entry_price - max(candle_open, sl_price)) / pip_size - _exit_cost
                        exit_reason = "STOP_LOSS_AMBIGUOUS"
                        break
                    elif sl_hit:
                        label       = 0
                        pips_result = (entry_price - max(candle_open, sl_price)) / pip_size - _exit_cost
                        exit_reason = "STOP_LOSS"
                        break
                    elif tp_hit:
                        label       = 1
                        pips_result = (entry_price - min(candle_open, tp_price)) / pip_size - _exit_cost
                        exit_reason = "TAKE_PROFIT"
                        break
            else:
                # Max hold reached — use close of last candle
                last_idx = min(i + max_hold_candles, n - 1)
                _exit_cost = spread_pips + slippage_pips + commission_pips
                if dir_name == "BUY":
                    pips_result = (closes[last_idx] - entry_price) / pip_size - _exit_cost
                else:
                    pips_result = (entry_price - closes[last_idx]) / pip_size - _exit_cost
                label = 1 if pips_result > 0 else 0

            # Apply overnight swap cost proportional to hold duration
            if (swap_long_pips_per_night != 0.0 or swap_short_pips_per_night != 0.0) and hold > 0:
                nights = hold / candles_per_day
                if dir_name == "BUY":
                    pips_result -= swap_long_pips_per_night * nights
                else:
                    pips_result -= swap_short_pips_per_night * nights
                if pips_result > 0:
                    label = 1
                else:
                    label = 0

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
