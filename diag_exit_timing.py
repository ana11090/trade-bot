"""
Step 1 — exit-timing parity diagnostic.

For rule_15_BUY_M5_4c_6179_ATR_Fixed_SL_016e_M5, for each stored trade:
  1. Python exit bar = M5 bar at exit_time (bar open timestamp).
  2. Load M1 bars within that M5 bar.
  3. Find the first M1 bar that crosses SL (low <= sl_price) or TP (high >= tp_price).
  4. Show timing: bar_open, first_m1_crossing_time, bar_close.

This confirms Python exits at bar open timestamp while MT5 exits mid-bar
at the first tick crossing. If MT5 exit times land inside the M5 exit bar,
the fix is to use M1 precision for exit_time (not exit_price, which matches).
"""
import sys, os, json, pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

RULE_JSON  = os.path.join(_HERE, 'project2_backtesting', 'outputs', 'rules',
                           'rule_15_BUY_M5_4c_6179_ATR_Fixed_SL_016e_M5.json')
DATA_DIR   = os.path.join(_HERE, 'data', 'sources', 'levereged_2026.05.03')
M1_PATH    = os.path.join(DATA_DIR, 'XAUUSD_M1.csv')
M5_TF_MIN  = 5

# ---- load rule + first N trades ------------------------------------------
with open(RULE_JSON) as f:
    rule = json.load(f)
trades = rule.get('trades', [])[:6]      # first 6 — shows variety

# ---- load M1 data (cached, so reading once is fine) ----------------------
print(f"Loading M1 data from {M1_PATH} ...")
m1_df = pd.read_csv(M1_PATH, dtype={'open': 'float32', 'high': 'float32',
                                     'low': 'float32', 'close': 'float32'})
m1_df['timestamp'] = pd.to_datetime(m1_df['timestamp'])
print(f"  {len(m1_df):,} M1 candles loaded.")
print()

# ---- per-trade diagnostic -------------------------------------------------
for t in trades:
    entry_ts  = pd.Timestamp(t['entry_time'])
    exit_ts   = pd.Timestamp(t['exit_time'])     # M5 bar open timestamp (Python)
    bar_close = exit_ts + pd.Timedelta(minutes=M5_TF_MIN)
    reason    = t['exit_reason']
    entry_px  = float(t['entry_price'])
    exit_px   = float(t['exit_price'])
    pips      = float(t['pips'])

    # Infer SL/TP levels from stored trade
    if 'SL' in reason:
        sl_price = exit_px
        tp_price = entry_px + (entry_px - sl_price) * 3.0  # tp_atr_mult=3 / sl_atr_mult=1
    else:
        tp_price = exit_px
        sl_price = entry_px - (tp_price - entry_px) / 3.0

    # Find M1 bars within the exit M5 bar
    m1_in_bar = m1_df[(m1_df['timestamp'] >= exit_ts) &
                      (m1_df['timestamp'] < bar_close)].copy()

    # Find first M1 bar crossing SL or TP
    first_sl_cross = None
    first_tp_cross = None
    for _, row in m1_in_bar.iterrows():
        if first_sl_cross is None and float(row['low']) <= sl_price:
            first_sl_cross = row['timestamp']
        if first_tp_cross is None and float(row['high']) >= tp_price:
            first_tp_cross = row['timestamp']
        if first_sl_cross is not None and first_tp_cross is not None:
            break

    first_cross = first_sl_cross if 'SL' in reason else first_tp_cross
    lag_min = None
    if first_cross is not None:
        lag_min = (first_cross - exit_ts).total_seconds() / 60

    print(f"Entry {entry_ts}  ->  Python exit {exit_ts}  ({reason})")
    print(f"  entry_price={entry_px:.2f}  exit_price={exit_px:.2f}  pips={pips:.0f}")
    print(f"  Inferred SL={sl_price:.2f}  TP={tp_price:.2f}")
    print(f"  M5 exit bar: [{exit_ts}  ->  {bar_close})   ({len(m1_in_bar)} M1 bars)")
    if m1_in_bar.empty:
        print(f"  M1 data: NOT FOUND for this bar")
    else:
        m1_row = m1_in_bar.iloc[0]
        print(f"  M1[0] {m1_row['timestamp']}: O={float(m1_row['open']):.2f} H={float(m1_row['high']):.2f} L={float(m1_row['low']):.2f} C={float(m1_row['close']):.2f}")
        if first_cross is not None:
            print(f"  First M1 {'SL' if 'SL' in reason else 'TP'} crossing: {first_cross}  (+{lag_min:.1f} min into M5 bar)")
            print(f"  Python exit_time {exit_ts} vs M1 crossing {first_cross} => lag = {lag_min:.1f} min")
        else:
            print(f"  WARN: no M1 {'SL' if 'SL' in reason else 'TP'} crossing found within bar (unexpected)")
    print()
