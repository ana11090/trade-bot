"""
COMPARE SCENARIOS
Compares results across all timeframe scenarios to identify the best match.
Analyzes model accuracy and validation match rate to determine winner.
"""

import os
import pandas as pd
import re


# ============================================================
# CONFIGURATION
# ============================================================
OUTPUT_FOLDER = './outputs/'
SCENARIOS = ['M5', 'M15', 'H1', 'H4', 'H1_M15']

# CHANGED: April 2026 — UI-safe logging (Phase 19d)
from shared.logging_setup import get_logger
log = get_logger(__name__)


def extract_metrics_from_scenario(scenario):
    """
    Extract key metrics from a scenario's output files.

    Args:
        scenario: Scenario name

    Returns:
        Dictionary of metrics or None if scenario didn't complete
    """
    output_dir = os.path.join(OUTPUT_FOLDER, f'scenario_{scenario}')

    metrics = {
        'scenario': scenario,
        'completed': False,
        'test_accuracy': None,
        'match_rate': None,
        'num_rules': 0,
        'win_rate': None
    }

    # Check if scenario completed
    if not os.path.exists(output_dir):
        return metrics

    # Extract test accuracy from model_metrics.txt
    metrics_file = os.path.join(output_dir, 'model_metrics.txt')
    if os.path.exists(metrics_file):
        with open(metrics_file, 'r') as f:
            content = f.read()

            # Look for test accuracy line
            match = re.search(r'Test Set Performance:.*?Accuracy:\s+([\d.]+)', content, re.DOTALL)
            if match:
                metrics['test_accuracy'] = float(match.group(1))

    # Extract match rate from validation_report.txt
    validation_file = os.path.join(output_dir, 'validation_report.txt')
    if os.path.exists(validation_file):
        with open(validation_file, 'r') as f:
            content = f.read()

            # Look for match rate
            match = re.search(r'Match Rate:\s+([\d.]+)%', content)
            if match:
                metrics['match_rate'] = float(match.group(1)) / 100

            # Look for win rate
            match = re.search(r'Overall Win Rate:\s+([\d.]+)%', content)
            if match:
                metrics['win_rate'] = float(match.group(1)) / 100

        metrics['completed'] = True

    # Count rules
    rules_file = os.path.join(output_dir, 'rules_summary.csv')
    if os.path.exists(rules_file):
        rules_df = pd.read_csv(rules_file)
        metrics['num_rules'] = len(rules_df)

    return metrics


