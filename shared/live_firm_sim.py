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
from datetime import datetime


def simulate_live_firm(trades, prop_firm_data, account_size=100000,
                       payout_period_days=14, withdrawal_pct=80.0,
                       pip_value_per_lot=None, risk_pct=1.0,
                       default_sl_pips=150.0):
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

    # WHY (Phase 72 Fix 19): Old code hardcoded pip_value_per_lot=10.0 which
    #      is only correct for XAUUSD at 1 lot. Other instruments (EURUSD,
    #      GBPUSD, index CFDs) have completely different pip values. If the
    #      firm JSON contains pip_value_per_lot, use it; else default to 10.0.
    # CHANGED: April 2026 — Phase 72 Fix 19 — resolve pip_value from config
    #          (audit Part F HIGH #19)
    if pip_value_per_lot is None:
        pip_value_per_lot = float(prop_firm_data.get('pip_value_per_lot', 10.0))

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
    # WHY (Phase 72 Fix 17): Old code reset dd_locked to False on blow without
    #      tracking whether locks were cleared. A user who reached the lock-after-gain
    #      threshold (e.g., +8% profit) then blew the account would start a new
    #      account with no indication that locks had been active. This makes it
    #      hard to diagnose whether the blow happened pre-lock or post-lock.
    # CHANGED: April 2026 — Phase 72 Fix 17 — track locks cleared on blow
    #          (audit Part F HIGH #17)
    _locks_cleared_on_blow = False

    period_start_day      = 0
    period_start_balance  = balance   # WHY: was named period_high — see Fix
    period_start_date     = None      # set on first iteration below

    warnings = []

    # WHY: Old code converted payout_period_days (calendar) to trading days
    #      via a 5/7 ratio, then compared day_idx differences to it. But
    #      day_idx differences ONLY equal trading days when there are no
    #      gaps in the strategy — strategies that skip a week (no trades)
    #      advance day_idx by 1 for what's actually 7 calendar days.
    #      Now we track period_start_date as a real calendar date and
    #      compare (current_date - period_start_date).days directly.
    # CHANGED: April 2026 — use real calendar dates (audit bug family #3)

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

        # WHY: period_start_date is tracked in real calendar dates so
        #      payout cycle timing respects actual elapsed time rather
        #      than trading-day index differences.
        # CHANGED: April 2026 — calendar date tracking (audit bug family #3)
        if period_start_date is None:
            period_start_date = day

        # WHY: Old code hardcoded $10/pip (XAUUSD 1 lot) — wrong for any other
        #      instrument or lot size. Use risk-based sizing from params.
        # CHANGED: April 2026 — use config values, not hardcoded $10/pip
        risk_dollars   = account_size * (risk_pct / 100.0)
        sl_denom       = default_sl_pips * pip_value_per_lot
        lot_size_calculated = risk_dollars / sl_denom if sl_denom > 0 else 0.01
        lot_size       = max(0.01, lot_size_calculated)

        # WHY (Phase 72 Fix 18): When lot_size_calculated < 0.01 (micro risk or
        #      huge SL), flooring to 0.01 inflates the actual risk taken. A
        #      strategy expecting $50 risk per trade but forced to 0.01 lots
        #      might actually risk $150. Warn once per simulation if this occurs.
        # CHANGED: April 2026 — Phase 72 Fix 18 — warn on lot floor inflation
        #          (audit Part F HIGH #18)
        if lot_size_calculated < 0.01 and day_idx == 0:
            import logging
            logging.warning(
                f"lot_size floored to 0.01 (calculated {lot_size_calculated:.4f}) — "
                f"actual risk inflated by {0.01/lot_size_calculated:.1f}×"
            )

        dollar_per_pip = pip_value_per_lot * lot_size

        for t in day_trades:
            # Prefer pre-computed dollar P&L if available (run_backtest sets this)
            if t.get('net_profit') is not None or t.get('dollar_pnl') is not None:
                day_pnl_dollars += float(t.get('net_profit') or t.get('dollar_pnl') or 0)
            else:
                net_pips = t.get('net_pips', t.get('pips', 0))
                day_pnl_dollars += float(net_pips) * dollar_per_pip

        # Apply daily P&L
        balance += day_pnl_dollars
        equity   = balance  # simulation simplification (no open positions)

        # ── Daily DD breach check ─────────────────────────────────────────
        # WHY: Old code never checked daily DD at all — it read daily_dd_pct
        #      from the firm config and reported it in the output, but the
        #      main loop only checked total DD. A strategy losing 10% in
        #      a single day on a 3%-daily-DD firm would survive as long as
        #      total DD stayed under the limit. Now we enforce the daily
        #      limit using the day's dollar loss divided by the daily
        #      reference (balance at start of day).
        # NOTE: This is an approximation because the simulator only has
        #      daily P&L aggregates, not intraday equity excursions. Real
        #      firms check equity continuously; we only check end-of-day.
        #      Errs on the side of under-detection (safe for strategies,
        #      not safe for the simulator's claim that accounts are OK).
        # CHANGED: April 2026 — enforce daily DD limit (audit bug family #3)
        daily_dd_triggered = False
        if daily_dd_pct is not None and daily_dd_pct > 0 and day_pnl_dollars < 0:
            # Balance at start of day = balance after P&L minus the P&L
            dd_ref_daily = balance - day_pnl_dollars
            if dd_ref_daily <= 0:
                dd_ref_daily = starting_balance  # safety guard
            daily_dd_this_day = abs(day_pnl_dollars) / dd_ref_daily * 100.0
            if daily_dd_this_day >= daily_dd_pct:
                daily_dd_triggered = True

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
                # WHY: Old code set dd_floor = starting_balance, leaving zero
                #      buffer. After lock, the floor is "starting balance
                #      minus the DD allowance" — same buffer as before, just
                #      anchored to the initial balance instead of trailing.
                #      For firms with the strict "no drop below initial"
                #      rule, use 'starting_balance_strict'.
                # CHANGED: April 2026 — preserve DD buffer after lock
                if lock_level == 'starting_balance':
                    dd_floor = starting_balance * (1.0 - total_dd_pct / 100.0)
                elif lock_level == 'starting_balance_strict':
                    dd_floor = starting_balance  # zero buffer (strict firms only)
                # else: leave dd_floor as-is (locks at current level)
                lock_triggered = True
                lock_day = day_idx

        # ── Total DD or daily DD breach check ─────────────────────────────
        # WHY: Either breach type blows the account. Daily DD was
        #      previously uncaught — Fix 1A introduced the check above.
        # CHANGED: April 2026 — daily DD enforcement (audit bug family #3)
        if equity <= dd_floor or daily_dd_triggered:
            blown = True
            blow_count += 1
            if first_blow_day is None:
                first_blow_day = day_idx

            # WHY (Phase 72 Fix 17): Old code reset dd_locked to False on blow
            #      without tracking whether locks were cleared. If a user reached
            #      lock-after-gain (+8% profit) then blew, the new account started
            #      fresh with no indication locks had been active. Track this so
            #      post-mortem analysis knows if blow happened pre-lock or post-lock.
            # CHANGED: April 2026 — Phase 72 Fix 17 — track locks cleared on blow
            #          (audit Part F HIGH #17)
            if dd_locked or post_payout_lock:
                _locks_cleared_on_blow = True

            # Simulate buying a new account for this period
            balance              = starting_balance
            equity               = starting_balance
            hwm                  = starting_balance
            dd_floor             = starting_balance * (1.0 - total_dd_pct / 100.0)
            dd_locked            = False
            post_payout_lock     = False
            period_start_day     = day_idx
            period_start_date    = day
            period_start_balance = balance
            continue

        # ── Payout cycle check ───────────────────────────────────────────
        # WHY: Old code computed period_profit as `balance - period_high` where
        #      period_high was the running max balance during the period. Since
        #      period_high >= balance always, period_profit was always ≤ 0 and
        #      payouts NEVER triggered. The variable was conceptually
        #      period_start_balance (balance at the START of the period).
        #      Old code also measured period length in trading-day indices,
        #      which is wrong when the strategy has gaps — payout timing
        #      is now based on real calendar days.
        # CHANGED: April 2026 — calendar-date payout cycle (audit bug family #3)
        calendar_days_in_period = (day - period_start_date).days
        if calendar_days_in_period >= payout_period_days:
            period_profit = balance - period_start_balance
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
            period_start_day     = day_idx
            period_start_date    = day
            period_start_balance = balance
        # End payout check — period_high tracking removed (was unused after fix)

    # ── Calculate annual estimate ────────────────────────────────────────
    # WHY: Old formula assumed blows were uniformly distributed and used a
    #      hardcoded 5% challenge fee. Now: configurable fee, and the unit
    #      error in the annual cost formula is fixed (was: dollars/year²).
    # CHANGED: April 2026 — fix unit error + parameterize fee
    #
    # NOTE: challenge_fee_pct can be added to prop_firm_data later. For now
    #       it falls back to a 5% default unless the firm specifies fees.
    challenge_fee_pct = float(prop_firm_data.get('challenge_fee_pct', 5.0))
    avg_per_cycle    = 0
    estimated_annual = 0
    success_rate     = 0.0

    # WHY: days_simulated counts TRADING days (entries in sorted_days), not
    #      calendar days. A strategy trading every weekday for a full year
    #      has ~252 trading days, not 365. Using trading-day count as
    #      calendar-day count inflates annual_blow_cost by 365/252 ≈ 1.45×
    #      (and shrinks attempted_cycles, which inflates success_rate).
    #      Fix: compute real elapsed time from the first to last trade
    #      date in the strategy's history.
    # CHANGED: April 2026 — use calendar-day span (audit bug family #3)
    if len(sorted_days) >= 2:
        calendar_days_span = max((sorted_days[-1] - sorted_days[0]).days + 1, 1)
    else:
        calendar_days_span = max(days_simulated, 1)

    if days_simulated > 0 and payout_cycles_completed > 0:
        cycles_per_year   = 365.0 / max(payout_period_days, 1)
        avg_per_cycle     = total_withdrawn / payout_cycles_completed
        # WHY: attempted_cycles uses REAL elapsed calendar days, not the
        #      count of trading days (which understates time by ~30%).
        # CHANGED: April 2026 — calendar span (audit bug family #3)
        attempted_cycles  = max(calendar_days_span / max(payout_period_days, 1), 1)
        success_rate      = max(0.0, min(1.0, payout_cycles_completed / attempted_cycles))

        # Cost per blow = challenge fee for a new account
        blow_cost_per_blow = account_size * (challenge_fee_pct / 100.0)

        gross_annual = avg_per_cycle * cycles_per_year * success_rate

        # Annualize blow cost: total blows over the simulation, scaled to per-year
        # WHY: Old formula was: blow_count * fee * (cycles_per_year / years_simulated)
        #      → units of dollars/year². Correct: blow_count * fee / years_simulated.
        #      AND years_simulated must be CALENDAR years, not trading-day years.
        # CHANGED: April 2026 — fix unit error + calendar years (audit bug family #3)
        years_simulated  = max(calendar_days_span / 365.0, 0.01)
        annual_blow_cost = (blow_count * blow_cost_per_blow) / years_simulated

        estimated_annual = max(0.0, gross_annual - annual_blow_cost)

    # ── Determine verdict ────────────────────────────────────────────────
    # WHY (Phase 76 Fix 20): Absolute blow_count ignores simulation length.
    #      1 blow in 3 months = ACCEPTABLE. 1 blow in 3 years = also ACCEPTABLE.
    #      Normalise to blows per year so longer simulations are held to the
    #      same standard.
    # CHANGED: April 2026 — Phase 76 Fix 20 — blows-per-year verdict
    #          (audit Part F MEDIUM #20)
    _years = max(calendar_days_span / 365.0, 0.25) if calendar_days_span > 0 else 1.0
    _blows_per_year = blow_count / _years

    if blow_count == 0 and payout_cycles_completed >= 3:
        verdict = "EXCELLENT"
    elif blow_count == 0 and payout_cycles_completed >= 1:
        verdict = "GOOD"
    elif _blows_per_year < 0.5 and payout_cycles_completed >= 1:
        verdict = "ACCEPTABLE"
    elif _blows_per_year < 1.5 and payout_cycles_completed >= 1:
        verdict = "MARGINAL"
    elif blow_count > 0 and payout_cycles_completed == 0:
        verdict = "RISKY"
    elif _blows_per_year >= 3.0:
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
        'success_rate':            round(success_rate, 3),
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
        'locks_cleared_on_blow':   _locks_cleared_on_blow,  # Phase 72 Fix 17
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


