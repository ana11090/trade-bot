"""
Prop Firm Rules Engine — checks whether trades pass a prop firm's challenge.

Core function: check_compliance(trades_df, firm_id, challenge_id, account_size)
Returns a detailed result with PASS/FAIL per phase, daily breakdown, and failure reasons.
"""

import os
import json
from dataclasses import dataclass, field
from typing import Optional

_SHARED_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SHARED_DIR)
_PROP_FIRMS_DIR = os.path.join(_PROJECT_ROOT, "prop_firms")


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class PhaseResult:
    phase_name: str
    passed: bool
    failure_reason: Optional[str]
    profit_target_pct: float
    profit_achieved_pct: float
    max_daily_dd_allowed_pct: Optional[float]
    max_daily_dd_hit_pct: float
    max_total_dd_allowed_pct: float
    max_total_dd_hit_pct: float
    trading_days: int
    min_trading_days_required: int
    consistency_check_passed: Optional[bool]
    daily_breakdown: list = field(default_factory=list)


@dataclass
class ComplianceResult:
    firm_name: str
    challenge_name: str
    account_size: int
    phases: list
    overall_passed: bool
    failure_reason: Optional[str]
    summary: dict = field(default_factory=dict)


# ── PropFirmProfile ───────────────────────────────────────────────────────────

class PropFirmProfile:
    def __init__(self, json_path: str):
        with open(json_path, "r", encoding="utf-8") as f:
            self._data = json.load(f)
        self.firm_id   = self._data["firm_id"]
        self.firm_name = self._data["firm_name"]
        self.market_type = self._data.get("market_type", "forex_cfd")
        self._challenges = {c["challenge_id"]: c for c in self._data.get("challenges", [])}

    def get_challenge(self, challenge_id: str) -> Optional[dict]:
        return self._challenges.get(challenge_id)

    def list_challenges(self) -> list[dict]:
        return [
            {"challenge_id": c["challenge_id"], "challenge_name": c["challenge_name"]}
            for c in self._data.get("challenges", [])
        ]

    def list_account_sizes(self, challenge_id: str) -> list[int]:
        ch = self.get_challenge(challenge_id)
        return ch.get("account_sizes", []) if ch else []


# ── Load firms ────────────────────────────────────────────────────────────────

def load_all_firms() -> dict:
    """Scan prop_firms/ and load all JSON profiles. Returns dict keyed by firm_id."""
    firms = {}
    if not os.path.isdir(_PROP_FIRMS_DIR):
        return firms
    for fname in os.listdir(_PROP_FIRMS_DIR):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(_PROP_FIRMS_DIR, fname)
        try:
            profile = PropFirmProfile(path)
            firms[profile.firm_id] = profile
        except (KeyError, json.JSONDecodeError, OSError):
            pass
    return firms


def list_all_firms() -> list[dict]:
    """Return summary list of all firms."""
    firms = load_all_firms()
    return [
        {
            "firm_id":          fid,
            "firm_name":        f.firm_name,
            "market_type":      f.market_type,
            "challenge_count":  len(f.list_challenges()),
        }
        for fid, f in sorted(firms.items(), key=lambda x: x[1].firm_name)
    ]


# ── Core compliance logic ─────────────────────────────────────────────────────

def _prepare_trades(trades_df):
    """Parse dates and sort by close time. Returns prepared DataFrame."""
    import pandas as pd
    df = trades_df.copy()
    df["_close_dt"] = pd.to_datetime(df["Close Date"], dayfirst=True, errors="coerce")
    df["_close_date"] = df["_close_dt"].dt.date
    df = df.sort_values("_close_dt").reset_index(drop=True)
    return df


