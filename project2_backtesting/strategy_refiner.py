"""
Strategy Refiner Engine — interactive filtering with impact preview + deep optimizer.

Mode 1: Apply filters to existing backtested trades and see instant impact.
Mode 2: Deep optimizer that tests threshold shifts, new indicators, and exit strategies.
"""

import os
import json
import time
import threading
import copy
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


def compute_monthly_pnl(trades, account_size=100000, risk_pct=1.0, pip_value=10.0):
    """
    Group trades by month, return monthly P&L breakdown with daily trade frequency stats.
    Returns list of dicts: [{month: '2020-01', pnl_pips: +340, trades: 12, wins: 8,
                             avg_trades_per_day: 2.4, min_trades_per_day: 1, max_trades_per_day: 5,
                             pnl_dollars: +2267, pnl_pct: +2.27}, ...]
    """
    # Calculate $ per pip based on risk settings
    sl_pips = 150
    risk_dollars = account_size * (risk_pct / 100)
    lot_size = risk_dollars / (sl_pips * pip_value) if sl_pips * pip_value > 0 else 0.01
    dollar_per_pip = pip_value * lot_size

    monthly = {}
    for t in trades:
        try:
            dt = pd.to_datetime(t.get('entry_time', ''))
            key = dt.strftime('%Y-%m')
            day = dt.strftime('%Y-%m-%d')
        except Exception:
            continue

        if key not in monthly:
            monthly[key] = {'month': key, 'pnl_pips': 0, 'trades': 0, 'wins': 0, 'losses': 0, 'daily_counts': {}}

        pnl = t.get('net_pips', 0)
        monthly[key]['pnl_pips'] += pnl
        monthly[key]['trades'] += 1
        if pnl > 0:
            monthly[key]['wins'] += 1
        else:
            monthly[key]['losses'] += 1

        monthly[key]['daily_counts'][day] = monthly[key]['daily_counts'].get(day, 0) + 1

    # Compute daily trade frequency stats and profit %
    for m in monthly.values():
        counts = list(m['daily_counts'].values()) if m['daily_counts'] else [0]
        m['trading_days'] = len(m['daily_counts'])
        m['avg_trades_per_day'] = round(m['trades'] / max(m['trading_days'], 1), 1)
        m['min_trades_per_day'] = min(counts) if counts else 0
        m['max_trades_per_day'] = max(counts) if counts else 0
        del m['daily_counts']

        # Profit as % of account
        m['pnl_dollars'] = round(m['pnl_pips'] * dollar_per_pip, 2)
        m['pnl_pct'] = round((m['pnl_dollars'] / account_size) * 100, 2)

    return sorted(monthly.values(), key=lambda x: x['month'])


