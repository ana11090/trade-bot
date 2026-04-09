"""
Prop Firm Tester — tests backtested strategies against prop firm rules.

Takes trades from strategy_backtester output and runs them through
the prop firm lifecycle simulator to compute pass rates and expected ROI.
"""

import os
import json
import csv
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, '..'))

BACKTEST_MATRIX_PATH = os.path.join(_HERE, 'outputs', 'backtest_matrix.json')

# WHY: backtest_matrix.json can be 40+ MB. Loading it every time freezes the GUI.
#      Cache the parsed result and only reload if the file changes (mtime check).
# CHANGED: April 2026 — add caching to prevent GUI freeze
_strategy_list_cache = None
_cache_mtime = None


def load_strategy_list():
    """
    Load the list of tested strategies from backtest_matrix.json.
    Returns list of dicts with: rule_combo, exit_strategy, stats summary, trade_count.
    Returns None if file doesn't exist.

    WHY: Caches the result to avoid re-parsing the 43MB JSON file on every panel open.
    CHANGED: April 2026 — add mtime-based caching
    """
    global _strategy_list_cache, _cache_mtime

    if not os.path.exists(BACKTEST_MATRIX_PATH):
        return None

    # Check if cached version is still valid
    current_mtime = os.path.getmtime(BACKTEST_MATRIX_PATH)
    if _strategy_list_cache is not None and _cache_mtime == current_mtime:
        return _strategy_list_cache

    # Load and parse (this is slow for 43MB files)
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

    # Update cache
    _strategy_list_cache = strategies
    _cache_mtime = current_mtime

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
    # WHY: Old code used .capitalize() which works for BUY/buy/Buy/SELL/sell
    #      but silently mangles LONG, L, B, "BUY " (trailing space), etc.,
    #      into nonsense strings that the simulator treats as unknown.
    #      Fix: explicit normalization with allowlist + fallback warning.
    # CHANGED: April 2026 — robust direction normalization (audit LOW)
    def _normalize_direction(raw):
        if not raw:
            return "Buy"
        s = str(raw).strip().upper()
        if s in ("BUY", "LONG", "L", "B"):
            return "Buy"
        if s in ("SELL", "SHORT", "S"):
            return "Sell"
        print(f"  [prop_tester] WARNING: unknown direction '{raw}' — defaulting to Buy")
        return "Buy"

    rows = []
    for t in trades:
        rows.append({
            "Open Date":  str(t.get("entry_time", "")),
            "Close Date": str(t.get("exit_time", "")),
            "Action":     _normalize_direction(t.get("direction")),
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
    # WHY: Old default was 10.0 which is correct for forex majors but
    #      WRONG for XAUUSD ($1/pip/lot). Since the project's primary
    #      instrument is XAUUSD, default to 1.0. Forex callers must
    #      explicitly override.
    # CHANGED: April 2026 — XAUUSD-correct default (audit MED — Family #1)
    pip_value_per_lot=1.0,
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
    # WHY: See run_prop_test — XAUUSD-correct default.
    # CHANGED: April 2026 — XAUUSD-correct default (audit MED — Family #1)
    pip_value_per_lot=1.0,
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


def _fmt_hold_time(minutes):
    """Format hold time in minutes to human-readable string like '2h 15m' or '45m'."""
    if minutes is None:
        return ""
    minutes = int(round(minutes))
    if minutes >= 60:
        h = minutes // 60
        m = minutes % 60
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{minutes}m"


def export_trades_csv(trades, filepath, account_size=None):
    """
    Export all individual trades to a CSV file with full details.

    Columns: Trade #, Entry Time, Exit Time, Direction, Entry Price, Exit Price,
             Gross Pips, Spread Cost, Commission Cost, Net Pips,
             Profit/Loss $ (if account_size provided), P&L %,
             Hold Time (min), Hold Time (readable), Exit Reason, Rule ID
    """
    rows = []
    for i, t in enumerate(trades, 1):
        entry_str = str(t.get('entry_time', ''))
        exit_str  = str(t.get('exit_time', ''))
        gross     = t.get('pnl_pips', 0)
        spread    = t.get('cost_pips', 0)
        commission = t.get('cost_pips', 0) - spread if 'commission_pips' in t else 0
        net       = t.get('net_pips', 0)
        dollar_pnl = t.get('dollar_pnl')
        candles   = t.get('candles_held', 0)
        # WHY: Hold time depends on the entry TF candle duration.
        #      M5 candle = 5 min, H1 = 60 min, H4 = 240 min.
        try:
            from project2_backtesting.panels.configuration import TF_MINUTES, load_config
            _cfg = load_config()
            _tf = _cfg.get('winning_scenario', 'H1')
            candle_min = TF_MINUTES.get(_tf, 60)
        except Exception:
            candle_min = 60
        hold_min  = candles * candle_min if candles else None

        pnl_pct = None
        if dollar_pnl is not None and account_size:
            pnl_pct = round(dollar_pnl / account_size * 100, 4)

        rows.append({
            'Trade #':         i,
            'Entry Time':      entry_str,
            'Exit Time':       exit_str,
            'Direction':       t.get('direction', ''),
            'Entry Price':     t.get('entry_price', ''),
            'Exit Price':      t.get('exit_price', ''),
            'Gross Pips':      gross,
            'Spread Cost':     spread,
            'Commission Cost': commission,
            'Net Pips':        net,
            'Profit/Loss $':   dollar_pnl if dollar_pnl is not None else '',
            'P&L %':           pnl_pct if pnl_pct is not None else '',
            'Hold Time (min)': hold_min if hold_min is not None else '',
            'Hold Time':       _fmt_hold_time(hold_min),
            'Exit Reason':     t.get('exit_reason', ''),
            'Rule ID':         t.get('rule_id', ''),
        })

    if not rows:
        return

    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
