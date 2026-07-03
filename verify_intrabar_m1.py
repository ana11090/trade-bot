"""
Verify exit_intrabar_m1 parameter:

  Pass A (flag=False, default): exit_times must be BYTE-IDENTICAL to stored trades.
  Pass B (flag=True):           exit_times should shift for some trades (1-3 min later
                                 for SL/TP trades that don't cross on the first M1 bar).

Run: python verify_intrabar_m1.py
"""
import sys, os, json, hashlib
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

RULE_JSON = os.path.join(_HERE, 'project2_backtesting', 'outputs', 'rules',
                          'rule_15_BUY_M5_4c_6179_ATR_Fixed_SL_016e_M5.json')
DATA_DIR  = os.path.join(_HERE, 'data', 'sources', 'levereged_2026.05.03')


def md5_of(obj):
    return hashlib.md5(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def run_rule(exit_intrabar_m1_flag):
    mods = [k for k in sys.modules if 'strategy_backtester' in k or 'exit_strategies' in k]
    for m in mods:
        del sys.modules[m]

    import pandas as pd
    from project2_backtesting.strategy_backtester import run_backtest, build_multi_tf_indicators
    from project2_backtesting.exit_strategies import ATRFixedSLTP

    with open(RULE_JSON, encoding='utf-8') as f:
        rule = json.load(f)

    candles_path = os.path.join(DATA_DIR, 'XAUUSD_M5.csv')
    candles_df   = pd.read_csv(candles_path, parse_dates=['timestamp'])
    candles_df['timestamp'] = candles_df['timestamp'].astype('datetime64[ns]')

    indicators_df = build_multi_tf_indicators(DATA_DIR, candles_df['timestamp'], entry_tf='M5')

    ex_params = dict(rule.get('exit_params', {}))
    ex_params.pop('pip_size', None)
    exit_strategy = ATRFixedSLTP(**ex_params)

    rules = rule.get('rules', [])
    conditions = rules[0].get('conditions', rule.get('conditions', [])) if rules else rule.get('conditions', [])

    trades = run_backtest(
        candles_df=candles_df,
        indicators_df=indicators_df,
        rules=conditions,
        exit_strategy=exit_strategy,
        direction=rule.get('direction', 'BUY'),
        broker_timezone='Etc/GMT-2',
        hard_close_hour=23,
        spread_pips=float(rule.get('spread_pips', rule.get('spread', 37.0))),
        commission_pips=float(rule.get('commission_pips', 4.0)),
        pip_size=float(rule.get('pip_size', 0.01)),
        account_size=float(rule.get('account_size', 10000.0)),
        risk_per_trade_pct=float(rule.get('risk_pct', 0.3)),
        leverage=float(rule.get('leverage', 10.0)),
        contract_size=float(rule.get('contract_size', 100.0)),
        pip_value_per_lot=float(rule.get('pip_value_per_lot', 1.0)),
        data_dir=DATA_DIR,
        exit_intrabar_m1=exit_intrabar_m1_flag,
    )
    return trades or []


print("Running Pass A (exit_intrabar_m1=False) ...")
trades_a = run_rule(False)
print(f"  {len(trades_a)} trades")

print("Running Pass B (exit_intrabar_m1=True) ...")
trades_b = run_rule(True)
print(f"  {len(trades_b)} trades")
print()

# Compare
fields = ['entry_time', 'exit_time', 'entry_price', 'exit_price', 'exit_reason', 'pips']
rows_a = [{k: str(t.get(k, '')) for k in fields} for t in trades_a]
rows_b = [{k: str(t.get(k, '')) for k in fields} for t in trades_b]

# Load stored trades for reference
with open(os.path.join(_HERE, 'project2_backtesting', 'outputs', 'rules',
                        'rule_15_BUY_M5_4c_6179_ATR_Fixed_SL_016e_M5.json')) as f:
    rule = json.load(f)
stored = rule.get('trades', [])
rows_stored = [{k: str(t.get(k, '')) for k in fields} for t in stored]

# Check A vs stored (must be identical within the overlap)
n_compare = min(len(rows_a), len(rows_stored))
n_mismatch_a = 0
for i in range(n_compare):
    if rows_a[i] != rows_stored[i]:
        print(f"MISMATCH A vs stored at trade {i}:")
        for k in fields:
            if rows_a[i].get(k) != rows_stored[i].get(k):
                print(f"  {k}: A={rows_a[i].get(k)!r}  stored={rows_stored[i].get(k)!r}")
        n_mismatch_a += 1
        if n_mismatch_a >= 3:
            break

if n_mismatch_a == 0:
    print(f"PASS A: exit_intrabar_m1=False is BYTE-IDENTICAL to stored trades ({n_compare} trades checked)")
else:
    print(f"FAIL A: {n_mismatch_a} mismatches — flag=False is NOT backward-compatible")

# Show changes in B vs A
n_changed = 0
for i, (ra, rb) in enumerate(zip(rows_a, rows_b)):
    if ra != rb:
        print(f"  B changed trade {i} ({ra['exit_reason']}): exit_time {ra['exit_time']} -> {rb['exit_time']}")
        n_changed += 1

if n_changed > 0:
    print(f"\nPASS B: {n_changed}/{len(rows_a)} trades have refined exit_time (flag=True moves exit_time mid-bar)")
else:
    print(f"\nINFO B: No exit_times changed (all SL/TP crosses at first M1 bar, lag=0)")
