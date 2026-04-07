"""
Live Firm Simulation — replays a strategy's trades day-by-day using the exact
firm rules from the prop firm JSON.

WHY: The basic prop firm DD simulator uses generic percentage-based DD checks.
     The real firms have much more specific rules (closed-balance trailing,
     lock-after-gain, post-payout lock, daily reset timing) that significantly
     affect realistic survival rates. This module models the rules accurately.

CHANGED: April 2026 — new module for accurate firm-specific testing.
"""

import pandas as pd
from datetime import datetime, timedelta


def simulate_live_firm(trades, prop_firm_data, account_size=100000,
                       payout_period_days=14, withdrawal_pct=80.0):
    """
    Replay trades through the exact firm rules.

    Args:
        trades: list of trade dicts (must have 'entry_time', 'net_pips', 'pips')
        prop_firm_data: full firm JSON dict (firm_name + drawdown_mechanics + ...)
        account_size: starting account size in dollars
        payout_period_days: how long between payout windows (firm-specific, default 14)
        withdrawal_pct: how much of profit to withdraw each cycle (% of period profit)

    Returns:
        dict with:
            'blown': bool (did the account blow at least once?)
            'blow_count': how many times during the simulation
            'first_blow_day': day index of first blow (or None)
            'lock_triggered': bool (did the lock-after-gain fire?)
            'lock_day': day index when lock fired (or None)
            'payout_cycles_completed': how many full payout cycles
            'total_withdrawn': dollars withdrawn across all cycles
            'avg_per_cycle': average dollars withdrawn per cycle
            'estimated_annual': extrapolated yearly income
            'final_equity': dollars at end of simulation
            'days_simulated': number of trading days
            'verdict': string verdict
            'warnings': list of human-readable warnings
    """
    if not trades:
        return _empty_result("No trades provided")

    # ── Read firm rules from JSON ─────────────────────────────────────────
    dd_mechanics = prop_firm_data.get('drawdown_mechanics', {})
    trailing_dd  = dd_mechanics.get('trailing_dd', {})
    post_payout  = dd_mechanics.get('post_payout', {})

    basis    = trailing_dd.get('basis', 'equity')              # 'equity' or 'closed_balance'
    lock_pct = trailing_dd.get('lock_after_gain_pct')          # number or None
    lock_level = trailing_dd.get('lock_level', 'starting_balance')

    # Get DD limits from funded phase, falling back to first challenge phase
    challenges = prop_firm_data.get('challenges', [])
    if not challenges:
        return _empty_result("No challenges defined in firm JSON")

    ch = challenges[0]
    funded = ch.get('funded', {})
    if funded:
        daily_dd_pct = funded.get('max_daily_drawdown_pct', 5.0)
        total_dd_pct = funded.get('max_total_drawdown_pct', 10.0)
    else:
        phases = ch.get('phases', [])
        if phases:
            daily_dd_pct = phases[0].get('max_daily_drawdown_pct', 5.0)
            total_dd_pct = phases[0].get('max_total_drawdown_pct', 10.0)
        else:
            daily_dd_pct = 5.0
            total_dd_pct = 10.0

    # ── Initialize state ──────────────────────────────────────────────────
    starting_balance = account_size
    balance          = account_size
    equity           = account_size
    hwm              = account_size
    dd_floor         = account_size * (1.0 - total_dd_pct / 100.0)
    dd_locked        = False
    post_payout_lock = False

    blown              = False
    blow_count         = 0
    first_blow_day     = None
    lock_triggered     = False
    lock_day           = None
    payout_cycles_completed = 0
    total_withdrawn    = 0.0

    period_start_day   = 0
    period_high        = balance

    warnings = []

    # ── Group trades by day ───────────────────────────────────────────────
    daily_trades = {}
    for t in trades:
        et = t.get('entry_time') or t.get('exit_time') or ''
        try:
            day = pd.to_datetime(et).date()
        except Exception:
            continue
        daily_trades.setdefault(day, []).append(t)

    if not daily_trades:
        return _empty_result("Could not parse trade timestamps")

    sorted_days = sorted(daily_trades.keys())
    days_simulated = len(sorted_days)

    # ── Day-by-day replay ─────────────────────────────────────────────────
    for day_idx, day in enumerate(sorted_days):
        day_trades = daily_trades[day]
        day_pnl_dollars = 0

        # Use net_pips * $10 per pip (XAUUSD 0.10 lot approx; dollar P&L used if present)
        for t in day_trades:
            if 'net_profit' in t or 'dollar_pnl' in t:
                day_pnl_dollars += float(t.get('net_profit') or t.get('dollar_pnl') or 0)
            else:
                net_pips = t.get('net_pips', t.get('pips', 0))
                day_pnl_dollars += float(net_pips) * 10

        # Apply daily P&L
        balance += day_pnl_dollars
        equity   = balance  # simulation simplification (no open positions)

        # ── HWM update (firm-specific basis) ──────────────────────────────
        if basis == 'closed_balance':
            if balance > hwm and not dd_locked:
                hwm      = balance
                dd_floor = hwm * (1.0 - total_dd_pct / 100.0)
        else:
            # Equity trailing (default)
            if equity > hwm and not dd_locked:
                hwm      = equity
                dd_floor = hwm * (1.0 - total_dd_pct / 100.0)

        # ── Lock-after-gain check ─────────────────────────────────────────
        if lock_pct and not dd_locked:
            if balance >= starting_balance * (1.0 + lock_pct / 100.0):
                dd_locked = True
                if lock_level == 'starting_balance':
                    dd_floor = starting_balance
                lock_triggered = True
                lock_day = day_idx

        # ── Total DD breach check ─────────────────────────────────────────
        if equity <= dd_floor:
            blown = True
            blow_count += 1
            if first_blow_day is None:
                first_blow_day = day_idx

            # Simulate buying a new account for this period
            balance          = starting_balance
            equity           = starting_balance
            hwm              = starting_balance
            dd_floor         = starting_balance * (1.0 - total_dd_pct / 100.0)
            dd_locked        = False
            post_payout_lock = False
            period_start_day = day_idx
            period_high      = balance
            continue

        # ── Payout cycle check ───────────────────────────────────────────
        days_in_period = day_idx - period_start_day
        if days_in_period >= payout_period_days:
            period_profit = balance - period_high
            if period_profit > 0:
                withdraw_amount = period_profit * (withdrawal_pct / 100.0)
                total_withdrawn += withdraw_amount
                balance         -= withdraw_amount
                payout_cycles_completed += 1

                # Apply post-payout lock if firm has the rule
                if post_payout.get('dd_locks_at') == 'initial_balance' and not post_payout_lock:
                    dd_locked        = True
                    dd_floor         = starting_balance
                    post_payout_lock = True

            # Reset period
            period_start_day = day_idx
            period_high      = balance

        # Track period high
        if balance > period_high:
            period_high = balance

    # ── Calculate annual estimate ────────────────────────────────────────
    if days_simulated > 0 and payout_cycles_completed > 0:
        cycles_per_year = 365 / payout_period_days
        avg_per_cycle   = total_withdrawn / payout_cycles_completed
        # Scale down by blow rate
        blow_rate        = blow_count / max(days_simulated / payout_period_days, 1)
        estimated_annual = avg_per_cycle * cycles_per_year * (1 - min(blow_rate, 0.95))
    else:
        avg_per_cycle    = 0
        estimated_annual = 0

    # ── Determine verdict ────────────────────────────────────────────────
    if blow_count == 0 and payout_cycles_completed >= 3:
        verdict = "EXCELLENT"
    elif blow_count == 0 and payout_cycles_completed >= 1:
        verdict = "GOOD"
    elif blow_count <= 1 and payout_cycles_completed >= 1:
        verdict = "ACCEPTABLE"
    elif blow_count > 0 and payout_cycles_completed == 0:
        verdict = "RISKY"
    elif blow_count >= 3:
        verdict = "DANGEROUS"
    else:
        verdict = "MARGINAL"

    # ── Generate warnings ────────────────────────────────────────────────
    if blow_count >= 2:
        warnings.append(
            f"Account blew {blow_count} times in {days_simulated} days — "
            "strategy unsafe for this firm")
    if first_blow_day is not None and first_blow_day < 30:
        warnings.append(f"First blow on day {first_blow_day} — too early to recover")
    if not lock_triggered and lock_pct:
        warnings.append(
            f"Never reached the +{lock_pct}% lock — "
            "strategy is too slow or too losing")
    if payout_cycles_completed == 0 and not blown:
        warnings.append("No payout cycles completed — strategy doesn't generate consistent profit")
    if estimated_annual > 0 and estimated_annual < account_size * 0.10:
        warnings.append(
            "Annual estimate is below 10% of account — "
            "consider higher-edge strategies")

    return {
        'blown':                   blown,
        'blow_count':              blow_count,
        'first_blow_day':          first_blow_day,
        'lock_triggered':          lock_triggered,
        'lock_day':                lock_day,
        'payout_cycles_completed': payout_cycles_completed,
        'payout_period_days':      payout_period_days,
        'total_withdrawn':         round(total_withdrawn, 2),
        'avg_per_cycle':           round(avg_per_cycle, 2),
        'estimated_annual':        round(estimated_annual, 2),
        'final_equity':            round(balance, 2),
        'days_simulated':          days_simulated,
        'verdict':                 verdict,
        'warnings':                warnings,
        'firm_name':               prop_firm_data.get('firm_name', 'Unknown'),
        'starting_balance':        starting_balance,
        'daily_dd_pct':            daily_dd_pct,
        'total_dd_pct':            total_dd_pct,
        'lock_pct':                lock_pct,
        'has_post_payout_lock':    post_payout.get('dd_locks_at') == 'initial_balance',
    }


