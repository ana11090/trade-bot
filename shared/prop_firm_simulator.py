"""
Prop Firm Lifecycle Simulator — windowed simulation with probability analysis.

Simulates the full prop firm lifecycle:
  Stage 1 (Evaluation): Can the robot pass the challenge? What's the probability?
  Stage 2 (Funded): Once funded, how long does the robot survive? How much does it earn?
  Stage 3 (Expected Value): Is it worth the challenge fee, factoring in retries?

Two simulation modes:
  - Sliding window: start a fresh challenge at every possible date in the trade history
  - Monte Carlo: randomly sample N starting dates

Daily DD safety: on losing days, the bot stops trading once the running daily loss
reaches `daily_dd_safety_pct` % of the firm's daily DD limit — protecting the account.
"""

from dataclasses import dataclass, field
from typing import Optional


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class SingleSimResult:
    """Result of one simulated challenge attempt starting at a specific date."""
    start_date: str
    # Stage 1 — Evaluation
    eval_outcome: str           # "PASS", "FAIL_DD", "FAIL_DAILY_DD", "FAIL_TIMEOUT", "INSUFFICIENT_TRADES"
    eval_days: int
    eval_trading_days: int
    eval_profit_pct: float
    eval_max_dd_pct: float
    eval_phase_results: list
    # Stage 2 — Funded (only if eval passed)
    funded_survival_days: Optional[int]
    funded_survival_trading_days: Optional[int]
    funded_total_payouts: Optional[float]
    funded_payout_count: Optional[int]
    funded_monthly_avg: Optional[float]
    funded_max_dd_pct: Optional[float]
    funded_end_reason: Optional[str]


@dataclass
class SimulationSummary:
    """Aggregated results across all simulation runs."""
    firm_name: str
    challenge_name: str
    account_size: int
    num_simulations: int
    simulation_mode: str

    # Stage 1
    eval_pass_rate: float
    eval_avg_days_to_pass: float
    eval_avg_days_to_fail: float
    eval_median_days_to_pass: float
    eval_avg_max_dd_pct: float
    eval_pass_count: int
    eval_fail_count: int
    eval_fail_reasons: dict

    # Stage 2
    funded_avg_survival_days: Optional[float]
    funded_median_survival_days: Optional[float]
    funded_avg_monthly_payout: Optional[float]
    funded_avg_total_payouts: Optional[float]
    funded_survival_rate_3mo: Optional[float]
    funded_survival_rate_6mo: Optional[float]
    funded_avg_payout_count: Optional[float]

    # Stage 3
    challenge_fee: Optional[float]
    avg_attempts_to_pass: float
    expected_cost: Optional[float]
    expected_funded_income: Optional[float]
    expected_net_profit: Optional[float]
    expected_roi_pct: Optional[float]

    # Configuration used
    # WHY: These are XAUUSD defaults. Other instruments should override:
    #      - default_sl_pips: 15-30 for forex
    #      - risk_per_trade_pct: typically 0.5-2.0 for prop accounts
    #      - daily_dd_safety_pct: % of firm's daily DD limit at which the
    #        bot self-stops; 80 = stop at 80% of the firm limit
    # CHANGED: April 2026 — explicit XAUUSD assumption
    risk_per_trade_pct: float = 1.0
    default_sl_pips: float = 150.0
    calculated_lot_size: float = 0.0
    daily_dd_safety_pct: float = 80.0

    individual_results: list = field(default_factory=list)


# ── Profit rescaling ──────────────────────────────────────────────────────────

def _rescale_trades(trades_df, account_size: float, risk_per_trade_pct: float,
                    default_sl_pips: float, pip_value_per_lot: float):
    """
    Rescale trade profits using Pips column with fixed lot sizing.
    Daily DD management happens during simulation, not here.
    Returns (modified_df, calculated_lot_size).
    """
    df = trades_df.copy()

    risk_dollars = account_size * (risk_per_trade_pct / 100.0)
    lot_size = risk_dollars / (default_sl_pips * pip_value_per_lot)
    lot_size = max(0.01, min(lot_size, 100.0))

    if "Pips" in df.columns:
        df["Profit_Original"] = df["Profit"].copy()
        df["Profit"] = df["Pips"] * pip_value_per_lot * lot_size
    else:
        avg_lot = df["Lots"].mean() if "Lots" in df.columns else 1.0
        scale = lot_size / avg_lot if avg_lot > 0 else 1.0
        df["Profit_Original"] = df["Profit"].copy()
        df["Profit"] = df["Profit"] * scale

    return df, lot_size


# ── Payout frequency map ───────────────────────────────────────────────────────