def compute_three_drawdowns(trades, account_size=100000, risk_pct=1.0, pip_value=10.0,
                             daily_reset_hour=0):
    """
    Compute three types of drawdown:

    1. Floating DD (intra-trade): worst equity drop DURING open trades
       - Includes unrealized P&L from highest_since_entry / lowest_since_entry

    2. Realized DD (trade-to-trade): worst equity drop between closed trade results
       - Standard: cumulative P&L peak to trough

    3. End-of-Day DD: worst equity drop measured at end of each trading day
       - This is what prop firms actually measure
       - Most important for passing challenges

    Returns dict with all three DD values in pips and % of account.
    """
    if not trades:
        return {
            'floating_dd_pips': 0, 'floating_dd_pct': 0,
            'realized_dd_pips': 0, 'realized_dd_pct': 0,
            'eod_dd_pips': 0, 'eod_dd_pct': 0,
            'daily_dd_worst_pips': 0, 'daily_dd_worst_pct': 0,
            'daily_dd_worst_date': None,
        }

    net_pips = [t.get('net_pips', 0) for t in trades]

    # ── 1. Realized DD (standard: closed trade equity curve) ──
    cum = np.cumsum(net_pips)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    realized_dd_pips = float(dd.max())

    # Convert to account %
    # Each pip value depends on lot size. Approximate:
    sl_pips = 150  # default assumption
    risk_dollars = account_size * (risk_pct / 100)
    lot_size = risk_dollars / (sl_pips * pip_value) if sl_pips * pip_value > 0 else 0.01
    realized_dd_dollars = realized_dd_pips * pip_value * lot_size
    realized_dd_pct = (realized_dd_dollars / account_size) * 100

    # ── 2. Floating DD (intra-trade: includes unrealized P&L) ──
    floating_dd_pips = realized_dd_pips  # start with realized

    # If trades have highest/lowest since entry, we can compute floating DD
    equity = 0
    equity_peak = 0
    worst_floating = 0

    for t in trades:
        # Worst point during this trade
        if t.get('direction') == 'BUY':
            worst_during = t.get('lowest_since_entry', t.get('entry_price', 0))
            entry = t.get('entry_price', 0)
            if entry > 0 and worst_during > 0:
                worst_unrealized = (worst_during - entry) / 0.01  # pip_size
                temp_equity = equity + worst_unrealized
                if equity_peak - temp_equity > worst_floating:
                    worst_floating = equity_peak - temp_equity

        # After trade closes
        pnl = t.get('net_pips', 0)
        equity += pnl
        equity_peak = max(equity_peak, equity)

    floating_dd_pips = max(realized_dd_pips, worst_floating)
    floating_dd_dollars = floating_dd_pips * pip_value * lot_size
    floating_dd_pct = (floating_dd_dollars / account_size) * 100

    # ── 3. End-of-Day DD (what prop firms measure) ──
    # Group trades by day, compute daily equity at close
    daily_equity = {}
    running_equity = 0

    for t in trades:
        try:
            dt = pd.to_datetime(t.get('exit_time', t.get('entry_time', '')))
            day = dt.strftime('%Y-%m-%d')
        except Exception:
            continue

        pnl = t.get('net_pips', 0)
        running_equity += pnl
        daily_equity[day] = running_equity  # last trade of the day sets EOD equity

    if daily_equity:
        days = sorted(daily_equity.keys())
        eod_values = [daily_equity[d] for d in days]
        eod_cum = np.array(eod_values)
        eod_peak = np.maximum.accumulate(eod_cum)
        eod_dd = eod_peak - eod_cum
        eod_dd_pips = float(eod_dd.max())
        worst_day_idx = int(eod_dd.argmax())
        worst_day = days[worst_day_idx] if worst_day_idx < len(days) else None

        # Daily DD: worst single-day loss
        daily_pnls = {}
        for t in trades:
            try:
                dt = pd.to_datetime(t.get('entry_time', ''))
                day = dt.strftime('%Y-%m-%d')
            except Exception:
                continue
            daily_pnls.setdefault(day, 0)
            daily_pnls[day] += t.get('net_pips', 0)

        if daily_pnls:
            worst_daily_pnl = min(daily_pnls.values())
            worst_daily_date = min(daily_pnls, key=daily_pnls.get)
            daily_dd_worst_pips = abs(worst_daily_pnl)
        else:
            daily_dd_worst_pips = 0
            worst_daily_date = None
    else:
        eod_dd_pips = 0
        worst_day = None
        daily_dd_worst_pips = 0
        worst_daily_date = None

    eod_dd_dollars = eod_dd_pips * pip_value * lot_size
    eod_dd_pct = (eod_dd_dollars / account_size) * 100
    daily_dd_dollars = daily_dd_worst_pips * pip_value * lot_size
    daily_dd_worst_pct = (daily_dd_dollars / account_size) * 100

    return {
        'floating_dd_pips': round(floating_dd_pips, 1),
        'floating_dd_pct': round(floating_dd_pct, 2),
        'realized_dd_pips': round(realized_dd_pips, 1),
        'realized_dd_pct': round(realized_dd_pct, 2),
        'eod_dd_pips': round(eod_dd_pips, 1),
        'eod_dd_pct': round(eod_dd_pct, 2),
        'eod_worst_date': worst_day,
        'daily_dd_worst_pips': round(daily_dd_worst_pips, 1),
        'daily_dd_worst_pct': round(daily_dd_worst_pct, 2),
        'daily_dd_worst_date': worst_daily_date,
    }


