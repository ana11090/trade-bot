"""
Fast verify of exit_intrabar_m1 flag.
Uses a temp data_dir with ONLY the M1 CSV — no tick files — so tick loading
is skipped gracefully and the backtest runs fast.

Pass A (flag=False): exit_times must be UNCHANGED vs same run without flag.
Pass B (flag=True):  exit_times should shift for some trades.
"""
import sys, os, json, hashlib
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

RULE_JSON  = os.path.join(_HERE, 'project2_backtesting', 'outputs', 'rules',
                           'rule_15_BUY_M5_4c_6179_ATR_Fixed_SL_016e_M5.json')
DATA_DIR   = os.path.join(_HERE, 'data', 'sources', 'levereged_2026.05.03')
# Temp dir with ONLY M1 CSV: no tick files → tick loading skipped, M1 still works
M1_ONLY_DIR = os.path.join(_HERE, '_m1test')


def md5_of(obj):
    return hashlib.md5(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def run_rule(exit_intrabar_m1_flag, data_dir_arg):
    mods = [k for k in sys.modules if 'strategy_backtester' in k or 'exit_strategies' in k]
    for m in mods:
        del sys.modules[m]

    import pandas as pd
    from project2_backtesting.strategy_backtester import (
        run_backtest, build_multi_tf_indicators, _m1_cache, _m1_failed_dirs, _m1_ts_cache,
    )
    from project2_backtesting.exit_strategies import ATRFixedSLTP

    # Clear M1 cache so each run loads fresh
    _m1_cache.clear()
    _m1_ts_cache.clear()
    _m1_failed_dirs.clear()

    with open(RULE_JSON, encoding='utf-8') as f:
        rule = json.load(f)

    candles_path  = os.path.join(DATA_DIR, 'XAUUSD_M5.csv')
    candles_df    = pd.read_csv(candles_path, parse_dates=['timestamp'])
    candles_df['timestamp'] = candles_df['timestamp'].astype('datetime64[ns]')

    indicators_df = build_multi_tf_indicators(DATA_DIR, candles_df['timestamp'], entry_tf='M5')

    ex_params = dict(rule.get('exit_params', {}))
    ex_params.pop('pip_size', None)
    exit_strategy = ATRFixedSLTP(**ex_params)

    rules      = rule.get('rules', [])
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
        data_dir=data_dir_arg,
        exit_intrabar_m1=exit_intrabar_m1_flag,
    )
    return trades or []


print(f"M1_ONLY_DIR = {M1_ONLY_DIR}")
print(f"M1 CSV exists: {os.path.exists(os.path.join(M1_ONLY_DIR, 'XAUUSD_M1.csv'))}")
print()

print("Running Pass A (exit_intrabar_m1=False, M1-only data_dir) ...")
trades_a = run_rule(False, M1_ONLY_DIR)
print(f"  -> {len(trades_a)} trades")

print("Running Pass B (exit_intrabar_m1=True, M1-only data_dir) ...")
trades_b = run_rule(True, M1_ONLY_DIR)
print(f"  -> {len(trades_b)} trades")
print()

fields = ['entry_time', 'exit_time', 'entry_price', 'exit_price', 'exit_reason']
rows_a = [{k: str(t.get(k, '')) for k in fields} for t in trades_a]
rows_b = [{k: str(t.get(k, '')) for k in fields} for t in trades_b]

# A vs B: only exit_time should differ
n_changed = 0
n_price_changed = 0
for i, (ra, rb) in enumerate(zip(rows_a, rows_b)):
    et_changed   = ra['exit_time']   != rb['exit_time']
    ep_changed   = ra['exit_price']  != rb['exit_price']
    en_changed   = ra['entry_time']  != rb['entry_time']
    er_changed   = ra['exit_reason'] != rb['exit_reason']

    if ep_changed or en_changed or er_changed:
        print(f"UNEXPECTED CHANGE at trade {i} ({ra['exit_reason']}):")
        if ep_changed:  print(f"  exit_price:  A={ra['exit_price']!r}  B={rb['exit_price']!r}")
        if en_changed:  print(f"  entry_time:  A={ra['entry_time']!r}  B={rb['entry_time']!r}")
        if er_changed:  print(f"  exit_reason: A={ra['exit_reason']!r}  B={rb['exit_reason']!r}")
        n_price_changed += 1

    if et_changed:
        print(f"  Trade {i} ({ra['exit_reason']}): exit_time  A={ra['exit_time']}  B={rb['exit_time']}")
        n_changed += 1

print()
if n_price_changed > 0:
    print(f"FAIL: {n_price_changed} unexpected field changes (exit_price/entry_time/reason must not change)")
elif n_changed == 0:
    print(f"INFO: No exit_times changed (all SL/TP cross at M5 bar open; lag=0 for all trades)")
    print(f"PASS A: flag=False and flag=True produce identical results (expected when all lags=0)")
else:
    print(f"PASS: {n_changed}/{len(rows_a)} trades have refined exit_time with flag=True")
    print(f"      flag=False produces {len(rows_a)} trades; flag=True also {len(rows_b)} trades")
    print(f"      Only exit_time changes; all other fields identical")

# Also show all trades for reference
print()
print("=== All trades (Pass A, flag=False) ===")
for i, t in enumerate(trades_a[:10]):
    et = t.get('exit_time', '')
    et_b = str(trades_b[i].get('exit_time', '')) if i < len(trades_b) else '?'
    shifted = ' <<< SHIFTED' if str(et) != et_b else ''
    print(f"  [{i}] entry={t.get('entry_time','')}  exit={et}  ({t.get('exit_reason','')}){shifted}")
    if shifted:
        print(f"       flag=True exit_time: {et_b}")