_PAYOUT_FREQ_DAYS = {
    "weekly":    7,
    "biweekly":  14,
    "monthly":   30,
    "on_demand": 7,
}


# ── Daily DD safety helper ─────────────────────────────────────────────────────

def _apply_daily_safety(trade_profits: list, safety_threshold: Optional[float]) -> float:
    """
    Process trades one by one within a day.
    Stops trading for the day when running loss reaches the safety threshold.
    Winning trades are never stopped — only protects against runaway losing days.
    Returns the effective day P&L after applying the safety rule.
    """
    if not trade_profits:
        return 0.0

    if safety_threshold is None:
        return sum(trade_profits)

    running_pnl = 0.0
    for profit in trade_profits:
        # Check safety BEFORE taking the next trade (only when already in a loss)
        if running_pnl < 0 and abs(running_pnl) >= safety_threshold:
            break
        running_pnl += profit

    return running_pnl


# ── Internal simulation helpers ────────────────────────────────────────────────

def _simulate_phase(trading_dates, daily_trades, start_idx, phase,
                    account_size, max_override_days, daily_dd_safety_pct,
                    dd_mechanics=None):
    """
    Simulate one evaluation phase.
    Returns dict with outcome, calendar_days, trading_days, profit_pct, max_dd_pct, next_idx.
    """
    profit_target_pct = phase.get("profit_target_pct") or 0.0
    max_daily_dd_pct  = phase.get("max_daily_drawdown_pct")
    max_total_dd_pct  = phase.get("max_total_drawdown_pct") or 999.0
    drawdown_type     = phase.get("drawdown_type", "static")
    min_trading_days  = phase.get("min_trading_days") or 0
    max_cal_days      = phase.get("max_calendar_days")
    if max_override_days is not None:
        max_cal_days = max_override_days

    consistency_pct   = phase.get("consistency_rule_pct")
    consistency_type  = phase.get("consistency_rule_type")

    # Compute daily safety threshold from firm's daily DD limit
    daily_dd_limit_abs  = account_size * (max_daily_dd_pct / 100.0) if max_daily_dd_pct else None
    safety_threshold    = (daily_dd_limit_abs * (daily_dd_safety_pct / 100.0)
                           if daily_dd_limit_abs else None)

    if start_idx >= len(trading_dates):
        return {"outcome": "INSUFFICIENT_TRADES", "calendar_days": 0,
                "trading_days": 0, "profit_pct": 0.0, "max_dd_pct": 0.0,
                "next_idx": start_idx}

    # ── Parse drawdown_mechanics ──────────────────────────────────────────
    # WHY: Firms like Leveraged have trailing DD on closed balance with HWM lock.
    #      Generic "trailing" doesn't capture this. Wrong DD = wrong pass rates.
    # CHANGED: April 2026 — firm-specific DD mechanics
    if dd_mechanics is None:
        dd_mechanics = {}
    trailing_dd       = dd_mechanics.get('trailing_dd', {})
    hwm_lock_gain_pct = trailing_dd.get('lock_after_gain_pct')
    hwm_locked        = False
    daily_dd_config   = dd_mechanics.get('daily_dd', {})
    daily_dd_ref_type = daily_dd_config.get('reference', '')

    balance  = float(account_size)
    hwm      = float(account_size)
    target   = account_size * profit_target_pct / 100.0

    trading_days = 0
    max_dd_hit   = 0.0
    day_profits  = []
    phase_start  = trading_dates[start_idx]

    for i in range(start_idx, len(trading_dates)):
        cur_date    = trading_dates[i]
        trade_list  = daily_trades[cur_date]
        day_pnl     = _apply_daily_safety(trade_list, safety_threshold)

        balance      += day_pnl
        trading_days += 1
        day_profits.append(day_pnl)

        cal_days = (cur_date - phase_start).days + 1

        # WHY: HWM lock — once gain hits threshold, HWM stops trailing
        # CHANGED: April 2026 — HWM lock for Leveraged
        if drawdown_type in ("trailing", "trailing_eod"):
            if hwm_lock_gain_pct and not hwm_locked:
                gain_pct = (balance - account_size) / account_size * 100.0
                if gain_pct >= hwm_lock_gain_pct:
                    hwm_locked = True
                    hwm = account_size
                else:
                    hwm = max(hwm, balance)
            elif hwm_locked:
                pass
            else:
                hwm = max(hwm, balance)

        # ── Daily DD calculation — firm-specific reference ────────────────
        # WHY: Leveraged uses max(balance, equity) as daily DD reference.
        # CHANGED: April 2026 — respect DD mechanics from JSON
        if daily_dd_ref_type == 'max_balance_equity':
            dd_ref = max(balance - day_pnl, account_size)  # balance at start of day
            daily_dd = abs(min(0.0, day_pnl)) / dd_ref * 100.0
        else:
            drawdown_basis = phase.get("drawdown_basis", "balance")
            if drawdown_basis == "balance_or_equity_higher":
                dd_reference = max(account_size, balance)
                daily_dd = abs(min(0.0, day_pnl)) / dd_reference * 100.0
            elif drawdown_basis == "equity":
                daily_dd = abs(min(0.0, day_pnl)) / balance * 100.0
            else:
                daily_dd = abs(min(0.0, day_pnl)) / account_size * 100.0

        if drawdown_type == "static":
            total_dd = max(0.0, (account_size - balance) / account_size * 100.0)
        else:
            total_dd = max(0.0, (hwm - balance) / account_size * 100.0)

        max_dd_hit = max(max_dd_hit, total_dd)

        # Daily DD breach (safety margin didn't prevent it fully)
        if max_daily_dd_pct is not None and daily_dd >= max_daily_dd_pct:
            return {"outcome": "FAIL_DAILY_DD", "calendar_days": cal_days,
                    "trading_days": trading_days,
                    "profit_pct": (balance - account_size) / account_size * 100.0,
                    "max_dd_pct": max_dd_hit, "next_idx": i + 1}

        # Total DD breach
        if total_dd >= max_total_dd_pct:
            return {"outcome": "FAIL_DD", "calendar_days": cal_days,
                    "trading_days": trading_days,
                    "profit_pct": (balance - account_size) / account_size * 100.0,
                    "max_dd_pct": max_dd_hit, "next_idx": i + 1}

        # Timeout
        if max_cal_days and cal_days > max_cal_days:
            return {"outcome": "FAIL_TIMEOUT", "calendar_days": cal_days,
                    "trading_days": trading_days,
                    "profit_pct": (balance - account_size) / account_size * 100.0,
                    "max_dd_pct": max_dd_hit, "next_idx": i + 1}

        # Profit target reached
        if (balance - account_size) >= target and trading_days >= min_trading_days:
            profit_pct = (balance - account_size) / account_size * 100.0
            cal_days_final = (cur_date - phase_start).days + 1

            # Consistency check
            if consistency_pct and consistency_type:
                pos_profits = [p for p in day_profits if p > 0]
                if pos_profits:
                    best = max(pos_profits)
                    if consistency_type == "best_day_vs_total":
                        total_pos = sum(pos_profits)
                        ratio = best / total_pos * 100.0 if total_pos > 0 else 0.0
                    elif consistency_type == "best_day_vs_target":
                        ratio = best / target * 100.0 if target > 0 else 0.0
                    else:
                        ratio = 0.0
                    if ratio >= consistency_pct:
                        return {"outcome": "FAIL_CONSISTENCY",
                                "calendar_days": cal_days_final,
                                "trading_days": trading_days,
                                "profit_pct": profit_pct,
                                "max_dd_pct": max_dd_hit, "next_idx": i + 1}

            return {"outcome": "PASS", "calendar_days": cal_days_final,
                    "trading_days": trading_days, "profit_pct": profit_pct,
                    "max_dd_pct": max_dd_hit, "next_idx": i + 1}

    # Ran out of trades
    final_cal = (trading_dates[-1] - phase_start).days + 1 if len(trading_dates) > start_idx else 0
    return {"outcome": "INSUFFICIENT_TRADES", "calendar_days": final_cal,
            "trading_days": trading_days,
            "profit_pct": (balance - account_size) / account_size * 100.0,
            "max_dd_pct": max_dd_hit, "next_idx": len(trading_dates)}