def _empty_result(reason):
    return {
        'blown': False, 'blow_count': 0, 'first_blow_day': None,
        'lock_triggered': False, 'lock_day': None,
        'payout_cycles_completed': 0, 'payout_period_days': 14,
        'total_withdrawn': 0, 'avg_per_cycle': 0, 'estimated_annual': 0,
        'final_equity': 0, 'days_simulated': 0,
        'verdict': 'INSUFFICIENT_DATA',
        'warnings': [reason],
        'firm_name': 'N/A',
        'starting_balance': 0, 'daily_dd_pct': 0,
        'total_dd_pct': 0, 'lock_pct': None, 'has_post_payout_lock': False,
    }


def simulate_all_firms(trades, account_size=100000):
    """
    Run live firm simulation for all available prop firms.
    Returns list of results, one per firm.
    """
    import os, json, glob

    firm_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'prop_firms'
    )

    results = []
    for fp in sorted(glob.glob(os.path.join(firm_dir, '*.json'))):
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                firm_data = json.load(f)
            result = simulate_live_firm(trades, firm_data, account_size=account_size)
            results.append(result)
        except Exception as e:
            results.append({
                'firm_name': os.path.basename(fp).replace('.json', ''),
                'verdict':   'ERROR',
                'warnings':  [f'Failed to simulate: {e}'],
                'blow_count': 0, 'payout_cycles_completed': 0,
                'avg_per_cycle': 0, 'estimated_annual': 0,
                'lock_day': None, 'payout_period_days': 14,
            })

    return results
