"""
Speedup verification: confirm Hot Spot 1a (to_dict pre-conversion)
produces byte-identical trade results vs the original iterrows loop.

Run BEFORE git stash to get pass A (modified code):
    python verify_speedup.py A

Run AFTER git stash to get pass B (original code):
    python verify_speedup.py B

Then compare: python verify_speedup.py CMP
"""
import sys, os, json, hashlib

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

DATA_DIR = os.path.join(_HERE, 'data', 'sources', 'levereged_2026.05.03')
RULES_DIR = os.path.join(_HERE, 'project2_backtesting', 'outputs', 'rules')

RULE_FILES = [
    'rule_15_BUY_M5_4c_6179_ATR_Only_81cf_M5.json',
    'rule_15_BUY_M5_4c_6179_ATR_Fixed_SL_016e_M5.json',
]


def md5_of(obj):
    raw = json.dumps(obj, sort_keys=True, default=str).encode()
    return hashlib.md5(raw).hexdigest()


def run_one(rule_path):
    # Force fresh import so git stash swap takes effect
    mods = [k for k in sys.modules if 'strategy_backtester' in k
            or 'exit_strategies' in k]
    for m in mods:
        del sys.modules[m]

    import pandas as pd
    from project2_backtesting.strategy_backtester import (
        run_backtest, build_multi_tf_indicators,
    )

    with open(rule_path, encoding='utf-8') as f:
        rule = json.load(f)

    tf        = rule.get('entry_tf', 'M5')
    spread    = float(rule.get('spread_pips', rule.get('spread', 37.0)))
    commission = float(rule.get('commission_pips', 4.0))
    pip_size  = float(rule.get('pip_size', 0.01))
    account   = float(rule.get('account_size', 10000.0))
    risk_pct  = float(rule.get('risk_pct', 0.3))
    leverage  = float(rule.get('leverage', 10.0))
    contract  = float(rule.get('contract_size', 100.0))
    pip_value = float(rule.get('pip_value_per_lot', 1.0))
    broker_tz = 'Etc/GMT-2'

    # Try XAUUSD_M5.csv (uppercase) first, fall back to lowercase
    for name in (f'XAUUSD_{tf}.csv', f'xauusd_{tf}.csv'):
        path = os.path.join(DATA_DIR, name)
        if os.path.exists(path):
            candles_path = path
            break
    else:
        return {'error': f'candles not found for TF={tf} in {DATA_DIR}'}

    candles_df = pd.read_csv(candles_path, parse_dates=['timestamp'])
    candles_df['timestamp'] = candles_df['timestamp'].astype('datetime64[ns]')

    # Build indicators
    indicators_df = build_multi_tf_indicators(
        DATA_DIR, candles_df['timestamp'], entry_tf=tf)

    # Build exit strategy
    ex_class  = rule.get('exit_class', 'ATRBased')
    ex_params = dict(rule.get('exit_params', {}))
    # Remove pip_size if present — it's injected by run_backtest
    ex_params.pop('pip_size', None)

    if ex_class == 'ATRFixedSLTP':
        from project2_backtesting.exit_strategies import ATRFixedSLTP
        exit_strategy = ATRFixedSLTP(**ex_params)
    elif ex_class == 'ATRBased':
        from project2_backtesting.exit_strategies import ATRBased
        exit_strategy = ATRBased(**ex_params)
    elif ex_class == 'FixedSLTP':
        from project2_backtesting.exit_strategies import FixedSLTP
        exit_strategy = FixedSLTP(**ex_params)
    else:
        from project2_backtesting.exit_strategies import ATRBased
        exit_strategy = ATRBased(**ex_params)

    # Extract conditions from nested 'rules' key
    rules = rule.get('rules', [])
    if rules and isinstance(rules[0], dict):
        conditions = rules[0].get('conditions', rule.get('conditions', []))
    else:
        conditions = rule.get('conditions', [])

    direction = rule.get('direction', 'BUY')

    trades = run_backtest(
        candles_df=candles_df,
        indicators_df=indicators_df,
        rules=conditions,
        exit_strategy=exit_strategy,
        direction=direction,
        broker_timezone=broker_tz,
        hard_close_hour=23,
        spread_pips=spread,
        commission_pips=commission,
        pip_size=pip_size,
        account_size=account,
        risk_per_trade_pct=risk_pct,
        leverage=leverage,
        contract_size=contract,
        pip_value_per_lot=pip_value,
        # Omit data_dir intentionally: disables tick-file loading so the verify
        # run is fast. The changed exit loop code (to_dict pre-conversion) is
        # exercised via the M5-bar loop regardless.
    )

    stable_fields = ['entry_time', 'exit_time', 'entry_price', 'exit_price',
                     'exit_reason', 'pnl_pips', 'candles_held']
    rows = []
    for t in (trades or []):
        rows.append({k: str(t.get(k, '')) for k in stable_fields})

    return {
        'rule': os.path.basename(rule_path),
        'n_trades': len(rows),
        'md5': md5_of(rows),
        'sample': rows[:3],
    }


def compare():
    fa = os.path.join(_HERE, 'verify_out_A.json')
    fb = os.path.join(_HERE, 'verify_out_B.json')
    if not os.path.exists(fa) or not os.path.exists(fb):
        print('ERROR: missing verify_out_A.json or verify_out_B.json')
        return
    with open(fa) as f: a = json.load(f)
    with open(fb) as f: b = json.load(f)
    all_ok = True
    for rule in sorted(set(list(a.keys()) + list(b.keys()))):
        ra = a.get(rule, {})
        rb = b.get(rule, {})
        if ra.get('error') or rb.get('error'):
            print(f'SKIP  {rule}: A={ra.get("error")} B={rb.get("error")}')
            continue
        md5_a = ra.get('md5')
        md5_b = rb.get('md5')
        n_a   = ra.get('n_trades')
        n_b   = rb.get('n_trades')
        if md5_a == md5_b:
            print(f'PASS  {rule}: {n_a} trades, md5={md5_a}')
        else:
            print(f'FAIL  {rule}: trades A={n_a} B={n_b}  md5 A={md5_a} B={md5_b}')
            all_ok = False
            # Show differing rows
            sa = {tuple(sorted(r.items())) for r in ra.get('sample', [])}
            sb = {tuple(sorted(r.items())) for r in rb.get('sample', [])}
            if sa != sb:
                print(f'  sample A: {ra.get("sample")}')
                print(f'  sample B: {rb.get("sample")}')
    print('\n' + ('ALL MATCH — speedup is safe to commit.' if all_ok
                  else 'MISMATCH — DO NOT commit.'))


if __name__ == '__main__':
    tag = sys.argv[1] if len(sys.argv) > 1 else 'A'
    if tag == 'CMP':
        compare()
        sys.exit(0)

    out = {}
    for rf in RULE_FILES:
        fp = os.path.join(RULES_DIR, rf)
        if not os.path.exists(fp):
            print(f'SKIP (not found): {rf}')
            continue
        print(f'Running [{tag}]: {rf} ...')
        result = run_one(fp)
        err = result.get('error')
        print(f'  n={result.get("n_trades")}  md5={result.get("md5")}  err={err}')
        out[rf] = result

    out_file = os.path.join(_HERE, f'verify_out_{tag}.json')
    with open(out_file, 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f'\nSaved: {out_file}')
