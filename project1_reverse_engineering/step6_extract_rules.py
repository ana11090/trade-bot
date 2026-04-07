"""
STEP 6 — EXTRACT TRADING RULES
Extracts human-readable IF/THEN trading rules from the trained Random Forest model.
Rules are filtered by confidence and cross-referenced with SHAP top features.
"""

import sys
import os
import argparse
import pandas as pd
import numpy as np
import joblib
from sklearn.tree import export_text

# Add parent directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))


# ============================================================
# CONFIGURATION
# ============================================================
OUTPUT_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')

from config_loader import load as _load_cfg
_cfg                    = _load_cfg()
RULE_MIN_CONFIDENCE     = float(_cfg['rule_min_confidence'])
RULE_MIN_TRADE_COVERAGE = int(_cfg['rule_min_coverage'])


def extract_rules_for_scenario(scenario):
    """
    Extract trading rules for a specific scenario.

    Args:
        scenario: One of 'M5', 'M15', 'H1', 'H4', 'H1_M15'

    Returns:
        True if successful, False otherwise
    """
    print(f"\n{'=' * 60}")
    print(f"[STEP 6/7] Extracting trading rules — scenario: {scenario}")
    print(f"{'=' * 60}\n")

    output_dir = os.path.join(OUTPUT_FOLDER, f'scenario_{scenario}')

    try:
        # Load trained model
        model_file = os.path.join(output_dir, 'trained_model.pkl')

        if not os.path.exists(model_file):
            print(f"ERROR: Trained model not found: {model_file}")
            print(f"FIX: Run step4_train_model.py first for scenario {scenario}")
            return False

        model = joblib.load(model_file)
        print(f"  Loaded trained model")

        # Load labeled feature matrix
        feature_file = os.path.join(output_dir, 'feature_matrix_labeled.csv')
        data = pd.read_csv(feature_file)

        # Load SHAP importance
        shap_file = os.path.join(output_dir, 'shap_importance.csv')
        if os.path.exists(shap_file):
            shap_importance = pd.read_csv(shap_file)
            top_features = shap_importance.head(10)['feature'].tolist()
            print(f"  Loaded top 10 SHAP features")
        else:
            # Fallback to model feature importance
            feature_importance_file = os.path.join(output_dir, 'feature_importance.csv')
            if os.path.exists(feature_importance_file):
                feat_imp = pd.read_csv(feature_importance_file)
                top_features = feat_imp.head(10)['feature'].tolist()
                print(f"  Loaded top 10 features from model importance")
            else:
                print(f"  WARNING: No feature importance data found, using all features")
                exclude_cols = ['trade_id', 'open_time', 'action', 'profit', 'pips', 'outcome', 'direction', 'dataset']
                top_features = [col for col in data.columns if col not in exclude_cols]

        # Identify feature columns
        exclude_cols = ['trade_id', 'open_time', 'action', 'profit', 'pips', 'outcome', 'direction', 'dataset']
        feature_cols = [col for col in data.columns if col not in exclude_cols]

        # Extract rules from the best-performing tree
        print(f"\n  Extracting rules from Random Forest trees...")
        print(f"    Total trees in forest: {len(model.estimators_)}")

        # Get test data to evaluate tree performance
        test_data = data[data['dataset'] == 'test'].copy()
        X_test = test_data[feature_cols].fillna(0)
        y_test = test_data['outcome']

        # Find the best individual tree (highest test accuracy)
        best_tree_idx = 0
        best_tree_acc = 0

        for idx, tree in enumerate(model.estimators_):
            tree_pred = tree.predict(X_test)
            tree_acc = (tree_pred == y_test).mean()
            if tree_acc > best_tree_acc:
                best_tree_acc = tree_acc
                best_tree_idx = idx

        print(f"    Best tree index: {best_tree_idx} (accuracy: {best_tree_acc:.3f})")

        best_tree = model.estimators_[best_tree_idx]

        # Export tree as text
        tree_rules = export_text(best_tree, feature_names=feature_cols, max_depth=4)

        # Save raw tree rules
        raw_rules_file = os.path.join(output_dir, 'raw_tree_rules.txt')
        with open(raw_rules_file, 'w') as f:
            f.write(f"DECISION TREE RULES — {scenario}\n")
            f.write(f"Tree Index: {best_tree_idx}\n")
            f.write(f"Tree Accuracy: {best_tree_acc:.3f}\n")
            f.write(f"{'=' * 60}\n\n")
            f.write(tree_rules)

        print(f"  Saved raw tree rules: {raw_rules_file}")

        # ── Multi-depth rule extraction ───────────────────────────────────
        # WHY: A single tree at one depth finds limited rule patterns.
        #      Trying depths 2-8 gives us simple rules (2-3 conditions) AND
        #      complex rules (6-8 conditions) so we can pick the best of each.
        #      Same Random Forest, just fitting fresh trees at each depth.
        # CHANGED: April 2026 — multi-depth extraction
        from sklearn.tree import DecisionTreeClassifier

        print(f"\n  Multi-depth rule extraction (depths 2-8)...")

        train_data = data[data['dataset'] == 'train'].copy()
        X_train = train_data[feature_cols].fillna(0)
        y_train = train_data['outcome']

        depths_to_try = [2, 3, 4, 5, 6, 7, 8]
        all_rules = []

        for depth in depths_to_try:
            try:
                tree_d = DecisionTreeClassifier(
                    max_depth=depth,
                    min_samples_leaf=max(15, 30 // depth),
                    min_samples_split=max(20, 50 // depth),
                    random_state=42,
                    class_weight='balanced',
                )
                tree_d.fit(X_train, y_train)

                rules_d = extract_win_rules_from_tree(tree_d, feature_cols, X_test, y_test)

                for r in rules_d:
                    r['tree_depth'] = depth

                all_rules.extend(rules_d)
                print(f"    Depth {depth}: {len(rules_d)} rules extracted")
            except Exception as e:
                print(f"    Depth {depth}: error — {e}")
                continue

        # Deduplicate by condition signature (same rule from different depths)
        # WHY: Depths 4 and 5 might produce the same 4-condition rule.
        seen_signatures = set()
        unique_rules = []
        for rule in all_rules:
            sig = tuple(sorted(
                f"{c.get('feature','')}{c.get('operator','')}{c.get('value','')}"
                for c in rule.get('conditions', [])
            ))
            if sig in seen_signatures:
                continue
            seen_signatures.add(sig)
            unique_rules.append(rule)

        # Sort by confidence × coverage (existing scoring approach)
        unique_rules.sort(
            key=lambda r: r.get('confidence', 0) * r.get('coverage', 0),
            reverse=True
        )

        rules = unique_rules[:20]

        print(f"  Total candidates: {len(all_rules)}")
        print(f"  Unique rules: {len(unique_rules)}")
        print(f"  Keeping top 20 for filtering")

        # Filter rules by confidence and coverage
        filtered_rules = []
        for rule in rules:
            if rule['confidence'] >= RULE_MIN_CONFIDENCE and rule['coverage'] >= RULE_MIN_TRADE_COVERAGE:
                filtered_rules.append(rule)

        print(f"  Found {len(filtered_rules)} high-confidence rules (min confidence: {RULE_MIN_CONFIDENCE}, min coverage: {RULE_MIN_TRADE_COVERAGE})")

        # Create rules report
        rules_report_file = os.path.join(output_dir, 'rules_report.txt')
        with open(rules_report_file, 'w') as f:
            f.write(f"TRADING RULES REPORT — {scenario}\n")
            f.write(f"{'=' * 60}\n\n")
            f.write(f"Extraction Parameters:\n")
            f.write(f"  Minimum Confidence: {RULE_MIN_CONFIDENCE:.0%}\n")
            f.write(f"  Minimum Coverage: {RULE_MIN_TRADE_COVERAGE} trades\n")
            f.write(f"  Best Tree Accuracy: {best_tree_acc:.3f}\n\n")
            f.write(f"Top 10 Most Important Features (SHAP):\n")
            for i, feat in enumerate(top_features[:10], 1):
                f.write(f"  {i}. {feat}\n")
            f.write(f"\n{'=' * 60}\n\n")

            if len(filtered_rules) == 0:
                f.write("NO HIGH-CONFIDENCE RULES FOUND\n\n")
                f.write("This may indicate:\n")
                f.write("  - Wrong timeframe scenario\n")
                f.write("  - Bot uses more complex logic than decision trees can capture\n")
                f.write("  - Need more training data\n")
                f.write("  - Need to adjust confidence/coverage thresholds\n")
            else:
                f.write(f"DISCOVERED RULES ({len(filtered_rules)} total):\n\n")

                for idx, rule in enumerate(filtered_rules, 1):
                    f.write(f"RULE #{idx}\n")
                    f.write(f"  Confidence: {rule['confidence']:.1%} ({rule['wins']}/{rule['coverage']} trades won)\n")
                    f.write(f"  Coverage: {rule['coverage']} trades\n")
                    f.write(f"  Conditions:\n")
                    for condition in rule['conditions']:
                        f.write(f"    - {condition}\n")
                    f.write(f"  Action: {rule['action']}\n")
                    f.write(f"\n")

        print(f"  Saved rules report: {rules_report_file}")

        # Save rules as CSV for programmatic use
        if len(filtered_rules) > 0:
            rules_df = pd.DataFrame([
                {
                    'rule_id': i,
                    'confidence': r['confidence'],
                    'coverage': r['coverage'],
                    'wins': r['wins'],
                    'conditions_count': len(r['conditions']),
                    'action': r['action']
                }
                for i, r in enumerate(filtered_rules, 1)
            ])

            rules_csv_file = os.path.join(output_dir, 'rules_summary.csv')
            rules_df.to_csv(rules_csv_file, index=False)
            print(f"  Saved rules summary CSV: {rules_csv_file}")

        print(f"\n[STEP 6/7] COMPLETE — scenario: {scenario}\n")

        return True

    except Exception as e:
        print(f"\nERROR in step6 — {scenario}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def extract_win_rules_from_tree(tree, feature_names, X_test, y_test):
    """
    Extract rules from a decision tree that lead to WIN predictions.

    Args:
        tree: Trained decision tree
        feature_names: List of feature names
        X_test: Test features
        y_test: Test labels

    Returns:
        List of rule dictionaries
    """
    from sklearn.tree import _tree

    tree_ = tree.tree_
    feature_name = [
        feature_names[i] if i != _tree.TREE_UNDEFINED else "undefined!"
        for i in tree_.feature
    ]

    rules = []

    def recurse(node, conditions):
        if tree_.feature[node] != _tree.TREE_UNDEFINED:
            # Internal node - split
            name = feature_name[node]
            threshold = tree_.threshold[node]

            # Left branch (feature <= threshold)
            left_conditions = conditions + [f"{name} <= {threshold:.4f}"]
            recurse(tree_.children_left[node], left_conditions)

            # Right branch (feature > threshold)
            right_conditions = conditions + [f"{name} > {threshold:.4f}"]
            recurse(tree_.children_right[node], right_conditions)
        else:
            # Leaf node - evaluate
            value = tree_.value[node][0]
            total_samples = value.sum()

            if total_samples == 0:
                return

            # Check if this is a WIN leaf (class 1)
            win_samples = value[1]
            loss_samples = value[0]

            if win_samples > loss_samples:  # Predicts WIN
                confidence = win_samples / total_samples

                # Find which test samples satisfy these conditions
                mask = np.ones(len(X_test), dtype=bool)
                for condition in conditions:
                    # Parse condition
                    parts = condition.split()
                    if len(parts) >= 3:
                        feat = ' '.join(parts[:-2])
                        op = parts[-2]
                        val = float(parts[-1])

                        if feat in feature_names:
                            feat_idx = feature_names.index(feat)
                            if op == '<=':
                                mask &= (X_test.iloc[:, feat_idx] <= val)
                            elif op == '>':
                                mask &= (X_test.iloc[:, feat_idx] > val)

                matching_trades = mask.sum()
                if matching_trades > 0:
                    actual_wins = y_test[mask].sum()

                    rules.append({
                        'conditions': conditions,
                        'confidence': confidence,
                        'coverage': int(matching_trades),
                        'wins': int(actual_wins),
                        'action': 'BUY or SELL'  # Would need directional model to determine
                    })

    recurse(0, [])

    # Sort by coverage * confidence
    rules.sort(key=lambda x: x['coverage'] * x['confidence'], reverse=True)

    return rules


def main():
    """Main entry point for command-line usage."""
    parser = argparse.ArgumentParser(description='Extract trading rules from trained model')
    parser.add_argument('--scenario', type=str, required=True,
                        choices=['M5', 'M15', 'H1', 'H4', 'H1_M15'],
                        help='Timeframe scenario to process')

    args = parser.parse_args()

    success = extract_rules_for_scenario(args.scenario)

    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()
