"""
Prop Firm Lifecycle Simulator — windowed simulation with probability analysis.

Simulates the full prop firm lifecycle:
  Stage 1 (Evaluation): Can the robot pass the challenge? What's the probability?
  Stage 2 (Funded): Once funded, how long does the robot survive? How much does it earn?
  Stage 3 (Expected Value): Is it worth the challenge fee, factoring in retries?

Two simulation modes:
  - Sliding window: start a fresh challenge at every possible date in the trade history
  - Monte Carlo: randomly sample N starting dates

Uses the robot's ACTUAL trade frequency — respects gaps between trades.
"""

from dataclasses import dataclass, field


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
    funded_survival_days: int | None
    funded_survival_trading_days: int | None
    funded_total_payouts: float | None
    funded_payout_count: int | None
    funded_monthly_avg: float | None
    funded_max_dd_pct: float | None
    funded_end_reason: str | None


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
    funded_avg_survival_days: float | None
    funded_median_survival_days: float | None
    funded_avg_monthly_payout: float | None
    funded_avg_total_payouts: float | None
    funded_survival_rate_3mo: float | None
    funded_survival_rate_6mo: float | None
    funded_avg_payout_count: float | None

    # Stage 3
    challenge_fee: float | None
    avg_attempts_to_pass: float
    expected_cost: float | None
    expected_funded_income: float | None
    expected_net_profit: float | None
    expected_roi_pct: float | None

    # Configuration used
    risk_per_trade_pct: float = 1.0
    default_sl_pips: float = 150.0
    calculated_lot_size: float = 0.0

    individual_results: list = field(default_factory=list)


# ── Profit rescaling ──────────────────────────────────────────────────────────

def _rescale_trades(trades_df, account_size: float, risk_per_trade_pct: float,
                    default_sl_pips: float, pip_value_per_lot: float):
    """
    Rescale trade profits from raw dollar values to what they would be
    on the simulated account using proper position sizing.

    Uses the Pips column (raw price movement) instead of the Profit column
    (which depends on the robot's actual lot size).

    Returns (modified_df, calculated_lot_size).
    """
    import pandas as pd

    df = trades_df.copy()

    # Calculate lot size for this account and risk level
    risk_dollars = account_size * (risk_per_trade_pct / 100.0)
    lot_size = risk_dollars / (default_sl_pips * pip_value_per_lot)
    lot_size = max(0.01, min(lot_size, 100.0))

    if "Pips" in df.columns:
        df["Profit_Original"] = df["Profit"].copy()
        df["Profit"] = df["Pips"] * pip_value_per_lot * lot_size
    else:
        # Fallback: proportional scaling by lot ratio
        avg_lot = df["Lots"].mean() if "Lots" in df.columns else 1.0
        if avg_lot > 0:
            scale_factor = lot_size / avg_lot
            df["Profit_Original"] = df["Profit"].copy()
            df["Profit"] = df["Profit"] * scale_factor

    return df, lot_size


# ── Payout frequency map ───────────────────────────────────────────────────────

_PAYOUT_FREQ_DAYS = {
    "weekly":    7,
    "biweekly":  14,
    "monthly":   30,
    "on_demand": 7,
}


# ── Internal simulation helpers ────────────────────────────────────────────────

def _simulate_phase(trading_dates, daily_pnl, start_idx, phase, account_size, max_override_days):
    """
    Simulate one evaluation phase.
    Returns dict with outcome, calendar_days, trading_days, profit_pct, max_dd_pct, next_idx.
    """
    profit_target_pct = phase.get("profit_target_pct") or 0.0
    max_daily_dd_pct  = phase.get("max_daily_drawdown_pct")      # may be None
    max_total_dd_pct  = phase.get("max_total_drawdown_pct") or 999.0
    drawdown_type     = phase.get("drawdown_type", "static")
    min_trading_days  = phase.get("min_trading_days") or 0
    max_cal_days      = phase.get("max_calendar_days")
    if max_override_days is not None:
        max_cal_days = max_override_days

    consistency_pct   = phase.get("consistency_rule_pct")
    consistency_type  = phase.get("consistency_rule_type")

    if start_idx >= len(trading_dates):
        return {"outcome": "INSUFFICIENT_TRADES", "calendar_days": 0,
                "trading_days": 0, "profit_pct": 0.0, "max_dd_pct": 0.0,
                "next_idx": start_idx}

    balance  = float(account_size)
    hwm      = float(account_size)
    target   = account_size * profit_target_pct / 100.0

    trading_days = 0
    max_dd_hit   = 0.0
    day_profits  = []          # for consistency check
    phase_start  = trading_dates[start_idx]

    for i in range(start_idx, len(trading_dates)):
        cur_date = trading_dates[i]
        day_pnl  = daily_pnl[cur_date]

        balance      += day_pnl
        trading_days += 1
        day_profits.append(day_pnl)

        cal_days = (cur_date - phase_start).days + 1

        if drawdown_type in ("trailing", "trailing_eod"):
            hwm = max(hwm, balance)

        daily_dd = abs(min(0.0, day_pnl)) / account_size * 100.0

        if drawdown_type == "static":
            total_dd = max(0.0, (account_size - balance) / account_size * 100.0)
        else:
            total_dd = max(0.0, (hwm - balance) / account_size * 100.0)

        max_dd_hit = max(max_dd_hit, total_dd)

        # Daily DD breach
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

    # Ran out of trades — target never reached
    final_cal = (trading_dates[-1] - phase_start).days + 1 if len(trading_dates) > start_idx else 0
    return {"outcome": "INSUFFICIENT_TRADES", "calendar_days": final_cal,
            "trading_days": trading_days,
            "profit_pct": (balance - account_size) / account_size * 100.0,
            "max_dd_pct": max_dd_hit, "next_idx": len(trading_dates)}


