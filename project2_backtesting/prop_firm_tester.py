"""
Prop Firm Tester — tests backtested strategies against prop firm rules.

Takes trades from strategy_backtester output and runs them through
the prop firm lifecycle simulator to compute pass rates and expected ROI.
"""

import os
import json
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, '..'))

BACKTEST_MATRIX_PATH = os.path.join(_HERE, 'outputs', 'backtest_matrix.json')


def load_strategy_list():
    """
    Load the list of tested strategies from backtest_matrix.json.
    Returns list of dicts with: rule_combo, exit_strategy, stats summary, trade_count.
    Returns None if file doesn't exist.
    """
    if not os.path.exists(BACKTEST_MATRIX_PATH):
        return None

    with open(BACKTEST_MATRIX_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    results = data.get('results', [])
    strategies = []
    for i, r in enumerate(results):
        strategies.append({
            'index': i,
            'label': f"{r.get('rule_combo', '?')} × {r.get('exit_name', '?')}",
            'rule_combo': r.get('rule_combo', '?'),
            'exit_strategy': r.get('exit_strategy', '?'),
            'exit_name': r.get('exit_name', '?'),
            'total_trades': r.get('total_trades', 0),
            'win_rate': r.get('win_rate', 0),
            'net_total_pips': r.get('net_total_pips', 0),
            'net_profit_factor': r.get('net_profit_factor', 0),
            'has_trades': 'trades' in r and bool(r.get('trades')),
        })
    return strategies


def load_strategy_trades(index):
    """
    Load the individual trades for a specific strategy from backtest_matrix.json.
    Returns list of trade dicts or None.
    """
    if not os.path.exists(BACKTEST_MATRIX_PATH):
        return None

    with open(BACKTEST_MATRIX_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    results = data.get('results', [])
    if index >= len(results):
        return None

    return results[index].get('trades', None)


def convert_trades_for_prop_sim(trades):
    """
    Convert backtester trade format to prop firm simulator format.

    Input:  [{"entry_time": "2023-01-05 10:00", "exit_time": "2023-01-05 14:00",
              "direction": "BUY", "net_pips": 232.0, ...}, ...]

    Output: DataFrame with columns: Open Date, Close Date, Action, Pips, Profit, Lots
            (Profit is a placeholder — the simulator's _rescale_trades recalculates it)
    """
    rows = []
    for t in trades:
        rows.append({
            "Open Date":  str(t.get("entry_time", "")),
            "Close Date": str(t.get("exit_time", "")),
            "Action":     t.get("direction", "Buy").capitalize(),
            "Pips":       t.get("net_pips", 0),
            "Profit":     t.get("net_pips", 0),  # Placeholder — _rescale_trades recalculates
            "Lots":       1.0,  # Placeholder — _rescale_trades recalculates
        })
    return pd.DataFrame(rows)


def load_available_firms():
    """
    Load all prop firm profiles and return structured list of firms + challenges.
    Returns list of dicts: {firm_id, firm_name, challenge_id, challenge_name, account_sizes}
    """
    import sys
    sys.path.insert(0, _ROOT)
    from shared.prop_firm_engine import load_all_firms

    firms = load_all_firms()
    result = []
    for firm_id, firm in firms.items():
        for ch in firm.list_challenges():
            challenge_id = ch['challenge_id']
            result.append({
                'firm_id': firm_id,
                'firm_name': firm.firm_name,
                'challenge_id': challenge_id,
                'challenge_name': ch['challenge_name'],
                'account_sizes': firm.list_account_sizes(challenge_id),
            })
    return result


def _closest_account_size(available_sizes, requested):
    """Return the closest available account size to the requested value."""
    if not available_sizes:
        return requested
    return min(available_sizes, key=lambda s: abs(s - requested))


def run_prop_test(
    trades,
    firm_id,
    challenge_id,
    account_size,
    risk_per_trade_pct=1.0,
    default_sl_pips=150.0,
    pip_value_per_lot=10.0,
    daily_dd_safety_pct=80.0,
):
    """
    Run one strategy's trades through one prop firm challenge.
    Returns SimulationSummary or None.
    """
    import sys
    sys.path.insert(0, _ROOT)
    from shared.prop_firm_simulator import simulate_challenge

    trades_df = convert_trades_for_prop_sim(trades)

    if len(trades_df) < 10:
        print(f"[prop_tester] Too few trades ({len(trades_df)}) for simulation")
        return None

    summary = simulate_challenge(
        trades_df=trades_df,
        firm_id=firm_id,
        challenge_id=challenge_id,
        account_size=account_size,
        mode="sliding_window",
        risk_per_trade_pct=risk_per_trade_pct,
        default_sl_pips=default_sl_pips,
        pip_value_per_lot=pip_value_per_lot,
        daily_dd_safety_pct=daily_dd_safety_pct,
    )

    return summary


def run_multi_firm_test(
    trades,
    firm_challenges,   # list of {firm_id, challenge_id, account_size, firm_name, challenge_name}
    risk_per_trade_pct=1.0,
    default_sl_pips=150.0,
    pip_value_per_lot=10.0,
    daily_dd_safety_pct=80.0,
    progress_callback=None,
):
    """
    Run one strategy against multiple firms/challenges.
    Returns list of result dicts sorted by expected ROI descending.
    """
    results = []
    total = len(firm_challenges)

    for i, fc in enumerate(firm_challenges):
        label = f"{fc.get('firm_name', fc['firm_id'])} — {fc.get('challenge_name', fc['challenge_id'])}"
        if progress_callback:
            progress_callback(i + 1, total, label)

        summary = run_prop_test(
            trades=trades,
            firm_id=fc['firm_id'],
            challenge_id=fc['challenge_id'],
            account_size=fc['account_size'],
            risk_per_trade_pct=risk_per_trade_pct,
            default_sl_pips=default_sl_pips,
            pip_value_per_lot=pip_value_per_lot,
            daily_dd_safety_pct=daily_dd_safety_pct,
        )

        if summary is not None:
            results.append({
                'firm_name': summary.firm_name,
                'challenge_name': summary.challenge_name,
                'account_size': fc['account_size'],
                'pass_rate': summary.eval_pass_rate,
                'pass_count': summary.eval_pass_count,
                'fail_count': summary.eval_fail_count,
                'num_simulations': summary.num_simulations,
                'avg_days_to_pass': summary.eval_avg_days_to_pass,
                'median_days_to_pass': summary.eval_median_days_to_pass,
                'avg_max_dd_pct': summary.eval_avg_max_dd_pct,
                'funded_avg_monthly': summary.funded_avg_monthly_payout,
                'funded_avg_total': summary.funded_avg_total_payouts,
                'funded_survival_3mo': summary.funded_survival_rate_3mo,
                'expected_roi_pct': summary.expected_roi_pct,
                'fail_reasons': summary.eval_fail_reasons,
            })

    results.sort(key=lambda r: r.get('expected_roi_pct') or -999, reverse=True)
    return results
