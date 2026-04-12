"""
Prop Firm Rules Engine — checks whether trades pass a prop firm's challenge.

Core function: check_compliance(trades_df, firm_id, challenge_id, account_size)
Returns a detailed result with PASS/FAIL per phase, daily breakdown, and failure reasons.
"""

import os
import json
from dataclasses import dataclass, field
from typing import Optional

# CHANGED: April 2026 — UI-safe logging (Phase 19d)
from shared.logging_setup import get_logger
log = get_logger(__name__)

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
        # WHY: The simulator accesses firm.config.get('trading_rules', []) but
        #      data was stored in self._data. This property fixes the AttributeError.
        # CHANGED: April 2026 — expose raw config as property
        self.config = self._data

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
    """Scan prop_firms/ and load all JSON profiles. Returns dict keyed by firm_id.

    WHY: Old code silently swallowed all exceptions, making typos in firm
         JSON files invisible. Now we print a warning so the user notices.
    CHANGED: April 2026 — visible firm load errors
    """
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
        except (KeyError, json.JSONDecodeError, OSError) as _e:
            # Surface the error so the user knows the file is broken
            log.warning(f"[PROP_FIRMS] failed to load {fname}: {type(_e).__name__}: {_e}")
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

def _prepare_trades(trades_df, daily_reset_tz=None):
    """Parse dates and sort by close time. Returns prepared DataFrame.

    Args:
        trades_df: Input DataFrame with 'Close Date' column
        daily_reset_tz: Optional timezone for daily reset boundary. When
            provided, trade timestamps are shifted into this timezone
            before extracting the date. This matches firms that reset
            the daily DD at a fixed local time (e.g., Leveraged uses
            23:00 GMT+3). Accepts 'UTC', 'Etc/GMT-3', 'Europe/Athens',
            or any IANA timezone name. Default None = UTC (old behavior).

    WHY: Old code used df["_close_dt"].dt.date which extracts the date
         in whatever timezone the Close Date column is in. For the
         user's Leveraged firm (23:00 GMT+3 daily reset), a trade at
         21:00 UTC on March 14 is actually 00:00 March 15 in the GMT+3
         reset frame and should count toward March 15's daily DD. Old
         code grouped it as March 14 — wrong day bucket, wrong daily
         DD attribution, pass/fail rates biased.
    CHANGED: April 2026 — daily reset timezone parameter (audit HIGH #59)
    """
    import pandas as pd
    df = trades_df.copy()
    # WHY (Phase 70 Fix 5): dayfirst=True only parses DD/MM/YYYY correctly.
    #      US-format CSVs (MM/DD/YYYY) get silently misparsed — 01/02/2024
    #      becomes Feb 1 instead of Jan 2. Use format='mixed' (pandas 2.0+)
    #      which auto-detects per-value; fall back to dayfirst=True parsing.
    # CHANGED: April 2026 — Phase 70 Fix 5 — format='mixed' for robustness
    #          (audit Part F HIGH #5)
    try:
        df["_close_dt"] = pd.to_datetime(df["Close Date"], format='mixed',
                                         dayfirst=True, errors="coerce")
    except TypeError:
        # pandas < 2.0 doesn't support format='mixed'
        df["_close_dt"] = pd.to_datetime(df["Close Date"], dayfirst=True, errors="coerce")

    if daily_reset_tz and daily_reset_tz.upper() != 'UTC':
        # WHY: Shift close times into the firm's reset timezone, then
        #      extract the local date. Preserves ordering (sort still
        #      works correctly on the original UTC timestamps).
        try:
            # Treat raw timestamps as UTC (most common) and convert to reset TZ
            close_utc = df["_close_dt"].dt.tz_localize('UTC', ambiguous='NaT', nonexistent='NaT')
            close_local = close_utc.dt.tz_convert(daily_reset_tz)
            df["_close_date"] = close_local.dt.date
        except Exception as e:
            # Fallback to naive UTC date extraction on error
            log.warning(f"[prop_firm_engine] daily_reset_tz='{daily_reset_tz}' failed: {e}")
            log.warning(f"[prop_firm_engine] falling back to naive UTC date extraction")
            df["_close_date"] = df["_close_dt"].dt.date
    else:
        df["_close_date"] = df["_close_dt"].dt.date

    df = df.sort_values("_close_dt").reset_index(drop=True)
    return df


