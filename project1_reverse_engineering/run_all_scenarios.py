"""
RUN ALL SCENARIOS
Orchestrates the execution of all 7 steps for all 5 timeframe scenarios.
Runs each scenario independently and reports results.
"""

import sys
import os
from datetime import datetime

# Import all step modules
import step1_align_price
import step2_compute_indicators
import step3_label_trades
import step4_train_model
import step5_shap_analysis
import step6_extract_rules
import step7_validate


# ============================================================
# CONFIGURATION
# ============================================================
SCENARIOS = ['M5', 'M15', 'H1', 'H4', 'H1_M15']


# WHY: align_all_timeframes runs once for ALL TFs at once.
#      Wrapper makes it compatible with the per-scenario step interface.
# CHANGED: April 2026 — fix step1 function name
_step1_already_run = [False]

def _step1_wrapper(scenario):
    if _step1_already_run[0]:
        print(f"  (Step 1 already run — skipping)")
        return True
    result = step1_align_price.align_all_timeframes()
    _step1_already_run[0] = (result is not None)
    return _step1_already_run[0]


def run_full_pipeline_for_scenario(scenario):
    """
    Run all 7 steps for a single scenario.

    Args:
        scenario: Timeframe scenario name

    Returns:
        True if all steps completed successfully, False otherwise
    """
    print(f"\n")
    print(f"{'#' * 70}")
    print(f"# RUNNING FULL PIPELINE FOR SCENARIO: {scenario}")
    print(f"{'#' * 70}")
    print(f"\n")

    steps = [
        ("Step 1: Align Price Data", _step1_wrapper),
        ("Step 2: Compute Indicators", step2_compute_indicators.compute_indicators_for_scenario),
        ("Step 3: Label Trades", step3_label_trades.label_trades_for_scenario),
        ("Step 4: Train Model", step4_train_model.train_model_for_scenario),
        ("Step 5: SHAP Analysis", step5_shap_analysis.shap_analysis_for_scenario),
        ("Step 6: Extract Rules", step6_extract_rules.extract_rules_for_scenario),
        ("Step 7: Validate Rules", step7_validate.validate_rules_for_scenario),
    ]

    for step_name, step_func in steps:
        print(f"\n>>> {step_name} — {scenario}")

        try:
            success = step_func(scenario)

            if not success:
                print(f"\n❌ ERROR: {step_name} failed for scenario {scenario}")
                print(f"   Skipping remaining steps for this scenario\n")
                return False

        except Exception as e:
            print(f"\n❌ EXCEPTION in {step_name} for scenario {scenario}:")
            print(f"   {str(e)}")
            import traceback
            traceback.print_exc()
            print(f"   Skipping remaining steps for this scenario\n")
            return False

    print(f"\n")
    print(f"{'#' * 70}")
    print(f"# ✓ SCENARIO {scenario} COMPLETED SUCCESSFULLY")
    print(f"{'#' * 70}")
    print(f"\n")

    return True


def main():
    """Main entry point."""
    start_time = datetime.now()

    print(f"\n")
    print(f"{'=' * 70}")
    print(f"  REVERSE ENGINEERING — RUNNING ALL SCENARIOS")
    print(f"  Start time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Scenarios: {', '.join(SCENARIOS)}")
    print(f"{'=' * 70}")
    print(f"\n")

    results = {}

    for scenario in SCENARIOS:
        success = run_full_pipeline_for_scenario(scenario)
        results[scenario] = success

    # Print final summary
    end_time = datetime.now()
    duration = end_time - start_time

    print(f"\n")
    print(f"{'=' * 70}")
    print(f"  FINAL SUMMARY")
    print(f"{'=' * 70}")
    print(f"\n")
    print(f"  Start time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  End time:   {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Duration:   {duration}")
    print(f"\n")
    print(f"  Results:")

    for scenario, success in results.items():
        status = "✓ SUCCESS" if success else "✗ FAILED"
        print(f"    {scenario:10s} {status}")

    successful_scenarios = [s for s, success in results.items() if success]
    failed_scenarios = [s for s, success in results.items() if not success]

    print(f"\n")
    print(f"  Successful: {len(successful_scenarios)}/{len(SCENARIOS)}")

    if len(failed_scenarios) > 0:
        print(f"  Failed: {', '.join(failed_scenarios)}")

    print(f"\n")
    print(f"  NEXT STEPS:")
    if len(successful_scenarios) > 0:
        print(f"    1. Run: python compare_scenarios.py")
        print(f"    2. Review outputs/scenario_comparison.txt to find the best scenario")
        print(f"    3. Check the best scenario's validation_report.txt and rules_report.txt")
        print(f"    4. If match rate >= 70%, proceed to Project 2 (Backtesting)")
    else:
        print(f"    ⚠ All scenarios failed. Check:")
        print(f"      - Is price data available in ../data/ folder?")
        print(f"      - Is trades data available and properly formatted?")
        print(f"      - Are all required libraries installed?")

    print(f"\n")
    print(f"{'=' * 70}")
    print(f"\n")

    # Return 0 if at least one scenario succeeded, 1 if all failed
    sys.exit(0 if len(successful_scenarios) > 0 else 1)


if __name__ == '__main__':
    main()
