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

# CHANGED: April 2026 — UI-safe logging (Phase 19d)
from shared.logging_setup import get_logger
log = get_logger(__name__)

BACKTEST_MATRIX_PATH = os.path.join(_HERE, 'outputs', 'backtest_matrix.json')

# ── Per-TF trade file cache (cleared at the start of each scoring run) ────────
# WHY: load_trades_from_matrix reads the same backtest_trades_H1.json (etc.) once
#      per strategy, making This Month scoring O(rules × TF-file-size). With a
#      cache the file is parsed exactly once per TF per scoring run.
_TRADES_FILE_CACHE: dict  = {}
_TRADES_CACHE_HITS        = [0]
_TRADES_CACHE_MISSES      = [0]


def clear_trades_cache():
    """Discard all cached trade-file data and reset hit/miss counters."""
    _TRADES_FILE_CACHE.clear()
    _TRADES_CACHE_HITS[0]   = 0
    _TRADES_CACHE_MISSES[0] = 0


def _load_trades_file_cached(path):
    """Return parsed JSON for *path*, reading the file only on the first call."""
    if path in _TRADES_FILE_CACHE:
        _TRADES_CACHE_HITS[0] += 1
        return _TRADES_FILE_CACHE[path]
    _TRADES_CACHE_MISSES[0] += 1
    with open(path, 'r', encoding='utf-8') as _f:
        _data = json.load(_f)
    _TRADES_FILE_CACHE[path] = _data
    return _data


def get_trades_cache_stats():
    """Return (hits, misses) since the last clear_trades_cache() call."""
    return (_TRADES_CACHE_HITS[0], _TRADES_CACHE_MISSES[0])


# Session hour ranges (UTC)
_SESSIONS = {
    "Asian":    (0, 8),
    "London":   (7, 16),
    "New York": (12, 21),
}

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def compute_monthly_pnl(trades, account_size=100000, risk_pct=1.0, pip_value=10.0,
                        default_sl_pips=150.0):
    """
    Group trades by month, return monthly P&L breakdown with daily trade frequency stats.
    Returns list of dicts: [{month: '2020-01', pnl_pips: +340, trades: 12, wins: 8,
                             avg_trades_per_day: 2.4, min_trades_per_day: 1, max_trades_per_day: 5,
                             pnl_dollars: +2267, pnl_pct: +2.27}, ...]

    WHY default_sl_pips: dollar values depend on lot sizing, which depends on
    the strategy's actual SL distance. Old code hardcoded 150 for XAUUSD. Now
    callers should pass the actual SL pips from the strategy's exit_params.
    """
    # Calculate $ per pip based on risk settings
    # CHANGED: April 2026 — sl_pips from parameter, not hardcoded
    sl_pips = float(default_sl_pips) if default_sl_pips and default_sl_pips > 0 else 150.0
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
            monthly[key] = {'month': key, 'pnl_pips': 0, 'trades': 0,
                            'wins': 0, 'losses': 0, 'breakeven': 0,
                            'daily_counts': {}}

        pnl = t.get('net_pips', 0)
        monthly[key]['pnl_pips'] += pnl
        monthly[key]['trades'] += 1
        # WHY: Old code lumped BE (pnl == 0) into losses, distorting the
        #      win/loss count. Now BE is its own bucket so it doesn't pollute
        #      either side. Total trades still includes BE.
        # CHANGED: April 2026 — separate BE bucket
        if pnl > 0:
            monthly[key]['wins'] += 1
        elif pnl < 0:
            monthly[key]['losses'] += 1
        else:
            monthly[key].setdefault('breakeven', 0)
            monthly[key]['breakeven'] += 1

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


# ─────────────────────────────────────────────────────────────────────────────
# Time-bucket performance aggregators
# WHY: Lets the refiner panel show per-hour / per-DoW / per-session tables
#      at a glance — audit view alongside the optimizer's actionable
#      candidates. Trades come pre-enriched (see enrich_trades) so we
#      just group on the existing fields.
# CHANGED: May 2026 — time-bucket aggregators
# ─────────────────────────────────────────────────────────────────────────────

def _bucket_stats(trade_list, pip_to_dollar):
    """Reduce a list of trades to {trades, wins, win_rate, pnl_pips, pnl_dollars}."""
    n = len(trade_list)
    if n == 0:
        return {'trades': 0, 'wins': 0, 'win_rate': 0.0,
                'pnl_pips': 0.0, 'pnl_dollars': 0.0}
    wins = 0
    pnl_pips = 0.0
    for t in trade_list:
        p = float(t.get('net_pips', 0) or 0)
        pnl_pips += p
        if p > 0:
            wins += 1
    return {
        'trades':      n,
        'wins':        wins,
        'win_rate':    round(wins / n * 100.0, 1),
        'pnl_pips':    round(pnl_pips, 1),
        'pnl_dollars': round(pnl_pips * pip_to_dollar, 2),
    }


def _pip_to_dollar(account_size, risk_pct, pip_value, default_sl_pips):
    """Compute $ per pip from the same lot-sizing formula compute_monthly_pnl uses.

    Single source of truth — same numbers as the monthly chart.
    """
    sl_pips = float(default_sl_pips) if default_sl_pips and default_sl_pips > 0 else 150.0
    risk_dollars = account_size * (risk_pct / 100.0)
    lot_size = risk_dollars / (sl_pips * pip_value) if sl_pips * pip_value > 0 else 0.01
    return pip_value * lot_size


def compute_hourly_pnl(trades, account_size=100000, risk_pct=1.0,
                       pip_value=10.0, default_sl_pips=150.0):
    """Per-hour aggregation. Returns list of 24 dicts (one per hour 0-23).

    Each dict: {hour: 0-23, trades, wins, win_rate, pnl_pips, pnl_dollars}.
    Hours with zero trades still appear (so the table is always 24 rows).
    """
    p2d = _pip_to_dollar(account_size, risk_pct, pip_value, default_sl_pips)
    buckets = {h: [] for h in range(24)}
    for t in trades:
        h = t.get('hour_of_day')
        if isinstance(h, int) and 0 <= h <= 23:
            buckets[h].append(t)
    return [{'hour': h, **_bucket_stats(buckets[h], p2d)} for h in range(24)]


def compute_dow_pnl(trades, account_size=100000, risk_pct=1.0,
                    pip_value=10.0, default_sl_pips=150.0):
    """Per-weekday aggregation. Returns list of 5 dicts (Mon-Fri).

    Weekend trades (Sat/Sun, rare edge cases) are dropped.
    """
    p2d = _pip_to_dollar(account_size, risk_pct, pip_value, default_sl_pips)
    DOW_ORDER = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    buckets = {d: [] for d in DOW_ORDER}
    for t in trades:
        d = t.get('day_abbrev')
        if d in buckets:
            buckets[d].append(t)
    return [{'dow': d, **_bucket_stats(buckets[d], p2d)} for d in DOW_ORDER]


def compute_session_pnl(trades, account_size=100000, risk_pct=1.0,
                        pip_value=10.0, default_sl_pips=150.0):
    """Per-session aggregation. Returns list of 3 dicts.

    Session names match _get_session() in this module: Asian / London /
    New York. Trades with session='Unknown' (failed enrichment) are
    dropped.
    """
    p2d = _pip_to_dollar(account_size, risk_pct, pip_value, default_sl_pips)
    SESSION_ORDER = ['Asian', 'London', 'New York']
    buckets = {s: [] for s in SESSION_ORDER}
    for t in trades:
        s = t.get('session')
        if s in buckets:
            buckets[s].append(t)
    return [{'session': s, **_bucket_stats(buckets[s], p2d)} for s in SESSION_ORDER]


# WHY (Phase 30 Fix 1): Old signature had no pip_size parameter. The body
#      used a sniff-from-trades inference with a hardcoded 0.01 fallback,
#      which always fired for non-XAUUSD callers because run_backtest and
#      fast_backtest don't emit 'pip_size' on trade dicts. Add pip_size as
#      an explicit parameter so callers have to pass it and the XAUUSD
#      default is visible at the signature level.
# CHANGED: April 2026 — Phase 30 Fix 1 — explicit pip_size parameter
#          (audit Part C HIGH #26 pip_size half)
def compute_three_drawdowns(trades, account_size=100000, risk_pct=1.0, pip_value=10.0,
                             daily_reset_hour=0, default_sl_pips=150.0, pip_size=0.01):
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
    # WHY: Old code hardcoded sl_pips=150 (XAUUSD-only). Now from parameter.
    # CHANGED: April 2026 — parameterized sl_pips
    sl_pips = float(default_sl_pips) if default_sl_pips and default_sl_pips > 0 else 150.0
    risk_dollars = account_size * (risk_pct / 100)
    lot_size = risk_dollars / (sl_pips * pip_value) if sl_pips * pip_value > 0 else 0.01
    realized_dd_dollars = realized_dd_pips * pip_value * lot_size
    realized_dd_pct = (realized_dd_dollars / account_size) * 100

    # ── 2. Floating DD (intra-trade: includes unrealized P&L) ──
    # WHY: Old code branched only on direction == 'BUY' and silently skipped
    #      SELL trades, returning realized DD as floating DD for any strategy
    #      with sells. Now both directions compute the worst unrealized point
    #      relative to the equity peak before the trade.
    #      Also: pip_size hardcoded to 0.01 (XAUUSD). New parameter pip_size
    #      added — but to keep the function signature stable, we infer it
    #      from the first trade's metadata or fall back to 0.01.
    # CHANGED: April 2026 — handle SELL trades + remove pip_size hardcode
    floating_dd_pips = realized_dd_pips  # start with realized

    # WHY: Old code tried to infer pip_size from trade dicts, which almost
    #      never carry a pip_size key. The 0.01 fallback won for every
    #      non-XAUUSD call. Now pip_size is an explicit parameter (default
    #      0.01 for backward compat with any caller that hasn't been
    #      updated).
    # CHANGED: April 2026 — Phase 30 Fix 1b — use pip_size parameter directly
    pip_size_local = float(pip_size) if pip_size and pip_size > 0 else 0.01

    equity = 0
    equity_peak = 0
    worst_floating = 0

    for t in trades:
        entry = t.get('entry_price', 0)
        tdir  = t.get('direction', 'BUY')
        worst_unrealized = 0.0

        if entry > 0:
            if tdir == 'BUY':
                # Worst point for a BUY = lowest price reached during the trade
                worst_during = t.get('lowest_since_entry', entry)
                if worst_during > 0:
                    worst_unrealized = (worst_during - entry) / pip_size_local
            elif tdir == 'SELL':
                # Worst point for a SELL = HIGHEST price reached during the trade
                worst_during = t.get('highest_since_entry', entry)
                if worst_during > 0:
                    worst_unrealized = (entry - worst_during) / pip_size_local
            # worst_unrealized is <= 0 (a loss in pips) for both directions

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
        # WHY: Old code keyed Daily DD by entry_time while EOD DD above
        #      keyed by exit_time. Same metric card showed two different
        #      day boundaries — a trade opening 23:50 Mon and closing
        #      00:10 Tue got credited to Mon in Daily DD and Tue in EOD DD.
        #      Prop firms credit the day the position closed (P&L is
        #      realized on exit). Use exit_time consistently.
        # CHANGED: April 2026 — Phase 30 Fix 2 — consistent day keying
        #          (audit Part C HIGH #28)
        daily_pnls = {}
        for t in trades:
            try:
                dt = pd.to_datetime(t.get('exit_time', t.get('entry_time', '')))
                day = dt.strftime('%Y-%m-%d')
            except Exception:
                continue
            daily_pnls.setdefault(day, 0)
            daily_pnls[day] += t.get('net_pips', 0)

        if daily_pnls:
            # WHY: Old code used min(daily_pnls.values()). On an all-winning
            #      strategy that returned the smallest WIN, and abs() then
            #      displayed it as a drawdown magnitude. The daily DD
            #      metric should only register actual losing days — floor
            #      at 0. If every day is positive, worst daily DD = 0.
            # CHANGED: April 2026 — Phase 30 Fix 3 — floor at 0 for
            #          all-winning strategies (audit Part C HIGH #29)
            raw_worst = min(daily_pnls.values())
            if raw_worst < 0:
                worst_daily_pnl  = raw_worst
                worst_daily_date = min(daily_pnls, key=daily_pnls.get)
                daily_dd_worst_pips = abs(worst_daily_pnl)
            else:
                worst_daily_pnl  = 0
                worst_daily_date = None
                daily_dd_worst_pips = 0
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
                       daily_dd_limit_pct=5.0, total_dd_limit_pct=10.0,
                       daily_dd_safety_pct=None, total_dd_safety_pct=None,
                       default_sl_pips=150.0,
                       funded_protect=False, payout_period_days=14,
                       total_dd_alert_pct=None,
                       # Trailing-lock mechanic (None/'static' = old behavior)
                       drawdown_type='static',
                       hwm_lock_gain_pct=None,
                       hwm_lock_level='starting_balance'):
    """
    Simulate equity curve, count prop firm DD breaches and safety stops.

    Firm breaches: account blown, challenge failed
    Safety stops: bot-imposed limits BEFORE firm limits, account survives

    After each breach, resets account (like restarting a challenge).
    Safety stops are tracked but don't reset the account.
    """
    if not trades:
        return {
            'daily_breaches': 0, 'total_breaches': 0, 'blown_count': 0,
            'daily_breach_dates': [], 'total_breach_dates': [],
            'daily_safety_stops': 0, 'total_safety_stops': 0,
            'daily_safety_dates': [], 'total_safety_dates': [],
            'avg_days_between_blows': 0, 'survival_rate_per_month': 0,
            'total_months': 0, 'months_blown': 0,
            'worst_daily_pct': 0, 'worst_total_pct': 0,
            'firm_breaches_total': 0, 'safety_stops_total': 0,
            'drawdown_type': drawdown_type, 'hwm_locked': False,
        }

    # WHY: Old code hardcoded sl_pips=150 (XAUUSD-only). Now from parameter.
    # CHANGED: April 2026 — parameterized sl_pips
    sl_pips = float(default_sl_pips) if default_sl_pips and default_sl_pips > 0 else 150.0
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
            'daily_safety_stops': 0, 'total_safety_stops': 0,
            'daily_safety_dates': [], 'total_safety_dates': [],
            'avg_days_between_blows': 0, 'survival_rate_per_month': 0,
            'total_months': 0, 'months_blown': 0,
            'worst_daily_pct': 0, 'worst_total_pct': 0,
            'firm_breaches_total': 0, 'safety_stops_total': 0,
            'drawdown_type': drawdown_type, 'hwm_locked': False,
        }

    days = sorted(daily_pnls.keys())
    daily_dd_limit = account_size * (daily_dd_limit_pct / 100)
    total_dd_limit = account_size * (total_dd_limit_pct / 100)

    # Funded protection state
    _protect_skip_until = None
    _protect_alert = None
    if funded_protect:
        _protect_alert = total_dd_alert_pct if total_dd_alert_pct else (total_dd_limit_pct * 0.92)
        _protect_alert_dollars = account_size * (_protect_alert / 100)
    _payout_trades_stopped = 0

    # Safety limits (bot stops before firm limits)
    daily_dd_safety = account_size * (daily_dd_safety_pct / 100) if daily_dd_safety_pct else None
    total_dd_safety = account_size * (total_dd_safety_pct / 100) if total_dd_safety_pct else None

    balance = account_size
    high_water = account_size
    blown_count = 0
    daily_breach_dates = []
    total_breach_dates = []
    daily_safety_dates = []
    total_safety_dates = []
    last_blown_day = None
    days_between_blows = []
    worst_daily_pct = 0.0
    worst_total_pct = 0.0
    hwm_locked = False

    for day in days:
        # WHY: Funded protection — when total trailing DD hits alert level,
        #      stop trading for the rest of the payout period.
        # CHANGED: April 2026 — funded_protect simulation
        if funded_protect and _protect_skip_until:
            try:
                if pd.to_datetime(day) < _protect_skip_until:
                    continue  # skip this day — bot is stopped
            except Exception:
                pass

        raw_day_pnl = daily_pnls[day]   # uncapped, real loss

        # ── Safety stop (informational; account survives) ──────────────────
        # WHY: bot voluntarily halts here. This is NOT a breach. It must never
        #      suppress the breach check below.
        daily_safety_triggered = False
        if daily_dd_safety and raw_day_pnl < 0 and abs(raw_day_pnl) >= daily_dd_safety:
            daily_safety_triggered = True
            daily_safety_dates.append(day)

        # ── ACTUAL firm breach — measured on the RAW uncapped loss ─────────
        # WHY: the firm kills the account at daily_dd_limit regardless of whether
        #      our bot would have stopped at the safety line. A 36% day is a
        #      breach even if our safety stop is 2.7%.
        if raw_day_pnl < 0:
            daily_pct = abs(raw_day_pnl) / account_size * 100
            worst_daily_pct = max(worst_daily_pct, daily_pct)

        daily_breach_today = (raw_day_pnl < 0 and abs(raw_day_pnl) >= daily_dd_limit)
        if daily_breach_today:
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
            hwm_locked = False
            continue

        # If no breach: advance balance. If the safety stop fired, the bot
        # halted at the safety line, so the realized loss for equity-curve
        # continuation is capped at the safety threshold (models the halt).
        # The breach decision above already used the raw loss, so this cap
        # cannot hide a breach.
        if daily_safety_triggered:
            day_pnl = -daily_dd_safety
        else:
            day_pnl = raw_day_pnl

        balance += day_pnl

        # ── Trailing DD high-water with optional lock (firm-specific) ──────
        # PARITY: mirrors shared/prop_firm_simulator.py L296-311 and
        #         project3_live_trading/ea_generator.py L1256-1299.
        if drawdown_type in ('trailing', 'trailing_eod'):
            if hwm_lock_gain_pct and not hwm_locked:
                gain_pct = (balance - account_size) / account_size * 100.0
                if gain_pct >= hwm_lock_gain_pct:
                    hwm_locked = True
                    if hwm_lock_level == 'starting_balance_strict':
                        # zero buffer: floor lands exactly at starting balance
                        high_water = account_size * (1.0 + total_dd_limit_pct / 100.0)
                    else:
                        high_water = account_size
                else:
                    high_water = max(high_water, balance)
            elif hwm_locked:
                pass   # frozen
            else:
                high_water = max(high_water, balance)
        else:
            # static: floor is fixed at starting balance
            high_water = account_size

        total_dd = high_water - balance              # RAW, uncapped
        total_dd_pct = total_dd / account_size * 100
        worst_total_pct = max(worst_total_pct, total_dd_pct)

        # Funded protection — stop trading when approaching total DD limit
        if funded_protect and _protect_alert and total_dd >= _protect_alert_dollars:
            try:
                _current_day = pd.to_datetime(day)
                _period_end = _current_day + pd.Timedelta(days=payout_period_days)
                _protect_skip_until = _period_end
                _payout_trades_stopped += 1
            except Exception:
                pass

        # Total safety stop (informational; account survives)
        if total_dd_safety and total_dd >= total_dd_safety and total_dd < total_dd_limit:
            total_safety_dates.append(day)
            # do NOT restore balance, do NOT cap the breach check

        # ACTUAL total breach — on RAW total_dd
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
            hwm_locked = False

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
        'daily_safety_stops': len(daily_safety_dates),
        'total_safety_stops': len(total_safety_dates),
        'daily_safety_dates': daily_safety_dates[:10],
        'total_safety_dates': total_safety_dates[:10],
        'avg_days_between_blows': int(avg_gap),
        'survival_rate_per_month': survival_rate,
        'total_months': int(total_months),
        'months_blown': months_blown,
        'worst_daily_pct': round(worst_daily_pct, 1),
        'worst_total_pct': round(worst_total_pct, 1),
        # WHY: Display needs to show the actual limits used, not hardcoded 5%/10%.
        # CHANGED: April 2026 — include limits in breach results
        'daily_dd_limit_pct': daily_dd_limit_pct,
        'total_dd_limit_pct': total_dd_limit_pct,
        'funded_protect_stops': _payout_trades_stopped if funded_protect else 0,
        'firm_breaches_total': len(daily_breach_dates) + len(total_breach_dates),
        'safety_stops_total': len(daily_safety_dates) + len(total_safety_dates),
        'drawdown_type': drawdown_type,
        'hwm_locked': hwm_locked,
    }