def _simulate_eval(trading_dates, daily_trades, start_date, start_idx,
                   phases, account_size, max_override_days, daily_dd_safety_pct,
                   dd_mechanics=None):
    """Simulate all evaluation phases in sequence. Returns eval result dict."""
    if not phases:
        return {"outcome": "PASS", "calendar_days": 0, "trading_days": 0,
                "profit_pct": 0.0, "max_dd_pct": 0.0, "next_idx": start_idx,
                "phase_results": []}

    current_idx    = start_idx
    total_cal      = 0
    total_tdays    = 0
    overall_max_dd = 0.0
    phase_results  = []

    for phase in phases:
        pr = _simulate_phase(trading_dates, daily_trades, current_idx,
                             phase, account_size, max_override_days, daily_dd_safety_pct,
                             dd_mechanics=dd_mechanics)
        phase_results.append(pr)
        total_cal   += pr["calendar_days"]
        total_tdays += pr["trading_days"]
        overall_max_dd = max(overall_max_dd, pr["max_dd_pct"])

        if pr["outcome"] != "PASS":
            return {"outcome": pr["outcome"], "calendar_days": total_cal,
                    "trading_days": total_tdays, "profit_pct": pr["profit_pct"],
                    "max_dd_pct": overall_max_dd, "next_idx": pr["next_idx"],
                    "phase_results": phase_results}

        current_idx = pr["next_idx"]

    return {"outcome": "PASS", "calendar_days": total_cal,
            "trading_days": total_tdays,
            "profit_pct": phase_results[-1]["profit_pct"],
            "max_dd_pct": overall_max_dd, "next_idx": current_idx,
            "phase_results": phase_results}