def compare_all_scenarios():
    """
    Compare all scenarios and generate comparison report.
    """
    log.info(f"\n{'=' * 70}")
    log.info(f"  COMPARING ALL SCENARIOS")
    log.info(f"{'=' * 70}\n")

    all_metrics = []

    for scenario in SCENARIOS:
        log.info(f"  Extracting metrics for {scenario}...")
        metrics = extract_metrics_from_scenario(scenario)
        all_metrics.append(metrics)

    # Convert to DataFrame
    comparison_df = pd.DataFrame(all_metrics)

    # Calculate composite score (weighted combination of metrics)
    # Only for completed scenarios
    for idx, row in comparison_df.iterrows():
        if row['completed'] and row['test_accuracy'] is not None and row['match_rate'] is not None:
            # Composite score: 40% test accuracy + 60% match rate
            score = 0.4 * row['test_accuracy'] + 0.6 * row['match_rate']
            comparison_df.loc[idx, 'composite_score'] = score
        else:
            comparison_df.loc[idx, 'composite_score'] = 0.0

    # Sort by composite score
    comparison_df = comparison_df.sort_values('composite_score', ascending=False)

    # Identify winner
    winner_row = comparison_df.iloc[0] if len(comparison_df) > 0 else None
    winner = winner_row['scenario'] if winner_row is not None and winner_row['completed'] else None

    # Print comparison table
    log.info(f"\n  COMPARISON TABLE:")
    log.info(f"  {'-' * 68}")
    log.info(f"  {'Scenario':12s} {'Accuracy':>10s} {'Match Rate':>12s} {'Rules':>8s} {'Score':>10s} {'Status':>10s}")
    log.info(f"  {'-' * 68}")

    for _, row in comparison_df.iterrows():
        scenario = row['scenario']
        accuracy = f"{row['test_accuracy']:.1%}" if row['test_accuracy'] is not None else "N/A"
        match_rate = f"{row['match_rate']:.1%}" if row['match_rate'] is not None else "N/A"
        rules = row['num_rules']
        score = f"{row['composite_score']:.3f}" if row['composite_score'] > 0 else "N/A"
        status = "✓ Done" if row['completed'] else "✗ Failed"

        winner_mark = " ★" if scenario == winner else ""

        log.info(f"  {scenario:12s} {accuracy:>10s} {match_rate:>12s} {rules:>8d} {score:>10s} {status:>10s}{winner_mark}")

    log.info(f"  {'-' * 68}\n")

    # Save comparison report
    comparison_file = os.path.join(OUTPUT_FOLDER, 'scenario_comparison.txt')
    with open(comparison_file, 'w') as f:
        f.write(f"SCENARIO COMPARISON REPORT\n")
        f.write(f"{'=' * 70}\n\n")

        f.write(f"SCORING METHODOLOGY:\n")
        f.write(f"  Composite Score = 0.4 × Test Accuracy + 0.6 × Match Rate\n")
        f.write(f"  Higher score indicates better reverse engineering results\n\n")

        f.write(f"COMPARISON TABLE:\n")
        f.write(f"{'-' * 70}\n")
        f.write(f"{'Scenario':12s} {'Accuracy':>10s} {'Match Rate':>12s} {'Rules':>8s} {'Score':>10s} {'Status':>10s}\n")
        f.write(f"{'-' * 70}\n")

        for _, row in comparison_df.iterrows():
            scenario = row['scenario']
            accuracy = f"{row['test_accuracy']:.1%}" if row['test_accuracy'] is not None else "N/A"
            match_rate = f"{row['match_rate']:.1%}" if row['match_rate'] is not None else "N/A"
            rules = row['num_rules']
            score = f"{row['composite_score']:.3f}" if row['composite_score'] > 0 else "N/A"
            status = "COMPLETE" if row['completed'] else "FAILED"

            winner_mark = " ★ WINNER" if scenario == winner else ""

            f.write(f"{scenario:12s} {accuracy:>10s} {match_rate:>12s} {rules:>8d} {score:>10s} {status:>10s}{winner_mark}\n")

        f.write(f"{'-' * 70}\n\n")

        if winner:
            winner_metrics = comparison_df[comparison_df['scenario'] == winner].iloc[0]

            f.write(f"WINNER: {winner}\n")
            f.write(f"{'-' * 70}\n")
            f.write(f"  Test Accuracy: {winner_metrics['test_accuracy']:.1%}\n")
            f.write(f"  Match Rate: {winner_metrics['match_rate']:.1%}\n")
            f.write(f"  Number of Rules: {winner_metrics['num_rules']}\n")
            f.write(f"  Composite Score: {winner_metrics['composite_score']:.3f}\n\n")

            if winner_metrics['match_rate'] >= 0.70:
                f.write(f"STATUS: ✓ SUCCESS\n")
                f.write(f"The bot's logic has been successfully reverse-engineered.\n\n")
                f.write(f"NEXT STEPS:\n")
                f.write(f"1. Review outputs/scenario_{winner}/validation_report.txt\n")
                f.write(f"2. Review outputs/scenario_{winner}/rules_report.txt\n")
                f.write(f"3. Check outputs/scenario_{winner}/shap_summary.png\n")
                f.write(f"4. Proceed to Project 2 (Backtesting) with these rules\n")
            elif winner_metrics['match_rate'] >= 0.60:
                f.write(f"STATUS: ⚠ MARGINAL\n")
                f.write(f"Results are acceptable but not ideal.\n\n")
                f.write(f"RECOMMENDATIONS:\n")
                f.write(f"1. Review the discovered rules for consistency\n")
                f.write(f"2. Consider adjusting model parameters and re-running\n")
                f.write(f"3. Verify data quality and timezone alignment\n")
                f.write(f"4. May proceed to backtesting with caution\n")
            else:
                f.write(f"STATUS: ✗ INSUFFICIENT MATCH\n")
                f.write(f"Match rate is too low to confidently proceed.\n\n")
                f.write(f"RECOMMENDATIONS:\n")
                f.write(f"1. Verify price data quality and completeness\n")
                f.write(f"2. Check timezone conversion settings\n")
                f.write(f"3. Consider using a different bot with better documentation\n")
                f.write(f"4. Increase training data if available\n")
        else:
            f.write(f"ERROR: No scenarios completed successfully\n")
            f.write(f"\nPossible issues:\n")
            f.write(f"  - Missing price data files in ../data/\n")
            f.write(f"  - Incorrect data format or paths\n")
            f.write(f"  - Missing Python dependencies\n")
            f.write(f"  - Check error messages from run_all_scenarios.py\n")

    log.info(f"  Saved comparison report: {comparison_file}\n")

    # Print recommendation
    if winner:
        winner_metrics = comparison_df[comparison_df['scenario'] == winner].iloc[0]

        log.info(f"  {'=' * 68}")
        log.info(f"  RECOMMENDATION:")
        log.info(f"  {'=' * 68}")
        log.info(f"  Best scenario: {winner}")
        log.info(f"  Match rate: {winner_metrics['match_rate']:.1%}")

        if winner_metrics['match_rate'] >= 0.70:
            log.info(f"  ✓ Proceed to Project 2 (Backtesting)")
        elif winner_metrics['match_rate'] >= 0.60:
            log.info(f"  ⚠ Review results before proceeding")
        else:
            log.info(f"  ✗ Consider improving data quality or trying different bot")

        log.info(f"  {'=' * 68}\n")

    # Save CSV
    csv_file = os.path.join(OUTPUT_FOLDER, 'scenario_comparison.csv')
    comparison_df.to_csv(csv_file, index=False)
    log.info(f"  Saved comparison CSV: {csv_file}\n")


def main():
    """Main entry point."""
    compare_all_scenarios()


if __name__ == '__main__':
    main()