def max_consecutive_dd_breaches(individual_results):
    """Longest run of CONSECUTIVE drawdown-failed attempts, in time order.

    A breach = an attempt whose eval_outcome indicates a DD failure
    (FAIL_DD or FAIL_DAILY_DD). Non-DD outcomes (PASS, TIMEOUT,
    INSUFFICIENT_TRADES) reset the run. Returns an int (0 if none / no data).

    WHY: prop traders care not just about how MANY times a rule breaches DD,
         but whether it breaches REPEATEDLY in a row — 3 consecutive blown
         attempts is a very different risk profile from 3 scattered ones.
    CHANGED: June 2026 — consecutive DD-breach metric
    """
    if not individual_results:
        return 0
    _dd_fail = {"FAIL_DD", "FAIL_DAILY_DD"}
    run = 0
    worst = 0
    for r in individual_results:
        outcome = getattr(r, 'eval_outcome', None)
        if outcome is None and isinstance(r, dict):
            outcome = r.get('eval_outcome')
        if outcome in _dd_fail:
            run += 1
            if run > worst:
                worst = run
        else:
            run = 0
    return worst


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────

def load_trades_from_matrix(strategy_index, entry_tf=None):
    """Load trades for one strategy from per-TF trade files or backtest_matrix.json.

    WHY (Phase A.48 fix): Trades are no longer stored in backtest_matrix.json
         (too large, caused OOM crashes). They're saved in separate per-TF
         files: backtest_trades_{TF}.json, keyed by combo index.
         This function tries the per-TF file first, falls back to the
         main JSON for backward compatibility with old backtest runs.

    Args:
        strategy_index: int index into results, or 'saved_X', 'optimizer_latest'
        entry_tf: optional TF string (e.g. 'H1') to find the right trades file.
                  If None, tries to read it from the matrix result.

    CHANGED: April 2026 — Phase A.48 fix — read from per-TF trade files
    """
    # ── Saved rules don't have trades in the matrix ───────────────────────
    if isinstance(strategy_index, str):
        if strategy_index.startswith('saved_'):
            return None
        if strategy_index == 'optimizer_latest':
            try:
                opt_path = os.path.join(os.path.dirname(BACKTEST_MATRIX_PATH), '_validator_optimized.json')
                if os.path.exists(opt_path):
                    with open(opt_path, 'r', encoding='utf-8') as f:
                        opt_data = json.load(f)
                    return opt_data.get('trades', None)
            except Exception:
                pass
            return None
        if strategy_index.startswith('__separator'):
            return None
        return None

    # ── Normal integer index — load from per-TF trade file or matrix ��─────
    if not isinstance(strategy_index, int) or strategy_index < 0:
        return None

    # Step 1: Determine entry_tf from the matrix result if not provided
    if entry_tf is None:
        try:
            if os.path.exists(BACKTEST_MATRIX_PATH):
                with open(BACKTEST_MATRIX_PATH, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                results = data.get('results', []) or data.get('matrix', [])
                if 0 <= strategy_index < len(results):
                    entry_tf = results[strategy_index].get('entry_tf', '')
        except Exception:
            pass

    # Step 2: Try per-TF trades file first (A.48 format)
    if entry_tf:
        trades_path = os.path.join(
            os.path.dirname(BACKTEST_MATRIX_PATH),
            f'backtest_trades_{entry_tf}.json'
        )
        if os.path.exists(trades_path):
            try:
                trades_data = _load_trades_file_cached(trades_path)

                # WHY: The trades file is keyed by the original enumerate
                #      index from the backtester's summary list. The panel
                #      re-sorts backtest_matrix.json by score after trades
                #      files are written, so position-based mapping
                #      (tf_local_idx) is unreliable.
                #      Primary strategy: read _trades_key from the matrix row
                #      (set by the backtester before saving trades).
                #      Fallback for old matrices: tf_local_idx + spiral search.
                # CHANGED: April 2026 — _trades_key direct lookup
                try:
                    with open(BACKTEST_MATRIX_PATH, 'r', encoding='utf-8') as f:
                        matrix_data = json.load(f)
                    all_results = matrix_data.get('results', []) or matrix_data.get('matrix', [])

                    _expected = all_results[strategy_index] if 0 <= strategy_index < len(all_results) else {}
                    _expected_count = int(_expected.get('total_trades', _expected.get('trade_count', 0)))

                    # ── Primary: use _trades_key (survives re-sort) ──
                    _trades_key = _expected.get('_trades_key')
                    if _trades_key is not None:
                        str_key = str(_trades_key)
                        if str_key in trades_data:
                            log.info(f"[REFINER] trades lookup idx={strategy_index} "
                                     f"_trades_key={str_key} "
                                     f"count={len(trades_data[str_key])}")
                            return trades_data[str_key]

                    # ── Fallback for old matrices without _trades_key ──
                    _expected_wr_raw = _expected.get('win_rate', 0)
                    _expected_wr = _expected_wr_raw / 100.0 if _expected_wr_raw > 1 else _expected_wr_raw

                    tf_local_idx = 0
                    for ri in range(strategy_index):
                        if ri < len(all_results) and all_results[ri].get('entry_tf', '') == entry_tf:
                            tf_local_idx += 1

                    def _trades_match(trades_list):
                        """Return True if this trade list matches the matrix row."""
                        if not trades_list or _expected_count <= 0:
                            return False
                        if len(trades_list) != _expected_count:
                            return False
                        if _expected_wr > 0:
                            wins = sum(1 for t in trades_list
                                       if t.get('profit_pips', t.get('pips', 0)) > 0)
                            actual_wr = wins / len(trades_list)
                            if abs(actual_wr - _expected_wr) > 0.02:
                                return False
                        return True

                    found_key = None
                    if str(tf_local_idx) in trades_data:
                        if _trades_match(trades_data[str(tf_local_idx)]):
                            found_key = str(tf_local_idx)

                    if found_key is None:
                        max_key = max(int(k) for k in trades_data if k.isdigit()) if trades_data else 0
                        for offset in range(1, min(30, max_key + 2)):
                            for delta in (offset, -offset):
                                candidate = tf_local_idx + delta
                                if candidate < 0:
                                    continue
                                k = str(candidate)
                                if k in trades_data and _trades_match(trades_data[k]):
                                    found_key = k
                                    break
                            if found_key is not None:
                                break

                    if found_key is not None:
                        log.info(f"[REFINER] trades fallback idx={strategy_index} "
                                 f"tf_local={tf_local_idx} found_key={found_key} "
                                 f"count={len(trades_data[found_key])}")
                        return trades_data[found_key]

                    str_idx = str(tf_local_idx)
                    if str_idx in trades_data:
                        return trades_data[str_idx]
                except Exception:
                    pass

                # Fallback: try direct global index
                str_idx = str(strategy_index)
                if str_idx in trades_data:
                    return trades_data[str_idx]

            except Exception as e:
                log.info(f"[REFINER] Error reading per-TF trades file: {e}")

    # Step 3: Fallback — try reading from main matrix (old format, pre-A.48)
    try:
        if os.path.exists(BACKTEST_MATRIX_PATH):
            with open(BACKTEST_MATRIX_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            results = data.get('results', []) or data.get('matrix', [])
            if 0 <= strategy_index < len(results):
                trades = results[strategy_index].get('trades', None)
                if trades:
                    return trades
    except Exception as e:
        log.info(f"[REFINER] Error loading trades from matrix: {e}")

    return None


def load_strategy_list():
    """Return list of strategy summary dicts from backtest_matrix.json + saved rules."""
    results = []

    # ── Load backtest matrix results ──────────────────────────────────────
    # WHY: Wrapped in try/except so saved rules still load if matrix is corrupt,
    #      being rewritten, or is a Git LFS pointer on a new machine.
    # CHANGED: April 2026 — error handling so saved rules survive matrix failures
    try:
        if os.path.exists(BACKTEST_MATRIX_PATH):
            with open(BACKTEST_MATRIX_PATH, 'r', encoding='utf-8') as f:
                first_line = f.readline()
                # WHY (Phase 66 Fix 5): Old substring check `'git-lfs' in first_line`
                #      would skip any JSON file whose first line contained the text
                #      "git-lfs" legitimately (e.g., in a comment field). Real LFS
                #      pointer files always begin with the exact magic string below.
                # CHANGED: April 2026 — Phase 66 Fix 5 — startswith LFS magic
                #          (audit Part E HIGH #5)
                if first_line.startswith('version https://git-lfs.github.com/spec/v1'):
                    log.info("[REFINER] backtest_matrix.json is a Git LFS pointer — run 'git lfs pull'")
                else:
                    f.seek(0)
                    data = json.load(f)
                    # WHY (Phase A.48 fix): Combined multi-TF save may use
                    #      'results' or 'matrix' key depending on version.
                    # CHANGED: April 2026 — Phase A.48 fix
                    _all_results = data.get('results', []) or data.get('matrix', [])
                    # WHY: Per-row run_settings is empty {} in the
                    #      current matrix format — the real settings
                    #      live ONCE at data['run_settings']. Use this
                    #      as a fallback for firm_id / stage / risk_pct
                    #      / starting_capital lookups so the grid's
                    #      Stage column, money columns, and the panel's
                    #      firm resolver don't all see None.
                    # CHANGED: May 2026 — top-level run_settings fallback
                    _top_rs = data.get('run_settings', {}) or {}
                    for i, r in enumerate(_all_results):
                        # Merge top-level run_settings as a fallback for
                        # each row. Per-row values win when present.
                        _row_rs = r.get('run_settings') or {}
                        if not _row_rs:
                            r['run_settings'] = dict(_top_rs)
                        else:
                            _merged = dict(_top_rs)
                            _merged.update(_row_rs)
                            r['run_settings'] = _merged
                        stats = r.get('stats', r)  # stats might be nested or at top level
                        wr = stats.get('win_rate', r.get('win_rate', 0))
                        # WHY: compute_stats in strategy_backtester.py always
                        #      stores win_rate as percent (0-100). The old
                        #      `wr > 1` ternary was a band-aid for an
                        #      inconsistency that no longer exists — the
                        #      else branch was dead code. Single format now.
                        # CHANGED: April 2026 — remove dead band-aid
                        wr_str = f"{wr:.0f}%"
                        net = stats.get('net_total_pips', r.get('net_total_pips', 0))
                        trades_count = stats.get('total_trades', r.get('total_trades', 0))
                        pf = stats.get('net_profit_factor', r.get('net_profit_factor', 0))

                        # WHY: rule_combo from the matrix is '#1 (BUY)' for rules
                        #      without _saved_rule_id. Resolve the descriptive ID
                        #      from the embedded rules list so the Treeview and
                        #      label show 'BUY_H1_5c_140e (BUY)' instead.
                        # CHANGED: April 2026 — descriptive rule ID in strategy list
                        _rc = r.get('rule_combo', '?')
                        if _rc.startswith('#') and r.get('rules'):
                            _first = (r['rules'][0] if isinstance(r['rules'], list)
                                      and r['rules'] else {})
                            _rid = _first.get('_saved_rule_id', _first.get('rule_id', ''))
                            if _rid:
                                _rc = _rc.replace(_rc.split(' ')[0], _rid, 1)

                        # WHY: Surface each rule's BUY/SELL direction as a
                        #      first-class field so the refiner grid can show a
                        #      Direction column. Read from the embedded rule
                        #      (action → direction), falling back to the
                        #      BUY/SELL token already in rule_combo. Default ''
                        #      (unknown) rather than guessing BUY — leaves the
                        #      Dir cell blank when the data really has neither.
                        # CHANGED: June 2026 — per-row direction for grid column
                        _dir_val = ''
                        _first_r = (r['rules'][0] if isinstance(r.get('rules'), list)
                                    and r.get('rules') else {})
                        if isinstance(_first_r, dict):
                            _dir_val = str(_first_r.get('action', '')
                                           or _first_r.get('direction', '') or '').upper().strip()
                        if _dir_val not in ('BUY', 'SELL', 'BOTH'):
                            _rc_up = str(_rc).upper()
                            if 'SELL' in _rc_up:
                                _dir_val = 'SELL'
                            elif 'BUY' in _rc_up:
                                _dir_val = 'BUY'
                            else:
                                _dir_val = ''

                        results.append({
                            'index':             i,
                            'source':            'backtest',
                            'label':             (f"{_rc} × {r.get('exit_strategy','?')}"
                                                  f"{'  [' + r.get('entry_tf','') + ']' if r.get('entry_tf','') else ''}"
                                                  f"  [{trades_count} trades, WR {wr_str}, PF {pf:.1f}, {net:+,.0f} pips]"),
                            'rule_combo':        _rc,
                            'direction':         _dir_val,
                            'exit_strategy':     r.get('exit_strategy', '?'),
                            'exit_name':         r.get('exit_name', '?'),
                            'total_trades':      trades_count,
                            'win_rate':          wr,
                            'net_total_pips':    net,
                            'net_avg_pips':      stats.get('net_avg_pips', stats.get('avg_pips', r.get('avg_pips', 0))),
                            'net_profit_factor': stats.get('net_profit_factor', r.get('net_profit_factor', 0)),
                            'max_dd_pips':       stats.get('max_dd_pips', r.get('max_dd_pips', 0)),
                            # WHY (May 2026): The lot-sizing fix writes
                            #      avg_sl_distance_pips and avg_lot_size
                            #      to the matrix at backtest time. Pass
                            #      them through to the panel so
                            #      _money_for_strategy can compute ATR
                            #      exits' $ stats correctly. Without
                            #      this passthrough, ATR exits fall
                            #      back to sl=150 → $ inflated 20×.
                            # CHANGED: May 2026 — propagate sizing fields
                            'avg_sl_distance_pips': stats.get('avg_sl_distance_pips', r.get('avg_sl_distance_pips')),
                            'avg_lot_size':         stats.get('avg_lot_size',         r.get('avg_lot_size')),
                            'spread_pips':       r.get('spread_pips', 25.0),
                            'commission_pips':   r.get('commission_pips', 0.0),
                            'entry_tf':          r.get('entry_tf', ''),
                            # WHY (Phase A.48 fix): Trades are stripped from
                            #      backtest_matrix.json. Check trade_count or
                            #      total_trades instead of looking for 'trades' key.
                            # CHANGED: April 2026 — Phase A.48 fix
                            'has_trades':        (r.get('trade_count', 0) > 0 or
                                                  stats.get('total_trades', r.get('total_trades', 0)) > 0),
                            'run_settings':      r.get('run_settings', {}),
                            'rules':             r.get('rules', []),
                            'rule_indices':      r.get('rule_indices'),
                            'leverage':          r.get('leverage', r.get('run_settings', {}).get('leverage', 0)),
                            'risk_pct':          (r.get('risk_pct')
                                                  or (r.get('rules') or [{}])[0].get('risk_pct')
                                                  or r.get('run_settings', {}).get('risk_pct')
                                                  or 0),
                            'pip_value_per_lot': (r.get('pip_value_per_lot')
                                                  or (r.get('rules') or [{}])[0].get('pip_value_per_lot')
                                                  or r.get('run_settings', {}).get('pip_value_per_lot')
                                                  or 1.0),
                            'pip_size':          (r.get('pip_size')
                                                  or (r.get('rules') or [{}])[0].get('pip_size')
                                                  or 0.01),
                            'dd_daily_pct':      r.get('dd_daily_pct', r.get('run_settings', {}).get('dd_daily_pct', 0)),
                            'dd_total_pct':      r.get('dd_total_pct', r.get('run_settings', {}).get('dd_total_pct', 0)),
                            'account_size':      r.get('account_size', r.get('run_settings', {}).get('starting_capital', 0)),
                            # WHY: run_settings saves firm as 'firm_name' (no prop_ prefix).
                            #      Without checking that key, rows backtested without config
                            #      lose their firm and the refiner shows the resolver error.
                            # CHANGED: June 2026 — also read run_settings.firm_name
                            'prop_firm_name':    (r.get('prop_firm_name')
                                                  or r.get('run_settings', {}).get('prop_firm_name')
                                                  or r.get('run_settings', {}).get('firm_name')
                                                  or (r.get('rules') or [{}])[0].get('prop_firm_name')
                                                  or ''),
                            'prop_firm_stage':   (r.get('prop_firm_stage')
                                                  or r.get('run_settings', {}).get('prop_firm_stage')
                                                  or (r.get('rules') or [{}])[0].get('prop_firm_stage')
                                                  or ''),
                            # WHY: firm_id (slug like "leveraged") is what
                            #      run_settings carries. Without this passthrough
                            #      the firm resolver can't fall back on it.
                            # CHANGED: May 2026 — firm_id passthrough
                            'firm_id':           (r.get('firm_id')
                                                  or r.get('run_settings', {}).get('firm_id')
                                                  or (r.get('rules') or [{}])[0].get('prop_firm_id')
                                                  or ''),
                            'data_source_id':    r.get('data_source_id', r.get('run_settings', {}).get('data_source_id', '')),
                            # WHY: _trades_key is the backtester's GLOBAL enumerate
                            #      index (strategy_backtester.py L4916). The panel
                            #      loader needs it to find the correct entry in
                            #      backtest_trades_<TF>.json after the matrix is
                            #      re-sorted by score. trade_count is the companion
                            #      field for validation (count must match).
                            # CHANGED: May 2026 — _trades_key passthrough
                            '_trades_key':       r.get('_trades_key'),
                            'trade_count':       r.get('trade_count', 0),
                            # WHY: exit_class + exit_params live at the result row's
                            #      TOP level (set by backtester from each exit_strategy).
                            #      Without passthrough, money calcs default to SL=150
                            #      and optimizer paths get empty exit configs.
                            # CHANGED: May 2026 — exit_params/exit_class passthrough
                            'exit_class':        r.get('exit_class', ''),
                            'exit_params':       r.get('exit_params', {}),
                            # WHY: Win Pass fields written by the backtester at
                            #      result-row level. Without passthrough the grid
                            #      column shows "—" even after a fresh backtest.
                            # CHANGED: May 2026 — win_pass passthrough
                            'win_pass_passed':   r.get('win_pass_passed'),
                            'win_pass_total':    r.get('win_pass_total'),
                            'win_pass_rate':     r.get('win_pass_rate'),
                            # WHY: consecutive DD metric written by the backtester
                            #      alongside win_pass. Legacy rows default to 0.
                            # CHANGED: June 2026 — consecutive DD-breach passthrough
                            'max_consecutive_dd_breaches': r.get('max_consecutive_dd_breaches', 0),
                            # WHY (T2b): Stability verdict + time-distribution fields
                            #      attached by the auto-stability gate in run_backtest_panel.
                            #      Expose them so View Results can render the badge.
                            # CHANGED: April 2026 — T2b
                            'stability_verdict':         r.get('stability_verdict'),
                            'stability_edge_held':       r.get('stability_edge_held'),
                            'stability_avg_degradation': r.get('stability_avg_degradation'),
                            'stability_windows_tested':  r.get('stability_windows_tested', 0),
                            'stability_verdict_reason':  r.get('stability_verdict_reason'),
                        })
    except Exception as e:
        # WHY: Don't let matrix errors prevent saved rules from loading.
        log.info(f"[REFINER] Error loading backtest matrix: {e}")
        import traceback; traceback.print_exc()

    # Load optimizer results if available
    try:
        opt_path = os.path.join(os.path.dirname(BACKTEST_MATRIX_PATH), '_validator_optimized.json')
        if os.path.exists(opt_path):
            with open(opt_path, 'r', encoding='utf-8') as f:
                opt_data = json.load(f)

            # Add separator
            results.append({
                'index':        '__separator_opt__',
                'source':       'separator',
                'label':        '─── OPTIMIZER RESULTS ─────────────────────────────────────────────────────────',
                'total_trades': 0,
                'has_trades':   False,
            })

            # Add optimizer result
            opt_trades = opt_data.get('trades', [])
            opt_rules = opt_data.get('rules', [])
            opt_name = opt_data.get('name', 'Optimized Strategy')

            wr = 0
            net = 0
            pf = 0
            if opt_trades:
                wins = sum(1 for t in opt_trades if t.get('net_pips', 0) > 0)
                wr = wins / len(opt_trades) if opt_trades else 0
                net = sum(t.get('net_pips', 0) for t in opt_trades)
                gross_profit = sum(t.get('net_pips', 0) for t in opt_trades if t.get('net_pips', 0) > 0)
                gross_loss = abs(sum(t.get('net_pips', 0) for t in opt_trades if t.get('net_pips', 0) < 0))
                # WHY (Phase 66 Fix 8): Old code returned PF=0 when there were
                #      no losing trades — the condition `gross_loss > 0` guards
                #      the division but the else branch emits 0. A perfect strategy
                #      showed PF=0.00 in the optimizer cards and users dismissed it
                #      as a losing strategy. Use 99.99 as the sentinel (matching
                #      compute_stats convention from Phase 31).
                # CHANGED: April 2026 — Phase 66 Fix 8 — PF=99.99 for no-loss
                #          (audit Part E HIGH #8)
                pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 99.99

            # WHY: compute_stats in strategy_backtester.py always
            #      stores win_rate as percent (0-100). The old
            #      `wr > 1` ternary was a band-aid for an
            #      inconsistency that no longer exists — the
            #      else branch was dead code. Single format now.
            # CHANGED: April 2026 — remove dead band-aid
            wr_str = f"{wr*100:.0f}%"

            results.append({
                'index':             'optimizer_latest',
                'source':            'optimizer',
                'label':             f"🎯 {opt_name}  [{len(opt_trades)} trades, WR {wr_str}, PF {pf:.1f}, {net:+,.0f} pips]",
                'rule_combo':        opt_name,
                'exit_strategy':     'Optimized',
                'exit_name':         'Optimized',
                'total_trades':      len(opt_trades),
                'win_rate':          wr,
                'net_total_pips':    net,
                'net_avg_pips':      net / len(opt_trades) if opt_trades else 0,
                'net_profit_factor': 0,
                'max_dd_pips':       0,
                'spread_pips':       25.0,
                'commission_pips':   0.0,
                'has_trades':        True,
                'optimizer_trades':  opt_trades,
                'optimizer_rules':   opt_rules,
                # WHY (T2b): No stability data for optimizer results.
                # CHANGED: April 2026 — T2b
                'stability_verdict':         None,
                'stability_edge_held':       None,
                'stability_avg_degradation': None,
                'stability_windows_tested':  0,
                'stability_verdict_reason':  None,
            })
    except Exception:
        pass

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
                    # WHY: compute_stats in strategy_backtester.py always
                    #      stores win_rate as percent (0-100). The old
                    #      `wr > 1` ternary was a band-aid for an
                    #      inconsistency that no longer exists — the
                    #      else branch was dead code. Single format now.
                    # CHANGED: April 2026 — remove dead band-aid
                    wr_str = f"{wr:.0f}%"
                    pf = rule.get('net_profit_factor', 0)
                    source = entry.get('source', '?')
                    notes = entry.get('notes', '')
                    rid = entry.get('id', '?')

                    # WHY: Labels must show at a glance: direction, exit,
                    #      conditions count, WR, pips, PF. The old label
                    #      "Saved #12 from Robot Analysis" tells nothing.
                    # CHANGED: April 2026 — descriptive saved rule labels
                    _sr_dir = rule.get('direction', rule.get('action', ''))
                    _sr_exit = rule.get('exit_name', rule.get('exit_class', ''))
                    _sr_conds = rule.get('conditions', [])
                    _sr_n = len(_sr_conds)
                    _sr_pips = rule.get('net_total_pips', 0)
                    _sr_trades = rule.get('total_trades', 0)

                    # Build descriptive label
                    _sr_header = f"💾 #{rid}"
                    if _sr_dir:
                        _sr_header += f" {_sr_dir}"
                    _sr_header += f" ({_sr_n}c)"
                    if _sr_exit and _sr_exit not in ('?', 'Default', ''):
                        _sr_header += f" × {_sr_exit}"

                    label_parts = [_sr_header]
                    if _sr_trades > 0:
                        label_parts.append(f"{_sr_trades}tr")
                    if wr > 0:
                        label_parts.append(f"WR {wr_str}")
                    if pf > 0:
                        label_parts.append(f"PF {pf:.1f}")
                    if _sr_pips:
                        label_parts.append(f"{_sr_pips:+,.0f}p")
                    if notes:
                        label_parts.append(notes[:20])

                    # ── Check if saved rule is stale ──────────────────────────────
                    # WHY: Old saved rules may be missing exit_class, filters, entry_timeframe.
                    #      Marking them ⚠️ in the dropdown tells the user to re-save.
                    # CHANGED: April 2026 — stale saved rule detection
                    is_stale = False
                    stale_issues = []
                    try:
                        from shared.stale_check import check_saved_rule
                        rule_check = check_saved_rule(rule)
                        if rule_check['is_stale']:
                            label_parts[0] = f"⚠️ {label_parts[0]}"
                            is_stale = True
                            stale_issues = rule_check['issues']
                    except ImportError:
                        pass

                    results.append({
                        'index':             f"saved_{rid}",
                        'source':            'saved',
                        # WHY (per-row-delete v3): Expose the saved-rule
                        #      entry ID at the top level so the refiner
                        #      panel's delete handler can find it. Without
                        #      this, the panel tried strategy['saved_rule']
                        #      ['id'] which is None (id lives at entry
                        #      level, not inside rule).
                        # CHANGED: April 2026 — per-row-delete v3
                        'id':                rid,
                        'rule_id':           rid,
                        'label':             '  '.join(label_parts),
                        'rule_combo':        f"Saved #{rid}",
                        # WHY (Hotfix): Old code hardcoded 'Default' for saved rules.
                        #      Read actual exit info from the saved rule data.
                        # CHANGED: April 2026 — Hotfix
                        'exit_strategy':     rule.get('exit_strategy',
                                             rule.get('exit_name', 'Default')),
                        'exit_name':         rule.get('exit_name',
                                             rule.get('exit_class', 'Default')),
                        'exit_class':        rule.get('exit_class', ''),
                        'exit_params':       rule.get('exit_params',
                                             rule.get('exit_strategy_params', {})),
                        'entry_tf':          rule.get('entry_timeframe',
                                             rule.get('entry_tf', '')),
                        'total_trades':      rule.get('total_trades', 0),
                        'win_rate':          wr,
                        'net_total_pips':    rule.get('net_total_pips', 0),
                        'net_avg_pips':      rule.get('avg_pips', 0),
                        'net_profit_factor': rule.get('net_profit_factor', 0),
                        'max_dd_pips':       rule.get('max_dd_pips', 0),
                        # WHY (May 2026): Saved rules may have these from
                        #      a previous backtest run that wrote them
                        #      into the matrix. None when the rule was
                        #      discovered without a backtest — the
                        #      panel's _money_for_strategy fallback
                        #      handles None correctly.
                        # CHANGED: May 2026 — propagate sizing fields
                        'avg_sl_distance_pips': rule.get('avg_sl_distance_pips'),
                        'avg_lot_size':         rule.get('avg_lot_size'),
                        'spread_pips':       25.0,
                        'commission_pips':   0.0,
                        'has_trades':        False,
                        'saved_rule':        rule,  # keep the original rule for loading
                        'prop_firm_name':    rule.get('prop_firm_name', ''),
                        'prop_firm_stage':   rule.get('prop_firm_stage', ''),
                        # WHY: firm_id needed by the panel's firm resolver
                        #      when prop_firm_name doesn't match a JSON.
                        # CHANGED: May 2026 — firm_id for saved rules
                        'firm_id':           rule.get('prop_firm_id', ''),
                        'account_size':      rule.get('account_size', 0),
                        # WHY: risk_pct + pip_value_per_lot drive the money
                        #      columns (_money_for_strategy). Without these
                        #      saved rules show 3x-inflated Net $ values
                        #      because the default risk_pct=1.0 fires.
                        # CHANGED: May 2026 — risk fields for saved rules
                        'risk_pct':          rule.get('risk_pct', 0),
                        'pip_value_per_lot': rule.get('pip_value_per_lot', 1.0),
                        'pip_size':          rule.get('pip_size', 0.01),
                        'leverage':          rule.get('leverage', 0),
                        'data_source_id':    rule.get('data_source_id', ''),
                        'data_source_path':  rule.get('data_source_path', ''),
                        # WHY: Win Pass placeholder fields so saved rules
                        #      render "—" cleanly in the column (instead of
                        #      blank). Will get real values if/when the user
                        #      runs a backtest on the saved rule.
                        # CHANGED: May 2026 — Win Pass placeholders
                        'win_pass_passed':   None,
                        'win_pass_total':    None,
                        'win_pass_rate':     None,
                        'is_stale':          is_stale,
                        'stale_issues':      stale_issues,
                        # WHY (T2b): No stability data for saved rules.
                        # CHANGED: April 2026 — T2b
                        'stability_verdict':         None,
                        'stability_edge_held':       None,
                        'stability_avg_degradation': None,
                        'stability_windows_tested':  0,
                        'stability_verdict_reason':  None,
                    })
    except Exception:
        pass

    # ── Helper: compute win_pass for a My Rules entry ─────────────────────
    def _mr_compute_win_pass(mrule):
        """Run simulate_challenge on embedded trades and return win_pass dict.

        WHY: My Rules entries have embedded trades but no win_pass stats —
             the backtester never ran for them. This computes it on the fly
             at load time so Win Pass and Prop Score columns show real values.
        CHANGED: June 2026 — live win_pass for My Rules
        """
        _wp_default = {
            'win_pass_passed': None,
            'win_pass_total':  None,
            'win_pass_rate':   None,
        }
        try:
            _wp_trades = mrule.get('trades', [])
            if not _wp_trades:
                return _wp_default

            _wp_firm_id = mrule.get('prop_firm_id', '') or mrule.get('firm_id', '')
            if not _wp_firm_id:
                return _wp_default

            # Load firm JSON to get challenge_id and account_size
            import glob as _wpg
            _wp_firms_dir = os.path.join(os.path.dirname(BACKTEST_MATRIX_PATH),
                                         '..', '..', 'prop_firms')
            _wp_firms_dir = os.path.normpath(_wp_firms_dir)
            _wp_firm_data = None
            for _fp in _wpg.glob(os.path.join(_wp_firms_dir, '*.json')):
                with open(_fp, encoding='utf-8') as _ff:
                    _fd = json.load(_ff)
                if _fd.get('firm_id') == _wp_firm_id:
                    _wp_firm_data = _fd
                    break
            if not _wp_firm_data:
                return _wp_default

            _wp_challenges = _wp_firm_data.get('challenges', [])
            if not _wp_challenges:
                return _wp_default
            _wp_ch_id = _wp_challenges[0].get('challenge_id', '')
            if not _wp_ch_id:
                return _wp_default

            # Account size — from rule or first challenge size
            _wp_acct = mrule.get('account_size') or 0
            if not _wp_acct:
                _wp_sizes = _wp_challenges[0].get('account_sizes', [10000])
                _wp_acct  = _wp_sizes[0] if _wp_sizes else 10000

            _wp_risk = mrule.get('risk_pct') or 1.0
            _wp_sl   = mrule.get('avg_sl_distance_pips') or 150.0
            _wp_pipv = mrule.get('pip_value_per_lot') or 1.0

            from project2_backtesting.strategy_validator import _trades_to_df
            from shared.prop_firm_simulator import simulate_challenge as _wp_sim

            _wp_df = _trades_to_df(
                _wp_trades,
                risk_per_trade_pct=float(_wp_risk),
                default_sl_pips=float(_wp_sl),
                pip_value_per_lot=float(_wp_pipv),
                account_size=int(_wp_acct),
            )
            _wp_result = _wp_sim(
                trades_df=_wp_df,
                firm_id=_wp_firm_id,
                challenge_id=_wp_ch_id,
                account_size=int(_wp_acct),
                mode='sliding_window',
                simulate_funded=False,
                risk_per_trade_pct=float(_wp_risk),
                default_sl_pips=float(_wp_sl),
                pip_value_per_lot=float(_wp_pipv),
                symbol='XAUUSD',
            )
            _wp_wins = _wp_result.individual_results or []
            _wp_passed = sum(1 for w in _wp_wins if (w.eval_outcome or '') == 'PASS')
            _wp_total  = len(_wp_wins)
            _wp_rate   = (_wp_passed / _wp_total) if _wp_total > 0 else 0.0
            # WHY: compute consecutive DD metric here since individual_results is
            #      already in scope — avoids a second simulate_challenge call.
            # CHANGED: June 2026 — consecutive DD-breach metric for My Rules
            _max_cdd = max_consecutive_dd_breaches(_wp_result.individual_results)
            return {
                'win_pass_passed':              _wp_passed,
                'win_pass_total':               _wp_total,
                'win_pass_rate':                _wp_rate,
                'max_consecutive_dd_breaches':  _max_cdd,
            }
        except Exception:
            return _wp_default

    # ── Load My Rules (manually saved via ★ button) ───────────────────────
    # WHY: my_rules.json holds rules the user explicitly saved via the ★ My
    #      Rules button. Previously never loaded into the refiner.
    #      Loaded here with source='my_rules' so the source dropdown can filter.
    # CHANGED: June 2026 — load my_rules.json into refiner
    try:
        _my_rules_path = os.path.join(
            os.path.dirname(BACKTEST_MATRIX_PATH), '..', '..', 'my_rules.json'
        )
        _my_rules_path = os.path.normpath(_my_rules_path)
        if os.path.exists(_my_rules_path):
            with open(_my_rules_path, 'r', encoding='utf-8') as _mrf:
                _my_rules_data = json.load(_mrf)
            if _my_rules_data:
                results.append({
                    'index':        '__separator_my_rules__',
                    'source':       'separator',
                    'label':        '─── MY RULES (★) ──────────────────────────────────────────────────────────────',
                    'total_trades': 0,
                    'has_trades':   False,
                })
                for _mre in _my_rules_data:
                    _mrule  = _mre.get('rule', {})
                    _mr_rid = _mre.get('id', '?')
                    _mr_dir = _mrule.get('direction', _mrule.get('action', ''))
                    _mr_exit = _mrule.get('exit_name', _mrule.get('exit_class', ''))
                    _mr_conds = _mrule.get('conditions', [])

                    # WHY: my_rules.json stores net_total_pips=0, win_rate=0,
                    #      total_trades=0 at the rule level — they were never
                    #      summed at save time. Compute from the trades list
                    #      instead so the profitable filter and grid columns
                    #      show real values.
                    # CHANGED: June 2026 — compute stats from trades list
                    _mr_trade_list = _mrule.get('trades', [])
                    _mr_pips   = _mrule.get('net_total_pips', 0) or 0
                    _mr_wr     = _mrule.get('win_rate', 0) or 0
                    _mr_trades = _mrule.get('total_trades', 0) or 0
                    _mr_pf     = _mrule.get('net_profit_factor', 0) or 0
                    _mr_avg    = _mrule.get('avg_pips', 0) or 0

                    if _mr_trade_list and (_mr_pips == 0 or _mr_trades == 0):
                        # Compute from the trades list
                        _mr_net_pips_list = [float(t.get('net_pips', 0) or 0) for t in _mr_trade_list]
                        _mr_pips   = sum(_mr_net_pips_list)
                        _mr_trades = len(_mr_net_pips_list)
                        _mr_wins   = sum(1 for p in _mr_net_pips_list if p > 0)
                        _mr_wr     = (_mr_wins / _mr_trades * 100) if _mr_trades > 0 else 0
                        _mr_avg    = (_mr_pips / _mr_trades) if _mr_trades > 0 else 0
                        _gross_win  = sum(p for p in _mr_net_pips_list if p > 0)
                        _gross_loss = abs(sum(p for p in _mr_net_pips_list if p < 0))
                        _mr_pf     = (_gross_win / _gross_loss) if _gross_loss > 0 else 0
                    _mr_header = f"★ #{_mr_rid}"
                    if _mr_dir:
                        _mr_header += f" {_mr_dir}"
                    _mr_header += f" ({len(_mr_conds)}c)"
                    if _mr_exit and _mr_exit not in ('?', 'Default', ''):
                        _mr_header += f" × {_mr_exit}"
                    _mr_parts = [_mr_header]
                    if _mr_trades > 0:
                        _mr_parts.append(f"{_mr_trades}tr")
                    if _mr_wr > 0:
                        _mr_parts.append(f"WR {_mr_wr:.0f}%")
                    if _mr_pips:
                        _mr_parts.append(f"{_mr_pips:+,.0f}p")
                    _mr_src = _mre.get('source', '')
                    if _mr_src:
                        _mr_parts.append(_mr_src[:20])
                    results.append({
                        'index':             f"my_rules_{_mr_rid}",
                        'source':            'my_rules',
                        'id':                _mr_rid,
                        'rule_id':           _mr_rid,
                        'label':             '  '.join(_mr_parts),
                        'rule_combo':        f"My Rule #{_mr_rid}",
                        'exit_strategy':     _mrule.get('exit_strategy', _mrule.get('exit_name', 'Default')),
                        'exit_name':         _mrule.get('exit_name', _mrule.get('exit_class', 'Default')),
                        'exit_class':        _mrule.get('exit_class', ''),
                        'exit_params':       _mrule.get('exit_params', {}),
                        'entry_tf':          _mrule.get('entry_timeframe', _mrule.get('entry_tf', '')),
                        'total_trades':      _mr_trades,
                        'win_rate':          _mr_wr,
                        'net_total_pips':    _mr_pips,
                        'net_avg_pips':      _mr_avg,
                        'net_profit_factor': _mr_pf,
                        'max_dd_pips':       _mrule.get('max_dd_pips', 0),
                        'has_trades':        False,
                        'saved_rule':        _mrule,
                        'prop_firm_name':    _mrule.get('prop_firm_name', ''),
                        'firm_id':           _mrule.get('prop_firm_id', ''),
                        'account_size':      _mrule.get('account_size', 0),
                        'risk_pct':          _mrule.get('risk_pct', 0),
                        'pip_value_per_lot': _mrule.get('pip_value_per_lot', 1.0),
                        # WHY: Compute win_pass by running simulate_challenge on
                        #      the embedded trades. Takes ~50ms per rule but gives
                        #      real Win Pass and Prop Score columns in the grid.
                        #      Falls back to None on any error (no crash on import
                        #      failure, missing firm data, etc.)
                        # CHANGED: June 2026 — live win_pass for My Rules
                        **_mr_compute_win_pass(_mrule),
                        'is_stale':          False,
                        'stale_issues':      [],
                        'stability_verdict':         None,
                        'stability_edge_held':       None,
                        'stability_avg_degradation': None,
                        'stability_windows_tested':  0,
                        'stability_verdict_reason':  None,
                    })
    except Exception:
        pass

    # ── Mark starred strategies and sort to top ───────────────────────────
    # WHY: Starred strategies appear at the top of every dropdown with ⭐ prefix.
    #      This makes it easy to find your best strategies across 36+ results.
    # CHANGED: April 2026 — star/favorite system
    try:
        from shared.starred import is_starred
        for s in results:
            if s.get('source') == 'separator':
                s['is_starred'] = False
                continue
            rc = s.get('rule_combo', '')
            es = s.get('exit_strategy', s.get('exit_name', ''))
            # WHY (Phase 66 Fix 9): Old lookup used (rc, es) but two rows for
            #      the same strategy on different entry_tf (H1 vs H4) share the
            #      same rc+es. Starring the H1 row also starred the H4 row.
            #      Include entry_tf in the star key to disambiguate.
            # CHANGED: April 2026 — Phase 66 Fix 9 — entry_tf in star lookup
            #          (audit Part E HIGH #9)
            tf = s.get('entry_tf', s.get('timeframe', ''))
            if is_starred(rc, es, tf):
                s['is_starred'] = True
                if not s['label'].startswith('⭐'):
                    s['label'] = f"⭐ {s['label']}"
            else:
                s['is_starred'] = False

        starred_results = [s for s in results if s.get('is_starred')]
        non_starred = [s for s in results if not s.get('is_starred')]

        if starred_results:
            return starred_results + [{
                'index':        '__separator_starred__',
                'source':       'separator',
                'label':        '─── ALL STRATEGIES ─────────────────────────────────────────────────────────────',
                'total_trades': 0,
                'has_trades':   False,
                'is_starred':   False,
            }] + non_starred
    except ImportError:
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
    """Return session name for a given UTC hour.

    WHY: Hours where multiple sessions overlap (e.g. hour 7 = end of Asian +
         start of London; hours 12-15 = London + NY) need a tiebreaker.
         The OLD code claimed "London wins" in a comment but actually
         returned whichever session appeared first in the dict (Asian).
         Standard convention: London wins over Asian, NY wins over London
         (the higher-volume session takes the hour).
    CHANGED: April 2026 — explicit priority order
    """
    # Priority order: NY > London > Asian > Sydney
    # Higher-volume session wins overlapping hours.
    if 13 <= hour < 22:
        return "New York"
    if 7 <= hour < 16:
        return "London"
    if 0 <= hour < 8:
        return "Asian"
    # 22-23: Sydney/late
    return "Asian"


def enrich_trades(trades):
    """Add computed fields to each trade dict in-place. Returns the list.

    WHY: Old code called pd.to_datetime() 3× per trade (entry + exit for hold,
         entry again for hour/day). With 1000+ trades that's 3000+ individual
         pandas calls — each has ~0.1ms overhead = seconds of lag on load.
         Vectorized batch parsing brings this down to 2 calls total.
    CHANGED: April 2026 — vectorized datetime parsing
    """
    if not trades:
        return trades

    entry_times = pd.to_datetime(
        [t.get('entry_time', '') for t in trades], errors='coerce'
    )
    exit_times = pd.to_datetime(
        [t.get('exit_time', '') for t in trades], errors='coerce'
    )
    # WHY: pandas 3.x changed TimedeltaIndex.total_seconds() return type from
    #      Float64Index (which had .iloc) to plain Index (which does not).
    #      Use direct integer indexing [i] which works on all Index types.
    # CHANGED: May 2026 — fix hold_minutes always 0 on pandas 3.x
    hold_secs = (exit_times - entry_times).total_seconds()

    for i, t in enumerate(trades):
        try:
            hs = hold_secs[i]
            t['hold_minutes'] = float(hs) / 60.0 if pd.notna(hs) else 0.0
        except Exception:
            t['hold_minutes'] = 0.0
        t['hold_display'] = _fmt_hold(t['hold_minutes'])

        try:
            ent = entry_times[i]
            if pd.isna(ent):
                raise ValueError
            t['hour_of_day'] = int(ent.hour)
            t['day_of_week'] = _DAY_NAMES[ent.dayofweek]
            t['day_abbrev']  = t['day_of_week'][:3]
            t['session']     = _get_session(t['hour_of_day'])
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
            'profit_factor': 0.0,
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

    # Profit factor
    # WHY: Old code used max(gross_loss, 0.01) → fake 5000x PFs. Old code also
    #      returned raw gross_profit when gross_loss == 0, which is a pip count
    #      not a profit factor. Now: 99.99 sentinel for "no losses", 0.0 for
    #      "no trades", correct ratio otherwise.
    # CHANGED: April 2026 — proper PF cap
    gross_profit = sum(p for p in net if p > 0)
    gross_loss = abs(sum(p for p in net if p < 0))
    if gross_loss < 1.0:  # Treat <1 pip total losses as "no losses"
        profit_factor = 99.99 if gross_profit > 0 else 0.0
    else:
        profit_factor = gross_profit / gross_loss

    # WHY: This function returns win_rate as a FRACTION (0..1). The refiner
    #      panel multiplies by *100 to display. Do NOT change this — would
    #      break the panel. compute_stats() in strategy_backtester returns
    #      win_rate as a PERCENT (0..100). The two formats are deliberately
    #      different per their caller expectations.
    # CHANGED: April 2026 — explicit format documentation
    return {
        'count':            total,
        'win_rate':         round(float(winners / total), 4),  # FRACTION (0-1)
        'avg_pips':         round(float(np.mean(net)), 2),
        'total_pips':       round(float(np.sum(net)), 1),
        'max_dd_pips':      round(max_dd, 1),
        'trades_per_day':   round(total / n_days, 2),
        'avg_hold_minutes': round(avg_hold, 1),
        'profit_factor':    round(float(profit_factor), 2),
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
        hours (tuple (low, high) — inclusive low, exclusive high; wraps
                       midnight when low > high, e.g. (22, 7) = 22..23 + 0..6),
        cooldown_minutes,
        custom_filters: [{"feature": str, "operator": str, "value": float}]

    WHY no min_pips: The old min_pips filter dropped trades whose final P&L
    was below a threshold. That uses information not available at entry time
    (look-ahead bias), so it inflated backtest stats but could not be applied
    in live trading. Removed April 2026.
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
            # Keep first N trades chronologically — no look-ahead bias
            chrono = sorted(day_trades, key=lambda x: str(x.get('entry_time', '')))
            for t in chrono[:max_per_day]:
                allowed_ids.add(id(t))
    else:
        allowed_ids = None

    min_hold    = filters.get('min_hold_minutes')
    max_hold    = filters.get('max_hold_minutes')
    sessions    = filters.get('sessions')    # None = all
    days        = filters.get('days')        # None = all

    # WHY: hour window — supports wrap-around for sessions like Asian late
    #      (e.g., (22, 7) = hour >= 22 OR hour < 7). Inclusive low, exclusive
    #      high so windows compose cleanly: (7, 12) + (12, 17) = no overlap.
    # CHANGED: May 2026 — hours filter for optimizer hour-window sweeps
    hours       = filters.get('hours')       # None = all; (lo, hi) tuple
    cooldown    = filters.get('cooldown_minutes')
    custom      = filters.get('custom_filters', [])
    # WHY: min_pips filter removed April 2026 — look-ahead bias.

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
        elif hours is not None:
            _h = t.get('hour_of_day')
            if isinstance(_h, int) and isinstance(hours, (tuple, list)) and len(hours) == 2:
                _lo, _hi = int(hours[0]), int(hours[1])
                if _lo <= _hi:
                    # Normal range: [lo, hi)
                    if not (_lo <= _h < _hi):
                        reason = 'hour'
                else:
                    # Wrap-around range: [lo, 24) ∪ [0, hi)
                    if not (_h >= _lo or _h < _hi):
                        reason = 'hour'
            else:
                # Malformed — keep the trade
                pass
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
    """Load presets from ALL prop firm JSON files dynamically."""
    prop_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'prop_firms')
    presets = {}

    if os.path.isdir(prop_dir):
        for f in sorted(os.listdir(prop_dir)):
            if not f.endswith('.json'):
                continue
            try:
                with open(os.path.join(prop_dir, f), 'r', encoding='utf-8') as fh:
                    firm = json.load(fh)

                name = firm.get('firm_name', f.replace('.json', ''))
                c = firm['challenges'][0]
                funded = c.get('funded', {})
                restr = c.get('restrictions', {})

                daily_dd = funded.get('max_daily_drawdown_pct', 5)
                total_dd = funded.get('max_total_drawdown_pct', 10)
                dd_type = funded.get('drawdown_type', 'static')

                preset = {
                    'description': f"{name}: daily DD {daily_dd}%, total DD {total_dd}% ({dd_type})",
                    'firm_data': firm,
                }

                # Auto-generate smart filters based on firm's DD limits
                # WHY: min_pips removed April 2026 — look-ahead bias.
                #      The remaining filters (max_trades_per_day, cooldown,
                #      min_hold) are all decidable at entry time, so they
                #      stay.
                if daily_dd <= 2:
                    preset['max_trades_per_day'] = 2
                    preset['cooldown_minutes'] = 90
                elif daily_dd <= 3:
                    preset['max_trades_per_day'] = 3
                    preset['cooldown_minutes'] = 60
                else:
                    preset['max_trades_per_day'] = 5
                    preset['cooldown_minutes'] = 30

                if dd_type in ('trailing', 'trailing_eod'):
                    preset['min_hold_minutes'] = 2
                else:
                    preset['min_hold_minutes'] = 5

                presets[name] = preset

            except Exception:
                continue

    presets["Custom"] = {"description": "Set your own filters"}
    return presets


# ─────────────────────────────────────────────────────────────────────────────
# Deep Optimizer
# ─────────────────────────────────────────────────────────────────────────────

_stop_flag = threading.Event()


def stop_optimization():
    _stop_flag.set()


def _score_trades(trades, target_firm=None, stage="funded", account_size=100000,
                  sl_pips=None, risk_pct=None, dd_daily_limit=5.0, dd_total_limit=10.0,
                  optimize_goal="wins"):
    """
    Score trades for prop firm suitability.

    optimize_goal:
      "wins"   — default: maximise win rate, PF, consistency (existing behaviour)
      "min_dd" — minimise daily and total drawdown; still requires profitability
    WHY: Users whose strategies are profitable but keep hitting DD limits need a
         way to find filter combinations that reduce exposure, not just improve WR.
    CHANGED: June 2026 — optimize_goal parameter

    stage="evaluation": maximize profit speed, ignore consistency
    stage="funded": maximize consistency + survival, penalize spiky days
    account_size: account size for proper DD% calculation
    sl_pips: the ACTUAL SL distance of the strategy being scored.
             If None, falls back to config default. Used in DD math
             so strategies with wide/narrow SL are scored fairly.

    # WHY: Old code read default_sl_pips from global config and used it
    #      for every strategy. A strategy with sl_pips=300 got lot size
    #      computed with 150, producing DD% that was 2× reality.
    # CHANGED: April 2026 — accept per-strategy sl_pips (audit family #2)
    """
    if not trades or len(trades) < 5:
        return -999.0

    net = [t.get('net_pips', 0) for t in trades]
    wr = sum(1 for p in net if p > 0) / len(net)
    avg = float(np.mean(net))
    total_pips = sum(net)

    # Profit factor
    gross_profit = sum(p for p in net if p > 0)
    gross_loss = abs(sum(p for p in net if p < 0))
    # WHY: 0.01 fallback → strategies with no losers got fake PF=50,000+.
    # CHANGED: April 2026 — proper PF cap at 99.99
    if gross_loss < 1.0:
        pf = 99.99 if gross_profit > 0 else 0.0
    else:
        pf = gross_profit / gross_loss

    # Trades per day
    try:
        dates = set(str(pd.to_datetime(t['entry_time']).date()) for t in trades)
        n_days = max(len(dates), 1)
    except Exception:
        n_days = max(len(trades) // 2, 1)
    tpd = len(trades) / n_days

    # Daily P&L for consistency
    daily_pnls = {}
    for t in trades:
        try:
            day = str(pd.to_datetime(t['entry_time']).date())
        except Exception:
            continue
        daily_pnls[day] = daily_pnls.get(day, 0) + t.get('net_pips', 0)

    # Max drawdown
    cum = np.cumsum(net)
    peak = np.maximum.accumulate(cum)
    max_dd = float(np.max(peak - cum)) if len(cum) > 0 else 0

    if stage == "evaluation":
        # EVALUATION: reach profit target fast
        score = 0
        score += wr * 30
        score += min(pf, 5) * 8
        score += avg * 0.1   # bigger avg wins = faster

        if 2 <= tpd <= 6:
            score += 10
        elif tpd < 1:
            score -= 10
        elif tpd > 8:
            score -= 5

        score += min(total_pips / 1000, 20)

        # WHY: Prefer per-strategy sl_pips passed into this function.
        #      Fall back to config only when caller didn't provide one.
        #      Old code ALWAYS used config, ignoring the specific
        #      strategy's actual SL — DD math was 2× off for a
        #      strategy with sl_pips=300 (config default = 150).
        # CHANGED: April 2026 — use per-strategy sl_pips (audit family #2)
        try:
            from project2_backtesting.panels.configuration import load_config
            _cfg = load_config()
            _sl_pips_eval = float(sl_pips) if sl_pips is not None else float(_cfg.get('default_sl_pips', 150))
            pip_value     = float(_cfg.get('pip_value_per_lot', 1.0))
            risk_pct_cfg  = float(risk_pct) if risk_pct is not None else float(_cfg.get('risk_pct', 1.0))
        except Exception:
            _sl_pips_eval = float(sl_pips) if sl_pips is not None else 150.0
            pip_value    = 1.0
            risk_pct_cfg = float(risk_pct) if risk_pct is not None else 1.0
        risk_dollars  = account_size * (risk_pct_cfg / 100)
        lot_size      = max(0.01, risk_dollars / (_sl_pips_eval * pip_value)) if (_sl_pips_eval * pip_value) > 0 else 0.01
        dollar_per_pip = pip_value * lot_size
        dd_dollars = max_dd * dollar_per_pip
        dd_pct_approx = (dd_dollars / account_size) * 100

        if dd_pct_approx > dd_total_limit:
            score -= (dd_pct_approx - dd_total_limit) * 3

    else:
        # FUNDED: survive + consistency + steady payouts
        score = 0
        score += wr * 40
        score += min(pf, 5) * 8
        score += avg * 0.05

        # Consistency: best day vs total
        # WHY (Phase 36 Fix 3): Old code used max(total_pips, 1) as a
        #      floor, which combined with a tiny-positive total_pips
        #      and a large best_day gave absurdly negative consistency
        #      values (e.g. total=5, best=50 → 1 - 10 = -9). score
        #      was then -9 * 15 = -135, a huge penalty for a profitable
        #      strategy. The outer guard already ensures total_pips > 0,
        #      so the max(…, 1) floor is redundant — use total_pips
        #      directly. Clamp the result to [0, 1] so degenerate
        #      one-winning-day strategies get 0 (least consistent),
        #      not a large negative penalty.
        # CHANGED: April 2026 — Phase 36 Fix 3 — clamp consistency
        #          (audit Part C MED #36)
        if daily_pnls and total_pips > 0:
            best_day = max(daily_pnls.values())
            consistency_raw = 1.0 - (best_day / total_pips)
            consistency     = max(0.0, min(1.0, consistency_raw))
            score += consistency * 15

        # Trades per day: sweet spot 1-3
        if 1 <= tpd <= 3:
            score += 10
        elif tpd < 1:
            score -= 5
        elif tpd > 5:
            score -= (tpd - 5) * 3

        # WHY: Same fix as eval-stage block above — respect per-strategy
        #      sl_pips. See Fix 5.3b comment for full explanation.
        # CHANGED: April 2026 — use per-strategy sl_pips (audit family #2)
        # DD penalty — use config values (same block as challenge phase above)
        try:
            from project2_backtesting.panels.configuration import load_config
            _cfg = load_config()
            _sl_pips_fund = float(sl_pips) if sl_pips is not None else float(_cfg.get('default_sl_pips', 150))
            pip_value     = float(_cfg.get('pip_value_per_lot', 1.0))
            risk_pct_cfg  = float(risk_pct) if risk_pct is not None else float(_cfg.get('risk_pct', 1.0))
        except Exception:
            _sl_pips_fund = float(sl_pips) if sl_pips is not None else 150.0
            pip_value    = 1.0
            risk_pct_cfg = float(risk_pct) if risk_pct is not None else 1.0
        risk_dollars  = account_size * (risk_pct_cfg / 100)
        lot_size      = max(0.01, risk_dollars / (_sl_pips_fund * pip_value)) if (_sl_pips_fund * pip_value) > 0 else 0.01
        dollar_per_pip = pip_value * lot_size
        dd_dollars = max_dd * dollar_per_pip
        dd_pct_approx = (dd_dollars / account_size) * 100

        if dd_pct_approx > dd_total_limit:
            score -= (dd_pct_approx - dd_total_limit) * 5
        elif dd_pct_approx > dd_total_limit * 0.8:
            score -= (dd_pct_approx - dd_total_limit * 0.8) * 2

        # Trailing DD penalty
        if target_firm and isinstance(target_firm, dict):
            firm_data = target_firm.get('firm_data')
            if firm_data:
                funded = firm_data['challenges'][0].get('funded', {})
                dd_type = funded.get('drawdown_type', 'static')
                if dd_type in ('trailing', 'trailing_eod'):
                    # WHY (Phase 36 Fix 2): Old code computed
                    #      abs(min(net)) — for an all-winning strategy
                    #      that's the smallest WIN, and biggest_win > 3x
                    #      small_positive fires a -5 penalty on strategies
                    #      that should be rewarded for having zero losses.
                    #      Only penalize when there are actual losing
                    #      trades to compare against.
                    # CHANGED: April 2026 — Phase 36 Fix 2 — actual losers
                    #          (audit Part C MED #35)
                    biggest_win  = max(net) if net else 0
                    losing_nets  = [x for x in net if x < 0]
                    if losing_nets:
                        biggest_loss = abs(min(losing_nets))
                        if biggest_win > biggest_loss * 3:
                            score -= 5
                    # else: no losing trades, no trailing-DD penalty

    # ── DD-minimization goal — override score when optimize_goal="min_dd" ──
    # WHY: When the user wants to minimize DD rather than maximize wins, the
    #      standard score (which rewards WR/PF) fights the goal. Instead,
    #      compute a score that rewards keeping both daily and total DD as far
    #      below the firm limits as possible, while still requiring profitability.
    # CHANGED: June 2026 — min_dd goal scoring
    if optimize_goal == "min_dd":
        if not trades or len(trades) < 5:
            return -999.0
        # Must still be net-profitable — no point reducing DD on a loser
        _dd_net = [t.get('net_pips', 0) for t in trades]
        if sum(_dd_net) <= 0:
            return -999.0
        # Compute dollar values (same block as above)
        try:
            from project2_backtesting.panels.configuration import load_config
            _cfg_dd = load_config()
            _sl_dd  = float(sl_pips) if sl_pips is not None else float(_cfg_dd.get('default_sl_pips', 150))
            _pv_dd  = float(_cfg_dd.get('pip_value_per_lot', 1.0))
            _rp_dd  = float(risk_pct) if risk_pct is not None else float(_cfg_dd.get('risk_pct', 1.0))
        except Exception:
            _sl_dd, _pv_dd, _rp_dd = (float(sl_pips) if sl_pips else 150.0), 1.0, 1.0
        _risk_usd_dd = account_size * (_rp_dd / 100)
        _lot_dd      = max(0.01, _risk_usd_dd / (_sl_dd * _pv_dd)) if (_sl_dd * _pv_dd) > 0 else 0.01
        _dpp_dd      = _pv_dd * _lot_dd  # dollars per pip

        # Max total DD %
        _cum_dd = np.cumsum(_dd_net)
        _peak_dd = np.maximum.accumulate(_cum_dd)
        _max_dd_pips = float(np.max(_peak_dd - _cum_dd)) if len(_cum_dd) > 0 else 0.0
        _total_dd_pct = (_max_dd_pips * _dpp_dd / account_size) * 100

        # Worst single-day DD %
        _daily_pnls_dd = {}
        for _t in trades:
            try:
                _day_dd = str(pd.to_datetime(_t['entry_time']).date())
            except Exception:
                continue
            _daily_pnls_dd[_day_dd] = _daily_pnls_dd.get(_day_dd, 0) + _t.get('net_pips', 0)
        _worst_day_pips = abs(min(_daily_pnls_dd.values())) if _daily_pnls_dd else 0.0
        _daily_dd_pct   = (_worst_day_pips * _dpp_dd / account_size) * 100

        # Headroom score: how much room is left before hitting the firm limits?
        # Higher headroom = better. Score = 100 - (dd_pct / limit_pct * 100)
        _total_headroom  = max(0.0, dd_total_limit - _total_dd_pct)
        _daily_headroom  = max(0.0, dd_daily_limit - _daily_dd_pct)
        _total_pct_used  = min(_total_dd_pct / dd_total_limit, 1.0) * 100  # 0–100
        _daily_pct_used  = min(_daily_dd_pct  / dd_daily_limit,  1.0) * 100  # 0–100

        # Base score: reward headroom (lower DD = higher score)
        score = (100 - _total_pct_used) * 0.55 + (100 - _daily_pct_used) * 0.45

        # Small bonus for still being profitable (up to +10)
        _wr_dd = sum(1 for p in _dd_net if p > 0) / len(_dd_net)
        score += _wr_dd * 10

        # Hard penalty if we're already over a limit
        if _total_dd_pct > dd_total_limit:
            score -= (_total_dd_pct - dd_total_limit) * 10
        if _daily_dd_pct > dd_daily_limit:
            score -= (_daily_dd_pct - dd_daily_limit) * 10

        return round(score, 2)

    return round(score, 2)


def deep_optimize(
    trades,
    candles_df,
    indicators_df,
    base_rules,
    exit_strategies,
    pip_size=0.01,
    spread_pips=25.0,
    commission_pips=0.0,
    target_firm=None,
    account_size=100000,
    progress_callback=None,
    lock_entry=False,
    lock_exit=False,
    lock_sltp=False,
    lock_filters=False,
    # WHY (Hotfix): Quick optimize candidates need exit info so the
    #      Validate button can write it to _validator_optimized.json.
    #      Without it, the validator defaults to FixedSLTP.
    # CHANGED: April 2026 — Hotfix
    exit_class='',
    exit_params=None,
    exit_name='',
    exit_strategy_desc='',
    leverage=0,
    contract_size=100.0,
    risk_per_trade_pct=1.0,
    dd_daily_limit=5.0,
    dd_total_limit=10.0,
    # WHY: entry_bar_offset from the loaded rule — optimizer candidates must
    #      inherit the same entry timing the rule was backtested with.
    # CHANGED: May 2026 — entry bar offset in optimizer
    entry_bar_offset=0,
    # WHY: Controls what the optimizer is trying to minimize/maximize.
    #      "wins"   = default (maximize win rate, PF, consistency)
    #      "min_dd" = minimize daily and total drawdown
    # CHANGED: June 2026 — optimize_goal parameter
    optimize_goal="wins",
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

    # Resolve target firm
    if target_firm and isinstance(target_firm, str):
        presets = get_prop_firm_presets()
        target_firm_data = presets.get(target_firm, {})
    elif target_firm and isinstance(target_firm, dict):
        target_firm_data = target_firm
    else:
        target_firm_data = None

    # Get stage from target_firm_data or default
    stage = "funded"  # default
    if target_firm_data and isinstance(target_firm_data, dict):
        stage = target_firm_data.get('stage', 'funded')

    # WHY: Extract actual SL from the first exit strategy so DD scoring
    #      can reflect the real per-trade risk. If exit_strategies is
    #      empty or the first one has no sl_pips attribute, fall back
    #      to None (which makes _score_trades use the config default).
    # CHANGED: April 2026 — pass per-strategy sl_pips (audit family #2)
    _base_sl_pips = None
    try:
        if exit_strategies and len(exit_strategies) > 0:
            _first_exit = exit_strategies[0]
            if hasattr(_first_exit, 'sl_pips'):
                _base_sl_pips = float(_first_exit.sl_pips)
            elif isinstance(_first_exit, dict):
                _base_sl_pips = float(_first_exit.get('sl_pips', 150))
    except Exception:
        _base_sl_pips = None

    base_stats  = compute_stats_summary(trades)
    base_score  = _score_trades(trades, target_firm_data, stage, account_size,
                                sl_pips=_base_sl_pips, risk_pct=risk_per_trade_pct,
                                dd_daily_limit=dd_daily_limit, dd_total_limit=dd_total_limit,
                                optimize_goal=optimize_goal)
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
        # WHY: Use the base strategy's sl_pips since candidates derived
        #      from filter scanning don't change SL. Candidates that
        #      change the exit strategy will need separate handling.
        # CHANGED: April 2026 — pass per-strategy sl_pips (audit family #2)
        score = _score_trades(kept_trades, target_firm_data, stage, account_size,
                              sl_pips=_base_sl_pips, risk_pct=risk_per_trade_pct,
                              dd_daily_limit=dd_daily_limit, dd_total_limit=dd_total_limit,
                              optimize_goal=optimize_goal)
        candidate = {
            'name':             name,
            'rules':            base_rules,
            'filters_applied':  filters_applied,
            'trades':           kept_trades,
            'stats':            s,
            'prop_score':       {},
            'score':            score,
            'changes_from_base': changes,
            # WHY (Hotfix): Quick optimize candidates need exit info.
            # CHANGED: April 2026 — Hotfix
            'exit_class':       exit_class,
            'exit_params':      exit_params or {},
            'exit_name':        exit_name,
            'exit_strategy':    exit_strategy_desc,
            'risk_pct':         None,  # filled by risk optimization step if relevant
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
            # Stream the FULL candidate so the panel can render a saveable
            # card live during long deep-optimize runs.
            if progress_callback:
                try:
                    progress_callback(
                        step=0,
                        total=0,
                        message=f"New best: {name} (score {score:.1f})",
                        current_best=best_so_far,
                        candidates_tested=len(candidates),
                        improvements_found=sum(1 for c in candidates if c['score'] > base_score),
                        new_best_candidate=candidate,
                    )
                except Exception:
                    pass

    presets = get_prop_firm_presets()

    # Only test the selected firm's preset, not ALL firms
    if target_firm and isinstance(target_firm, dict):
        # Find which firm was selected by matching firm_data
        selected_firm_name = None
        for pname, pvals in presets.items():
            if pname == 'Custom':
                continue
            if pvals.get('firm_data') == target_firm.get('firm_data'):
                selected_firm_name = pname
                break

        if selected_firm_name:
            preset_list = [(selected_firm_name, presets[selected_firm_name])]
        else:
            preset_list = []
    elif target_firm and isinstance(target_firm, str) and target_firm in presets:
        preset_list = [(target_firm, presets[target_firm])]
    else:
        # No firm selected — test all presets
        preset_list = [(k, v) for k, v in presets.items() if k != 'Custom']

    # Add risk optimization steps (approximate — actual count varies by firm)
    # 20 = approx of legacy steps; +13 for new dow+hour sweeps
    total_steps = len(preset_list) + 20 + 5 + 3 + 10 + 13

    # ── Apply locks ───────────────────────────────────────────────────────────
    # WHY: User explicitly told us not to touch certain parts of the strategy.
    # CHANGED: April 2026 — surgical optimization mode
    if lock_entry:
        log.info("[LOCK] Entry rule locked — skipping condition optimization")
    if lock_exit:
        log.info("[LOCK] Exit type locked — keeping current exit strategy")
    if lock_sltp:
        log.info("[LOCK] SL/TP locked — keeping current pip distances")
    if lock_filters:
        log.info("[LOCK] Filters locked — skipping all filter combinations")

    # ── Step 1: Preset filters ────────────────────────────────────────────────
    if not lock_filters:
        for i, (pname, pvals) in enumerate(preset_list):
            if _stop_flag.is_set():
                break
            _report(f"Testing preset: {pname}", total_steps, i + 1)
            filt = {k: v for k, v in pvals.items() if k not in ('description', 'firm_data', 'stage')}
            kept, _ = apply_filters(trades, filt)
            _maybe_add(f"{pname} filters", kept, pname, filt)

    # ── Step 2: Min hold time sweep ───────────────────────────────────────────
    hold_values = [1, 2, 5, 10, 15, 20, 30]
    if not lock_filters:
        for i, hv in enumerate(hold_values):
            if _stop_flag.is_set():
                break
            step_n = len(preset_list) + i + 1
            _report(f"Testing min hold: {hv} min", total_steps, step_n)
            kept, _ = apply_filters(trades, {'min_hold_minutes': hv})
            _maybe_add(f"Min hold {hv}m", kept, f"min hold {hv}m", {'min_hold_minutes': hv})

    # ── Step 3: Max trades per day sweep ──────────────────────────────────────
    if not lock_filters:
        for i, maxn in enumerate([1, 2, 3, 5, 8]):
            if _stop_flag.is_set():
                break
            step_n = len(preset_list) + len(hold_values) + i + 1
            _report(f"Testing max trades/day: {maxn}", total_steps, step_n)
            kept, _ = apply_filters(trades, {'max_trades_per_day': maxn})
            _maybe_add(f"Max {maxn} trades/day", kept, f"max {maxn}/day", {'max_trades_per_day': maxn})

    # ── Step 4: Time-bucket sweeps (session, day-of-week, hour) ───────────────
    # WHY: Strategies often have time-of-day or day-of-week edges that are
    #      invisible at the per-rule level. Sweep each bucket type and let
    #      the optimizer surface "Friday-only" or "Hours 7-12" as ranked
    #      candidates, same UI flow as the other steps.
    # CHANGED: May 2026 — extend Step 4 with DoW and hour sweeps
    session_combos = [
        (["London"],             "London only"),
        (["New York"],           "NY only"),
        (["London", "New York"], "London + NY"),
        (["Asian", "London"],    "Asian + London"),
    ]
    # Day-of-week: individual days + common groupings
    dow_combos = [
        (["Mon"], "Mon only"),
        (["Tue"], "Tue only"),
        (["Wed"], "Wed only"),
        (["Thu"], "Thu only"),
        (["Fri"], "Fri only"),
        (["Mon", "Tue", "Wed", "Thu"],        "Mon-Thu (no Fri)"),
        (["Tue", "Wed", "Thu", "Fri"],        "Tue-Fri (no Mon)"),
        (["Tue", "Wed", "Thu"],                "Mid-week (Tue-Thu)"),
        (["Mon", "Tue", "Wed", "Thu", "Fri"], "All weekdays"),
    ]
    # Hour windows (GMT). London open at 7, NY open around 13, NY close at 22.
    # (low, high) — inclusive low, exclusive high. Wrap-around supported.
    hour_combos = [
        ((7, 12),  "Hours 07-12 (London AM)"),
        ((12, 17), "Hours 12-17 (London/NY overlap)"),
        ((13, 21), "Hours 13-21 (NY)"),
        ((22, 7),  "Hours 22-07 (Asian, wraps midnight)"),
    ]
    base_step = len(preset_list) + len(hold_values) + 5
    if not lock_filters:
        # Session sweeps
        for i, (sess, desc) in enumerate(session_combos):
            if _stop_flag.is_set():
                break
            _report(f"Testing sessions: {desc}", total_steps, base_step + i + 1)
            kept, _ = apply_filters(trades, {'sessions': sess})
            _maybe_add(f"Session: {desc}", kept, f"sessions={desc}", {'sessions': sess})

        # Day-of-week sweeps
        _dow_offset = base_step + len(session_combos)
        for i, (dows, desc) in enumerate(dow_combos):
            if _stop_flag.is_set():
                break
            _report(f"Testing days: {desc}", total_steps, _dow_offset + i + 1)
            kept, _ = apply_filters(trades, {'days': dows})
            _maybe_add(f"DoW: {desc}", kept, f"days={desc}", {'days': dows})

        # Hour-window sweeps
        _hr_offset = _dow_offset + len(dow_combos)
        for i, (hrange, desc) in enumerate(hour_combos):
            if _stop_flag.is_set():
                break
            _report(f"Testing hours: {desc}", total_steps, _hr_offset + i + 1)
            kept, _ = apply_filters(trades, {'hours': hrange})
            _maybe_add(f"Hour: {desc}", kept, f"hours={hrange[0]:02d}-{hrange[1]:02d}",
                       {'hours': hrange})

    # ── Step 5: Combination — hold + max/day ──────────────────────────────────
    combos = [(5, 3), (5, 5), (10, 3), (2, 5), (15, 2)]
    base_step2 = base_step + len(session_combos) + len(dow_combos) + len(hour_combos)
    if not lock_filters:
        for i, (hold, maxd) in enumerate(combos):
            if _stop_flag.is_set():
                break
            _report(f"Combo: min hold {hold}m + max {maxd}/day", total_steps, base_step2 + i + 1)
            filt = {'min_hold_minutes': hold, 'max_trades_per_day': maxd}
            kept, _ = apply_filters(trades, filt)
            _maybe_add(f"Hold {hold}m + max {maxd}/day", kept,
                       f"min hold {hold}m, max {maxd}/day", filt)

    # ── Step 5b: DD-breach diagnosis → targeted actionable filters ────────────
    # WHY: When a strategy has daily DD breaches, the optimizer needs to find
    #      filters that remove the SPECIFIC losing trades — but using only
    #      information known at trade entry (hour, session, day-of-week, hold
    #      time, trade count that day). This step:
    #   1. Identifies which trades caused daily DD breaches (pips × lot → $)
    #   2. Analyses what those trades have in common (hour, session, DoW)
    #   3. Generates targeted filter candidates from those patterns
    #   All resulting filters are 100% applicable in live trading.
    # CHANGED: June 2026 — breach diagnosis replaces look-ahead breach-day filter
    if not lock_filters and not _stop_flag.is_set():
        try:
            # ── Compute daily pnl in dollars to find breach days ──
            from project2_backtesting.panels.configuration import load_config as _bdd_load
            _bdd_cfg  = _bdd_load()
            _bdd_sl   = float(sl_pips) if sl_pips is not None else float(_bdd_cfg.get('default_sl_pips', 150))
            _bdd_pipv = float(_bdd_cfg.get('pip_value_per_lot', 1.0))
            _bdd_rp   = float(risk_per_trade_pct) if risk_per_trade_pct else float(_bdd_cfg.get('risk_pct', 1.0))
        except Exception:
            _bdd_sl, _bdd_pipv, _bdd_rp = 150.0, 1.0, 1.0

        _bdd_risk_usd = account_size * (_bdd_rp / 100)
        _bdd_lot      = max(0.01, _bdd_risk_usd / (_bdd_sl * _bdd_pipv)) if (_bdd_sl * _bdd_pipv) > 0 else 0.01
        _bdd_dpp      = _bdd_pipv * _bdd_lot  # dollars per pip

        # Get daily DD limit from firm or default
        _bdd_daily_limit_pct = dd_daily_limit  # passed into deep_optimize
        _bdd_daily_limit_usd = account_size * (_bdd_daily_limit_pct / 100)

        # Compute cumulative daily $ pnl per day
        _bdd_day_usd = {}
        for _bt in trades:
            try:
                _bdd_d = str(pd.to_datetime(_bt['entry_time']).date())
            except Exception:
                continue
            _bdd_day_usd[_bdd_d] = (
                _bdd_day_usd.get(_bdd_d, 0.0)
                + float(_bt.get('net_pips', 0) or 0) * _bdd_dpp
            )

        # Identify breach days (days where daily $ loss >= firm daily DD limit)
        _bdd_breach_days = {d for d, usd in _bdd_day_usd.items()
                            if usd < -_bdd_daily_limit_usd}

        if _bdd_breach_days:
            # Collect trades that occurred on breach days
            _bdd_breach_trades = [
                t for t in trades
                if str(pd.to_datetime(t.get('entry_time', '')).date()) in _bdd_breach_days
            ]

            if _bdd_breach_trades:
                _bdd_base = base_step2 + len(combos)
                _bdd_step = [0]

                def _bdd_report(msg):
                    _bdd_step[0] += 1
                    _report(f"DD diagnosis: {msg}", total_steps, _bdd_base + _bdd_step[0])

                # ── Analysis: what do breach trades have in common? ──

                # Hours on breach days
                _bdd_hours = [t.get('hour_of_day') for t in _bdd_breach_trades
                              if t.get('hour_of_day') is not None]
                # Hours on ALL days
                _all_hours = [t.get('hour_of_day') for t in trades
                              if t.get('hour_of_day') is not None]

                if _bdd_hours and _all_hours:
                    from collections import Counter
                    _hour_breach_counts = Counter(_bdd_hours)
                    _hour_all_counts    = Counter(_all_hours)
                    # Find hours over-represented in breach trades vs all trades
                    _breach_hour_ratio = {}
                    for h, bc in _hour_breach_counts.items():
                        ac = _hour_all_counts.get(h, 1)
                        _breach_hour_ratio[h] = (bc / len(_bdd_breach_trades)) / (ac / len(_all_hours))
                    # Hours with ratio > 1.5 are more common in breach days
                    _hot_hours = sorted(
                        [h for h, r in _breach_hour_ratio.items() if r > 1.5],
                        key=lambda h: -_breach_hour_ratio[h]
                    )
                    if _hot_hours:
                        # Build hour-exclusion filter: avoid those specific hours
                        # Group consecutive hot hours into windows
                        _hot_set = set(_hot_hours)
                        # Find safe hours (not in hot set) and test that as a window
                        _safe_hours = sorted(set(_all_hours) - _hot_set)
                        if _safe_hours and len(_safe_hours) >= 2:
                            _safe_lo = min(_safe_hours)
                            _safe_hi = max(_safe_hours) + 1
                            _bdd_report(f"exclude risky hours {_hot_hours[:3]}")
                            _hf = {'hours': (_safe_lo, _safe_hi)}
                            _hk, _ = apply_filters(trades, _hf)
                            _maybe_add(
                                f"DD: avoid hours {_hot_hours[:3]} (breach-concentrated)",
                                _hk,
                                f"hours {_safe_lo:02d}-{_safe_hi:02d} (avoids breach hours)",
                                _hf,
                            )

                # Day-of-week on breach days
                _bdd_dows = []
                for _bt in _bdd_breach_trades:
                    try:
                        _bdd_dows.append(
                            pd.to_datetime(_bt['entry_time']).strftime('%a')
                        )
                    except Exception:
                        pass
                _all_dows = []
                for _bt in trades:
                    try:
                        _all_dows.append(
                            pd.to_datetime(_bt['entry_time']).strftime('%a')
                        )
                    except Exception:
                        pass

                if _bdd_dows and _all_dows:
                    from collections import Counter
                    _dow_breach = Counter(_bdd_dows)
                    _dow_all    = Counter(_all_dows)
                    # Days over-represented in breaches
                    _risky_dows = [
                        dow for dow, bc in _dow_breach.items()
                        if bc / len(_bdd_breach_trades) > (_dow_all.get(dow, 1) / len(_all_dows)) * 1.5
                    ]
                    if _risky_dows:
                        _safe_dows = [d for d in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
                                      if d not in _risky_dows]
                        if _safe_dows and len(_safe_dows) >= 2:
                            _bdd_report(f"exclude risky DoW {_risky_dows}")
                            _df = {'days': _safe_dows}
                            _dk, _ = apply_filters(trades, _df)
                            _maybe_add(
                                f"DD: avoid {', '.join(_risky_dows)} (breach-concentrated)",
                                _dk,
                                f"days={', '.join(_safe_dows)} (skip {', '.join(_risky_dows)})",
                                _df,
                            )

                # Session on breach days (Asian, London, New York)
                _bdd_sessions = [t.get('session') for t in _bdd_breach_trades
                                 if t.get('session') and t.get('session') != 'Unknown']
                _all_sessions  = [t.get('session') for t in trades
                                  if t.get('session') and t.get('session') != 'Unknown']
                if _bdd_sessions and _all_sessions:
                    from collections import Counter
                    _sess_breach = Counter(_bdd_sessions)
                    _sess_all    = Counter(_all_sessions)
                    # Sessions over-represented in breach trades
                    _risky_sessions = [
                        s for s, bc in _sess_breach.items()
                        if (bc / len(_bdd_sessions)) > (_sess_all.get(s, 1) / len(_all_sessions)) * 1.5
                    ]
                    if _risky_sessions:
                        _safe_sessions = [s for s in ['Asian', 'London', 'New York']
                                          if s not in _risky_sessions and s in _sess_all]
                        if _safe_sessions:
                            _bdd_report(f"exclude risky sessions {_risky_sessions}")
                            _sf = {'sessions': _safe_sessions}
                            _sk, _ = apply_filters(trades, _sf)
                            _maybe_add(
                                f"DD: avoid {', '.join(_risky_sessions)} session (breach-concentrated)",
                                _sk,
                                f"sessions={', '.join(_safe_sessions)} (skip {', '.join(_risky_sessions)})",
                                _sf,
                            )
                            # Also test: avoid risky session + max 1/day combined
                            if not _stop_flag.is_set() and len(_safe_sessions) >= 1:
                                _sf2 = {'sessions': _safe_sessions, 'max_trades_per_day': 2}
                                _sk2, _ = apply_filters(trades, _sf2)
                                _maybe_add(
                                    f"DD: avoid {', '.join(_risky_sessions)} + max 2/day",
                                    _sk2,
                                    f"sessions={', '.join(_safe_sessions)}, max 2/day",
                                    _sf2,
                                )

                # Hold time on breach trades vs all trades
                _bdd_holds = [t.get('hold_minutes', 0) for t in _bdd_breach_trades
                              if t.get('hold_minutes') is not None]
                _all_holds  = [t.get('hold_minutes', 0) for t in trades
                               if t.get('hold_minutes') is not None]
                if _bdd_holds and _all_holds:
                    _med_breach_hold = sorted(_bdd_holds)[len(_bdd_holds) // 2]
                    _med_all_hold    = sorted(_all_holds)[len(_all_holds) // 2]
                    # If breach trades have significantly shorter holds, try min_hold
                    if _med_breach_hold < _med_all_hold * 0.75:
                        _suggested_min = int(_med_breach_hold * 1.5)
                        if _suggested_min > 0:
                            _bdd_report(f"min hold to skip fast breach trades")
                            _mhf = {'min_hold_minutes': _suggested_min}
                            _mhk, _ = apply_filters(trades, _mhf)
                            _maybe_add(
                                f"DD: min hold {_suggested_min}m (breach trades held shorter)",
                                _mhk,
                                f"min hold {_suggested_min}m (targets breach-trade pattern)",
                                _mhf,
                            )

                # Trade sequence: are breaches the Nth trade of the day?
                _bdd_seq = []
                _day_seq_count = {}
                for _bt in sorted(trades, key=lambda x: str(x.get('entry_time', ''))):
                    try:
                        _d = str(pd.to_datetime(_bt['entry_time']).date())
                    except Exception:
                        continue
                    _day_seq_count[_d] = _day_seq_count.get(_d, 0) + 1
                    if _d in _bdd_breach_days:
                        _bdd_seq.append(_day_seq_count[_d])

                if _bdd_seq:
                    _med_seq = sorted(_bdd_seq)[len(_bdd_seq) // 2]
                    if _med_seq >= 2:
                        # Breach trades tend to be the 2nd+ trade of the day
                        _bdd_report("max 1 trade/day (breaches on later trades)")
                        _s1f = {'max_trades_per_day': _med_seq - 1}
                        _s1k, _ = apply_filters(trades, _s1f)
                        _maybe_add(
                            f"DD: max {_med_seq - 1} trades/day (breach trades are #{_med_seq}+)",
                            _s1k,
                            f"max {_med_seq - 1}/day (targets breach sequence pattern)",
                            _s1f,
                        )

        # ── Risk reduction: lower risk% to stay under daily DD limit ──────────
        # WHY: If the entry/filter pattern can't be changed, lower risk so that
        #      even on bad days the loss stays within the daily DD limit. This IS
        #      actionable in live trading — just size down.
        # CHANGED: June 2026 — risk-reduction as DD alternative
        if not _stop_flag.is_set() and _bdd_breach_days:
            # What risk% would keep the worst losing day within the limit?
            _worst_day_pips = 0.0
            for _d, _usd in _bdd_day_usd.items():
                if _usd < 0:
                    _pips = abs(_usd) / max(_bdd_dpp, 0.0001)
                    _worst_day_pips = max(_worst_day_pips, _pips)

            if _worst_day_pips > 0:
                # risk% that would keep worst day at 80% of daily DD limit
                _target_loss_usd = _bdd_daily_limit_usd * 0.80
                # At current risk, worst day costs: _worst_day_pips * _bdd_dpp
                # At new risk r: worst day costs _worst_day_pips * (r/_bdd_rp) * _bdd_dpp
                # Solve: _worst_day_pips * (r/_bdd_rp) * _bdd_dpp = _target_loss_usd
                _safe_risk = (_target_loss_usd / max(_worst_day_pips * _bdd_dpp, 0.0001)) * _bdd_rp
                _safe_risk = round(max(0.1, min(_safe_risk, _bdd_rp)), 2)
                if _safe_risk < _bdd_rp:
                    _bdd_report(f"risk {_safe_risk}% to survive worst day")
                    # Can't filter by risk in apply_filters (it's a lot-size param),
                    # but we can still score it via _maybe_add with the current trades
                    # and note it as a risk suggestion in the name
                    _maybe_add(
                        f"DD: reduce risk to {_safe_risk}% (worst day within limit)",
                        trades,  # same trades, different risk changes the score
                        f"risk={_safe_risk}% (keeps worst day < {_bdd_daily_limit_pct}% DD)",
                        {'risk_pct_suggestion': _safe_risk},
                    )

    # ── Step 6: Risk % optimization ──────────────────────────────────────────
    # WHY: Different risk levels produce different DD profiles. The optimizer
    #      tests a grid of risk values on the best candidate's trades to find
    #      the sweet spot — maximum score balancing profit speed and DD safety.
    #      This is fast because risk only affects lot size → DD%, not trades.
    # CHANGED: April 2026 — risk optimization step
    if not _stop_flag.is_set():
        # Build risk grid from firm trading_rules or default
        _risk_grid = [0.25, 0.3, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
        if target_firm_data and isinstance(target_firm_data, dict):
            _firm_data = target_firm_data.get('firm_data', {})
            _trading_rules = _firm_data.get('trading_rules', [])
            for _rule in _trading_rules:
                if _rule.get('stage') == stage:
                    _params = _rule.get('parameters', {})
                    _range = _params.get('risk_pct_range', [])
                    if _range and len(_range) == 2:
                        _lo, _hi = float(_range[0]), float(_range[1])
                        # Build fine grid within the firm's recommended range
                        _risk_grid = []
                        _step = round((_hi - _lo) / 8, 2)
                        if _step < 0.05:
                            _step = 0.05
                        _v = _lo
                        while _v <= _hi + 0.001:
                            _risk_grid.append(round(_v, 2))
                            _v += _step
                        # Also test slightly outside the range
                        if _lo > 0.1:
                            _risk_grid.insert(0, round(_lo * 0.75, 2))
                        _risk_grid.append(round(_hi * 1.2, 2))
                        break
                    _single = _params.get('risk_pct')
                    if _single:
                        _s = float(_single)
                        _risk_grid = [round(_s * 0.5, 2), round(_s * 0.75, 2),
                                      _s, round(_s * 1.25, 2), round(_s * 1.5, 2)]
                        break

        # Use best candidate's trades (or base trades if no candidates)
        _risk_trades = trades
        _risk_filters = {}
        if candidates:
            _best = candidates[0]
            _risk_trades = _best.get('trades', trades)
            _risk_filters = _best.get('filters_applied', {})

        _risk_base_step = base_step2 + len(combos) + 1 if not lock_filters else base_step + 1
        for _ri, _rp in enumerate(_risk_grid):
            if _stop_flag.is_set():
                break
            _report(f"Risk test: {_rp}%", total_steps, _risk_base_step + _ri)

            _r_score = _score_trades(_risk_trades, target_firm_data, stage, account_size,
                                     sl_pips=_base_sl_pips, risk_pct=_rp if _rp else risk_per_trade_pct,
                                     dd_daily_limit=dd_daily_limit, dd_total_limit=dd_total_limit,
                                     optimize_goal=optimize_goal)
            if _r_score > -900:
                _r_stats = compute_stats_summary(_risk_trades)
                _r_candidate = {
                    'name':             f"Risk {_rp}%",
                    'rules':            base_rules,
                    'filters_applied':  dict(_risk_filters),
                    'trades':           _risk_trades,
                    'stats':            _r_stats,
                    'prop_score':       {},
                    'score':            _r_score,
                    'changes_from_base': f"risk={_rp}%",
                    'exit_class':       exit_class,
                    'exit_params':      exit_params or {},
                    'exit_name':        exit_name,
                    'exit_strategy_desc': exit_strategy_desc,
                    'risk_pct':         _rp,
                }
                candidates.append(_r_candidate)

                if _r_score > best_so_far['score']:
                    best_so_far = {
                        'name':           f"Risk {_rp}%",
                        'trades':         len(_risk_trades),
                        'win_rate':       _r_stats['win_rate'],
                        'avg_pips':       _r_stats['avg_pips'],
                        'trades_per_day': _r_stats['trades_per_day'],
                        'prop_pass_rate': None,
                        'score':          _r_score,
                        'risk_pct':       _rp,
                    }

        print(f"[OPTIMIZER] Risk optimization: tested {len(_risk_grid)} values "
              f"({min(_risk_grid):.2f}% - {max(_risk_grid):.2f}%)")

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

    # WHY: Candidates inherit the rule's entry_bar_offset so saved rules
    #      from the optimizer carry the correct offset for EA generation.
    # CHANGED: May 2026 — preserve entry bar offset in optimizer candidates
    for _c in candidates:
        if 'entry_bar_offset' not in _c:
            _c['entry_bar_offset'] = entry_bar_offset

    return candidates  # return ALL — panel handles filtering/display


# ─────────────────────────────────────────────────────────────────────────────
# Deep Optimizer — Generate New Trades (modifies rules, re-runs backtests)
# ─────────────────────────────────────────────────────────────────────────────

def deep_optimize_generate(
    trades,
    base_rules,
    candles_path,
    timeframe=None,  # WHY: None = read from config. Don't default to H1.
    pip_size=0.01,
    spread_pips=25.0,
    commission_pips=0.0,
    target_firm=None,
    account_size=100000,
    filters=None,
    progress_callback=None,
    feature_matrix_path=None,
    direction='BUY',  # NEW: pass strategy direction; was hardcoded BUY
    leverage=0,
    contract_size=100.0,
    risk_per_trade_pct=1.0,
    dd_daily_limit=5.0,
    dd_total_limit=10.0,
    # WHY: Per-firm cost/exit parity with Run Backtest. Forwarded to
    #      fast_backtest. Defaults preserve pre-prompt behaviour.
    # CHANGED: April 2026 — per-firm parity in deep optimizer
    max_spread_pips=0.0,
    hard_close_hour=-1,
    variable_spread=False,
    session_spread_multipliers=None,
    min_hold_minutes=0,
    cooldown_candles=0,
    slippage_pips=0.0,
    # WHY: entry_bar_offset from the loaded rule — optimizer must produce
    #      trades with the same entry timing the rule was backtested with.
    # CHANGED: May 2026 — entry bar offset in deep optimizer
    entry_bar_offset=0,
    # WHY: Controls what the optimizer is trying to minimize/maximize.
    # CHANGED: June 2026 — optimize_goal parameter
    optimize_goal="wins",
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
    if timeframe is None:
        # WHY: The panel (strategy_refiner_panel.py) resolves entry_tf from the
        #      selected strategy row and passes it explicitly. This block only
        #      runs when called directly without a timeframe (e.g. in tests).
        #      Try analysis_report first, fall back to global config.
        # CHANGED: April 2026 — multi-TF support; try report before config
        try:
            import json as _json, os as _os
            _here = _os.path.dirname(_os.path.abspath(__file__))
            _report = _os.path.join(_here, '..', 'project1_reverse_engineering',
                                    'outputs', 'analysis_report.json')
            if _os.path.exists(_report):
                with open(_report, 'r', encoding='utf-8') as _f:
                    _r = _json.load(_f)
                timeframe = _r.get('entry_timeframe') or None
        except Exception:
            pass
        if not timeframe:
            try:
                from project2_backtesting.panels.configuration import load_config
                timeframe = load_config().get('winning_scenario', 'H1')
            except Exception:
                timeframe = 'H1'
    log.info(f"[REFINER] deep_optimize_generate using entry TF: {timeframe}")

    _stop_flag.clear()
    start_time = time.time()
    candidates = []

    import sys
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from project2_backtesting.strategy_backtester import run_backtest, compute_stats, fast_backtest
    from project2_backtesting.exit_strategies import (
        FixedSLTP, TrailingStop,
    )

    from shared.data_sources import assert_not_lfs_stub
    assert_not_lfs_stub(candles_path)
    candles_df = pd.read_csv(candles_path)
    ts_col = candles_df.columns[0]
    candles_df['timestamp'] = pd.to_datetime(candles_df[ts_col]).astype('datetime64[ns]')

    # Load indicators — partial build for speed
    data_dir = os.path.dirname(candles_path)
    indicators_df = None

    # WHY: Old code built cache_path as candles_path.replace('.csv',
    #      '_indicators.parquet') → e.g. data/xauusd_H1_indicators.parquet,
    #      but strategy_backtester._load_tf_indicators writes the full
    #      cache to data_dir/.cache_{tf}_indicators.parquet. Different
    #      filenames meant the refiner's fast-path cache check NEVER hit,
    #      forcing a full ~5 minute rebuild on every Deep Explore run.
    #      Match the backtester's path format so the fast-path works.
    # CHANGED: April 2026 — Phase 30 Fix 5 — match backtester cache path
    #          (audit Part C HIGH #30)
    # WHY (Deep Optimizer Fix): Old code loaded the per-TF cache
    #      (.cache_H1_indicators.parquet) which only has H1_ columns.
    #      Rules use cross-TF indicators (M5_, M15_, H4_, D1_) which
    #      aren't in a single-TF cache. The cross-TF build was skipped
    #      because indicators_df was already set, causing every
    #      fast_backtest call to fail (missing columns) and producing
    #      "No candidates found."
    #      Fix: Check if the cache has cross-TF columns. If not, set
    #      indicators_df = None so the cross-TF build runs.
    # CHANGED: April 2026 — Deep Optimizer Fix
    cache_path = os.path.join(data_dir, f".cache_{timeframe}_indicators.parquet")
    if os.path.exists(cache_path):
        log.info(f"  [GENERATE] Loading cached indicators: {cache_path}")
        indicators_df = pd.read_parquet(cache_path)
        if 'timestamp' in indicators_df.columns:
            indicators_df['timestamp'] = indicators_df['timestamp'].astype('datetime64[ns]')

        # Check if rules need cross-TF columns not in this cache
        _needed_prefixes = set()
        for r in base_rules:
            for c in r.get('conditions', []):
                feat = c.get('feature', '')
                parts = feat.split('_', 1)
                if len(parts) == 2 and parts[0] in ('M5', 'M15', 'H1', 'H4', 'D1'):
                    _needed_prefixes.add(parts[0])

        _cache_prefixes = set()
        for col in indicators_df.columns:
            if col == 'timestamp':
                continue
            parts = col.split('_', 1)
            if len(parts) == 2 and parts[0] in ('M5', 'M15', 'H1', 'H4', 'D1'):
                _cache_prefixes.add(parts[0])

        _missing_tfs = _needed_prefixes - _cache_prefixes
        if _missing_tfs:
            log.info(f"  [GENERATE] Per-TF cache missing cross-TF data: "
                     f"need {_needed_prefixes}, cache has {_cache_prefixes}, "
                     f"missing {_missing_tfs} — forcing cross-TF build")
            indicators_df = None  # Force the cross-TF build below

    # Load top features list first (needed for partial build)
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

    if indicators_df is None:
        log.info(f"  [GENERATE] Building indicators (partial — rules + top features)...")
        from project2_backtesting.strategy_backtester import (
            build_multi_tf_indicators, _extract_required_indicators
        )

        # Get indicators the rules need
        required = _extract_required_indicators(base_rules)

        # Also add top features from feature importance
        if top_features:
            for feat_name in top_features[:30]:
                if isinstance(feat_name, (list, tuple)):
                    feat_name = feat_name[0]
                parts = feat_name.split('_', 1)
                if len(parts) == 2 and parts[0] in ('M5', 'M15', 'H1', 'H4', 'D1'):
                    required.setdefault(parts[0], [])
                    if parts[1] not in required[parts[0]]:
                        required[parts[0]].append(parts[1])

        total = sum(len(v) for v in required.values())
        log.info(f"  [GENERATE] Loading {total} indicators across {len(required)} TFs")

        indicators_df = build_multi_tf_indicators(
            data_dir, candles_df['timestamp'], required_indicators=required,
            entry_tf=timeframe)
        log.info(f"  [GENERATE] Built {len(indicators_df.columns)} indicator columns")

    # ── Pre-compute SMART/REGIME features ONCE ────────────────────────────
    # WHY: run_backtest re-computes SMART features on every call (275 times).
    #      Computing once here and passing the enriched indicators_df saves
    #      massive redundant computation.
    # CHANGED: April 2026 — pre-compute for speed
    smart_needed = any(
        c.get('feature', '').startswith('SMART_')
        for r in base_rules for c in r.get('conditions', [])
    )
    regime_needed = any(
        c.get('feature', '').startswith('REGIME_')
        for r in base_rules for c in r.get('conditions', [])
    )

    if smart_needed and not any(c.startswith('SMART_') for c in indicators_df.columns):
        try:
            from project1_reverse_engineering.smart_features import (
                _add_tf_divergences, _add_indicator_dynamics,
                _add_alignment_scores, _add_session_intelligence,
                _add_volatility_regimes, _add_price_action,
                _add_momentum_quality,
            )
            if 'hour_of_day' not in indicators_df.columns:
                indicators_df['hour_of_day'] = candles_df['timestamp'].dt.hour
            if 'open_time' not in indicators_df.columns:
                indicators_df['open_time'] = candles_df['timestamp'].astype(str)
            indicators_df = _add_tf_divergences(indicators_df)
            indicators_df = _add_indicator_dynamics(indicators_df)
            indicators_df = _add_alignment_scores(indicators_df)
            indicators_df = _add_session_intelligence(indicators_df)
            indicators_df = _add_volatility_regimes(indicators_df)
            indicators_df = _add_price_action(indicators_df)
            indicators_df = _add_momentum_quality(indicators_df)
            log.info(f"  [GENERATE] Pre-computed SMART features: "
                     f"{sum(1 for c in indicators_df.columns if c.startswith('SMART_'))} columns")
        except Exception as e:
            log.info(f"  [GENERATE] SMART feature error: {e}")

    if regime_needed and not any(c.startswith('REGIME_') for c in indicators_df.columns):
        try:
            from project1_reverse_engineering.smart_features import _add_regime_features
            indicators_df = _add_regime_features(indicators_df)
            log.info(f"  [GENERATE] Pre-computed REGIME features")
        except Exception as e:
            log.info(f"  [GENERATE] REGIME feature error: {e}")

    # ── Pre-trim DataFrames (skip warmup) — do this ONCE, not 275 times ──
    # WHY: run_backtest trims warmup (first 200 candles) every call.
    #      Pre-trim here so fast_backtest doesn't need to.
    # CHANGED: April 2026 — eliminate redundant trimming
    _candles_trimmed    = candles_df.iloc[200:].reset_index(drop=True)
    _indicators_trimmed = indicators_df.iloc[200:].reset_index(drop=True)
    log.info(f"  [GENERATE] Pre-trimmed to {len(_candles_trimmed)} candles (skipped 200 warmup)")

    available_indicators = [c for c in indicators_df.columns if c != 'timestamp']

    default_sl = 150.0
    default_tp = 300.0
    exit_strategies = [
        FixedSLTP(sl_pips=default_sl, tp_pips=default_tp, pip_size=pip_size),
        FixedSLTP(sl_pips=100, tp_pips=200, pip_size=pip_size),
        FixedSLTP(sl_pips=200, tp_pips=400, pip_size=pip_size),
        TrailingStop(sl_pips=default_sl, trail_distance_pips=100, pip_size=pip_size),
        TrailingStop(sl_pips=default_sl, trail_distance_pips=50, pip_size=pip_size),
    ]

    # Resolve target firm
    if target_firm and isinstance(target_firm, str):
        presets = get_prop_firm_presets()
        target_firm_data = presets.get(target_firm, {})
    elif target_firm and isinstance(target_firm, dict):
        target_firm_data = target_firm
    else:
        target_firm_data = None

    # Get stage from target_firm_data or default
    stage = "funded"  # default
    if target_firm_data and isinstance(target_firm_data, dict):
        stage = target_firm_data.get('stage', 'funded')

    # WHY: Extract actual SL from the first exit strategy for proper DD scoring.
    # CHANGED: April 2026 — pass per-strategy sl_pips (audit family #2)
    _base_sl_pips = None
    try:
        if exit_strategies and len(exit_strategies) > 0:
            _first_exit = exit_strategies[0]
            if hasattr(_first_exit, 'sl_pips'):
                _base_sl_pips = float(_first_exit.sl_pips)
            elif isinstance(_first_exit, dict):
                _base_sl_pips = float(_first_exit.get('sl_pips', 150))
    except Exception:
        _base_sl_pips = None

    base_stats = compute_stats_summary(trades)
    base_score = _score_trades(trades, target_firm_data, stage, account_size,
                                sl_pips=_base_sl_pips, risk_pct=risk_per_trade_pct,
                                dd_daily_limit=dd_daily_limit, dd_total_limit=dd_total_limit,
                                optimize_goal=optimize_goal)
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
        """Test a rule set using fast_backtest (no DataFrame copies).

        WHY: run_backtest copies 130K×670 DataFrames every call.
             fast_backtest uses pre-trimmed, pre-SMART'd data — read-only.
             ~10-50x faster for the 275 iterations in deep optimization.
        CHANGED: April 2026 — use fast_backtest for speed
        """
        nonlocal best_so_far
        try:
            # WHY: Old code hardcoded direction="BUY". For SELL strategies the
            #      optimizer would generate BUY trades unrelated to what the
            #      strategy actually does. Use the strategy direction passed
            #      to the outer function.
            # CHANGED: April 2026 — respect strategy direction
            new_trades = fast_backtest(
                df=_candles_trimmed,
                ind=_indicators_trimmed,
                rules=rules,
                exit_strategy=exit_strat,
                direction=direction,
                pip_size=pip_size,
                spread_pips=spread_pips,
                commission_pips=commission_pips,
                account_size=account_size,
                leverage=leverage,
                contract_size=contract_size,
                risk_per_trade_pct=risk_per_trade_pct,
                # WHY: Pass data_dir so tick/M1 resolution works during deep
                #      optimization — same exit behavior as the main backtest.
                #      data_dir is captured from the enclosing function scope.
                # CHANGED: April 2026 — tick/M1 parity in deep optimizer
                data_dir=data_dir,
                # WHY: Per-firm parity — same cost/exit model as Run Backtest.
                # CHANGED: April 2026 — per-firm parity in deep optimizer
                max_spread_pips=max_spread_pips,
                hard_close_hour=hard_close_hour,
                variable_spread=variable_spread,
                session_spread_multipliers=session_spread_multipliers,
                min_hold_minutes=min_hold_minutes,
                cooldown_candles=cooldown_candles,
                slippage_pips=slippage_pips,
                # WHY: Use the rule's entry_bar_offset so optimizer results
                #      match the original backtest entry timing.
                # CHANGED: May 2026 — entry bar offset from loaded rule
                entry_bar_offset=entry_bar_offset,
            )
        except Exception as e:
            # WHY (Phase 36 Fix 4): Old code used `except Exception: return None`,
            #      silently skipping every failed candidate. If the failure
            #      was systematic (missing indicator, bad rule structure,
            #      exit crash), EVERY candidate failed and the user saw
            #      "no improvements found" with zero diagnostics. Log the
            #      exception with dedup (first 5 unique messages) so
            #      systematic errors surface without spamming.
            # CHANGED: April 2026 — Phase 36 Fix 4 — log exception
            #          (audit Part C MED #37)
            _err_key = f"{type(e).__name__}:{str(e)[:120]}"
            if not hasattr(_test_rules, '_seen_errors'):
                _test_rules._seen_errors = set()
            if _err_key not in _test_rules._seen_errors and len(_test_rules._seen_errors) < 5:
                _test_rules._seen_errors.add(_err_key)
                log.warning(
                    f"[OPTIMIZER] _test_rules failed for candidate "
                    f"{name!r}: {type(e).__name__}: {e}"
                )
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
        # WHY: Extract sl_pips from the exit strategy being tested for proper DD scoring.
        # CHANGED: April 2026 — pass per-strategy sl_pips (audit family #2)
        _exit_sl_pips = None
        try:
            if hasattr(exit_strat, 'sl_pips'):
                _exit_sl_pips = float(exit_strat.sl_pips)
            elif isinstance(exit_strat, dict):
                _exit_sl_pips = float(exit_strat.get('sl_pips', 150))
        except Exception:
            _exit_sl_pips = None
        score = _score_trades(final_trades, target_firm_data, stage, account_size,
                              sl_pips=_exit_sl_pips, risk_pct=risk_per_trade_pct,
                              dd_daily_limit=dd_daily_limit, dd_total_limit=dd_total_limit,
                              optimize_goal=optimize_goal)

        exit_name = exit_strat.name if hasattr(exit_strat, 'name') else str(exit_strat)
        exit_desc = exit_strat.describe() if hasattr(exit_strat, 'describe') else exit_name

        candidate = {
            'name':              name,
            'rules':             rules,
            'exit_strategy':     exit_desc,
            'exit_name':         exit_name,
            # WHY (Validator Fix): Candidate was missing exit_class.
            #      The validator needs it to reconstruct the exit strategy
            #      object. Without it, _validator_optimized.json has no
            #      exit_class and walk_forward_validate crashes.
            # CHANGED: April 2026 — Validator Fix
            'exit_class':        type(exit_strat).__name__,
            'exit_params':       exit_strat.params if hasattr(exit_strat, 'params') else {},
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

    # WHY: Log rule structure so we can diagnose KeyError crashes from terminal output
    # CHANGED: April 2026 — debug logging for conditions structure
    log.info(f"[OPTIMIZER] win_rules: {len(win_rules)} rules")
    for ri, wr in enumerate(win_rules):
        conds = wr.get('conditions', 'MISSING')
        n_conds = len(conds) if isinstance(conds, list) else conds
        keys = sorted(wr.keys())
        log.info(f"  Rule {ri}: conditions={n_conds}, keys={keys}")

    # ── STEP 1: Threshold shifts ──────────────────────────────────────────────
    if not _report(1, "Step 1: Testing threshold shifts..."):
        return candidates

    # WHY: Each step is wrapped in try/except so one bad rule/indicator doesn't
    #      crash the entire optimization. Errors are logged and skipped.
    # CHANGED: April 2026 — per-iteration error handling
    # WHY (Phase 36 Fix 1): Old grid was multiplicative [0.7..1.3] ONLY.
    #      For RSI=30 that tested {21..39} (range 18); for RSI=70 it
    #      tested {49..91} (range 42) — same indicator, asymmetric
    #      coverage. And original_val==0 was skipped entirely, so
    #      common "above-zero" rules (macd>0, ema_distance>0, etc.)
    #      never got threshold-optimized. Add an additive grid based
    #      on the indicator's in-sample IQR, and drop the zero-skip.
    #      The additive grid handles zero naturally.
    # CHANGED: April 2026 — Phase 36 Fix 1 — additive IQR grid +
    #          allow zero original_val (audit Part C MED #33 + #34)
    multiplicative_factors = [0.7, 0.8, 0.9, 1.1, 1.2, 1.3]
    add_factors = [-0.3, -0.15, 0.15, 0.3]   # fractions of IQR

    for rule_idx, rule in enumerate(win_rules):
        for cond_idx, cond in enumerate(rule.get('conditions', [])):
            try:
                original_val = cond.get('value', 0)
                feat = cond.get('feature', '?')

                # Compute IQR on in-sample slice of the indicator column.
                # Uses _is_col convention from Phase 30 Fix 6.
                iqr = 0.0
                try:
                    if feat in _indicators_trimmed.columns:
                        _col = _indicators_trimmed[feat].dropna()
                        if len(_col) >= 100:
                            _is_cutoff = int(len(_col) * 0.7)
                            _is_col = _col.iloc[:_is_cutoff] if _is_cutoff >= 100 else _col
                            iqr = float(_is_col.quantile(0.75) - _is_col.quantile(0.25))
                except Exception:
                    iqr = 0.0

                # Build the combined shift list. Multiplicative shifts
                # are skipped when original_val is zero (zero × anything
                # = zero, dead grid). Additive shifts always run when
                # iqr > 0.
                new_vals = []
                if original_val != 0:
                    for s in multiplicative_factors:
                        new_vals.append(original_val * s)
                if iqr > 0:
                    for f in add_factors:
                        new_vals.append(original_val + f * iqr)
                # Deduplicate (a mult-shifted 30 can coincide with an
                # additive-shifted 30 on certain indicators) and drop
                # any values equal to the original.
                seen = set()
                deduped = []
                for v in new_vals:
                    key = round(v, 6)
                    if key in seen or round(v, 6) == round(original_val, 6):
                        continue
                    seen.add(key)
                    deduped.append(v)

                if not deduped:
                    # Nothing to test for this condition — either IQR
                    # is zero (degenerate indicator) and original_val
                    # is zero, or the dedup collapsed everything.
                    continue

                for new_val in deduped:
                    if _stop_flag.is_set():
                        break
                    modified_rules = copy.deepcopy(win_rules)
                    # WHY: Safe access — check 'conditions' exists before bracket access
                    if 'conditions' not in modified_rules[rule_idx]:
                        log.warning(f"[OPTIMIZER] Rule {rule_idx} missing 'conditions' key, skipping")
                        continue
                    modified_rules[rule_idx]['conditions'][cond_idx]['value'] = new_val
                    change = f"R{rule_idx+1} {feat}: {original_val:.4f} → {new_val:.4f}"
                    # WHY: Testing all 5 exits per threshold shift causes
                    #      1,200+ backtests in Step 1 alone (8+ hours).
                    #      Only test the first exit here. Step 4 handles
                    #      exit strategy variations separately.
                    # CHANGED: April 2026 — fix Step 1 performance
                    _es = exit_strategies[0] if exit_strategies else None
                    if _es:
                        _test_rules(f"Threshold shift: {change}", modified_rules, _es, change)
                    _report(1, f"Threshold shifts: R{rule_idx+1} {feat} = {new_val:.4f}")
            except Exception as e:
                log.info(f"[OPTIMIZER] Step 1 error at rule {rule_idx}, cond {cond_idx}: {e}")
                import traceback; traceback.print_exc()
                continue

    # ── STEP 2: Add new indicator conditions ──────────────────────────────────
    if not _report(2, "Step 2: Testing additional indicators..."):
        return candidates

    # WHY: Guard against rules without 'conditions' key. Use .setdefault() to
    #      ensure the key exists before appending. Wrap each indicator test in
    #      try/except so a crash on one indicator doesn't stop all testing.
    # CHANGED: April 2026 — defensive conditions access + per-indicator error handling
    test_indicators = top_features[:30] if top_features else available_indicators[:30]
    for ind_name in test_indicators:
        if _stop_flag.is_set():
            break
        try:
            if ind_name not in indicators_df.columns:
                continue
            col = indicators_df[ind_name].dropna()
            if len(col) < 100:
                continue
            # WHY: Old code computed quantiles over the full indicator
            #      history, including the OOS portion. Every threshold
            #      variation was fit with knowledge of future data the
            #      strategy theoretically wouldn't have at deployment.
            #      True walk-forward requires per-trade recomputation
            #      (multi-day refactor). Minimal honest fix: compute
            #      quantiles from the first 70% of the series so the
            #      rightmost 30% ("the future" relative to threshold
            #      selection) is excluded from the fit.
            # CHANGED: April 2026 — Phase 30 Fix 6 — in-sample quantile
            #          (audit Part C HIGH #31)
            _is_cutoff = int(len(col) * 0.7)
            _is_col = col.iloc[:_is_cutoff] if _is_cutoff >= 100 else col
            for pct in [25, 50, 75]:
                threshold = _is_col.quantile(pct / 100.0)
                for operator in ['>', '<']:
                    # WHY: Old code hardcoded modified_rules[0] — only the
                    #      FIRST rule ever got a new indicator condition.
                    #      Multi-rule strategies (2-4 rules is common) lost
                    #      2/3 of their candidate search space because rules
                    #      1..N were never enriched. Iterate over all rule
                    #      indices so each rule gets its own candidate.
                    # CHANGED: April 2026 — Phase 30 Fix 7 — iterate all
                    #          rules (audit Part C HIGH #32)
                    for rule_idx in range(len(win_rules)):
                        modified_rules = copy.deepcopy(win_rules)
                        if not modified_rules:
                            continue
                        # setdefault ensures 'conditions' exists defensively
                        modified_rules[rule_idx].setdefault('conditions', []).append({
                            'feature':  ind_name,
                            'operator': operator,
                            'value':    float(threshold),
                        })
                        change = f"Added {ind_name} {operator} {threshold:.4f} to Rule {rule_idx + 1}"
                        # WHY: Testing all exits per indicator add mirrors the
                        #      Step 1 performance problem. Only test the first
                        #      exit here; Step 4 handles exit variations.
                        # CHANGED: April 2026 — fix Step 2 performance
                        _es = exit_strategies[0] if exit_strategies else None
                        if _es:
                            _test_rules(
                                f"+ {ind_name} {operator} {threshold:.2f} to R{rule_idx + 1}",
                                modified_rules, _es, change,
                            )
            _report(2, f"Testing indicator: {ind_name}")
        except Exception as e:
            log.info(f"[OPTIMIZER] Step 2 error on indicator '{ind_name}': {e}")
            import traceback; traceback.print_exc()
            continue

    # ── STEP 3: Remove weak conditions ────────────────────────────────────────
    if not _report(3, "Step 3: Testing condition removal..."):
        return candidates

    # WHY: Safe access to 'conditions' and per-condition error handling.
    # CHANGED: April 2026 — defensive access
    for rule_idx, rule in enumerate(win_rules):
        conditions = rule.get('conditions', [])
        if len(conditions) <= 1:
            continue
        for cond_idx, cond in enumerate(conditions):
            if _stop_flag.is_set():
                break
            try:
                modified_rules = copy.deepcopy(win_rules)
                rule_conds = modified_rules[rule_idx].get('conditions', [])
                if cond_idx >= len(rule_conds):
                    continue
                removed_cond = rule_conds.pop(cond_idx)
                modified_rules[rule_idx]['conditions'] = rule_conds
                feat = removed_cond.get('feature', '?')
                change = f"Removed {feat} from Rule {rule_idx+1}"
                # WHY: Same fix as steps 1+2 — test ALL provided exits.
                # CHANGED: April 2026 — test all exits per condition removal
                for _es_idx, _es in enumerate(exit_strategies):
                    _es_name = _es.name if hasattr(_es, 'name') else f"exit{_es_idx}"
                    _test_rules(f"- {feat} from R{rule_idx+1} ({_es_name})", modified_rules, _es, change)
                _report(3, f"Remove: {feat} from R{rule_idx+1}")
            except Exception as e:
                log.info(f"[OPTIMIZER] Step 3 error at rule {rule_idx}, cond {cond_idx}: {e}")
                import traceback; traceback.print_exc()
                continue

    # ── STEP 4: Exit strategy scan on top candidates ──────────────────────────
    if not _report(4, "Step 4: Testing exit strategies on best candidates..."):
        return candidates

    top_rule_sets = sorted(candidates, key=lambda c: c.get('score', 0), reverse=True)[:5]
    for rank, top_cand in enumerate(top_rule_sets):
        try:
            for exit_strat in exit_strategies:
                if _stop_flag.is_set():
                    break
                exit_name = exit_strat.name if hasattr(exit_strat, 'name') else str(exit_strat)
                name = f"{top_cand['name']} × {exit_name}"
                change = f"{top_cand['changes_from_base']} + {exit_name}"
                _test_rules(name, top_cand['rules'], exit_strat, change)
                _report(4, f"Exit test: {exit_name} on #{rank+1}")
        except Exception as e:
            log.info(f"[OPTIMIZER] Step 4 error on candidate {rank}: {e}")
            import traceback; traceback.print_exc()
            continue

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


# WHY (per-row-delete v3): Index-keyed row removal. The strategy dicts
#      the refiner panel renders carry the array position in their
#      'index' field (set at loader line 700: 'index': i). Using it
#      directly as the key eliminates the fuzzy (rule_combo,exit,tf)
#      matching that failed in v2 because the loader rewrites
#      rule_combo before the panel sees it. Sanity check verifies the
#      row at that index still matches the expected shape before
#      deletion — catches the case where another process modified the
#      file between render and click.
# CHANGED: April 2026 — per-row-delete v3
def delete_matrix_row(array_index, expected_rule_combo=None,
                      expected_exit_strategy=None, expected_entry_tf=None):
    """Remove row at `array_index` from backtest_matrix.json.

    Parameters
    ----------
    array_index : int
        Position in the results/matrix array of the row to remove.
    expected_rule_combo, expected_exit_strategy, expected_entry_tf :
        Optional sanity-check values. If any are provided and the row
        at `array_index` doesn't match, raises ValueError (caller can
        decide to refresh and retry). A None value skips that check.

    Returns
    -------
    dict with keys:
        'removed':    bool — whether a row was removed
        'reason':     str  — human-readable status
        'row_count_before': int
        'row_count_after':  int
        'row_snapshot': dict | None — the deleted row's key fields
                        for logging/verification

    Raises
    ------
    FileNotFoundError  — backtest_matrix.json doesn't exist
    ValueError         — JSON structure is unrecognized, is an LFS
                         pointer, or sanity check failed
    """
    import json as _json
    import os as _os
    import tempfile as _tempfile

    if not _os.path.exists(BACKTEST_MATRIX_PATH):
        raise FileNotFoundError(
            f"backtest_matrix.json not found at {BACKTEST_MATRIX_PATH}"
        )

    with open(BACKTEST_MATRIX_PATH, 'r', encoding='utf-8') as f:
        _first = f.readline()
        if _first.startswith('version https://git-lfs.github.com/spec/v1'):
            raise ValueError(
                "backtest_matrix.json is a Git LFS pointer — run "
                "'git lfs pull' first."
            )
        f.seek(0)
        data = _json.load(f)

    # Support both {'results': [...]} and {'matrix': [...]} layouts
    # (same duality the loader handles at line 671).
    if 'results' in data and isinstance(data.get('results'), list):
        rows_key = 'results'
    elif 'matrix' in data and isinstance(data.get('matrix'), list):
        rows_key = 'matrix'
    else:
        raise ValueError(
            "backtest_matrix.json has neither 'results' nor 'matrix' "
            "array — unknown structure, refusing to rewrite."
        )

    rows = data[rows_key]
    n_before = len(rows)

    # Coerce array_index to int; reject nonsense up front.
    try:
        idx = int(array_index)
    except (TypeError, ValueError) as _ce:
        raise ValueError(
            f"array_index must be an integer, got "
            f"{type(array_index).__name__}={array_index!r}"
        ) from _ce

    if idx < 0 or idx >= n_before:
        return {
            'removed': False,
            'reason': f"index {idx} out of range (have {n_before} rows)",
            'row_count_before': n_before,
            'row_count_after':  n_before,
            'row_snapshot':     None,
        }

    row = rows[idx]

    # Sanity check: verify the row at this index still matches what
    # the caller expected. If not, the file was modified between
    # render and click — abort rather than delete the wrong thing.
    raw_rc = str(row.get('rule_combo', ''))
    raw_ex = str(row.get('exit_strategy', ''))
    raw_tf = str(row.get('entry_tf', '') or '')

    # For rule_combo the panel has the rewritten descriptive form.
    # Accept a match if the expected value either equals the raw form
    # OR starts with the row's descriptive form (the first word after
    # the # gets swapped per loader:690-697). If caller wants strict
    # match, they can pass None for this field to skip.
    if expected_rule_combo is not None:
        rc_match = False
        if str(expected_rule_combo) == raw_rc:
            rc_match = True
        else:
            # Try: raw is like '#1 (BUY)', expected is like
            # 'BUY_H1_4c_0423_6c21 (BUY)'. Strip leading token.
            raw_rest = ' '.join(raw_rc.split(' ')[1:]) if ' ' in raw_rc else ''
            exp_rest = ' '.join(str(expected_rule_combo).split(' ')[1:]) \
                       if ' ' in str(expected_rule_combo) else ''
            if raw_rest and raw_rest == exp_rest:
                # Same trailing token (e.g., '(BUY)'), assume same row
                rc_match = True
            # Also try substring — the loader's descriptive ID may be
            # embedded via the rules[0]._saved_rule_id path.
            _first = (row.get('rules', [{}])[0]
                      if isinstance(row.get('rules'), list) and row.get('rules')
                      else {})
            _rid = _first.get('_saved_rule_id', _first.get('rule_id', ''))
            if _rid and _rid in str(expected_rule_combo):
                rc_match = True
        if not rc_match:
            raise ValueError(
                f"Row at index {idx} has rule_combo {raw_rc!r}, but "
                f"caller expected {expected_rule_combo!r}. File may "
                f"have been modified since render — refresh and retry."
            )

    if expected_exit_strategy is not None:
        if str(expected_exit_strategy) != raw_ex:
            raise ValueError(
                f"Row at index {idx} has exit_strategy {raw_ex!r}, "
                f"but caller expected {expected_exit_strategy!r}. "
                f"File may have been modified since render."
            )

    if expected_entry_tf is not None:
        if str(expected_entry_tf) != raw_tf:
            raise ValueError(
                f"Row at index {idx} has entry_tf {raw_tf!r}, "
                f"but caller expected {expected_entry_tf!r}. "
                f"File may have been modified since render."
            )

    snapshot = {
        'rule_combo':    raw_rc,
        'exit_strategy': raw_ex,
        'entry_tf':      raw_tf,
        'index':         idx,
    }

    del rows[idx]
    data[rows_key] = rows

    # Atomic write: tempfile in SAME directory so os.replace is atomic.
    _dir = _os.path.dirname(BACKTEST_MATRIX_PATH) or '.'
    _fd, _tmp = _tempfile.mkstemp(prefix='.backtest_matrix.',
                                  suffix='.tmp', dir=_dir)
    try:
        with _os.fdopen(_fd, 'w', encoding='utf-8') as f:
            _json.dump(data, f, indent=2, default=str)
        _os.replace(_tmp, BACKTEST_MATRIX_PATH)
    except Exception:
        try:
            _os.remove(_tmp)
        except Exception:
            pass
        raise

    log.info(
        f"[REFINER] delete_matrix_row: removed idx={idx} "
        f"rule_combo={raw_rc!r} exit={raw_ex!r} entry_tf={raw_tf!r} "
        f"(now {len(rows)} rows, was {n_before})"
    )

    return {
        'removed': True,
        'reason':  'ok',
        'row_count_before': n_before,
        'row_count_after':  len(rows),
        'row_snapshot':     snapshot,
    }