def _simulate_funded_stage(trading_dates, daily_trades, start_idx,
                           funded_cfg, account_size, daily_dd_safety_pct,
                           trading_rules=None, dd_mechanics=None):
    """Simulate funded account from start_idx onward with trading_rules support."""
    max_daily_dd  = funded_cfg.get("max_daily_drawdown_pct")
    max_total_dd  = funded_cfg.get("max_total_drawdown_pct") or 999.0
    dd_type       = funded_cfg.get("drawdown_type", "static")
    split_pct     = float(funded_cfg.get("profit_split_pct") or 80)
    payout_freq   = funded_cfg.get("payout_frequency", "biweekly")
    dd_reset      = funded_cfg.get("dd_reset_on_payout", False)
    min_payout    = float(funded_cfg.get("min_payout_amount") or 0)

    payout_interval = _PAYOUT_FREQ_DAYS.get(payout_freq, 14)

    # Parse trading_rules
    consistency_max_pct = None
    min_profitable_days_count = 0
    min_profitable_day_pct = 0
    emergency_total_dd_pct = None
    stop_after_conditions_met = False

    if trading_rules:
        for rule in trading_rules:
            if rule.get('stage') != 'funded':
                continue
            rtype = rule.get('type', '')
            params = rule.get('parameters', {})

            if rtype == 'funded_accumulate':
                emergency_total_dd_pct = params.get('emergency_total_dd_stop_pct')

            elif rtype == 'funded_protect':
                stop_after_conditions_met = params.get('stop_trading', False)

            elif rtype == 'consistency':
                consistency_max_pct = params.get('max_day_pct')

            elif rtype == 'min_profitable_days':
                min_profitable_days_count = params.get('min_days', 0)
                min_profitable_day_pct = params.get('min_pct_per_day', 0)

    # Funded safety threshold
    daily_dd_limit_abs = account_size * (max_daily_dd / 100.0) if max_daily_dd else None
    safety_threshold   = (daily_dd_limit_abs * (daily_dd_safety_pct / 100.0)
                          if daily_dd_limit_abs else None)

    if start_idx >= len(trading_dates):
        return {"survival_days": 0, "trading_days": 0, "total_payouts": 0.0,
                "payout_count": 0, "monthly_avg": 0.0, "max_dd_pct": 0.0,
                "end_reason": "TRADES_EXHAUSTED"}

    # ── Parse drawdown_mechanics for funded stage ─────────────────────────
    # WHY: HWM lock and daily DD reference also apply in the funded stage.
    # CHANGED: April 2026 — firm-specific DD mechanics in funded
    if dd_mechanics is None:
        dd_mechanics = {}
    _trailing_dd_f       = dd_mechanics.get('trailing_dd', {})
    hwm_lock_gain_pct_f  = _trailing_dd_f.get('lock_after_gain_pct')
    hwm_locked           = False
    _daily_dd_cfg_f      = dd_mechanics.get('daily_dd', {})
    daily_dd_ref_type_f  = _daily_dd_cfg_f.get('reference', '')

    balance      = float(account_size)
    hwm          = float(account_size)
    total_payout = 0.0
    payout_count = 0
    trading_days = 0
    max_dd_hit   = 0.0

    funded_start     = trading_dates[start_idx]
    last_payout_date = funded_start

    # Payout period tracking for trading_rules
    period_daily_pnls = {}  # {date_str: pnl} for current payout period
    payout_conditions_met = False
    stopped_for_period = False

    for i in range(start_idx, len(trading_dates)):
        cur_date   = trading_dates[i]
        trade_list = daily_trades[cur_date]

        # If stopped for period (emergency DD or payout met), skip trading
        if stopped_for_period:
            # Check if new payout period started
            if (cur_date - last_payout_date).days >= payout_interval:
                # New period — reset
                stopped_for_period = False
                payout_conditions_met = False
                period_daily_pnls = {}
                # Process payout for previous period
                profit = balance - account_size
                if profit > 0 and payout_conditions_met:
                    payout = profit * split_pct / 100.0
                    if payout >= min_payout:
                        total_payout += payout
                        payout_count += 1
                        balance      -= payout
                        if dd_reset:
                            hwm = balance
                last_payout_date = cur_date
            else:
                continue

        day_pnl = _apply_daily_safety(trade_list, safety_threshold)

        balance      += day_pnl
        trading_days += 1

        cal_days = (cur_date - funded_start).days + 1

        # Track daily P&L for this payout period
        day_str = str(cur_date)
        period_daily_pnls[day_str] = period_daily_pnls.get(day_str, 0) + day_pnl

        # WHY: HWM lock — Leveraged locks at starting balance after +6% gain
        # CHANGED: April 2026 — HWM lock for funded stage
        if dd_type in ("trailing", "trailing_eod"):
            if hwm_lock_gain_pct_f and not hwm_locked:
                gain_pct = (balance - account_size) / account_size * 100.0
                if gain_pct >= hwm_lock_gain_pct_f:
                    hwm_locked = True
                    hwm = account_size
                else:
                    hwm = max(hwm, balance)
            elif hwm_locked:
                pass
            else:
                hwm = max(hwm, balance)

        # ── Daily DD — firm-specific reference ────────────────────────────
        # CHANGED: April 2026 — respect DD mechanics from JSON
        if daily_dd_ref_type_f == 'max_balance_equity':
            dd_ref_f = max(balance - day_pnl, account_size)
            daily_dd = abs(min(0.0, day_pnl)) / dd_ref_f * 100.0
        else:
            drawdown_basis = funded_cfg.get("drawdown_basis", "balance")
            if drawdown_basis == "balance_or_equity_higher":
                dd_reference = max(account_size, balance)
                daily_dd = abs(min(0.0, day_pnl)) / dd_reference * 100.0
            elif drawdown_basis == "equity":
                daily_dd = abs(min(0.0, day_pnl)) / balance * 100.0
            else:
                daily_dd = abs(min(0.0, day_pnl)) / account_size * 100.0

        if dd_type == "static":
            total_dd = max(0.0, (account_size - balance) / account_size * 100.0)
        else:
            total_dd = max(0.0, (hwm - balance) / account_size * 100.0)

        max_dd_hit = max(max_dd_hit, total_dd)

        if max_daily_dd is not None and daily_dd >= max_daily_dd:
            monthly = total_payout / (cal_days / 30) if cal_days > 0 else 0.0
            return {"survival_days": cal_days, "trading_days": trading_days,
                    "total_payouts": total_payout, "payout_count": payout_count,
                    "monthly_avg": monthly, "max_dd_pct": max_dd_hit,
                    "end_reason": "DAILY_DD_BREACH"}

        if total_dd >= max_total_dd:
            monthly = total_payout / (cal_days / 30) if cal_days > 0 else 0.0
            return {"survival_days": cal_days, "trading_days": trading_days,
                    "total_payouts": total_payout, "payout_count": payout_count,
                    "monthly_avg": monthly, "max_dd_pct": max_dd_hit,
                    "end_reason": "DD_BREACH"}

        # Emergency total DD stop
        if emergency_total_dd_pct and not payout_conditions_met:
            if total_dd >= emergency_total_dd_pct:
                stopped_for_period = True
                continue

        # Check payout conditions
        if not payout_conditions_met and consistency_max_pct:
            period_profit = sum(v for v in period_daily_pnls.values() if v > 0)

            if period_profit > 0:
                # Check consistency: best day under max_pct of total
                best_day = max(period_daily_pnls.values()) if period_daily_pnls else 0
                best_day_pct = (best_day / period_profit * 100) if period_profit > 0 else 100
                consistency_ok = best_day_pct <= consistency_max_pct

                # Check min profitable days
                min_profit_threshold = account_size * (min_profitable_day_pct / 100)
                profitable_days = sum(1 for v in period_daily_pnls.values() if v >= min_profit_threshold)
                min_days_ok = profitable_days >= min_profitable_days_count

                if consistency_ok and min_days_ok:
                    payout_conditions_met = True
                    if stop_after_conditions_met:
                        stopped_for_period = True

        # Payout processing
        if (cur_date - last_payout_date).days >= payout_interval:
            profit = balance - account_size
            if profit > 0 and payout_conditions_met:
                payout = profit * split_pct / 100.0
                if payout >= min_payout:
                    total_payout += payout
                    payout_count += 1
                    balance      -= payout
                    # WHY: After payout, DD behavior depends on firm mechanics.
                    #      Leveraged: DD locks at initial balance after withdrawal.
                    #      Generic: DD resets to current balance.
                    # CHANGED: April 2026 — post-payout DD lock
                    post_payout = dd_mechanics.get('post_payout', {}) if dd_mechanics else {}
                    if post_payout.get('dd_locks_at') == 'initial_balance':
                        hwm = account_size
                        hwm_locked = True
                    elif dd_reset:
                        hwm = balance

            # Reset for next period
            last_payout_date = cur_date
            period_daily_pnls = {}
            payout_conditions_met = False
            stopped_for_period = False

    final_cal = (trading_dates[-1] - funded_start).days + 1 if len(trading_dates) > start_idx else 0
    monthly   = total_payout / (final_cal / 30) if final_cal > 0 else 0.0
    return {"survival_days": final_cal, "trading_days": trading_days,
            "total_payouts": total_payout, "payout_count": payout_count,
            "monthly_avg": monthly, "max_dd_pct": max_dd_hit,
            "end_reason": "TRADES_EXHAUSTED"}


