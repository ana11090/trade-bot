"""
STEP 7 — VALIDATE RULES
Validates discovered trading rules against actual trade history.
Calculates match rate and analyzes rule performance.
"""

import sys
import os
import argparse
import pandas as pd
import numpy as np

# Add parent directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

# CHANGED: April 2026 — UI-safe logging (Phase 19d)
from shared.logging_setup import get_logger
log = get_logger(__name__)

# WHY: share the NaN sentinel with step4
# CHANGED: April 2026 — replace fillna(0) (audit bug #12)
from step4_train_model import fill_feature_nans


# ============================================================
# CONFIGURATION
# ============================================================
OUTPUT_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')

from config_loader import load as _load_cfg
_cfg                 = _load_cfg()
MATCH_RATE_THRESHOLD = float(_cfg['match_rate_threshold'])


# WHY: step7 was using model predictions as a proxy for "rule match rate",
#      which means the rules were never actually tested. Implement a real
#      rule evaluator that parses analysis_report.json rules and evaluates
#      them against the feature matrix.
# CHANGED: April 2026 — implement real rule validation (audit bug #11)
def _evaluate_rule(rule, df):
    """Return a boolean Series indicating which rows of df satisfy the rule.

    A rule is a dict with:
        prediction: 'WIN' or 'LOSS'
        conditions: list of dicts with {feature, operator, value}
                    where operator is one of: >, <, >=, <=, ==, !=

    All conditions in a rule are AND'd together.
    """
    import numpy as np
    import operator as op

    ops = {
        '>':  op.gt,
        '<':  op.lt,
        '>=': op.ge,
        '<=': op.le,
        '==': op.eq,
        '!=': op.ne,
    }

    if not rule.get('conditions'):
        return np.zeros(len(df), dtype=bool)

    mask = np.ones(len(df), dtype=bool)
    for cond in rule['conditions']:
        feat = cond.get('feature')
        opname = cond.get('operator', '>').strip()
        value = cond.get('value')

        if feat not in df.columns:
            # Feature doesn't exist in the feature matrix → rule can't fire
            return np.zeros(len(df), dtype=bool)

        op_fn = ops.get(opname)
        if op_fn is None:
            log.info(f"    [rule eval] Unknown operator {opname!r}, treating as no-match")
            return np.zeros(len(df), dtype=bool)

        try:
            col_vals = df[feat].values.astype(float)
            # Treat sentinel (-999) as non-matching — warmup periods
            # should not trigger rules
            from step4_train_model import NAN_SENTINEL
            sentinel_mask = col_vals <= NAN_SENTINEL + 1
            cond_mask = op_fn(col_vals, float(value))
            cond_mask = cond_mask & ~sentinel_mask
            mask = mask & cond_mask
        except Exception as e:
            log.info(f"    [rule eval] Error evaluating {feat} {opname} {value}: {e}")
            return np.zeros(len(df), dtype=bool)

    return mask


def _validate_rules_on_test_set(rules, test_df, feature_cols):
    """Evaluate each rule on test_df and return a dict of per-rule stats.

    Returns a list of dicts, one per rule:
        {
            'rule_id':       int or str,
            'prediction':    'WIN' or 'LOSS',
            'conditions':    [...],
            'hit_count':     int  (how many test trades this rule fires on),
            'hit_rate':      float (hit_count / len(test_df)),
            'wins_in_hits':  int,
            'losses_in_hits':int,
            'win_rate':      float  (wins_in_hits / hit_count),
            'accuracy':      float  (how often the rule's prediction matched outcome),
        }
    """
    import numpy as np

    results = []
    if len(test_df) == 0:
        return results

    # Ensure numeric features (use sentinel-filled copy)
    X = fill_feature_nans(test_df[feature_cols]) if feature_cols else test_df
    y = test_df['outcome'].astype(int).values

    for i, rule in enumerate(rules):
        try:
            mask = _evaluate_rule(rule, X)
            hit_count = int(mask.sum())
            hit_rate = hit_count / len(test_df) if len(test_df) > 0 else 0.0

            if hit_count == 0:
                results.append({
                    'rule_id':       rule.get('id', i),
                    'prediction':    rule.get('prediction', 'WIN'),
                    'conditions':    rule.get('conditions', []),
                    'hit_count':     0,
                    'hit_rate':      0.0,
                    'wins_in_hits':  0,
                    'losses_in_hits':0,
                    'win_rate':      0.0,
                    'accuracy':      0.0,
                })
                continue

            hit_outcomes = y[mask]
            wins = int((hit_outcomes == 1).sum())
            losses = int((hit_outcomes == 0).sum())

            win_rate = wins / hit_count
            # Accuracy = how often the rule's prediction matched reality
            target = 1 if rule.get('prediction') == 'WIN' else 0
            accuracy = float((hit_outcomes == target).mean())

            results.append({
                'rule_id':       rule.get('id', i),
                'prediction':    rule.get('prediction', 'WIN'),
                'conditions':    rule.get('conditions', []),
                'hit_count':     hit_count,
                'hit_rate':      hit_rate,
                'wins_in_hits':  wins,
                'losses_in_hits':losses,
                'win_rate':      win_rate,
                'accuracy':      accuracy,
            })
        except Exception as e:
            log.info(f"    [rule eval] Rule {i} failed: {e}")
            results.append({
                'rule_id': rule.get('id', i),
                'error':   str(e),
                'hit_count': 0,
                'hit_rate': 0.0,
                'win_rate': 0.0,
                'accuracy': 0.0,
            })

    return results