def _simulate_eval(trading_dates, daily_pnl, start_date, start_idx,
                   phases, account_size, max_override_days):
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
        pr = _simulate_phase(trading_dates, daily_pnl, current_idx,
                             phase, account_size, max_override_days)
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
        # Most firms reset balance to account_size for next phase (handled implicitly
        # since _simulate_phase always starts from account_size)

    return {"outcome": "PASS", "calendar_days": total_cal,
            "trading_days": total_tdays,
            "profit_pct": phase_results[-1]["profit_pct"],
            "max_dd_pct": overall_max_dd, "next_idx": current_idx,
            "phase_results": phase_results}


def _simulate_funded_stage(trading_dates, daily_pnl, start_idx, funded_cfg, account_size):
    """Simulate funded account from start_idx onward. Returns funded result dict."""
    max_daily_dd  = funded_cfg.get("max_daily_drawdown_pct")
    max_total_dd  = funded_cfg.get("max_total_drawdown_pct") or 999.0
    dd_type       = funded_cfg.get("drawdown_type", "static")
    split_pct     = float(funded_cfg.get("profit_split_pct") or 80)
    payout_freq   = funded_cfg.get("payout_frequency", "biweekly")
    dd_reset      = funded_cfg.get("dd_reset_on_payout", False)

    payout_interval = _PAYOUT_FREQ_DAYS.get(payout_freq, 14)

    if start_idx >= len(trading_dates):
        return {"survival_days": 0, "trading_days": 0, "total_payouts": 0.0,
                "payout_count": 0, "monthly_avg": 0.0, "max_dd_pct": 0.0,
                "end_reason": "TRADES_EXHAUSTED"}

    balance      = float(account_size)
    hwm          = float(account_size)
    total_payout = 0.0
    payout_count = 0
    trading_days = 0
    max_dd_hit   = 0.0

    funded_start      = trading_dates[start_idx]
    last_payout_date  = funded_start

    for i in range(start_idx, len(trading_dates)):
        cur_date = trading_dates[i]
        day_pnl  = daily_pnl[cur_date]

        balance      += day_pnl
        trading_days += 1

        cal_days = (cur_date - funded_start).days + 1

        if dd_type in ("trailing", "trailing_eod"):
            hwm = max(hwm, balance)

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

        # Payout
        if (cur_date - last_payout_date).days >= payout_interval:
            profit = balance - account_size
            if profit > 0:
                payout        = profit * split_pct / 100.0
                total_payout += payout
                payout_count += 1
                balance      -= payout
                if dd_reset:
                    hwm = balance
            last_payout_date = cur_date

    final_cal = (trading_dates[-1] - funded_start).days + 1 if len(trading_dates) > start_idx else 0
    monthly   = total_payout / (final_cal / 30) if final_cal > 0 else 0.0
    return {"survival_days": final_cal, "trading_days": trading_days,
            "total_payouts": total_payout, "payout_count": payout_count,
            "monthly_avg": monthly, "max_dd_pct": max_dd_hit,
            "end_reason": "TRADES_EXHAUSTED"}