def _single_sim(trading_dates, daily_trades, start_date, date_to_idx,
                phases, funded_cfg, account_size, simulate_funded,
                max_override_days, daily_dd_safety_pct, trading_rules=None,
                dd_mechanics=None):
    """Run one complete simulation (eval + optional funded) with trading_rules."""
    start_idx = date_to_idx[start_date]
    eval_r    = _simulate_eval(trading_dates, daily_trades, start_date, start_idx,
                               phases, account_size, max_override_days, daily_dd_safety_pct,
                               dd_mechanics=dd_mechanics)

    if eval_r["outcome"] != "PASS" or not simulate_funded:
        return SingleSimResult(
            start_date=str(start_date),
            eval_outcome=eval_r["outcome"],
            eval_days=eval_r["calendar_days"],
            eval_trading_days=eval_r["trading_days"],
            eval_profit_pct=round(eval_r["profit_pct"], 4),
            eval_max_dd_pct=round(eval_r["max_dd_pct"], 4),
            eval_phase_results=eval_r.get("phase_results", []),
            funded_survival_days=None,
            funded_survival_trading_days=None,
            funded_total_payouts=None,
            funded_payout_count=None,
            funded_monthly_avg=None,
            funded_max_dd_pct=None,
            funded_end_reason=None,
        )

    funded_r = _simulate_funded_stage(
        trading_dates, daily_trades, eval_r["next_idx"],
        funded_cfg, account_size, daily_dd_safety_pct,
        trading_rules=trading_rules, dd_mechanics=dd_mechanics)

    return SingleSimResult(
        start_date=str(start_date),
        eval_outcome="PASS",
        eval_days=eval_r["calendar_days"],
        eval_trading_days=eval_r["trading_days"],
        eval_profit_pct=round(eval_r["profit_pct"], 4),
        eval_max_dd_pct=round(eval_r["max_dd_pct"], 4),
        eval_phase_results=eval_r.get("phase_results", []),
        funded_survival_days=funded_r["survival_days"],
        funded_survival_trading_days=funded_r["trading_days"],
        funded_total_payouts=round(funded_r["total_payouts"], 2),
        funded_payout_count=funded_r["payout_count"],
        funded_monthly_avg=round(funded_r["monthly_avg"], 2),
        funded_max_dd_pct=round(funded_r["max_dd_pct"], 4),
        funded_end_reason=funded_r["end_reason"],
    )


