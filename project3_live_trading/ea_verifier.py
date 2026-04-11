"""
EA Verifier — compares EA trade log CSV to backtest trade list.

Detects matched trades, missed trades, extra trades, slippage, and P&L drift.
Used by the Live Monitor panel to show how well the EA reproduces backtested behaviour.
"""

import os
import csv
import json
import numpy as np
from datetime import datetime


def load_ea_log(ea_log_path):
    """
    Load EA trade log CSV.
    Expected columns (EA-generated):
      timestamp, symbol, direction, lots, entry_price, exit_price, net_pips, exit_reason,
      entry_time, exit_time, sl, tp, magic, rule_id, skip_reason (if skipped)

    Returns list of trade dicts.
    """
    if not os.path.exists(ea_log_path):
        raise FileNotFoundError(f"EA log not found: {ea_log_path}")

    trades = []
    with open(ea_log_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(dict(row))
    return trades


def _parse_dt(val):
    """Try to parse various datetime string formats."""
    if not val:
        return None
    for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M',
                '%Y-%m-%dT%H:%M', '%Y.%m.%d %H:%M:%S', '%Y.%m.%d %H:%M']:
        try:
            return datetime.strptime(str(val).strip(), fmt)
        except ValueError:
            continue
    return None


def verify_ea_trades(ea_log_path, backtest_trades, tolerance_minutes=90,
                     pip_size=0.01):
    """
    Compare EA's logged trades to backtest predictions.

    For each backtest trade, find the matching EA trade within ±tolerance_minutes
    of entry time (same direction required).

    Parameters
    ----------
    ea_log_path       : str   — path to EA-generated trade log CSV
    backtest_trades   : list  — trade dicts from backtest_matrix.json
    tolerance_minutes : int   — entry time tolerance for matching
    pip_size          : float — pip size in price units (default 0.01 for
                                XAUUSD/JPY pairs; use 0.0001 for forex majors,
                                1.0 for indices). Wrong value gives slippage
                                100× off between 0.01 and 0.0001 instruments.

    WHY: Old code hardcoded pip_size=0.01 in the slippage calc.
         XAUUSD-only. Forex pairs showed 100× wrong slippage.
    CHANGED: April 2026 — parameterize pip_size (audit HIGH — Family #1)

    Returns
    -------
    dict with:
      summary: matched/missed/extra counts, avg_slippage, pnl_diff
      matched_trades: list of {backtest, ea, slippage_pips, pnl_diff}
      missed_trades: list of {backtest, skip_reason}
      extra_trades: list of {ea}
      verdict: str — 'EXCELLENT' / 'GOOD' / 'POOR'
      match_rate: float (0-1)
    """
    try:
        ea_trades = load_ea_log(ea_log_path)
    except FileNotFoundError as e:
        return {'error': str(e), 'verdict': 'ERROR'}

    # Separate actual EA trades from skipped signals
    ea_actual  = [t for t in ea_trades if not t.get('skip_reason') and t.get('entry_price')]
    ea_skipped = [t for t in ea_trades if t.get('skip_reason')]

    # Index EA trades by (entry_dt, direction)
    ea_indexed = []
    for t in ea_actual:
        dt = _parse_dt(t.get('entry_time') or t.get('timestamp'))
        ea_indexed.append({'dt': dt, 'trade': t, 'matched': False})

    matched_trades = []
    missed_trades  = []

    for bt in backtest_trades:
        bt_dt  = _parse_dt(bt.get('entry_time'))
        bt_dir = str(bt.get('direction', '')).upper()

        if bt_dt is None:
            continue

        # Find best matching EA trade
        best_match = None
        best_diff  = None
        for ea_entry in ea_indexed:
            if ea_entry['matched']:
                continue
            if ea_entry['dt'] is None:
                continue
            ea_dir = str(ea_entry['trade'].get('direction', '')).upper()
            if ea_dir != bt_dir:
                continue
            diff = abs((ea_entry['dt'] - bt_dt).total_seconds() / 60.0)
            if diff <= tolerance_minutes:
                if best_diff is None or diff < best_diff:
                    best_match = ea_entry
                    best_diff  = diff

        if best_match:
            best_match['matched'] = True
            ea_t = best_match['trade']

            # Calculate slippage
            try:
                bt_entry  = float(bt.get('entry_price', 0))
                ea_entry_ = float(ea_t.get('entry_price', 0))
                # WHY: Uses per-symbol pip_size parameter. Old code
                #      hardcoded 0.01 which was XAUUSD-only.
                # CHANGED: April 2026 — per-symbol pip_size (audit HIGH)
                slippage  = abs(ea_entry_ - bt_entry) / pip_size if pip_size > 0 else 0.0
            except Exception:
                slippage = 0.0

            # P&L difference
            try:
                bt_pnl = float(bt.get('net_pips', 0))
                ea_pnl = float(ea_t.get('net_pips', 0))
                pnl_diff = ea_pnl - bt_pnl
            except Exception:
                pnl_diff = 0.0

            matched_trades.append({
                'backtest':     bt,
                'ea':           ea_t,
                'slippage_pips': round(slippage, 2),
                'pnl_diff':     round(pnl_diff, 2),
                'time_diff_min': round(best_diff, 1),
            })
        else:
            # Find skip reason if the signal was logged but skipped
            # WHY: Old code matched skips by time only. A SELL skip 30 min
            #      after a missed BUY signal would be mis-attributed as the
            #      "reason" for the BUY miss. Now also require direction
            #      match — a skip only counts if it has the same BUY/SELL
            #      direction as the backtest signal. Skips with no direction
            #      field (generic, like "daily_dd_hit") still match any
            #      direction.
            # CHANGED: April 2026 — direction-aware skip match (audit MED)
            skip_reason = 'unknown'
            for sk in ea_skipped:
                sk_dt = _parse_dt(sk.get('timestamp') or sk.get('entry_time'))
                if sk_dt is None or bt_dt is None:
                    continue
                # Direction match (allow missing direction — generic skips)
                sk_dir = str(sk.get('direction', '')).upper()
                if sk_dir and sk_dir != bt_dir:
                    continue
                if abs((sk_dt - bt_dt).total_seconds() / 60.0) <= tolerance_minutes:
                    skip_reason = sk.get('skip_reason', 'unknown')
                    break
            missed_trades.append({'backtest': bt, 'skip_reason': skip_reason})

    # Extra trades: EA trades not matched to any backtest
    extra_trades = [e['trade'] for e in ea_indexed if not e['matched']]

    # Summary stats
    n_bt      = len([b for b in backtest_trades if _parse_dt(b.get('entry_time'))])
    n_matched = len(matched_trades)
    match_rate = n_matched / max(n_bt, 1)

    slippages = [m['slippage_pips'] for m in matched_trades]
    pnl_diffs = [m['pnl_diff'] for m in matched_trades]
    avg_slip  = round(float(np.mean(slippages)), 2) if slippages else 0.0
    avg_pnl_diff = round(float(np.mean(pnl_diffs)), 2) if pnl_diffs else 0.0

    # Verdict
    if match_rate >= 0.90 and avg_slip < 2.0:
        verdict = 'EXCELLENT'
    elif match_rate >= 0.80:
        verdict = 'GOOD'
    else:
        verdict = 'POOR'

    return {
        'summary': {
            'backtest_count':  n_bt,
            'matched_count':   n_matched,
            'missed_count':    len(missed_trades),
            'extra_count':     len(extra_trades),
            'match_rate':      round(match_rate, 4),
            'avg_slippage_pips': avg_slip,
            'avg_pnl_diff':    avg_pnl_diff,
        },
        'matched_trades': matched_trades,
        'missed_trades':  missed_trades,
        'extra_trades':   extra_trades,
        'verdict':        verdict,
        'match_rate':     match_rate,
    }
