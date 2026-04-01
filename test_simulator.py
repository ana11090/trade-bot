"""
Quick test for the prop firm lifecycle simulator.
Runs two modes and prints summary stats.

Usage:
    python test_simulator.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from shared.prop_firm_simulator import simulate_challenge

TRADES_PATH = os.path.join(os.path.dirname(__file__),
                           "trade_histories", "original_bot", "trades_clean.csv")

def main():
    # Fallback: try trades_original.csv if clean not found
    global TRADES_PATH
    if not os.path.exists(TRADES_PATH):
        alt = TRADES_PATH.replace("trades_clean.csv", "trades_original.csv")
        if os.path.exists(alt):
            TRADES_PATH = alt
        else:
            print(f"ERROR: trades file not found at {TRADES_PATH}")
            print("Load a trade history first via the app ('+ Load trades').")
            sys.exit(1)

    trades_df = pd.read_csv(TRADES_PATH)
    print(f"Loaded {len(trades_df)} trades from {TRADES_PATH}\n")

    # ── Test 1: FTMO 2-Step Standard — sliding window ───────────────────────
    print("=" * 60)
    print("TEST 1 — FTMO 2-Step Standard (sliding window, $100k account)")
    print("=" * 60)
    s = simulate_challenge(
        trades_df, "ftmo", "ftmo_2step_standard", account_size=100000,
        mode="sliding_window", simulate_funded=True
    )
    if s:
        _print_summary(s)
    else:
        print("  No result returned (firm/challenge not found or size mismatch).")

    # ── Test 2: FundedNext Stellar 2-Step — Monte Carlo ──────────────────────
    print()
    print("=" * 60)
    print("TEST 2 — FundedNext Stellar 2-Step (monte_carlo 200 samples, $100k)")
    print("=" * 60)
    s2 = simulate_challenge(
        trades_df, "fundednext", "fundednext_stellar_2step", account_size=100000,
        mode="monte_carlo", num_samples=200, simulate_funded=True
    )
    if s2:
        _print_summary(s2)
    else:
        print("  No result returned (firm/challenge not found or size mismatch).")

    # ── Test 3: Topstep $100K — sliding window (dd_reset_on_payout=True) ─────
    print()
    print("=" * 60)
    print("TEST 3 — Topstep $100K (sliding window, $100k, dd_reset_on_payout=True)")
    print("=" * 60)
    s3 = simulate_challenge(
        trades_df, "topstep", "topstep_100k", account_size=100000,
        mode="sliding_window", simulate_funded=True
    )
    if s3:
        _print_summary(s3)
    else:
        print("  No result returned (firm/challenge not found or size mismatch).")


def _print_summary(s):
    print(f"  Windows simulated  : {s.num_simulations}")
    print(f"  Eval pass rate     : {s.eval_pass_rate * 100:.1f}%")
    if s.eval_avg_days_to_pass:
        print(f"  Avg days to pass   : {s.eval_avg_days_to_pass:.1f}")
    fail = s.eval_fail_reasons
    if fail:
        print(f"  Fail reasons       : {dict(fail)}")
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


if __name__ == "__main__":
    main()