def _aggregate(results, firm, challenge, account_size, mode,
               risk_per_trade_pct=1.0, default_sl_pips=150.0,
               calculated_lot_size=0.0, daily_dd_safety_pct=80.0):
    """Build SimulationSummary from a list of SingleSimResult."""
    import statistics as stats_mod

    passes = [r for r in results if r.eval_outcome == "PASS"]
    fails  = [r for r in results if r.eval_outcome != "PASS"]
    total  = len(results)

    pass_rate    = len(passes) / total if total > 0 else 0.0
    fail_reasons = {}
    for r in fails:
        fail_reasons[r.eval_outcome] = fail_reasons.get(r.eval_outcome, 0) + 1

    days_pass = [r.eval_days for r in passes]
    days_fail = [r.eval_days for r in fails]
    all_dd    = [r.eval_max_dd_pct for r in results]

    funded_ok = [r for r in passes if r.funded_survival_days is not None]

    def _mean(lst):   return stats_mod.mean(lst) if lst else 0.0
    def _median(lst): return stats_mod.median(lst) if lst else 0.0

    f_survival  = [r.funded_survival_days for r in funded_ok]
    f_monthly   = [r.funded_monthly_avg   for r in funded_ok if r.funded_monthly_avg]
    f_total_pay = [r.funded_total_payouts for r in funded_ok if r.funded_total_payouts is not None]
    f_count     = [r.funded_payout_count  for r in funded_ok if r.funded_payout_count  is not None]

    surv_3mo = (len([r for r in funded_ok if r.funded_survival_days >= 90])  / len(funded_ok)
                if funded_ok else None)
    surv_6mo = (len([r for r in funded_ok if r.funded_survival_days >= 180]) / len(funded_ok)
                if funded_ok else None)

    # Fee lookup
    fee = None
    fee_raw = challenge.get("costs", {}).get("challenge_fee_by_size", {}).get(str(account_size))
    if fee_raw is not None:
        try:
            fee = float(fee_raw)
        except (TypeError, ValueError):
            fee = None

    avg_attempts    = 1.0 / pass_rate if pass_rate > 0 else float("inf")
    expected_cost   = fee * avg_attempts if (fee is not None and pass_rate > 0) else None
    expected_income = _mean(f_total_pay) if f_total_pay else None
    expected_net    = ((expected_income - expected_cost)
                       if (expected_income is not None and expected_cost is not None) else None)
    expected_roi    = ((expected_net / expected_cost * 100)
                       if (expected_net is not None and expected_cost and expected_cost > 0) else None)

    return SimulationSummary(
        firm_name=firm.firm_name,
        challenge_name=challenge["challenge_name"],
        account_size=account_size,
        num_simulations=total,
        simulation_mode=mode,
        eval_pass_rate=round(pass_rate, 4),
        eval_avg_days_to_pass=round(_mean(days_pass), 1),
        eval_avg_days_to_fail=round(_mean(days_fail), 1),
        eval_median_days_to_pass=round(_median(days_pass), 1),
        eval_avg_max_dd_pct=round(_mean(all_dd), 4),
        eval_pass_count=len(passes),
        eval_fail_count=len(fails),
        eval_fail_reasons=fail_reasons,
        funded_avg_survival_days=round(_mean(f_survival), 1) if f_survival else None,
        funded_median_survival_days=round(_median(f_survival), 1) if f_survival else None,
        funded_avg_monthly_payout=round(_mean(f_monthly), 2) if f_monthly else None,
        funded_avg_total_payouts=round(_mean(f_total_pay), 2) if f_total_pay else None,
        funded_survival_rate_3mo=round(surv_3mo, 4) if surv_3mo is not None else None,
        funded_survival_rate_6mo=round(surv_6mo, 4) if surv_6mo is not None else None,
        funded_avg_payout_count=round(_mean(f_count), 2) if f_count else None,
        challenge_fee=fee,
        avg_attempts_to_pass=round(avg_attempts, 2) if avg_attempts != float("inf") else float("inf"),
        expected_cost=round(expected_cost, 2) if expected_cost is not None else None,
        expected_funded_income=round(expected_income, 2) if expected_income is not None else None,
        expected_net_profit=round(expected_net, 2) if expected_net is not None else None,
        expected_roi_pct=round(expected_roi, 1) if expected_roi is not None else None,
        risk_per_trade_pct=risk_per_trade_pct,
        default_sl_pips=default_sl_pips,
        calculated_lot_size=round(calculated_lot_size, 4),
        daily_dd_safety_pct=daily_dd_safety_pct,
        individual_results=results,
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def simulate_challenge(
    trades_df,
    firm_id: str,
    challenge_id: str,
    account_size: int,
    mode: str = "sliding_window",
    num_samples: int = 500,
    simulate_funded: bool = True,
    max_eval_calendar_days: Optional[int] = None,
    random_seed: int = 42,
    risk_per_trade_pct: float = 1.0,
    default_sl_pips: float = 150.0,
    pip_value_per_lot: float = 1.0,
    daily_dd_safety_pct: float = 80.0,
) -> Optional[SimulationSummary]:
    """
    Simulate the full prop firm challenge lifecycle.

    Parameters
    ----------
    trades_df            : pd.DataFrame — trades with 'Close Date' and 'Profit' columns
    firm_id              : str          — e.g. "ftmo"
    challenge_id         : str          — e.g. "ftmo_2step_standard"
    account_size         : int          — e.g. 100000
    mode                 : "sliding_window" | "monte_carlo"
    num_samples          : int          — Monte Carlo sample count
    simulate_funded      : bool         — run Stage 2 for passed windows
    max_eval_calendar_days: override firm's time limit (None = use firm's)
    random_seed          : int          — reproducible Monte Carlo
    risk_per_trade_pct   : float        — % of account risked per trade (e.g. 1.0 = 1%)
    default_sl_pips      : float        — SL distance for lot sizing
    pip_value_per_lot    : float        — $ per pip per standard lot (XAUUSD = 1.0)
    daily_dd_safety_pct  : float        — stop trading when daily loss reaches this
                                          % of the firm's daily DD limit (default 80%)

    Returns SimulationSummary or None if firm/challenge not found.
    """
    import pandas as pd
    import random
    from shared.prop_firm_engine import load_all_firms

    firms = load_all_firms()
    if firm_id not in firms:
        return None
    firm      = firms[firm_id]
    challenge = firm.get_challenge(challenge_id)
    if not challenge or account_size not in challenge.get("account_sizes", []):
        return None

    phases     = challenge.get("phases", [])
    funded_cfg = challenge.get("funded", {})

    # Load trading_rules from firm data
    trading_rules = firm.config.get('trading_rules', [])

    # WHY: drawdown_mechanics override generic DD behavior with firm-specific rules
    # CHANGED: April 2026 — pass DD mechanics to all simulation functions
    dd_mechanics = firm.config.get('drawdown_mechanics', {})

    # ── Prepare trades ────────────────────────────────────────────────────────
    df = trades_df.copy()
    df["_close_dt"]   = pd.to_datetime(df["Close Date"], dayfirst=True, errors="coerce")

    # Convert to firm's DD reset timezone for accurate daily grouping
    dd_reset_tz = firm.config.get("dd_reset_timezone", "UTC")
    if dd_reset_tz != "UTC":
        try:
            import pytz
            tz_map = {
                'CET': 'Europe/Berlin',
                'CT': 'US/Central',
                'ET': 'US/Eastern',
                'UTC': 'UTC',
            }
            tz_name = tz_map.get(dd_reset_tz, dd_reset_tz)
            # Localize to UTC first, then convert to firm's timezone
            df["_close_dt"] = df["_close_dt"].dt.tz_localize('UTC').dt.tz_convert(tz_name)
        except Exception:
            pass  # fallback to UTC if timezone conversion fails

    df["_close_date"] = df["_close_dt"].dt.date
    df = df.dropna(subset=["_close_dt"]).sort_values("_close_dt").reset_index(drop=True)

    # Rescale profits to match account size and risk level
    df, calculated_lot_size = _rescale_trades(
        df, account_size, risk_per_trade_pct, default_sl_pips, pip_value_per_lot
    )
    print(f"[SIMULATOR] Lot size: {calculated_lot_size:.2f} lots "
          f"(risk: {risk_per_trade_pct}%, SL: {default_sl_pips} pips, "
          f"daily DD safety: {daily_dd_safety_pct}%)")

    # Build daily_trades: date → sorted list of individual trade profits
    daily_trades: dict = {}
    for _, row in df.iterrows():
        d = row["_close_date"]
        if d not in daily_trades:
            daily_trades[d] = []
        daily_trades[d].append(float(row["Profit"]))
    # Trades are already sorted by _close_dt, so lists are chronologically ordered

    trading_dates = sorted(daily_trades.keys())
    if not trading_dates:
        return None

    date_to_idx = {d: i for i, d in enumerate(trading_dates)}

    # ── Build starting points ─────────────────────────────────────────────────
    MIN_REMAINING = 10
    valid_starts  = [d for i, d in enumerate(trading_dates)
                     if len(trading_dates) - i >= MIN_REMAINING]

    if not valid_starts:
        return None

    if mode == "sliding_window":
        start_dates = valid_starts
    else:
        rng = random.Random(random_seed)
        n   = min(num_samples, len(valid_starts))
        start_dates = rng.sample(valid_starts, n)
        start_dates.sort()

    # ── Run simulations ───────────────────────────────────────────────────────
    total       = len(start_dates)
    all_results = []

    print(f"[SIMULATOR] {firm.firm_name} — {challenge['challenge_name']} "
          f"({account_size:,}) | {mode} | {total} windows")

    for idx, start_date in enumerate(start_dates):
        if (idx + 1) % 50 == 0 or idx == total - 1:
            print(f"[SIMULATOR] Running simulation {idx+1}/{total}...")

        r = _single_sim(
            trading_dates, daily_trades, start_date, date_to_idx,
            phases, funded_cfg, account_size, simulate_funded,
            max_eval_calendar_days, daily_dd_safety_pct,
            trading_rules=trading_rules, dd_mechanics=dd_mechanics,
        )
        all_results.append(r)

    print(f"[SIMULATOR] Done — {sum(1 for r in all_results if r.eval_outcome == 'PASS')}/{total} passed")

    return _aggregate(all_results, firm, challenge, account_size, mode,
                      risk_per_trade_pct, default_sl_pips,
                      calculated_lot_size, daily_dd_safety_pct)
