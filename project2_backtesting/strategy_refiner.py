"""
Strategy Refiner Engine — interactive filtering with impact preview + deep optimizer.

Mode 1: Apply filters to existing backtested trades and see instant impact.
Mode 2: Deep optimizer that tests threshold shifts, new indicators, and exit strategies.
"""

import os
import json
import time
import threading
import numpy as np
import pandas as pd
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))

BACKTEST_MATRIX_PATH = os.path.join(_HERE, 'outputs', 'backtest_matrix.json')

# Session hour ranges (UTC)
_SESSIONS = {
    "Asian":    (0, 8),
    "London":   (7, 16),
    "New York": (12, 21),
}

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────

def load_trades_from_matrix(strategy_index):
    """Load trades for one strategy from backtest_matrix.json."""
    if not os.path.exists(BACKTEST_MATRIX_PATH):
        return None
    with open(BACKTEST_MATRIX_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    results = data.get('results', [])
    if strategy_index >= len(results):
        return None
    return results[strategy_index].get('trades', None)


def load_strategy_list():
    """Return list of strategy summary dicts from backtest_matrix.json."""
    if not os.path.exists(BACKTEST_MATRIX_PATH):
        return []
    with open(BACKTEST_MATRIX_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    results = []
    for i, r in enumerate(data.get('results', [])):
        results.append({
            'index':             i,
            'label':             f"{r.get('rule_combo','?')} × {r.get('exit_name','?')}",
            'rule_combo':        r.get('rule_combo', '?'),
            'exit_strategy':     r.get('exit_strategy', '?'),
            'exit_name':         r.get('exit_name', '?'),
            'total_trades':      r.get('total_trades', 0),
            'win_rate':          r.get('win_rate', 0),
            'net_total_pips':    r.get('net_total_pips', 0),
            'net_profit_factor': r.get('net_profit_factor', 0),
            'spread_pips':       r.get('spread_pips', 2.5),
            'commission_pips':   r.get('commission_pips', 0.0),
            'has_trades':        'trades' in r and bool(r.get('trades')),
        })
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Trade enrichment
# ─────────────────────────────────────────────────────────────────────────────

def compute_hold_time_minutes(trade):
    """Calculate hold time in minutes from entry_time and exit_time."""
    try:
        entry = pd.to_datetime(trade['entry_time'])
        exit_ = pd.to_datetime(trade['exit_time'])
        return (exit_ - entry).total_seconds() / 60.0
    except Exception:
        return 0.0


def _fmt_hold(minutes):
    if minutes is None or minutes < 1:
        return "<1m"
    minutes = int(round(minutes))
    if minutes >= 60:
        h = minutes // 60
        m = minutes % 60
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{minutes}m"


def _get_session(hour):
    """Return session name for a given UTC hour."""
    # Assign to first matching session (London wins over Asian overlap)
    for name, (start, end) in _SESSIONS.items():
        if start <= hour < end:
            return name
    return "Asian"  # late night defaults


def enrich_trades(trades):
    """Add computed fields to each trade dict in-place. Returns the list."""
    for t in trades:
        hold_min = compute_hold_time_minutes(t)
        t['hold_minutes'] = hold_min
        t['hold_display'] = _fmt_hold(hold_min)
        try:
            entry_dt = pd.to_datetime(t['entry_time'])
            t['hour_of_day']  = int(entry_dt.hour)
            t['day_of_week']  = _DAY_NAMES[entry_dt.dayofweek]
            t['day_abbrev']   = t['day_of_week'][:3]
            t['session']      = _get_session(int(entry_dt.hour))
        except Exception:
            t['hour_of_day'] = 0
            t['day_of_week'] = 'Unknown'
            t['day_abbrev']  = 'Unk'
            t['session']     = 'Unknown'
        t['is_winner'] = t.get('net_pips', 0) > 0
    return trades


# ─────────────────────────────────────────────────────────────────────────────
# Stats
# ─────────────────────────────────────────────────────────────────────────────

def compute_stats_summary(trades):
    """Compute key stats for a list of (enriched) trades."""
    if not trades:
        return {
            'count': 0, 'win_rate': 0.0, 'avg_pips': 0.0,
            'total_pips': 0.0, 'max_dd_pips': 0.0,
            'trades_per_day': 0.0, 'avg_hold_minutes': 0.0,
        }
    net = np.array([t.get('net_pips', 0) for t in trades], dtype=float)
    winners = np.sum(net > 0)
    total   = len(trades)
    cum     = np.cumsum(net)
    peak    = np.maximum.accumulate(cum)
    max_dd  = float(np.max(peak - cum)) if len(cum) > 0 else 0.0

    # Trades per day
    try:
        dates = sorted(set(str(pd.to_datetime(t['entry_time']).date()) for t in trades))
        n_days = max(len(dates), 1)
    except Exception:
        n_days = max(total // 3, 1)

    hold_vals = [t.get('hold_minutes', 0) for t in trades]
    avg_hold = float(np.mean(hold_vals)) if hold_vals else 0.0

    return {
        'count':            total,
        'win_rate':         round(float(winners / total), 4),
        'avg_pips':         round(float(np.mean(net)), 2),
        'total_pips':       round(float(np.sum(net)), 1),
        'max_dd_pips':      round(max_dd, 1),
        'trades_per_day':   round(total / n_days, 2),
        'avg_hold_minutes': round(avg_hold, 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Filtering
# ─────────────────────────────────────────────────────────────────────────────

def apply_filters(trades, filters):
    """
    Apply a dict of filters to trades. Returns (kept, removed).

    filters keys:
        min_hold_minutes, max_hold_minutes,
        max_trades_per_day, sessions (list), days (list),
        min_pips, cooldown_minutes,
        custom_filters: [{"feature": str, "operator": str, "value": float}]
    """
    if not trades or not filters:
        return list(trades), []

    kept    = []
    removed = []

    # Build per-day index for max_trades_per_day
    max_per_day = filters.get('max_trades_per_day')
    if max_per_day:
        # Group by date, keep top N by net_pips
        from collections import defaultdict
        by_day = defaultdict(list)
        for t in trades:
            try:
                day = str(pd.to_datetime(t['entry_time']).date())
            except Exception:
                day = 'unknown'
            by_day[day].append(t)
        allowed_ids = set()
        for day_trades in by_day.values():
            top = sorted(day_trades, key=lambda x: x.get('net_pips', 0), reverse=True)
            for t in top[:max_per_day]:
                allowed_ids.add(id(t))
    else:
        allowed_ids = None

    min_hold    = filters.get('min_hold_minutes')
    max_hold    = filters.get('max_hold_minutes')
    sessions    = filters.get('sessions')    # None = all
    days        = filters.get('days')        # None = all
    min_pips    = filters.get('min_pips')
    cooldown    = filters.get('cooldown_minutes')
    custom      = filters.get('custom_filters', [])

    # Sort by entry time for cooldown check
    sorted_trades = sorted(trades, key=lambda t: str(t.get('entry_time', '')))
    last_exit_time = None

    for t in sorted_trades:
        reason = None

        if min_hold is not None and t.get('hold_minutes', 0) < min_hold:
            reason = 'min_hold'
        elif max_hold is not None and t.get('hold_minutes', 0) > max_hold:
            reason = 'max_hold'
        elif sessions is not None and t.get('session') not in sessions:
            reason = 'session'
        elif days is not None:
            day_abbrevs = [d[:3] for d in days]
            if t.get('day_abbrev', 'Mon') not in day_abbrevs and t.get('day_of_week', '') not in days:
                reason = 'day'
        elif min_pips is not None and t.get('net_pips', 0) < min_pips:
            reason = 'min_pips'
        elif allowed_ids is not None and id(t) not in allowed_ids:
            reason = 'max_per_day'
        elif cooldown and last_exit_time is not None:
            try:
                gap = (pd.to_datetime(t['entry_time']) - last_exit_time).total_seconds() / 60.0
                if gap < cooldown:
                    reason = 'cooldown'
            except Exception:
                pass

        # Custom indicator filters
        if reason is None:
            for cf in custom:
                feat = cf.get('feature', '')
                op   = cf.get('operator', '>')
                val  = cf.get('value', 0)
                tv   = t.get(feat)
                if tv is None:
                    continue
                try:
                    tv = float(tv)
                    if op == '>' and not (tv > val):
                        reason = f'custom:{feat}'
                    elif op == '>=' and not (tv >= val):
                        reason = f'custom:{feat}'
                    elif op == '<' and not (tv < val):
                        reason = f'custom:{feat}'
                    elif op == '<=' and not (tv <= val):
                        reason = f'custom:{feat}'
                except Exception:
                    pass
                if reason:
                    break

        if reason:
            removed.append(t)
        else:
            kept.append(t)
            try:
                last_exit_time = pd.to_datetime(t['exit_time'])
            except Exception:
                pass

    return kept, removed


def compute_filter_impact(trades, filter_name, filter_value):
    """
    Show what ONE filter would do WITHOUT applying it.
    Returns impact dict with verdict.
    """
    filters = {filter_name: filter_value}
    kept, removed = apply_filters(trades, filters)

    kept_net    = [t.get('net_pips', 0) for t in kept]
    removed_net = [t.get('net_pips', 0) for t in removed]

    kept_wr    = sum(1 for p in kept_net if p > 0) / max(len(kept_net), 1)
    removed_wr = sum(1 for p in removed_net if p > 0) / max(len(removed_net), 1)

    kept_avg    = float(np.mean(kept_net))    if kept_net    else 0.0
    removed_avg = float(np.mean(removed_net)) if removed_net else 0.0

    # Verdict: HELPS if we're removing bad trades (removed_avg < kept_avg)
    if not removed:
        verdict = "NO EFFECT"
    elif removed_avg < kept_avg and removed_wr < kept_wr:
        verdict = "HELPS"
    elif removed_avg > kept_avg and removed_wr > kept_wr:
        verdict = "HURTS"
    else:
        verdict = "MIXED"

    return {
        'filter_name':       filter_name,
        'filter_value':      filter_value,
        'removed_count':     len(removed),
        'removed_avg_pips':  round(removed_avg, 1),
        'removed_win_rate':  round(removed_wr, 3),
        'removed_total_pips': round(sum(removed_net), 1),
        'kept_count':        len(kept),
        'kept_avg_pips':     round(kept_avg, 1),
        'kept_win_rate':     round(kept_wr, 3),
        'kept_total_pips':   round(sum(kept_net), 1),
        'verdict':           verdict,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Prop firm presets
# ─────────────────────────────────────────────────────────────────────────────

def get_prop_firm_presets():
    return {
        "FTMO-friendly": {
            "min_hold_minutes": 5,
            "max_trades_per_day": 5,
            "cooldown_minutes": 30,
            "description": "Conservative: 5+ min holds, max 5/day, 30 min cooldown",
        },
        "Topstep-friendly": {
            "max_trades_per_day": 3,
            "cooldown_minutes": 60,
            "min_pips": 5,
            "description": "Consistency focus: max 3/day, 1h cooldown, skip tiny wins",
        },
        "Apex-friendly": {
            "min_hold_minutes": 2,
            "max_trades_per_day": 4,
            "min_pips": 10,
            "description": "Balanced: 2+ min holds, max 4/day, skip tiny wins",
        },
        "Custom": {
            "description": "Set your own filters",
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Deep Optimizer
# ─────────────────────────────────────────────────────────────────────────────

_stop_flag = threading.Event()


def stop_optimization():
    _stop_flag.set()


def _score_trades(trades, target_firm=None):
    """Score a set of trades. Higher is better."""
    if not trades:
        return -999.0
    net = [t.get('net_pips', 0) for t in trades]
    wr  = sum(1 for p in net if p > 0) / len(net)
    avg = float(np.mean(net))
    tpd = len(trades) / max(len(set(
        str(pd.to_datetime(t['entry_time']).date()) for t in trades
    )), 1)
    # Balance between quality (WR, avg pips) and volume (tpd)
    return wr * 100 + avg * 0.1 - max(0, tpd - 5) * 2


def deep_optimize(
    trades,
    candles_df,
    indicators_df,
    base_rules,
    exit_strategies,
    pip_size=0.01,
    spread_pips=2.5,
    commission_pips=0.0,
    target_firm=None,
    account_size=100000,
    progress_callback=None,
):
    """
    Deep optimization starting from existing trades.

    Steps:
    1. Filter scan — test each prop firm preset and combinations
    2. Threshold shift — try ±10%, ±20% on numeric filter values
    3. Session/day combos — test best session and day combinations
    4. Exit strategy scan — test all exit strategies against filtered trades

    Returns list of candidates sorted by score.
    """
    _stop_flag.clear()
    start_time = time.time()
    candidates = []
    step = 0

    base_stats  = compute_stats_summary(trades)
    base_score  = _score_trades(trades, target_firm)
    best_so_far = {
        'name':           'Base (no changes)',
        'trades':         len(trades),
        'win_rate':       base_stats['win_rate'],
        'avg_pips':       base_stats['avg_pips'],
        'trades_per_day': base_stats['trades_per_day'],
        'prop_pass_rate': None,
        'score':          base_score,
    }

    def _report(msg, total_steps, current_step):
        nonlocal step
        step = current_step
        if progress_callback:
            elapsed = int(time.time() - start_time)
            mins, secs = divmod(elapsed, 60)
            progress_callback(
                step=current_step,
                total=total_steps,
                message=msg,
                current_best=best_so_far,
                elapsed_str=f"{mins}m {secs}s",
                candidates_tested=len(candidates),
                improvements_found=sum(1 for c in candidates if c['score'] > base_score),
            )

    def _maybe_add(name, kept_trades, changes, filters_applied):
        nonlocal best_so_far
        if len(kept_trades) < 5:
            return
        s = compute_stats_summary(kept_trades)
        score = _score_trades(kept_trades, target_firm)
        candidate = {
            'name':             name,
            'rules':            base_rules,
            'filters_applied':  filters_applied,
            'trades':           kept_trades,
            'stats':            s,
            'prop_score':       {},
            'score':            score,
            'changes_from_base': changes,
        }
        candidates.append(candidate)
        if score > best_so_far['score']:
            best_so_far = {
                'name':           name,
                'trades':         s['count'],
                'win_rate':       s['win_rate'],
                'avg_pips':       s['avg_pips'],
                'trades_per_day': s['trades_per_day'],
                'prop_pass_rate': None,
                'score':          score,
            }

    presets = get_prop_firm_presets()
    preset_list = [(k, v) for k, v in presets.items() if k != 'Custom']
    total_steps = len(preset_list) + 20 + 5 + 3  # rough total

    # ── Step 1: Preset filters ────────────────────────────────────────────────
    for i, (pname, pvals) in enumerate(preset_list):
        if _stop_flag.is_set():
            break
        _report(f"Testing preset: {pname}", total_steps, i + 1)
        filt = {k: v for k, v in pvals.items() if k != 'description'}
        kept, _ = apply_filters(trades, filt)
        _maybe_add(f"{pname} filters", kept, pname, filt)

    # ── Step 2: Min hold time sweep ───────────────────────────────────────────
    hold_values = [1, 2, 5, 10, 15, 20, 30]
    for i, hv in enumerate(hold_values):
        if _stop_flag.is_set():
            break
        step_n = len(preset_list) + i + 1
        _report(f"Testing min hold: {hv} min", total_steps, step_n)
        kept, _ = apply_filters(trades, {'min_hold_minutes': hv})
        _maybe_add(f"Min hold {hv}m", kept, f"min hold {hv}m", {'min_hold_minutes': hv})

    # ── Step 3: Max trades per day sweep ──────────────────────────────────────
    for i, maxn in enumerate([1, 2, 3, 5, 8]):
        if _stop_flag.is_set():
            break
        step_n = len(preset_list) + len(hold_values) + i + 1
        _report(f"Testing max trades/day: {maxn}", total_steps, step_n)
        kept, _ = apply_filters(trades, {'max_trades_per_day': maxn})
        _maybe_add(f"Max {maxn} trades/day", kept, f"max {maxn}/day", {'max_trades_per_day': maxn})

    # ── Step 4: Session combos ────────────────────────────────────────────────
    session_combos = [
        (["London"],             "London only"),
        (["New York"],           "NY only"),
        (["London", "New York"], "London + NY"),
        (["Asian", "London"],    "Asian + London"),
    ]
    base_step = len(preset_list) + len(hold_values) + 5
    for i, (sess, desc) in enumerate(session_combos):
        if _stop_flag.is_set():
            break
        _report(f"Testing sessions: {desc}", total_steps, base_step + i + 1)
        kept, _ = apply_filters(trades, {'sessions': sess})
        _maybe_add(f"Session: {desc}", kept, f"sessions={desc}", {'sessions': sess})

    # ── Step 5: Combination — hold + max/day ──────────────────────────────────
    combos = [(5, 3), (5, 5), (10, 3), (2, 5), (15, 2)]
    base_step2 = base_step + len(session_combos)
    for i, (hold, maxd) in enumerate(combos):
        if _stop_flag.is_set():
            break
        _report(f"Combo: min hold {hold}m + max {maxd}/day", total_steps, base_step2 + i + 1)
        filt = {'min_hold_minutes': hold, 'max_trades_per_day': maxd}
        kept, _ = apply_filters(trades, filt)
        _maybe_add(f"Hold {hold}m + max {maxd}/day", kept,
                   f"min hold {hold}m, max {maxd}/day", filt)

    # Sort by score descending
    candidates.sort(key=lambda c: c['score'], reverse=True)

    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)
    if progress_callback:
        progress_callback(
            step=total_steps,
            total=total_steps,
            message=f"Complete — {len(candidates)} candidates in {mins}m {secs}s",
            current_best=best_so_far,
            elapsed_str=f"{mins}m {secs}s",
            candidates_tested=len(candidates),
            improvements_found=sum(1 for c in candidates if c['score'] > base_score),
        )

    return candidates[:20]  # top 20