def _check_phase(df, phase_config: dict, account_size: float, start_idx: int) -> tuple:
    """
    Simulate one challenge phase against trades starting at start_idx.
    Returns (PhaseResult, next_trade_idx).
    """
    profit_target_pct     = phase_config.get("profit_target_pct") or 0.0
    max_daily_dd_pct      = phase_config.get("max_daily_drawdown_pct")   # may be None
    max_total_dd_pct      = phase_config.get("max_total_drawdown_pct") or 999.0
    drawdown_type         = phase_config.get("drawdown_type", "static")
    min_trading_days      = phase_config.get("min_trading_days") or 0
    consistency_rule_pct  = phase_config.get("consistency_rule_pct")
    consistency_rule_type = phase_config.get("consistency_rule_type")
    phase_name            = phase_config.get("phase_name", "Phase")

    balance   = float(account_size)
    hwm       = float(account_size)   # high water mark
    profit_target_abs = account_size * profit_target_pct / 100.0

    daily_breakdown   = []
    max_daily_dd_hit  = 0.0
    max_total_dd_hit  = 0.0
    passed            = False
    failure_reason    = None
    next_idx          = start_idx
    trading_days      = 0
    day_profits       = []   # for consistency check

    if start_idx >= len(df):
        # No trades left — fail: didn't hit target
        failure_reason = f"No trades available for {phase_name}"
        return _build_phase_result(
            phase_name, False, failure_reason,
            profit_target_pct, 0.0,
            max_daily_dd_pct, max_daily_dd_hit,
            max_total_dd_pct, max_total_dd_hit,
            0, min_trading_days, None, []
        ), start_idx

    # Group remaining trades by close date
    sub_df = df.iloc[start_idx:].copy()
    dates  = sorted(sub_df["_close_date"].dropna().unique())

    last_processed_idx = start_idx

    for trade_date in dates:
        day_trades = sub_df[sub_df["_close_date"] == trade_date]
        day_pnl    = float(day_trades["Profit"].sum())
        day_count  = len(day_trades)

        day_start_balance = balance
        balance  += day_pnl
        trading_days += 1
        day_profits.append(day_pnl)

        # Update HWM
        if drawdown_type in ("trailing", "trailing_eod"):
            hwm = max(hwm, balance)
        # For static, hwm stays at account_size (not used in calculation but track anyway)

        # Daily DD: how much was lost today vs starting balance
        daily_dd_pct = 0.0
        if day_pnl < 0:
            daily_dd_pct = abs(day_pnl) / account_size * 100.0

        # Total DD based on type
        if drawdown_type == "static":
            total_dd_pct = max(0.0, (account_size - balance) / account_size * 100.0)
        else:  # trailing or trailing_eod
            total_dd_pct = max(0.0, (hwm - balance) / account_size * 100.0)

        max_daily_dd_hit = max(max_daily_dd_hit, daily_dd_pct)
        max_total_dd_hit = max(max_total_dd_hit, total_dd_pct)

        daily_dd_limit_ok = True
        total_dd_limit_ok = True

        # Check daily DD limit
        if max_daily_dd_pct is not None and daily_dd_pct >= max_daily_dd_pct:
            daily_dd_limit_ok = False
            failure_reason = (
                f"Daily drawdown breached on {trade_date}: "
                f"{daily_dd_pct:.2f}% >= limit {max_daily_dd_pct}%"
            )

        # Check total DD limit
        if total_dd_pct >= max_total_dd_pct:
            total_dd_limit_ok = False
            if not failure_reason:
                failure_reason = (
                    f"Total drawdown breached on {trade_date}: "
                    f"{total_dd_pct:.2f}% >= limit {max_total_dd_pct}%"
                )

        # Record last index of day's trades
        last_processed_idx = start_idx + day_trades.index[-1] - sub_df.index[0] + 1

        daily_breakdown.append({
            "date":               str(trade_date),
            "trades_count":       day_count,
            "day_pnl":            round(day_pnl, 2),
            "cumulative_pnl":     round(balance - account_size, 2),
            "balance":            round(balance, 2),
            "high_water_mark":    round(hwm, 2),
            "daily_dd_pct":       round(daily_dd_pct, 4),
            "total_dd_pct":       round(total_dd_pct, 4),
            "daily_dd_limit_ok":  daily_dd_limit_ok,
            "total_dd_limit_ok":  total_dd_limit_ok,
        })

        if not daily_dd_limit_ok or not total_dd_limit_ok:
            next_idx = last_processed_idx
            break

        # Check if profit target reached
        profit_achieved = balance - account_size
        if profit_achieved >= profit_target_abs:
            next_idx = last_processed_idx
            passed = True
            break

        next_idx = last_processed_idx

    profit_achieved_pct = (balance - account_size) / account_size * 100.0

    # Post-loop checks (only if not already failed)
    if not failure_reason:
        if not passed:
            failure_reason = (
                f"Profit target not reached: "
                f"{profit_achieved_pct:.2f}% < {profit_target_pct}%"
            )

        # Min trading days
        if passed and trading_days < min_trading_days:
            passed = False
            failure_reason = (
                f"Insufficient trading days: {trading_days} < {min_trading_days} required"
            )

    # Consistency rule
    consistency_passed = None
    if passed and consistency_rule_pct is not None and consistency_rule_type:
        positive_profits = [p for p in day_profits if p > 0]
        if positive_profits:
            best_day = max(positive_profits)
            if consistency_rule_type == "best_day_vs_total":
                total_positive = sum(positive_profits)
                ratio = best_day / total_positive * 100.0 if total_positive > 0 else 0.0
            elif consistency_rule_type == "best_day_vs_target":
                target_abs = account_size * profit_target_pct / 100.0
                ratio = best_day / target_abs * 100.0 if target_abs > 0 else 0.0
            else:
                ratio = 0.0
            consistency_passed = ratio < consistency_rule_pct
            if not consistency_passed:
                passed = False
                failure_reason = (
                    f"Consistency rule failed: best day {ratio:.1f}% "
                    f">= {consistency_rule_pct}% limit ({consistency_rule_type})"
                )
        else:
            consistency_passed = True  # no positive days — rule trivially passes

    return _build_phase_result(
        phase_name, passed, failure_reason,
        profit_target_pct, profit_achieved_pct,
        max_daily_dd_pct, max_daily_dd_hit,
        max_total_dd_pct, max_total_dd_hit,
        trading_days, min_trading_days,
        consistency_passed, daily_breakdown
    ), next_idx