def count_dd_breaches(trades, account_size=100000, risk_pct=1.0, pip_value=10.0,
                       daily_dd_limit_pct=5.0, total_dd_limit_pct=10.0):
    """
    Simulate equity curve, count prop firm DD breaches.
    After each breach, resets account (like restarting a challenge).
    """
    if not trades:
        return {
            'daily_breaches': 0, 'total_breaches': 0, 'blown_count': 0,
            'daily_breach_dates': [], 'total_breach_dates': [],
            'avg_days_between_blows': 0, 'survival_rate_per_month': 0,
            'total_months': 0, 'months_blown': 0,
            'worst_daily_pct': 0, 'worst_total_pct': 0,
        }

    sl_pips = 150
    risk_dollars = account_size * (risk_pct / 100)
    lot_size = risk_dollars / (sl_pips * pip_value) if sl_pips * pip_value > 0 else 0.01

    daily_pnls = {}
    for t in trades:
        try:
            dt = pd.to_datetime(t.get('entry_time', ''))
            day = dt.strftime('%Y-%m-%d')
        except Exception:
            continue
        pnl_dollars = t.get('net_pips', 0) * pip_value * lot_size
        daily_pnls.setdefault(day, 0)
        daily_pnls[day] += pnl_dollars

    if not daily_pnls:
        return {
            'daily_breaches': 0, 'total_breaches': 0, 'blown_count': 0,
            'daily_breach_dates': [], 'total_breach_dates': [],
            'avg_days_between_blows': 0, 'survival_rate_per_month': 0,
            'total_months': 0, 'months_blown': 0,
            'worst_daily_pct': 0, 'worst_total_pct': 0,
        }

    days = sorted(daily_pnls.keys())
    daily_dd_limit = account_size * (daily_dd_limit_pct / 100)
    total_dd_limit = account_size * (total_dd_limit_pct / 100)

    balance = account_size
    high_water = account_size
    blown_count = 0
    daily_breach_dates = []
    total_breach_dates = []
    last_blown_day = None
    days_between_blows = []
    worst_daily_pct = 0.0
    worst_total_pct = 0.0

    for day in days:
        day_pnl = daily_pnls[day]

        if day_pnl < 0:
            daily_pct = abs(day_pnl) / account_size * 100
            worst_daily_pct = max(worst_daily_pct, daily_pct)

        if day_pnl < 0 and abs(day_pnl) >= daily_dd_limit:
            daily_breach_dates.append(day)
            blown_count += 1
            if last_blown_day:
                try:
                    gap = (pd.to_datetime(day) - pd.to_datetime(last_blown_day)).days
                    days_between_blows.append(gap)
                except Exception:
                    pass
            last_blown_day = day
            balance = account_size
            high_water = account_size
            continue

        balance += day_pnl
        high_water = max(high_water, balance)

        total_dd = high_water - balance
        total_dd_pct = total_dd / account_size * 100
        worst_total_pct = max(worst_total_pct, total_dd_pct)

        if total_dd >= total_dd_limit:
            total_breach_dates.append(day)
            blown_count += 1
            if last_blown_day:
                try:
                    gap = (pd.to_datetime(day) - pd.to_datetime(last_blown_day)).days
                    days_between_blows.append(gap)
                except Exception:
                    pass
            last_blown_day = day
            balance = account_size
            high_water = account_size

    total_days = (pd.to_datetime(days[-1]) - pd.to_datetime(days[0])).days if len(days) > 1 else 1
    total_months = max(total_days / 30, 1)
    avg_gap = round(sum(days_between_blows) / len(days_between_blows), 0) if days_between_blows else total_days

    months_blown = len(set(d[:7] for d in daily_breach_dates + total_breach_dates))
    total_unique_months = len(set(d[:7] for d in days))
    survival_rate = round((1 - months_blown / max(total_unique_months, 1)) * 100, 1)

    return {
        'daily_breaches': len(daily_breach_dates),
        'total_breaches': len(total_breach_dates),
        'blown_count': blown_count,
        'daily_breach_dates': daily_breach_dates[:10],
        'total_breach_dates': total_breach_dates[:10],
        'avg_days_between_blows': int(avg_gap),
        'survival_rate_per_month': survival_rate,
        'total_months': int(total_months),
        'months_blown': months_blown,
        'worst_daily_pct': round(worst_daily_pct, 1),
        'worst_total_pct': round(worst_total_pct, 1),
    }


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
    """Return list of strategy summary dicts from backtest_matrix.json + saved rules."""
    results = []

    # Load backtest matrix results
    if os.path.exists(BACKTEST_MATRIX_PATH):
        with open(BACKTEST_MATRIX_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for i, r in enumerate(data.get('results', [])):
            stats = r.get('stats', r)  # stats might be nested or at top level
            wr = stats.get('win_rate', r.get('win_rate', 0))
            wr_str = f"{wr:.0f}%" if wr > 1 else f"{wr*100:.0f}%"
            net = stats.get('net_total_pips', r.get('net_total_pips', 0))
            trades_count = stats.get('total_trades', r.get('total_trades', 0))

            results.append({
                'index':             i,
                'source':            'backtest',
                'label':             (f"{r.get('rule_combo','?')} × {r.get('exit_strategy','?')}"
                                      f"  [{trades_count} trades, WR {wr_str}, {net:+,.0f} pips]"),
                'rule_combo':        r.get('rule_combo', '?'),
                'exit_strategy':     r.get('exit_strategy', '?'),
                'exit_name':         r.get('exit_name', '?'),
                'total_trades':      trades_count,
                'win_rate':          wr,
                'net_total_pips':    net,
                'net_avg_pips':      stats.get('net_avg_pips', stats.get('avg_pips', r.get('avg_pips', 0))),
                'net_profit_factor': stats.get('net_profit_factor', r.get('net_profit_factor', 0)),
                'max_dd_pips':       stats.get('max_dd_pips', r.get('max_dd_pips', 0)),
                'spread_pips':       r.get('spread_pips', 2.5),
                'commission_pips':   r.get('commission_pips', 0.0),
                'has_trades':        'trades' in r and bool(r.get('trades')),
            })

    # Load saved rules
    try:
        saved_path = os.path.join(os.path.dirname(BACKTEST_MATRIX_PATH), '..', '..', 'saved_rules.json')
        saved_path = os.path.normpath(saved_path)
        if os.path.exists(saved_path):
            with open(saved_path, 'r', encoding='utf-8') as f:
                saved = json.load(f)

            if saved:
                # Add separator
                results.append({
                    'index':        '__separator__',
                    'source':       'separator',
                    'label':        '─── SAVED RULES ───────────────────────────────────────────────────────────────',
                    'total_trades': 0,
                    'has_trades':   False,
                })

                for entry in saved:
                    rule = entry.get('rule', {})
                    wr = rule.get('win_rate', 0)
                    wr_str = f"{wr:.0f}%" if wr > 1 else f"{wr*100:.0f}%"
                    source = entry.get('source', '?')
                    notes = entry.get('notes', '')
                    rid = entry.get('id', '?')

                    label_parts = [f"💾 Saved #{rid} — from {source}"]
                    if wr > 0:
                        label_parts.append(f"WR {wr_str}")
                    if notes:
                        label_parts.append(notes[:30])

                    results.append({
                        'index':             f"saved_{rid}",
                        'source':            'saved',
                        'label':             '  '.join(label_parts),
                        'rule_combo':        f"Saved #{rid}",
                        'exit_strategy':     'Default',
                        'exit_name':         'Default',
                        'total_trades':      rule.get('total_trades', 0),
                        'win_rate':          wr,
                        'net_total_pips':    rule.get('net_total_pips', 0),
                        'net_avg_pips':      rule.get('avg_pips', 0),
                        'net_profit_factor': rule.get('net_profit_factor', 0),
                        'max_dd_pips':       rule.get('max_dd_pips', 0),
                        'spread_pips':       2.5,
                        'commission_pips':   0.0,
                        'has_trades':        False,
                        'saved_rule':        rule,  # keep the original rule for loading
                    })
    except Exception:
        pass

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


# ─────────────────────────────────────────────────────────────────────────────
# Deep Optimizer — Generate New Trades (modifies rules, re-runs backtests)
# ─────────────────────────────────────────────────────────────────────────────

def deep_optimize_generate(
    trades,
    base_rules,
    candles_path,
    timeframe='H1',
    pip_size=0.01,
    spread_pips=2.5,
    commission_pips=0.0,
    target_firm=None,
    account_size=100000,
    filters=None,
    progress_callback=None,
    feature_matrix_path=None,
):
    """
    Deep optimization — modifies rules and re-runs backtests to find NEW trades.

    Unlike Mode 1 (filtering), this actually changes the strategy:
    - Shifts condition thresholds to find better entry points
    - Adds new indicator conditions that improve the edge
    - Removes weak conditions that aren't helping
    - Tests different exit strategies with each modified rule set
    - Scores everything by prop firm pass rate + profitability

    The output trades will be DIFFERENT from the input trades.
    """
    _stop_flag.clear()
    start_time = time.time()
    candidates = []

    import sys
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from project2_backtesting.strategy_backtester import run_backtest, compute_stats
    from project2_backtesting.exit_strategies import (
        FixedSLTP, TrailingStop,
    )

    candles_df = pd.read_csv(candles_path)
    ts_col = candles_df.columns[0]
    candles_df['timestamp'] = pd.to_datetime(candles_df[ts_col]).astype('datetime64[ns]')

    cache_path = candles_path.replace('.csv', '_indicators.parquet')
    if os.path.exists(cache_path):
        indicators_df = pd.read_parquet(cache_path)
        if 'timestamp' in indicators_df.columns:
            indicators_df['timestamp'] = indicators_df['timestamp'].astype('datetime64[ns]')
    else:
        from project2_backtesting.strategy_backtester import build_multi_tf_indicators
        data_dir = os.path.dirname(candles_path)
        # Pass all indicator groups to force compute_indicators code path
        # (compute_all_indicators has integer index bug)
        _ALL_GROUPS = [
            'adx', 'ao', 'aroon', 'atr', 'bb', 'cci', 'dmi', 'donchian', 'dpo',
            'elder_ray', 'ema', 'fib', 'ichimoku', 'keltner', 'kst', 'macd',
            'mass_index', 'pivot', 'price_action', 'psar', 'roc', 'rsi', 'session',
            'sma', 'std_dev', 'stoch', 'supertrend', 'swing', 'tsi', 'uo',
            'volume', 'vwap', 'williams_r',
        ]
        _ALL_TF = {tf: _ALL_GROUPS for tf in ['M5', 'M15', 'H1', 'H4', 'D1']}
        indicators_df = build_multi_tf_indicators(
            data_dir, candles_df['timestamp'], required_indicators=_ALL_TF)

    top_features = []
    if feature_matrix_path and os.path.exists(feature_matrix_path):
        try:
            report_path = os.path.join(
                os.path.dirname(feature_matrix_path), 'analysis_report.json'
            )
            if os.path.exists(report_path):
                with open(report_path) as f:
                    report = json.load(f)
                top_features = [
                    feat for feat, _score
                    in report.get('feature_importance', {}).get('top_20', [])
                ]
        except Exception:
            pass

    available_indicators = [c for c in indicators_df.columns if c != 'timestamp']

    default_sl = 150.0
    default_tp = 300.0
    exit_strategies = [
        FixedSLTP(sl_pips=default_sl, tp_pips=default_tp, pip_size=pip_size),
        FixedSLTP(sl_pips=100, tp_pips=200, pip_size=pip_size),
        FixedSLTP(sl_pips=200, tp_pips=400, pip_size=pip_size),
        TrailingStop(sl_pips=default_sl, trail_pips=100, pip_size=pip_size),
        TrailingStop(sl_pips=default_sl, trail_pips=50, pip_size=pip_size),
    ]

    base_stats = compute_stats_summary(trades)
    base_score = _score_trades(trades, target_firm)
    best_so_far = {
        'name':           'Base (original)',
        'trades':         len(trades),
        'win_rate':       base_stats['win_rate'],
        'avg_pips':       base_stats['avg_pips'],
        'trades_per_day': base_stats['trades_per_day'],
        'score':          base_score,
    }

    total_steps = 4

    def _report(step, msg):
        if _stop_flag.is_set():
            return False
        if progress_callback:
            elapsed = int(time.time() - start_time)
            mins, secs = divmod(elapsed, 60)
            progress_callback(
                step=step, total=total_steps, message=msg,
                current_best=best_so_far,
                elapsed_str=f"{mins}m {secs}s",
                candidates_tested=len(candidates),
                improvements_found=sum(1 for c in candidates if c['score'] > base_score),
            )
        return True

    def _test_rules(name, rules, exit_strat, changes_desc):
        nonlocal best_so_far
        try:
            new_trades = run_backtest(
                candles_df=candles_df,
                indicators_df=indicators_df,
                rules=rules,
                exit_strategy=exit_strat,
                direction="BUY",
                pip_size=pip_size,
                spread_pips=spread_pips,
                commission_pips=commission_pips,
                account_size=account_size,
            )
        except Exception:
            return None

        if not new_trades or len(new_trades) < 5:
            return None

        enriched = enrich_trades(new_trades)
        if filters:
            kept, _ = apply_filters(enriched, filters)
            if len(kept) < 5:
                return None
            final_trades = kept
        else:
            final_trades = enriched

        stats = compute_stats_summary(final_trades)
        score = _score_trades(final_trades, target_firm)

        exit_name = exit_strat.name if hasattr(exit_strat, 'name') else str(exit_strat)
        exit_desc = exit_strat.describe() if hasattr(exit_strat, 'describe') else exit_name

        candidate = {
            'name':              name,
            'rules':             rules,
            'exit_strategy':     exit_desc,
            'exit_name':         exit_name,
            'filters_applied':   filters or {},
            'trades':            final_trades,
            'stats':             stats,
            'score':             score,
            'changes_from_base': changes_desc,
        }
        candidates.append(candidate)

        if score > best_so_far['score']:
            best_so_far = {
                'name':           name,
                'trades':         stats['count'],
                'win_rate':       stats['win_rate'],
                'avg_pips':       stats['avg_pips'],
                'trades_per_day': stats['trades_per_day'],
                'score':          score,
            }

        return candidate

    win_rules = [r for r in base_rules if r.get('prediction') == 'WIN']

    # ── STEP 1: Threshold shifts ──────────────────────────────────────────────
    if not _report(1, "Step 1: Testing threshold shifts..."):
        return candidates

    shifts = [0.7, 0.8, 0.9, 1.1, 1.2, 1.3]
    for rule_idx, rule in enumerate(win_rules):
        for cond_idx, cond in enumerate(rule.get('conditions', [])):
            original_val = cond['value']
            if original_val == 0:
                continue
            for shift in shifts:
                if _stop_flag.is_set():
                    break
                new_val = original_val * shift
                modified_rules = copy.deepcopy(win_rules)
                modified_rules[rule_idx]['conditions'][cond_idx]['value'] = new_val
                feat = cond['feature']
                change = f"R{rule_idx+1} {feat}: {original_val:.4f} → {new_val:.4f}"
                _test_rules(f"Threshold shift: {change}", modified_rules, exit_strategies[0], change)
                _report(1, f"Threshold shifts: R{rule_idx+1} {feat} ×{shift}")

    # ── STEP 2: Add new indicator conditions ──────────────────────────────────
    if not _report(2, "Step 2: Testing additional indicators..."):
        return candidates

    test_indicators = top_features[:30] if top_features else available_indicators[:30]
    for ind_name in test_indicators:
        if _stop_flag.is_set():
            break
        if ind_name not in indicators_df.columns:
            continue
        col = indicators_df[ind_name].dropna()
        if len(col) < 100:
            continue
        for pct in [25, 50, 75]:
            threshold = col.quantile(pct / 100.0)
            for operator in ['>', '<']:
                modified_rules = copy.deepcopy(win_rules)
                if not modified_rules:
                    continue
                modified_rules[0]['conditions'].append({
                    'feature':  ind_name,
                    'operator': operator,
                    'value':    float(threshold),
                })
                change = f"Added {ind_name} {operator} {threshold:.4f} to Rule 1"
                _test_rules(f"+ {ind_name} {operator} {threshold:.2f}", modified_rules, exit_strategies[0], change)
        _report(2, f"Testing indicator: {ind_name}")

    # ── STEP 3: Remove weak conditions ────────────────────────────────────────
    if not _report(3, "Step 3: Testing condition removal..."):
        return candidates

    for rule_idx, rule in enumerate(win_rules):
        conditions = rule.get('conditions', [])
        if len(conditions) <= 1:
            continue
        for cond_idx, cond in enumerate(conditions):
            if _stop_flag.is_set():
                break
            modified_rules = copy.deepcopy(win_rules)
            removed_cond = modified_rules[rule_idx]['conditions'].pop(cond_idx)
            feat = removed_cond['feature']
            change = f"Removed {feat} from Rule {rule_idx+1}"
            _test_rules(f"- {feat} from R{rule_idx+1}", modified_rules, exit_strategies[0], change)
            _report(3, f"Remove: {feat} from R{rule_idx+1}")

    # ── STEP 4: Exit strategy scan on top candidates ──────────────────────────
    if not _report(4, "Step 4: Testing exit strategies on best candidates..."):
        return candidates

    top_rule_sets = sorted(candidates, key=lambda c: c['score'], reverse=True)[:5]
    for rank, top_cand in enumerate(top_rule_sets):
        for exit_strat in exit_strategies:
            if _stop_flag.is_set():
                break
            exit_name = exit_strat.name if hasattr(exit_strat, 'name') else str(exit_strat)
            name = f"{top_cand['name']} × {exit_name}"
            change = f"{top_cand['changes_from_base']} + {exit_name}"
            _test_rules(name, top_cand['rules'], exit_strat, change)
            _report(4, f"Exit test: {exit_name} on #{rank+1}")

    candidates.sort(key=lambda c: c['score'], reverse=True)

    elapsed = time.time() - start_time
    if progress_callback:
        progress_callback(
            step=total_steps, total=total_steps,
            message=f"Done! {len(candidates)} candidates in {elapsed:.0f}s",
            current_best=best_so_far,
            elapsed_str=f"{int(elapsed//60)}m {int(elapsed%60)}s",
            candidates_tested=len(candidates),
            improvements_found=sum(1 for c in candidates if c['score'] > base_score),
        )

    return candidates