def _check_phase(df, phase_config: dict, account_size: float, start_idx: int,
                 dd_mechanics: dict = None) -> tuple:
    """
    Simulate one challenge phase against trades starting at start_idx.
    Returns (PhaseResult, next_trade_idx).

    ⚠ LIMITATION — CLOSED BALANCE ONLY:
    ==========================================================
    This engine tracks CLOSED trade balance only, not intraday
    equity. The user's firm rule (Leveraged) uses:

        daily_dd_reference = MAX(balance, equity) at 23:00 GMT+3

    where `equity` includes the floating P&L of open positions at
    the snapshot moment. This engine does not have per-candle
    position state tracking and cannot model open-position
    floating P&L.

    As a result, for days with either:
      - Open positions at the daily snapshot moment, or
      - Intraday equity swings that close profitable

    the engine's daily DD attribution will be LOWER than what the
    real firm would see. Pass rates reported by this engine should
    be treated as an OPTIMISTIC upper bound — the real firm may
    fail some days that this engine passes.

    For a strategy that holds positions overnight or has significant
    intraday equity volatility, use a live simulation with equity
    snapshots (not yet implemented) or add a safety margin to the
    pass-rate interpretation.

    For a strategy that closes all positions before the daily reset
    (flat at 23:00 GMT+3), this engine's daily DD matches the real
    firm's daily DD exactly.
    ==========================================================

    WHY: Fix is a major refactor requiring per-candle equity state.
         Phase 18 documents the limitation loudly so users don't
         misinterpret the numbers. Full fix deferred.
    CHANGED: April 2026 — document closed-balance limitation (audit HIGH #60)
    """
    # WHY: One-time warning so users see the limitation in the console.
    #      The _phase_limitation_warned flag lives on the function itself
    #      so it fires once per process lifetime.
    # CHANGED: April 2026 — runtime limitation notice (audit HIGH #60)
    if not getattr(_check_phase, '_limitation_warned', False):
        log.warning("=" * 70)
        log.warning("⚠  PROP FIRM ENGINE — CLOSED BALANCE LIMITATION")
        log.warning("=" * 70)
        log.warning("  This engine tracks closed trade balance only. Daily DD")
        log.warning("  numbers are a LOWER bound on real firm behavior for")
        log.warning("  strategies with open positions at 23:00 GMT+3 or with")
        log.warning("  significant intraday equity volatility.")
        log.warning("  See _check_phase docstring for details.")
        log.warning("=" * 70)
        _check_phase._limitation_warned = True

    profit_target_pct     = phase_config.get("profit_target_pct") or 0.0
    max_daily_dd_pct      = phase_config.get("max_daily_drawdown_pct")   # may be None
    # WHY (Phase 70 Fix 4): Old code used 999.0 as a sentinel for "no DD limit".
    #      Any firm with a literal limit ≥ 999% (or 998.5%) would be silently
    #      treated as no-limit. Use None as the canonical "no limit" sentinel.
    # CHANGED: April 2026 — Phase 70 Fix 4 — None sentinel for no-limit DD
    #          (audit Part F HIGH #4)
    max_total_dd_pct      = phase_config.get("max_total_drawdown_pct") or None
    drawdown_type         = phase_config.get("drawdown_type", "static")
    min_trading_days      = phase_config.get("min_trading_days") or 0
    consistency_rule_pct  = phase_config.get("consistency_rule_pct")
    consistency_rule_type = phase_config.get("consistency_rule_type")
    phase_name            = phase_config.get("phase_name", "Phase")

    # ── Parse drawdown_mechanics from firm JSON ───────────────────────────
    # WHY: The generic "trailing" type doesn't capture firm-specific behavior.
    #      Leveraged uses trailing on CLOSED BALANCE with HWM lock after +6%.
    #      Without this, pass/fail rates are wrong.
    # CHANGED: April 2026 — firm-specific DD mechanics
    if dd_mechanics is None:
        dd_mechanics = {}
    trailing_dd       = dd_mechanics.get('trailing_dd', {})
    hwm_lock_gain_pct = trailing_dd.get('lock_after_gain_pct')
    hwm_locked        = False
    daily_dd_config   = dd_mechanics.get('daily_dd', {})
    daily_dd_ref_type = daily_dd_config.get('reference', '')

    balance   = float(account_size)
    hwm       = float(account_size)   # high water mark
    profit_target_abs = account_size * profit_target_pct / 100.0
    # WHY: Track DD floor explicitly so lock-after-gain breach math is correct.
    # CHANGED: April 2026 — explicit floor variable
    # Phase 70 Fix 4: None means no total DD limit → floor = 0 (unreachable)
    dd_floor  = account_size * (1.0 - max_total_dd_pct / 100.0) if max_total_dd_pct is not None else 0.0

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

        # ── Update HWM — respect firm-specific trailing mechanics ─────────
        # WHY: Generic trailing updates HWM on every balance increase.
        #      Leveraged locks HWM once gain reaches +6% of starting balance.
        #      After lock, HWM stays at starting balance permanently.
        # CHANGED: April 2026 — HWM lock support
        # ── HWM and DD floor update ───────────────────────────────────────
        # WHY: After lock-after-gain fires, the rule says the DD FLOOR locks
        #      at account_size. Old code set hwm=account_size then checked
        #      (hwm-balance)/account_size, which required balance to drop
        #      BELOW account_size by max_total_dd_pct before breaching. Wrong.
        #      Now dd_floor is tracked explicitly: breach fires when balance
        #      hits or drops below dd_floor (not some percentage below it).
        # CHANGED: April 2026 — explicit dd_floor; remove account_size floor
        #          from daily DD reference
        if drawdown_type in ("trailing", "trailing_eod"):
            if hwm_lock_gain_pct and not hwm_locked:
                gain_pct = (balance - account_size) / account_size * 100.0
                if gain_pct >= hwm_lock_gain_pct:
                    hwm_locked = True
                    dd_floor   = account_size   # floor locks at starting balance
                else:
                    hwm      = max(hwm, balance)
                    dd_floor = hwm * (1.0 - max_total_dd_pct / 100.0)
            elif hwm_locked:
                dd_floor = account_size   # stays locked
            else:
                hwm      = max(hwm, balance)
                dd_floor = hwm * (1.0 - max_total_dd_pct / 100.0)
        else:
            # Static: floor is fixed below account_size
            dd_floor = account_size * (1.0 - max_total_dd_pct / 100.0)

        # ── Daily DD — respect firm-specific reference ────────────────────
        # WHY: Leveraged rule uses yesterday's closing balance as reference —
        #      no artificial floor at account_size.
        # CHANGED: April 2026 — remove artificial account_size floor
        daily_dd_pct = 0.0
        if day_pnl < 0:
            if daily_dd_ref_type == 'max_balance_equity':
                # Reference is yesterday's closing balance (no floor)
                dd_ref = day_start_balance if day_start_balance > 0 else account_size
                daily_dd_pct = abs(day_pnl) / dd_ref * 100.0
            else:
                daily_dd_pct = abs(day_pnl) / account_size * 100.0

        # ── Total DD — check balance against explicit floor ───────────────
        # WHY: Checking `balance <= dd_floor` is exact. When floor = account_size
        #      (post-lock), the moment balance ≤ account_size → breach.
        # CHANGED: April 2026 — floor-based breach check
        if balance <= dd_floor:
            total_dd_pct = max_total_dd_pct + 0.01   # exceeds limit → triggers breach
        else:
            if drawdown_type != "static":
                consumed = max(0.0, hwm - balance)
            else:
                consumed = max(0.0, account_size - balance)
            total_dd_pct = (consumed / max(1.0, account_size)) * 100.0

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
            elif consistency_rule_type == "best_day_vs_net_total":
                # WHY: Some prop firms auto-reject if your best winning day is an
                #      "unusually high" percentage of your total profit. E.g. if
                #      best_day = +$5k and final balance = $106k (net = +$6k),
                #      that's 83% of your entire edge from one lucky day.
                # CHANGED: April 2026 — third consistency rule type (Phase 21)
                net = balance - account_size
                ratio = best_day / net * 100.0 if net > 0 else 0.0
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
            # WHY (Phase 76 Fix 8): A week with only losses trivially passed
            #      the consistency rule because there were no positive days to
            #      check. Semantically: you can't have a "consistent" payout
            #      week if you never made money. Set to N/A (None) so callers
            #      can distinguish "no positive days" from "rule passed".
            # CHANGED: April 2026 — Phase 76 Fix 8 — N/A for losing-only weeks
            #          (audit Part F MEDIUM #8)
            consistency_passed = None  # no positive profit to check consistency against

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

    # WHY: Extract the firm's daily reset timezone so day boundaries
    #      match the firm's real rule (e.g. Leveraged 23:00 GMT+3).
    #      Falls back to UTC if not specified in the firm config.
    # CHANGED: April 2026 — pass daily_reset_tz (audit HIGH #59)
    dd_mechanics = firm._data.get('drawdown_mechanics', {})
    _dd_config = dd_mechanics.get('daily_dd', {})
    _reset_tz  = _dd_config.get('reset_timezone', 'UTC')

    df = _prepare_trades(trades_df, daily_reset_tz=_reset_tz)

    if start_date is not None:
        cutoff = pd.to_datetime(start_date).date()
        df = df[df["_close_date"] >= cutoff].reset_index(drop=True)

    phases_config = challenge.get("phases", [])
    phase_results = []
    trade_idx = 0
    overall_failed = False

    for phase_config in phases_config:
        phase_result, trade_idx = _check_phase(
            df, phase_config, account_size, trade_idx,
            dd_mechanics=dd_mechanics,
        )
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
            # WHY (Phase 70 Fix 7): Old code picked the middle account size
            #      (sizes[len//2]) when the requested size wasn't in the list.
            #      For [10k,25k,50k,100k,200k] that's $50k — a user expecting
            #      $100k results gets $50k silently. Use the LARGEST available
            #      size as the default (most common real-world scenario: max
            #      funded account). User can always pass account_size explicitly.
            # CHANGED: April 2026 — Phase 70 Fix 7 — largest size as default
            #          (audit Part F HIGH #7)
            size = account_size if (account_size and account_size in sizes) else sizes[-1]
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
            # Phase 70 Fix 7: largest size as default (see earlier occurrence for full WHY)
            size = account_size if (account_size and account_size in sizes) else sizes[-1]
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