def _build_phase_result(
    phase_name, passed, failure_reason,
    profit_target_pct, profit_achieved_pct,
    max_daily_dd_pct, max_daily_dd_hit,
    max_total_dd_pct, max_total_dd_hit,
    trading_days, min_trading_days,
    consistency_passed, daily_breakdown
) -> PhaseResult:
    return PhaseResult(
        phase_name=phase_name,
        passed=passed,
        failure_reason=failure_reason,
        profit_target_pct=profit_target_pct,
        profit_achieved_pct=round(profit_achieved_pct, 4),
        max_daily_dd_allowed_pct=max_daily_dd_pct,
        max_daily_dd_hit_pct=round(max_daily_dd_hit, 4),
        max_total_dd_allowed_pct=max_total_dd_pct,
        max_total_dd_hit_pct=round(max_total_dd_hit, 4),
        trading_days=trading_days,
        min_trading_days_required=min_trading_days,
        consistency_check_passed=consistency_passed,
        daily_breakdown=daily_breakdown,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def check_compliance(
    trades_df,
    firm_id: str,
    challenge_id: str,
    account_size: int,
    start_date=None,
) -> Optional[ComplianceResult]:
    """
    Check whether trades pass a specific prop firm challenge.
    Returns ComplianceResult, or None if firm/challenge/account_size not found.
    """
    import pandas as pd

    firms = load_all_firms()
    if firm_id not in firms:
        return None
    firm = firms[firm_id]
    challenge = firm.get_challenge(challenge_id)
    if not challenge:
        return None
    if account_size not in challenge.get("account_sizes", []):
        return None

    df = _prepare_trades(trades_df)

    if start_date is not None:
        cutoff = pd.to_datetime(start_date).date()
        df = df[df["_close_date"] >= cutoff].reset_index(drop=True)

    phases_config = challenge.get("phases", [])
    phase_results = []
    trade_idx = 0
    overall_failed = False

    for phase_config in phases_config:
        phase_result, trade_idx = _check_phase(df, phase_config, account_size, trade_idx)
        phase_results.append(phase_result)
        if not phase_result.passed:
            overall_failed = True
            break
        # Most firms reset balance to account_size for the next phase

    # If no phases (instant funding), it's automatically "passed evaluation"
    if not phases_config:
        overall_passed = True
    else:
        overall_passed = not overall_failed and len(phase_results) == len(phases_config)

    failure_reason = None
    for p in phase_results:
        if not p.passed:
            failure_reason = p.failure_reason
            break

    # Summary stats
    total_trades = len(df)
    winning = int((df["Profit"] > 0).sum()) if "Profit" in df.columns else 0
    win_rate = round(winning / total_trades * 100, 2) if total_trades > 0 else 0.0
    total_pnl = round(float(df["Profit"].sum()), 2) if "Profit" in df.columns else 0.0

    return ComplianceResult(
        firm_name=firm.firm_name,
        challenge_name=challenge["challenge_name"],
        account_size=account_size,
        phases=phase_results,
        overall_passed=overall_passed,
        failure_reason=failure_reason,
        summary={
            "total_trades":   total_trades,
            "win_rate":       win_rate,
            "total_pnl":      total_pnl,
            "total_pnl_pct":  round(total_pnl / account_size * 100, 4) if account_size else 0,
            "max_dd_hit_pct": round(
                max((p.max_total_dd_hit_pct for p in phase_results), default=0.0), 4
            ),
            "days_traded": max((p.trading_days for p in phase_results), default=0),
        },
    )


def check_compliance_all_firms(trades_df, account_size: Optional[int] = None) -> list:
    """
    Run compliance against all firms and all their challenges.
    Returns list of ComplianceResults sorted: passes first.
    """
    firms = load_all_firms()
    results = []
    for firm_id, firm in firms.items():
        for ch in firm.list_challenges():
            challenge_id = ch["challenge_id"]
            sizes = firm.list_account_sizes(challenge_id)
            if not sizes:
                continue
            size = account_size if (account_size and account_size in sizes) else sizes[len(sizes) // 2]
            result = check_compliance(trades_df, firm_id, challenge_id, size)
            if result is not None:
                results.append(result)
    results.sort(key=lambda r: (0 if r.overall_passed else 1, r.firm_name))
    return results


def get_compliance_matrix(trades_df, firm_ids=None, account_size: Optional[int] = None):
    """
    Generate a comparison DataFrame across firms/challenges.
    Rows = challenges, columns = key metrics.
    """
    import pandas as pd

    firms = load_all_firms()
    if firm_ids:
        firms = {fid: f for fid, f in firms.items() if fid in firm_ids}

    rows = []
    for firm_id, firm in sorted(firms.items(), key=lambda x: x[1].firm_name):
        for ch in firm.list_challenges():
            challenge_id = ch["challenge_id"]
            sizes = firm.list_account_sizes(challenge_id)
            if not sizes:
                continue
            size = account_size if (account_size and account_size in sizes) else sizes[len(sizes) // 2]
            result = check_compliance(trades_df, firm_id, challenge_id, size)
            if result is None:
                continue
            rows.append({
                "firm_name":          result.firm_name,
                "challenge_name":     result.challenge_name,
                "account_size":       result.account_size,
                "passed":             result.overall_passed,
                "profit_achieved_pct": round(
                    max((p.profit_achieved_pct for p in result.phases), default=0.0), 2
                ),
                "max_dd_hit_pct":     result.summary.get("max_dd_hit_pct", 0.0),
                "days_traded":        result.summary.get("days_traded", 0),
                "failure_reason":     result.failure_reason or "",
            })

    if not rows:
        return None
    df = pd.DataFrame(rows)
    df.sort_values(["passed", "firm_name"], ascending=[False, True], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df