def simulate_all_firms(trades, account_size=100000, **kwargs):
    """
    Run live firm simulation for all available prop firms.
    kwargs are forwarded to simulate_live_firm (pip_value_per_lot, risk_pct, etc.)
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
            result = simulate_live_firm(trades, firm_data,
                                        account_size=account_size, **kwargs)
            results.append(result)
        except Exception as e:
            # WHY (Phase 76 Fix 21): ERROR verdict looks like a real test result.
            #      Users can't tell if ERROR = strategy failed or file corrupt.
            #      Add 'error_detail' so callers can distinguish.
            # CHANGED: April 2026 — Phase 76 Fix 21 — error_detail field
            firm_name = os.path.basename(fp).replace('.json', '')
            try:
                _firm_data = firm_data if 'firm_data' in locals() else {}
                firm_name = _firm_data.get('firm_name', firm_name)
            except Exception:
                pass
            results.append({
                'firm_name':              firm_name,
                'verdict':                'ERROR',
                'error_detail':           str(e),
                'error_type':             type(e).__name__,
                'warnings':               [f'Failed to simulate: {e}'],
                'blow_count':             0,
                'payout_cycles_completed': 0,
                'avg_per_cycle':          0,
                'estimated_annual':       0,
                'lock_day':               None,
                'payout_period_days':     14,
            })

    return results