def _single_sim(trading_dates, daily_pnl, start_date, date_to_idx,
                phases, funded_cfg, account_size, simulate_funded, max_override_days):
    """Run one complete simulation (eval + optional funded)."""
    start_idx  = date_to_idx[start_date]
    eval_r     = _simulate_eval(trading_dates, daily_pnl, start_date, start_idx,
                                phases, account_size, max_override_days)

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
        trading_dates, daily_pnl, eval_r["next_idx"], funded_cfg, account_size)

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
               risk_per_trade_pct=1.0, default_sl_pips=150.0, calculated_lot_size=0.0):
    """Build SimulationSummary from a list of SingleSimResult."""
    import statistics as stats_mod

    passes = [r for r in results if r.eval_outcome == "PASS"]
    fails  = [r for r in results if r.eval_outcome != "PASS"]
    total  = len(results)

    pass_rate  = len(passes) / total if total > 0 else 0.0
    fail_reasons = {}
    for r in fails:
        fail_reasons[r.eval_outcome] = fail_reasons.get(r.eval_outcome, 0) + 1

    days_pass = [r.eval_days for r in passes]
    days_fail = [r.eval_days for r in fails]
    all_dd    = [r.eval_max_dd_pct for r in results]

    funded_ok = [r for r in passes if r.funded_survival_days is not None]

    def _mean(lst):  return stats_mod.mean(lst) if lst else 0.0
    def _median(lst): return stats_mod.median(lst) if lst else 0.0

    f_survival   = [r.funded_survival_days for r in funded_ok]
    f_monthly    = [r.funded_monthly_avg    for r in funded_ok if r.funded_monthly_avg]
    f_total_pay  = [r.funded_total_payouts  for r in funded_ok if r.funded_total_payouts is not None]
    f_count      = [r.funded_payout_count   for r in funded_ok if r.funded_payout_count  is not None]

    surv_3mo = len([r for r in funded_ok if r.funded_survival_days >= 90])  / len(funded_ok) if funded_ok else None
    surv_6mo = len([r for r in funded_ok if r.funded_survival_days >= 180]) / len(funded_ok) if funded_ok else None

    # Fee lookup
    fee = None
    fee_raw = challenge.get("costs", {}).get("challenge_fee_by_size", {}).get(str(account_size))
    if fee_raw is not None:
        try:
            fee = float(fee_raw)
        except (TypeError, ValueError):
            fee = None

    avg_attempts   = 1.0 / pass_rate if pass_rate > 0 else float("inf")
    expected_cost  = fee * avg_attempts if (fee is not None and pass_rate > 0) else None
    expected_income = _mean(f_total_pay) if f_total_pay else None
    expected_net   = (expected_income - expected_cost) if (expected_income is not None and expected_cost is not None) else None
    expected_roi   = (expected_net / expected_cost * 100) if (expected_net is not None and expected_cost and expected_cost > 0) else None

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
    max_eval_calendar_days: int | None = None,
    random_seed: int = 42,
    risk_per_trade_pct: float = 1.0,
    default_sl_pips: float = 150.0,
    pip_value_per_lot: float = 1.0,
) -> SimulationSummary | None:
    """
    Simulate the full prop firm challenge lifecycle.

    Parameters
    ----------
    trades_df : pd.DataFrame  — trades with 'Close Date' and 'Profit' columns
    firm_id   : str           — e.g. "ftmo"
    challenge_id : str        — e.g. "ftmo_2step_standard"
    account_size : int        — e.g. 100000
    mode      : "sliding_window" | "monte_carlo"
    num_samples : int         — Monte Carlo sample count
    simulate_funded : bool    — run Stage 2 for passed windows
    max_eval_calendar_days : override firm's time limit (None = use firm's)
    random_seed : int         — reproducible Monte Carlo

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

    # ── Prepare trades ────────────────────────────────────────────────────────
    df = trades_df.copy()
    df["_close_dt"]   = pd.to_datetime(df["Close Date"], dayfirst=True, errors="coerce")
    df["_close_date"] = df["_close_dt"].dt.date
    df = df.dropna(subset=["_close_dt"]).sort_values("_close_dt").reset_index(drop=True)

    # Rescale profits to match account size and risk level
    df, calculated_lot_size = _rescale_trades(
        df, account_size, risk_per_trade_pct, default_sl_pips, pip_value_per_lot
    )
    print(f"[SIMULATOR] Lot size: {calculated_lot_size:.2f} lots "
          f"(risk: {risk_per_trade_pct}%, SL: {default_sl_pips} pips)")

    # Aggregate daily P&L
    daily_pnl: dict = {}
    for _, row in df.iterrows():
        d = row["_close_date"]
        daily_pnl[d] = daily_pnl.get(d, 0.0) + float(row["Profit"])

    trading_dates = sorted(daily_pnl.keys())
    if not trading_dates:
        return None

    date_to_idx = {d: i for i, d in enumerate(trading_dates)}

    # ── Build starting points ─────────────────────────────────────────────────
    MIN_REMAINING = 10   # skip starting dates with < 10 trading days remaining
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
            trading_dates, daily_pnl, start_date, date_to_idx,
            phases, funded_cfg, account_size, simulate_funded, max_eval_calendar_days,
        )
        all_results.append(r)

    print(f"[SIMULATOR] Done — {sum(1 for r in all_results if r.eval_outcome == 'PASS')}/{total} passed")

    return _aggregate(all_results, firm, challenge, account_size, mode,
                      risk_per_trade_pct, default_sl_pips, calculated_lot_size)
