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
                       total_dd_alert_pct=None):
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

        day_pnl = daily_pnls[day]

        # ── Apply daily safety stop (bot stops trading when threshold hit) ──
        # WHY: If safety stop is set at 4% and the day's losses reach 4%, the
        #      bot pauses and CAN'T lose more. Previously the simulator counted
        #      the safety stop AND applied the full loss, which could let the day
        #      "blow up" the account even though trading had already stopped —
        #      logically impossible. Cap the loss at the safety threshold.
        # CHANGED: April 2026 — cap losses at safety threshold
        daily_safety_triggered = False
        if daily_dd_safety and day_pnl < 0 and abs(day_pnl) >= daily_dd_safety:
            day_pnl = -daily_dd_safety   # cap the loss at the safety level
            daily_safety_triggered = True
            daily_safety_dates.append(day)

        if day_pnl < 0:
            daily_pct = abs(day_pnl) / account_size * 100
            worst_daily_pct = max(worst_daily_pct, daily_pct)

        # Check daily breach — only possible if safety didn't trigger first
        if not daily_safety_triggered and day_pnl < 0 and abs(day_pnl) >= daily_dd_limit:
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

        # Funded protection — stop trading when approaching total DD limit
        if funded_protect and _protect_alert and total_dd >= _protect_alert_dollars:
            try:
                _current_day = pd.to_datetime(day)
                _period_end = _current_day + pd.Timedelta(days=payout_period_days)
                _protect_skip_until = _period_end
                _payout_trades_stopped += 1
            except Exception:
                pass

        # ── Apply total safety stop (cap total DD at safety threshold) ───────
        # WHY: The old code restored balance to (high_water - total_dd_safety)
        #      when the safety trigger fired. But the trigger condition
        #      (total_dd >= total_dd_safety) means balance was ALREADY at
        #      or below the safety level. So restoration was always an
        #      upward adjustment — phantom equity equal to the overshoot.
        #      Example: high_water=$10,500, safety=$300, day's loss pushed
        #      balance to $9,900. Restoration set balance to $10,200 → a
        #      phantom $300 gain. Subsequent days compounded from the
        #      phantom balance, inflating strategy performance.
        #      Fix: leave balance at its real value. Cap total_dd for the
        #      breach check (preserving the modeling intent that the bot
        #      halted at the safety line and prevented a real breach).
        #      Subsequent days continue from the real balance, which is
        #      more conservative and more honest.
        # CHANGED: April 2026 — remove phantom equity restoration (audit HIGH)
        total_safety_triggered = False
        if total_dd_safety and total_dd >= total_dd_safety and total_dd < total_dd_limit:
            # Do NOT restore balance — leave it at its real value.
            # Only cap total_dd for the breach check below.
            total_dd = total_dd_safety
            total_dd_pct = total_dd / account_size * 100
            total_safety_triggered = True
            total_safety_dates.append(day)

        # Check total breach — only possible if total safety didn't trigger
        if not total_safety_triggered and total_dd >= total_dd_limit:
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
    }


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
                with open(trades_path, 'r', encoding='utf-8') as f:
                    trades_data = json.load(f)

                # The per-TF file is keyed by combo index WITHIN that TF's run.
                # But strategy_index is the GLOBAL index across all TFs.
                # We need to find which per-TF index this corresponds to.

                # First try: count how many results before this TF
                # to compute the per-TF offset
                try:
                    with open(BACKTEST_MATRIX_PATH, 'r', encoding='utf-8') as f:
                        matrix_data = json.load(f)
                    all_results = matrix_data.get('results', []) or matrix_data.get('matrix', [])

                    # Count results with this TF that come before strategy_index
                    tf_local_idx = 0
                    for ri in range(strategy_index):
                        if ri < len(all_results) and all_results[ri].get('entry_tf', '') == entry_tf:
                            tf_local_idx += 1

                    str_idx = str(tf_local_idx)
                    if str_idx in trades_data:
                        return trades_data[str_idx]
                except Exception:
                    pass

                # Fallback: try direct index
                str_idx = str(strategy_index)
                if str_idx in trades_data:
                    return trades_data[str_idx]

                # Fallback: try matching by rule_combo + exit name
                try:
                    with open(BACKTEST_MATRIX_PATH, 'r', encoding='utf-8') as f:
                        matrix_data = json.load(f)
                    all_results = matrix_data.get('results', []) or matrix_data.get('matrix', [])
                    if 0 <= strategy_index < len(all_results):
                        target = all_results[strategy_index]
                        target_combo = target.get('rule_combo', '')
                        target_exit = target.get('exit_strategy', target.get('exit_name', ''))
                        # Find matching index in trades file
                        tf_results = [r for r in all_results if r.get('entry_tf', '') == entry_tf]
                        for ti, tr in enumerate(tf_results):
                            if (tr.get('rule_combo', '') == target_combo and
                                (tr.get('exit_strategy', '') == target_exit or
                                 tr.get('exit_name', '') == target_exit)):
                                if str(ti) in trades_data:
                                    return trades_data[str(ti)]
                                break
                except Exception:
                    pass

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
                    for i, r in enumerate(_all_results):
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

                        results.append({
                            'index':             i,
                            'source':            'backtest',
                            'label':             (f"{_rc} × {r.get('exit_strategy','?')}"
                                                  f"{'  [' + r.get('entry_tf','') + ']' if r.get('entry_tf','') else ''}"
                                                  f"  [{trades_count} trades, WR {wr_str}, PF {pf:.1f}, {net:+,.0f} pips]"),
                            'rule_combo':        _rc,
                            'exit_strategy':     r.get('exit_strategy', '?'),
                            'exit_name':         r.get('exit_name', '?'),
                            'total_trades':      trades_count,
                            'win_rate':          wr,
                            'net_total_pips':    net,
                            'net_avg_pips':      stats.get('net_avg_pips', stats.get('avg_pips', r.get('avg_pips', 0))),
                            'net_profit_factor': stats.get('net_profit_factor', r.get('net_profit_factor', 0)),
                            'max_dd_pips':       stats.get('max_dd_pips', r.get('max_dd_pips', 0)),
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
                            'risk_pct':          r.get('risk_pct', r.get('run_settings', {}).get('risk_pct', 0)),
                            'dd_daily_pct':      r.get('dd_daily_pct', r.get('run_settings', {}).get('dd_daily_pct', 0)),
                            'dd_total_pct':      r.get('dd_total_pct', r.get('run_settings', {}).get('dd_total_pct', 0)),
                            'account_size':      r.get('account_size', r.get('run_settings', {}).get('starting_capital', 0)),
                            'prop_firm_name':    r.get('prop_firm_name', r.get('run_settings', {}).get('prop_firm_name', '')),
                            'prop_firm_stage':   r.get('prop_firm_stage', r.get('run_settings', {}).get('prop_firm_stage', '')),
                            'data_source_id':    r.get('data_source_id', r.get('run_settings', {}).get('data_source_id', '')),
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
                        'spread_pips':       25.0,
                        'commission_pips':   0.0,
                        'has_trades':        False,
                        'saved_rule':        rule,  # keep the original rule for loading
                        'prop_firm_name':    rule.get('prop_firm_name', ''),
                        'prop_firm_stage':   rule.get('prop_firm_stage', ''),
                        'account_size':      rule.get('account_size', 0),
                        'leverage':          rule.get('leverage', 0),
                        'data_source_id':    rule.get('data_source_id', ''),
                        'data_source_path':  rule.get('data_source_path', ''),
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
    hold_secs = (exit_times - entry_times).total_seconds()

    for i, t in enumerate(trades):
        try:
            hs = hold_secs.iloc[i]
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
                  sl_pips=None, risk_pct=None, dd_daily_limit=5.0, dd_total_limit=10.0):
    """
    Score trades for prop firm suitability.

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
                                dd_daily_limit=dd_daily_limit, dd_total_limit=dd_total_limit)
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
                              dd_daily_limit=dd_daily_limit, dd_total_limit=dd_total_limit)
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
    total_steps = len(preset_list) + 20 + 5 + 3 + 10

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

    # ── Step 4: Session combos ────────────────────────────────────────────────
    session_combos = [
        (["London"],             "London only"),
        (["New York"],           "NY only"),
        (["London", "New York"], "London + NY"),
        (["Asian", "London"],    "Asian + London"),
    ]
    base_step = len(preset_list) + len(hold_values) + 5
    if not lock_filters:
        for i, (sess, desc) in enumerate(session_combos):
            if _stop_flag.is_set():
                break
            _report(f"Testing sessions: {desc}", total_steps, base_step + i + 1)
            kept, _ = apply_filters(trades, {'sessions': sess})
            _maybe_add(f"Session: {desc}", kept, f"sessions={desc}", {'sessions': sess})

    # ── Step 5: Combination — hold + max/day ──────────────────────────────────
    combos = [(5, 3), (5, 5), (10, 3), (2, 5), (15, 2)]
    base_step2 = base_step + len(session_combos)
    if not lock_filters:
        for i, (hold, maxd) in enumerate(combos):
            if _stop_flag.is_set():
                break
            _report(f"Combo: min hold {hold}m + max {maxd}/day", total_steps, base_step2 + i + 1)
            filt = {'min_hold_minutes': hold, 'max_trades_per_day': maxd}
            kept, _ = apply_filters(trades, filt)
            _maybe_add(f"Hold {hold}m + max {maxd}/day", kept,
                       f"min hold {hold}m, max {maxd}/day", filt)

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
                                     dd_daily_limit=dd_daily_limit, dd_total_limit=dd_total_limit)
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
            data_dir, candles_df['timestamp'], required_indicators=required)
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
                                dd_daily_limit=dd_daily_limit, dd_total_limit=dd_total_limit)
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
                              dd_daily_limit=dd_daily_limit, dd_total_limit=dd_total_limit)

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