def validate_rules_for_scenario(scenario):
    """
    Validate trading rules for a specific scenario.

    Args:
        scenario: One of 'M5', 'M15', 'H1', 'H4', 'H1_M15'

    Returns:
        True if successful, False otherwise
    """
    log.info(f"\n{'=' * 60}")
    log.info(f"[STEP 7/7] Validating rules — scenario: {scenario}")
    log.info(f"{'=' * 60}\n")

    output_dir = os.path.join(OUTPUT_FOLDER, f'scenario_{scenario}')

    try:
        # Load feature matrix
        feature_file = os.path.join(output_dir, 'feature_matrix_labeled.csv')

        if not os.path.exists(feature_file):
            log.error(f"Feature matrix not found: {feature_file}")
            log.info(f"FIX: Run previous steps first for scenario {scenario}")
            return False

        data = pd.read_csv(feature_file)
        data['open_time'] = pd.to_datetime(data['open_time'])

        log.info(f"  Loaded feature matrix: {len(data)} trades")

        # Load model metrics to get accuracy
        metrics_file = os.path.join(output_dir, 'model_metrics.txt')
        model_accuracy = None
        if os.path.exists(metrics_file):
            with open(metrics_file, 'r', encoding='utf-8') as f:
                content = f.read()
            # WHY: Previously opened the file twice per line (nested open call).
            # CHANGED: April 2026 — read once, scan in memory
            in_test_section = False
            for line in content.splitlines():
                if 'Test Set Performance' in line:
                    in_test_section = True
                if in_test_section and 'Accuracy:' in line:
                    try:
                        model_accuracy = float(line.split(':')[1].split('(')[0].strip())
                        break
                    except Exception:
                        pass

        # Check if rules exist
        rules_file = os.path.join(output_dir, 'rules_summary.csv')

        if not os.path.exists(rules_file):
            log.info(f"  WARNING: No rules summary found at {rules_file}")
            log.info(f"  Using model predictions for validation instead\n")

            # Fallback: validate using model predictions
            model_file = os.path.join(output_dir, 'trained_model.pkl')
            if os.path.exists(model_file):
                import joblib
                model = joblib.load(model_file)

                # WHY: Use shared transform from step4 — handles timestamps,
                #      label-encoding, and excludes leakage features.
                # CHANGED: April 2026 — shared transform helper
                from step4_train_model import prepare_features
                data, feature_cols = prepare_features(data, scenario=scenario)
                # WHY: Validating on the full dataset (train+test) inflates
                #      match_rate by ~20% since the model was fit on train.
                #      Only the test set gives an honest out-of-sample number.
                # CHANGED: April 2026 — test-only validation
                test_mask = data['dataset'] == 'test'
                X = fill_feature_nans(data.loc[test_mask, feature_cols])
                y_pred = model.predict(X)
                y_true = data.loc[test_mask, 'outcome']

                match_rate = (y_pred == y_true).mean()

                log.info(f"  Model prediction match rate: {match_rate:.1%}")
            else:
                log.info(f"  ERROR: Neither rules nor model found for validation")
                return False
        else:
            # WHY: Old code used model.predict() as a "proxy" for rule
            #      validation — but that's just the model's test accuracy
            #      with a different label, not a test of the rules at all.
            #      Now we load the actual rules from analysis_report.json
            #      and evaluate each one's conditions against the feature
            #      matrix on the test set. Real per-rule win rate, real
            #      hit count.
            # CHANGED: April 2026 — real rule validation (audit bug #11)
            import json
            analysis_report_path = os.path.join(output_dir, 'analysis_report.json')
            rules = []
            if os.path.exists(analysis_report_path):
                try:
                    with open(analysis_report_path, 'r', encoding='utf-8') as f:
                        report = json.load(f)
                    rules = [r for r in report.get('rules', []) if r.get('conditions')]
                    log.info(f"  Loaded {len(rules)} rules from analysis_report.json")
                except Exception as e:
                    log.info(f"  Could not load analysis_report.json: {e}")
            else:
                log.info(f"  analysis_report.json not found at {analysis_report_path}")

            # Load and prepare the feature matrix exactly the same way
            # step4 did so indicator column names match what the rules expect
            from step4_train_model import prepare_features
            data, feature_cols = prepare_features(data, scenario=scenario)

            test_mask = data['dataset'] == 'test'
            test_df = data.loc[test_mask].copy()
            log.info(f"  Evaluating rules on {len(test_df)} test trades...")

            rule_results = _validate_rules_on_test_set(rules, test_df, feature_cols)

            # Compute aggregate numbers for the old `match_rate` slot
            total_hits = sum(r['hit_count'] for r in rule_results)
            total_correct = sum(r.get('accuracy', 0) * r['hit_count'] for r in rule_results)
            match_rate = (total_correct / total_hits) if total_hits > 0 else 0.0

            # Per-rule breakdown for the report
            log.info(f"\n  Per-rule results on test set:")
            for r in rule_results[:10]:  # show up to 10
                if r.get('hit_count', 0) > 0:
                    log.info(f"    Rule {r['rule_id']:>3} ({r['prediction']}): "
                          f"{r['hit_count']:4d} hits, WR {r['win_rate']:5.1%}, "
                          f"accuracy {r['accuracy']:5.1%}")
                else:
                    log.info(f"    Rule {r['rule_id']:>3} ({r['prediction']}): "
                          f"0 hits (never fired on test set)")
            if len(rule_results) > 10:
                log.info(f"    ... {len(rule_results) - 10} more rules")

            # Save per-rule results alongside the validation report
            rule_results_file = os.path.join(output_dir, 'rule_validation_results.json')
            try:
                with open(rule_results_file, 'w', encoding='utf-8') as f:
                    json.dump(rule_results, f, indent=2, default=str)
                log.info(f"  Saved per-rule results: {rule_results_file}")
            except Exception as e:
                log.info(f"  Could not save rule results: {e}")

        # Analyze performance by dataset split
        train_data = data[data['dataset'] == 'train']
        test_data = data[data['dataset'] == 'test']

        # Calculate win rates
        overall_win_rate = data['outcome'].mean()
        train_win_rate = train_data['outcome'].mean()
        test_win_rate = test_data['outcome'].mean()

        # Analyze by time period
        data['year_month'] = data['open_time'].dt.to_period('M')
        monthly_stats = data.groupby('year_month').agg({
            'outcome': ['count', 'sum', 'mean'],
            'profit': 'sum'
        }).round(3)

        # Create validation report
        validation_report_file = os.path.join(output_dir, 'validation_report.txt')
        # WHY: Windows defaults to cp1252 which can't encode ✓ or other
        #      Unicode characters. Force UTF-8.
        # CHANGED: April 2026 — encoding fix
        with open(validation_report_file, 'w', encoding='utf-8') as f:
            f.write(f"VALIDATION REPORT — {scenario}\n")
            f.write(f"{'=' * 60}\n\n")

            f.write(f"OVERALL STATISTICS\n")
            f.write(f"{'-' * 60}\n")
            f.write(f"Total Trades: {len(data)}\n")
            f.write(f"Overall Win Rate: {overall_win_rate:.1%}\n")
            f.write(f"Train Win Rate: {train_win_rate:.1%} ({len(train_data)} trades)\n")
            f.write(f"Test Win Rate: {test_win_rate:.1%} ({len(test_data)} trades)\n\n")

            if model_accuracy is not None:
                f.write(f"MODEL PERFORMANCE\n")
                f.write(f"{'-' * 60}\n")
                f.write(f"Test Accuracy: {model_accuracy:.1%}\n")
                f.write(f"Prediction Match Rate: {match_rate:.1%}\n\n")

            f.write(f"MATCH RATE ASSESSMENT\n")
            f.write(f"{'-' * 60}\n")
            f.write(f"Match Rate: {match_rate:.1%}\n")
            f.write(f"Target Threshold: {MATCH_RATE_THRESHOLD:.1%}\n")

            if match_rate >= MATCH_RATE_THRESHOLD:
                f.write(f"STATUS: ✓ PASS - Match rate exceeds threshold\n")
                f.write(f"RECOMMENDATION: PROCEED to Project 2 (Backtesting)\n")
            elif match_rate >= 0.60:
                f.write(f"STATUS: ⚠ MARGINAL - Match rate is acceptable but not ideal\n")
                f.write(f"RECOMMENDATION: Consider testing other scenarios or adjusting parameters\n")
            else:
                f.write(f"STATUS: ✗ FAIL - Match rate too low\n")
                f.write(f"RECOMMENDATION: Try different scenario or check data quality\n")

            f.write(f"\n")

            f.write(f"PERFORMANCE BY MONTH\n")
            f.write(f"{'-' * 60}\n")
            f.write(f"{'Month':12s} {'Trades':>8s} {'Wins':>8s} {'Win Rate':>10s} {'Profit':>12s}\n")
            f.write(f"{'-' * 60}\n")

            for period, row in monthly_stats.iterrows():
                month_str = str(period)
                trades = int(row[('outcome', 'count')])
                wins = int(row[('outcome', 'sum')])
                win_rate = row[('outcome', 'mean')]
                profit = row[('profit', 'sum')]

                f.write(f"{month_str:12s} {trades:8d} {wins:8d} {win_rate:9.1%} ${profit:11.2f}\n")

            f.write(f"\n")

            f.write(f"DECISION SUMMARY\n")
            f.write(f"{'-' * 60}\n")
            if match_rate >= MATCH_RATE_THRESHOLD:
                f.write(f"This scenario ({scenario}) successfully reverse-engineered the bot's logic.\n")
                f.write(f"The discovered indicators and rules match the bot's actual behavior.\n\n")
                f.write(f"Next Steps:\n")
                f.write(f"1. Review the rules_report.txt to understand the trading logic\n")
                f.write(f"2. Check shap_summary.png to see which indicators matter most\n")
                f.write(f"3. Proceed to Project 2 to backtest these rules\n")
            else:
                f.write(f"This scenario ({scenario}) did not successfully match the bot's behavior.\n")
                f.write(f"Consider:\n")
                f.write(f"1. Testing other timeframe scenarios\n")
                f.write(f"2. Checking if price data quality is good\n")
                f.write(f"3. Verifying timezone alignment is correct\n")
                f.write(f"4. Trying the combined H1_M15 scenario if not already tested\n")

        log.info(f"  Saved validation report: {validation_report_file}")

        # Print summary to console
        log.info(f"\n  {'=' * 50}")
        log.info(f"  VALIDATION SUMMARY — {scenario}")
        log.info(f"  {'=' * 50}")
        log.info(f"  Total Trades: {len(data)}")
        log.info(f"  Overall Win Rate: {overall_win_rate:.1%}")
        log.info(f"  Match Rate: {match_rate:.1%}")
        log.info(f"  Target Threshold: {MATCH_RATE_THRESHOLD:.1%}")

        if match_rate >= MATCH_RATE_THRESHOLD:
            log.info(f"  STATUS: ✓ PASS")
            log.info(f"  RECOMMENDATION: Proceed to Project 2 (Backtesting)")
        elif match_rate >= 0.60:
            log.info(f"  STATUS: ⚠ MARGINAL")
            log.info(f"  RECOMMENDATION: Consider other scenarios")
        else:
            log.info(f"  STATUS: ✗ FAIL")
            log.info(f"  RECOMMENDATION: Try different scenario")

        log.info(f"  {'=' * 50}\n")

        log.info(f"\n[STEP 7/7] COMPLETE — scenario: {scenario}\n")

        return True

    except Exception as e:
        log.info(f"\nERROR in step7 — {scenario}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main entry point for command-line usage."""
    parser = argparse.ArgumentParser(description='Validate trading rules against trade history')
    parser.add_argument('--scenario', type=str, required=True,
                        choices=['M5', 'M15', 'H1', 'H4', 'H1_M15'],
                        help='Timeframe scenario to process')

    args = parser.parse_args()

    success = validate_rules_for_scenario(args.scenario)

    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()
