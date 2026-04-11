"""
Test script: load the Original Bot trades and check compliance against all prop firms.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.prop_firm_engine import load_all_firms, check_compliance, get_compliance_matrix
from shared.trade_history_manager import get_active_history, get_history_trades_path
import pandas as pd

# Load trades
active = get_active_history()
if not active:
    print("No active trade history. Run migrate_to_workspaces.py first.")
    sys.exit(1)

trades_path = get_history_trades_path(active['history_id'])
trades_df = pd.read_csv(trades_path)
print(f"Loaded {len(trades_df)} trades from '{active['robot_name']}'")

# WHY: Phase 27 Fix 2 — Old code printed iloc[-1] and iloc[0] as
#      "first/last" but the dataframe isn't sorted by date — those
#      are ROW order, not chronological. For CSVs sorted oldest-first,
#      the values were even backwards. Now parses dates and uses
#      min()/max() for true chronological range.
# CHANGED: April 2026 — Phase 27 Fix 2 (audit Part B #29)
try:
    _dates = pd.to_datetime(trades_df['Open Date'], errors='coerce', dayfirst=True).dropna()
    if len(_dates) > 0:
        print(f"Date range: {_dates.min()} to {_dates.max()}")
    else:
        print("Date range: (could not parse 'Open Date' column)")
except (KeyError, ValueError) as _e:
    print(f"Date range: (error parsing dates: {_e})")
print()

# Load all firms
firms = load_all_firms()
print(f"Loaded {len(firms)} prop firm profiles:")
for fid, firm in sorted(firms.items(), key=lambda x: x[1].firm_name):
    challenges = firm.list_challenges()
    print(f"  {firm.firm_name}: {len(challenges)} challenge types")
print()

# Run compliance matrix for $100K accounts
print("=" * 70)
print("COMPLIANCE MATRIX — $100,000 accounts")
print("=" * 70)
matrix = get_compliance_matrix(trades_df, account_size=100000)
if matrix is not None and len(matrix) > 0:
    print(matrix.to_string(index=False))
else:
    print("No results (no challenges available for this account size)")

print()

# Run a specific check with detail
print("=" * 70)
print("DETAILED CHECK — FTMO 2-Step Standard $100K")
print("=" * 70)
result = check_compliance(trades_df, "ftmo", "ftmo_2step_standard", 100000)
if result:
    print(f"Overall: {'PASSED' if result.overall_passed else 'FAILED'}")
    if result.failure_reason:
        print(f"Reason: {result.failure_reason}")
    for phase in result.phases:
        print(f"\n  Phase: {phase.phase_name}")
        print(f"    Passed: {phase.passed}")
        print(f"    Profit: {phase.profit_achieved_pct:.2f}% (target: {phase.profit_target_pct}%)")
        print(f"    Max daily DD hit: {phase.max_daily_dd_hit_pct:.2f}% (limit: {phase.max_daily_dd_allowed_pct}%)")
        print(f"    Max total DD hit: {phase.max_total_dd_hit_pct:.2f}% (limit: {phase.max_total_dd_allowed_pct}%)")
        print(f"    Trading days: {phase.trading_days} (min required: {phase.min_trading_days_required})")
        if phase.consistency_check_passed is not None:
            print(f"    Consistency rule: {'PASSED' if phase.consistency_check_passed else 'FAILED'}")

print("\n=== TEST COMPLETE ===")
