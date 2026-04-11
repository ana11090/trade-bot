"""
Quick test for the prop firm lifecycle simulator.
Runs multiple modes and prints summary stats, including sanity checks.

Usage:
    python test_simulator.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from shared.prop_firm_simulator import simulate_challenge

# WHY: Phase 27 Fix 1 — Old code hardcoded the path to
#      trade_histories/original_bot/trades_clean.csv. Users with
#      different active workspaces got the wrong trades or a "file
#      not found" error. Now resolves the path from the active
#      workspace via trade_history_manager. Falls back to the legacy
#      hardcoded path for backward compatibility.
# CHANGED: April 2026 — Phase 27 Fix 1 (audit Part B #28 path-only)
def _resolve_trades_path():
    """Resolve the trades CSV path from the active workspace.

    Tries the active workspace first, then falls back to the legacy
    hardcoded original_bot path.
    """
    try:
        from shared.trade_history_manager import (
            get_active_history, get_history_trades_path
        )
        active = get_active_history()
        if active and active.get('history_id'):
            workspace_path = get_history_trades_path(active['history_id'])
            if os.path.exists(workspace_path):
                return workspace_path
    except (ImportError, AttributeError, KeyError) as _e:
        print(f"  (workspace lookup failed: {_e}, trying legacy path)")

    # Legacy fallback
    legacy = os.path.join(
        os.path.dirname(__file__),
        "trade_histories", "original_bot", "trades_clean.csv"
    )
    if os.path.exists(legacy):
        return legacy
    # Try the alternate filename
    legacy_alt = legacy.replace("trades_clean.csv", "trades_original.csv")
    if os.path.exists(legacy_alt):
        return legacy_alt
    return None


TRADES_PATH = _resolve_trades_path()


def main():
    global TRADES_PATH

    if TRADES_PATH is None or not os.path.exists(TRADES_PATH):
        print("ERROR: No trades file found.")
        print("  - Either load a trade history via the app ('+ Load trades')")
        print("  - Or place a trades CSV at trade_histories/<history_id>/trades_clean.csv")
        sys.exit(1)

    trades_df = pd.read_csv(TRADES_PATH)
    print(f"Loaded {len(trades_df)} trades from {TRADES_PATH}\n")

    account_size = 100000

    # ── Test 1: FTMO 2-Step Standard — Monte Carlo with rescaling ────────────
    print("=" * 70)
    print("TEST 1 — FTMO 2-Step Standard (monte_carlo 100 samples, $100k, 1% risk)")
    print("=" * 70)
    s = simulate_challenge(
        trades_df, "ftmo", "ftmo_2step_standard", account_size=account_size,
        mode="monte_carlo", num_samples=100, simulate_funded=True,
        risk_per_trade_pct=1.0, default_sl_pips=150.0, pip_value_per_lot=1.0,
    )
    if s:
        _print_summary(s)
        _sanity_checks(s, account_size)
    else:
        print("  No result (firm/challenge not found or size mismatch).")

    # ── Test 2: FundedNext — sliding window with rescaling ───────────────────
    print()
    print("=" * 70)
    print("TEST 2 — FundedNext Stellar 2-Step (sliding_window, $100k, 1% risk)")
    print("=" * 70)
    s2 = simulate_challenge(
        trades_df, "fundednext", "fundednext_stellar_2step", account_size=account_size,
        mode="sliding_window", simulate_funded=True,
        risk_per_trade_pct=1.0, default_sl_pips=150.0, pip_value_per_lot=1.0,
    )
    if s2:
        _print_summary(s2)
        _sanity_checks(s2, account_size)
    else:
        print("  No result (firm/challenge not found or size mismatch).")

    # ── Test 3: Topstep $100K — dd_reset_on_payout=True ─────────────────────
    print()
    print("=" * 70)
    print("TEST 3 — Topstep $100K (monte_carlo 50 samples, dd_reset_on_payout=True)")
    print("=" * 70)
    s3 = simulate_challenge(
        trades_df, "topstep", "topstep_100k", account_size=account_size,
        mode="monte_carlo", num_samples=50, simulate_funded=True,
        risk_per_trade_pct=1.0, default_sl_pips=150.0, pip_value_per_lot=1.0,
    )
    if s3:
        _print_summary(s3)
        _sanity_checks(s3, account_size)
    else:
        print("  No result (firm/challenge not found or size mismatch).")

    # ── Test 4: Risk level comparison ────────────────────────────────────────
    print()
    print("=" * 70)
    print("TEST 4 — Risk level comparison (FTMO 2-Step, $100k, 50 MC samples)")
    print("Expected: higher risk = faster passes but more failures, different lot sizes")
    print("=" * 70)
    for risk in [0.5, 1.0, 2.0]:
        r = simulate_challenge(
            trades_df, "ftmo", "ftmo_2step_standard", account_size,
            mode="monte_carlo", num_samples=50,
            risk_per_trade_pct=risk, default_sl_pips=150.0, pip_value_per_lot=1.0,
            daily_dd_safety_pct=80.0,
        )
        if r:
            monthly = f"${r.funded_avg_monthly_payout:,.0f}" if r.funded_avg_monthly_payout else "—"
            print(f"  Risk {risk:.1f}%: pass rate {r.eval_pass_rate*100:.0f}%, "
                  f"avg eval days {r.eval_avg_days_to_pass:.0f}, "
                  f"lot size {r.calculated_lot_size:.2f}, "
                  f"monthly payout {monthly}")
        else:
            print(f"  Risk {risk:.1f}%: no result")

    print()
    print("If all three risk levels show the same pass rate, rescaling is broken.")

    # ── Test 5: Safety margin effect ─────────────────────────────────────────
    print()
    print("=" * 70)
    print("TEST 5 — Safety margin effect (FTMO 2-Step, $100k, 50 MC samples)")
    print("Expected: lower safety = more conservative (stops earlier on bad days)")
    print("=" * 70)
    for safety in [60, 80, 100]:
        r = simulate_challenge(
            trades_df, "ftmo", "ftmo_2step_standard", account_size,
            mode="monte_carlo", num_samples=50,
            risk_per_trade_pct=1.0, default_sl_pips=150.0,
            daily_dd_safety_pct=float(safety),
        )
        if r:
            monthly = f"${r.funded_avg_monthly_payout:,.0f}" if r.funded_avg_monthly_payout else "—"
            print(f"  Safety {safety}%: pass rate {r.eval_pass_rate*100:.0f}%, "
                  f"avg days {r.eval_avg_days_to_pass:.0f}, "
                  f"monthly {monthly}")
        else:
            print(f"  Safety {safety}%: no result")


def _print_summary(s):
    print(f"  Lot size           : {s.calculated_lot_size:.4f} lots")
    print(f"  Risk / SL          : {s.risk_per_trade_pct}% / {s.default_sl_pips:.0f} pips")
    print(f"  Windows simulated  : {s.num_simulations}")
    print(f"  Eval pass rate     : {s.eval_pass_rate * 100:.1f}%")
    if s.eval_avg_days_to_pass:
        print(f"  Avg days to pass   : {s.eval_avg_days_to_pass:.1f}")
    if s.eval_fail_reasons:
        print(f"  Fail reasons       : {dict(s.eval_fail_reasons)}")
    if s.funded_avg_survival_days is not None:
        print(f"  Avg funded days    : {s.funded_avg_survival_days:.1f}")
    if s.funded_avg_monthly_payout is not None:
        print(f"  Avg monthly payout : ${s.funded_avg_monthly_payout:,.2f}")
    if s.funded_survival_rate_3mo is not None:
        print(f"  3-month survival   : {s.funded_survival_rate_3mo * 100:.1f}%")
    if s.funded_survival_rate_6mo is not None:
        print(f"  6-month survival   : {s.funded_survival_rate_6mo * 100:.1f}%")
    if s.challenge_fee is not None:
        print(f"  Challenge fee      : ${s.challenge_fee:,.0f}")
    if s.avg_attempts_to_pass is not None:
        print(f"  Avg attempts       : {s.avg_attempts_to_pass:.2f}")
    if s.expected_cost is not None:
        print(f"  Expected total cost: ${s.expected_cost:,.2f}")
    if s.expected_net_profit is not None:
        print(f"  Expected net profit: ${s.expected_net_profit:,.2f}")
    if s.expected_roi_pct is not None:
        print(f"  Expected ROI       : {s.expected_roi_pct:.1f}%")


def _sanity_checks(s, account_size):
    failures = []

    if s.eval_pass_rate >= 0.99:
        failures.append(
            f"Pass rate {s.eval_pass_rate*100:.1f}% is suspiciously high "
            f"— rescaling may not be working")

    if s.funded_avg_monthly_payout and s.funded_avg_monthly_payout >= account_size:
        failures.append(
            f"Monthly payout ${s.funded_avg_monthly_payout:,.0f} exceeds account size "
            f"— rescaling broken")

    if failures:
        for msg in failures:
            print(f"  [SANITY FAIL] {msg}")
    else:
        print("  Sanity checks: PASS")


if __name__ == "__main__":
    main()
