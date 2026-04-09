"""
STRATEGY SEARCH ENGINE — Pattern Discovery

Systematically tests indicator combinations across all features in feature_matrix.csv
to find profitable entry patterns. Uses vectorized numpy operations for speed.

Quick search: ~5 min, tests top-20 features
Full search: ~30-60 min, tests all 620 features
"""

import os
import sys
import json
import time
import numpy as np
import pandas as pd
from collections import defaultdict
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FEATURE_MATRIX = os.path.join(_HERE, 'outputs', 'feature_matrix.csv')
DEFAULT_REPORT = os.path.join(_HERE, 'outputs', 'analysis_report.json')
DEFAULT_OUTPUT = os.path.join(_HERE, 'outputs', 'strategy_search_results.json')

# Metadata columns to exclude from feature set
METADATA_COLS = {
    'trade_id', 'open_time', 'close_time', 'action', 'pips', 'profit',
    'hour_of_day', 'day_of_week', 'trade_duration_minutes', 'is_winner', 'trade_direction'
}


def _evaluate_rule_on_holdout(rule_conditions, df_holdout, min_coverage_holdout):
    """
    Re-evaluate a discovered rule's conditions on the holdout set.

    Returns dict with win_rate, matches, avg_pips, or None if coverage
    too low to trust.

    WHY: The main search operates on the train split and returns rules
         ranked by train score. Those rankings are unreliable at scale
         (family-wise error). This helper checks each top candidate
         against unseen holdout data as a final gate.
    CHANGED: April 2026 — held-out validation (audit CRITICAL)
    """
    import numpy as np
    if 'pips' not in df_holdout.columns:
        return None

    holdout_pips = df_holdout['pips'].values
    holdout_is_winner = holdout_pips > 0
    n_holdout = len(df_holdout)

    mask = np.ones(n_holdout, dtype=bool)
    for cond_key in rule_conditions:
        feat, op, val = cond_key
        if feat not in df_holdout.columns:
            return None
        col = df_holdout[feat].values
        nan_mask = ~np.isnan(col) if col.dtype.kind == 'f' else np.ones_like(col, dtype=bool)
        if op == '<=':
            mask &= (col <= val) & nan_mask
        elif op == '>':
            mask &= (col > val) & nan_mask
        else:
            return None

    matches = int(mask.sum())
    if matches < min_coverage_holdout:
        return None

    wins = int((holdout_is_winner & mask).sum())
    wr = wins / matches if matches > 0 else 0.0
    ap = float(holdout_pips[mask].mean()) if matches > 0 else 0.0
    return {
        'matches':  matches,
        'win_rate': wr,
        'avg_pips': ap,
    }


def search_strategies(
    feature_matrix_path=None,
    report_path=None,
    mode="quick",                # "quick" or "full"
    timeframe_filter=None,       # e.g. ["H1", "H4"] or None for all
    max_conditions=2,            # max conditions per rule (1, 2, or 3)
    min_coverage=15,             # minimum trades matching the rule
    min_win_rate=0.55,           # minimum win rate to keep a rule
    num_thresholds=5,            # thresholds to test per feature
    progress_callback=None,      # callable(current, total, message)
) -> dict:
    """
    Search for profitable trading strategies by testing indicator combinations.

    Args:
        feature_matrix_path: Path to feature_matrix.csv (default: outputs/feature_matrix.csv)
        report_path: Path to analysis_report.json (default: outputs/analysis_report.json)
        mode: "quick" (top 20 features) or "full" (all features)
        timeframe_filter: List of timeframes to include (e.g. ["H1", "H4"]), or None for all
        max_conditions: Maximum conditions per rule (1, 2, or 3)
        min_coverage: Minimum number of trades a rule must match
        min_win_rate: Minimum win rate (0-1) to keep a rule
        num_thresholds: Number of threshold values to test per feature
        progress_callback: Optional function(current, total, message) for progress updates

    Returns:
        dict: Results with strategies list
    """
    start_time = time.time()

    # Default paths
    if feature_matrix_path is None:
        feature_matrix_path = DEFAULT_FEATURE_MATRIX
    if report_path is None:
        report_path = DEFAULT_REPORT

    print(f"\n{'='*70}")
    print(f"STRATEGY SEARCH — {mode.upper()} MODE")
    print(f"{'='*70}\n")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 1: Load and prepare data
    # ═══════════════════════════════════════════════════════════════════════════
    print("[1/7] Loading feature matrix...")

    if not os.path.exists(feature_matrix_path):
        raise FileNotFoundError(f"Feature matrix not found: {feature_matrix_path}")

    df_full = pd.read_csv(feature_matrix_path)
    print(f"  Loaded {len(df_full)} trades")

    # WHY: Old code ran the entire search (6K+ single tests, up to 19M pair
    #      tests) on the full dataset with no train/holdout split. At that
    #      test count, the family-wise error rate under the null hypothesis
    #      is effectively 1.0 — almost every "discovered" rule is noise.
    #      Fix: chronological 70/30 split. Discover on the 70% train portion.
    #      After ranking, re-evaluate top candidates on the 30% holdout and
    #      require them to still pass min_win_rate. This filters out
    #      overfit rules BEFORE they reach the user.
    # CHANGED: April 2026 — held-out validation (audit CRITICAL)
    _split_idx = int(len(df_full) * 0.7)
    df = df_full.iloc[:_split_idx].reset_index(drop=True)  # train
    df_holdout = df_full.iloc[_split_idx:].reset_index(drop=True)
    print(f"  Split: {len(df)} train / {len(df_holdout)} holdout (70/30 chronological)")

    # Separate metadata from features
    all_cols = set(df.columns)
    feature_cols = [c for c in df.columns if c not in METADATA_COLS]
    print(f"  {len(feature_cols)} feature columns")

    # Apply timeframe filter
    if timeframe_filter:
        feature_cols = [c for c in feature_cols
                       if any(c.startswith(f"{tf}_") for tf in timeframe_filter)]
        print(f"  Filtered to {len(feature_cols)} features (timeframes: {timeframe_filter})")

    # Quick mode: use only top-20 features from analysis report
    if mode == "quick" and os.path.exists(report_path):
        with open(report_path, 'r', encoding='utf-8') as f:
            report = json.load(f)

        top_features = [feat for feat, _ in report.get('feature_importance', {}).get('top_20', [])]
        feature_cols = [f for f in feature_cols if f in top_features]
        print(f"  Quick mode: using top-{len(feature_cols)} features from analysis report")

    # Drop features with >90% NaN (empty columns)
    valid_features = []
    for feat in feature_cols:
        non_null_pct = df[feat].notna().sum() / len(df)
        if non_null_pct > 0.10:  # Keep if >10% non-null
            valid_features.append(feat)

    feature_cols = valid_features
    print(f"  {len(feature_cols)} features after removing empty columns")

    if len(feature_cols) < 10:
        raise ValueError(f"Too few features remaining ({len(feature_cols)}). Run 'Full Analysis' first to populate feature_matrix.csv")

    # Extract target and metrics
    # WHY: step2_compute_indicators.py explicitly does NOT write
    #      'is_winner' to the feature matrix (see its line 91 NOTE:
    #      "trade_duration_minutes and is_winner are NOT added here").
    #      Reading df['is_winner'] throws KeyError on every run
    #      against the canonical feature_matrix.csv. Derive from
    #      pips instead — pips are unambiguous and match what the
    #      score formulas below use.
    # CHANGED: April 2026 — derive is_winner from pips (audit CRITICAL)
    pips = df['pips'].values
    is_winner = pips > 0  # numpy bool array
    n_trades = len(df)

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 2: Generate thresholds
    # ═══════════════════════════════════════════════════════════════════════════
    print(f"\n[2/7] Generating thresholds ({num_thresholds} per feature)...")

    if num_thresholds == 5:
        percentiles = [20, 35, 50, 65, 80]
    elif num_thresholds == 10:
        percentiles = [10, 20, 30, 40, 50, 60, 70, 80, 90, 95]
    else:
        percentiles = np.linspace(20, 80, num_thresholds).tolist()

    thresholds = {}
    for feat in feature_cols:
        col = df[feat].dropna()
        if len(col) > 0:
            thresholds[feat] = np.percentile(col, percentiles).tolist()
        else:
            thresholds[feat] = []

    total_thresholds = sum(len(t) for t in thresholds.values())
    print(f"  Generated {total_thresholds} threshold values")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 3: Pre-build boolean masks (CRITICAL FOR SPEED)
    # ═══════════════════════════════════════════════════════════════════════════
    print(f"\n[3/7] Pre-building boolean masks...")

    masks = {}
    mask_count = 0

    for feat in feature_cols:
        col = df[feat].values
        nan_mask = ~np.isnan(col)

        for threshold in thresholds[feat]:
            le_mask = (col <= threshold) & nan_mask
            gt_mask = (col > threshold) & nan_mask

            masks[(feat, "<=", threshold)] = le_mask
            masks[(feat, ">", threshold)] = gt_mask
            mask_count += 2

    print(f"  Built {mask_count} boolean masks")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 4: Test single conditions
    # ═══════════════════════════════════════════════════════════════════════════
    print(f"\n[4/7] Testing single conditions...")

    singles = []
    total_singles = len(masks)

    for i, (condition_key, mask) in enumerate(masks.items()):
        if i % 200 == 0 and progress_callback:
            progress_callback(i, total_singles, "Testing single conditions")

        matches = mask.sum()
        if matches < min_coverage:
            continue

        wins = (is_winner & mask).sum()
        win_rate = wins / matches if matches > 0 else 0

        if win_rate < min_win_rate:
            continue

        avg_pips = pips[mask].mean()
        total_pips = pips[mask].sum()

        # WHY: Old score formula went NEGATIVE for avg_pips <= -100, flipping
        #      ranking so LOSING rules could outrank profitable ones. Drop
        #      unprofitable rules entirely before scoring — discovery targets
        #      profitable patterns, not just high-WR ones.
        # CHANGED: April 2026 — drop unprofitable rules (audit HIGH)
        if avg_pips <= 0:
            continue

        # Score: balance win rate, coverage, and profitability
        score = win_rate * np.sqrt(matches) * (1 + avg_pips / 100)

        feat, op, val = condition_key
        singles.append({
            'condition_key': condition_key,
            'mask': mask,
            'matches': matches,
            'win_rate': win_rate,
            'avg_pips': avg_pips,
            'total_pips': total_pips,
            'score': score,
        })

    # Sort by score descending
    singles.sort(key=lambda x: x['score'], reverse=True)

    print(f"  Found {len(singles)} qualifying single conditions")

    # WHY: Old limits were too tight — after the avg_pips > 0 filter the
    #      qualifying pool shrinks, so a tighter cap would miss real patterns.
    #      Increased to 500 (quick) / 1500 (full) to compensate.
    # CHANGED: April 2026 — wider pruning limits (audit MEDIUM)
    top_singles_for_pairs = 500 if mode == "quick" else 1500
    top_singles = singles[:top_singles_for_pairs]

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 5: Test pairs (if max_conditions >= 2)
    # ═══════════════════════════════════════════════════════════════════════════
    pairs = []

    if max_conditions >= 2:
        print(f"\n[5/7] Testing condition pairs...")
        print(f"  Testing {len(top_singles)} × {len(top_singles)} combinations...")

        total_pairs = len(top_singles) * (len(top_singles) - 1) // 2
        pair_count = 0

        for i in range(len(top_singles)):
            for j in range(i + 1, len(top_singles)):
                if pair_count % 500 == 0 and progress_callback:
                    progress_callback(pair_count, total_pairs, "Testing condition pairs")

                pair_count += 1

                cond1 = top_singles[i]
                cond2 = top_singles[j]

                # Skip if same feature
                feat1 = cond1['condition_key'][0]
                feat2 = cond2['condition_key'][0]
                if feat1 == feat2:
                    continue

                # AND the masks
                combined_mask = cond1['mask'] & cond2['mask']

                matches = combined_mask.sum()
                if matches < min_coverage:
                    continue

                wins = (is_winner & combined_mask).sum()
                win_rate = wins / matches if matches > 0 else 0

                if win_rate < min_win_rate:
                    continue

                avg_pips = pips[combined_mask].mean()
                total_pips = pips[combined_mask].sum()

                # WHY: Same negative-score bug as singles — drop unprofitable pairs.
                # CHANGED: April 2026 — drop unprofitable rules (audit HIGH)
                if avg_pips <= 0:
                    continue

                score = win_rate * np.sqrt(matches) * (1 + avg_pips / 100)

                pairs.append({
                    'conditions': [cond1['condition_key'], cond2['condition_key']],
                    'matches': matches,
                    'win_rate': win_rate,
                    'avg_pips': avg_pips,
                    'total_pips': total_pips,
                    'score': score,
                })

        pairs.sort(key=lambda x: x['score'], reverse=True)
        print(f"  Found {len(pairs)} qualifying pairs")
    else:
        print(f"\n[5/7] Skipping pairs (max_conditions < 2)")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 6: Test triples (only if max_conditions == 3 AND mode == "full")
    # ═══════════════════════════════════════════════════════════════════════════
    triples = []

    if max_conditions == 3 and mode == "full":
        print(f"\n[6/7] Testing condition triples...")

        top_pairs = pairs[:100]
        # WHY: Same widening rationale as pairs limit — allow more candidates.
        # CHANGED: April 2026 — wider pruning limits (audit MEDIUM)
        top_singles_for_triples = singles[:400]

        total_triples = len(top_pairs) * len(top_singles_for_triples)
        triple_count = 0

        for pair in top_pairs:
            # Build mask for pair
            feat1, op1, val1 = pair['conditions'][0]
            feat2, op2, val2 = pair['conditions'][1]
            mask1 = masks[(feat1, op1, val1)]
            mask2 = masks[(feat2, op2, val2)]
            pair_mask = mask1 & mask2

            for single in top_singles_for_triples:
                if triple_count % 500 == 0 and progress_callback:
                    progress_callback(triple_count, total_triples, "Testing condition triples")

                triple_count += 1

                feat3 = single['condition_key'][0]

                # Skip if feature already used
                if feat3 in [feat1, feat2]:
                    continue

                # AND with third condition
                combined_mask = pair_mask & single['mask']

                matches = combined_mask.sum()
                if matches < min_coverage:
                    continue

                wins = (is_winner & combined_mask).sum()
                win_rate = wins / matches if matches > 0 else 0

                if win_rate < min_win_rate:
                    continue

                avg_pips = pips[combined_mask].mean()
                total_pips = pips[combined_mask].sum()

                # WHY: Same negative-score bug as singles/pairs — drop unprofitable triples.
                # CHANGED: April 2026 — drop unprofitable rules (audit HIGH)
                if avg_pips <= 0:
                    continue

                score = win_rate * np.sqrt(matches) * (1 + avg_pips / 100)

                triples.append({
                    'conditions': pair['conditions'] + [single['condition_key']],
                    'matches': matches,
                    'win_rate': win_rate,
                    'avg_pips': avg_pips,
                    'total_pips': total_pips,
                    'score': score,
                })

        triples.sort(key=lambda x: x['score'], reverse=True)
        print(f"  Found {len(triples)} qualifying triples")
    else:
        print(f"\n[6/7] Skipping triples (max_conditions < 3 or mode != 'full')")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 7: Compile and save
    # ═══════════════════════════════════════════════════════════════════════════
    print(f"\n[7/7] Compiling results...")

    # Combine all strategies
    all_strategies = []

    # Add singles
    for s in singles:
        feat, op, val = s['condition_key']
        all_strategies.append({
            'conditions': [{'feature': feat, 'operator': op, 'value': float(val)}],
            # WHY: Holdout validation (S2c) needs the raw condition tuples to
            #      re-evaluate each rule on df_holdout. The 'conditions' list
            #      above uses dicts (JSON-friendly) but loses tuple identity;
            #      condition_key_tuple carries the original (feat, op, val) tuples.
            # CHANGED: April 2026 — held-out validation (audit CRITICAL)
            'condition_key_tuple': [s['condition_key']],
            'prediction': 'WIN',
            'confidence': s['win_rate'],
            'coverage': int(s['matches']),
            'coverage_pct': round(s['matches'] / n_trades * 100, 1),
            'win_rate': s['win_rate'],
            'avg_pips': s['avg_pips'],
            'total_pips': s['total_pips'],
            'score': s['score'],
            'num_conditions': 1,
            'timeframes_used': [feat.split('_')[0]] if '_' in feat else [],
        })

    # Add pairs
    for p in pairs:
        conditions_list = []
        tfs = set()
        for feat, op, val in p['conditions']:
            conditions_list.append({'feature': feat, 'operator': op, 'value': float(val)})
            if '_' in feat:
                tfs.add(feat.split('_')[0])

        all_strategies.append({
            'conditions': conditions_list,
            'condition_key_tuple': p['conditions'],
            'prediction': 'WIN',
            'confidence': p['win_rate'],
            'coverage': int(p['matches']),
            'coverage_pct': round(p['matches'] / n_trades * 100, 1),
            'win_rate': p['win_rate'],
            'avg_pips': p['avg_pips'],
            'total_pips': p['total_pips'],
            'score': p['score'],
            'num_conditions': 2,
            'timeframes_used': sorted(list(tfs)),
        })

    # Add triples
    for t in triples:
        conditions_list = []
        tfs = set()
        for feat, op, val in t['conditions']:
            conditions_list.append({'feature': feat, 'operator': op, 'value': float(val)})
            if '_' in feat:
                tfs.add(feat.split('_')[0])

        all_strategies.append({
            'conditions': conditions_list,
            'condition_key_tuple': t['conditions'],
            'prediction': 'WIN',
            'confidence': t['win_rate'],
            'coverage': int(t['matches']),
            'coverage_pct': round(t['matches'] / n_trades * 100, 1),
            'win_rate': t['win_rate'],
            'avg_pips': t['avg_pips'],
            'total_pips': t['total_pips'],
            'score': t['score'],
            'num_conditions': 3,
            'timeframes_used': sorted(list(tfs)),
        })

    # Sort by score descending
    all_strategies.sort(key=lambda x: x['score'], reverse=True)

    # WHY: Train-set rankings are unreliable at this search scale (FWER ≈ 1.0
    #      for 6K+ single tests). Re-evaluate every candidate on the unseen
    #      holdout set; keep only those that still pass min_win_rate AND have
    #      positive avg_pips on holdout. This removes overfit rules before they
    #      reach the user or downstream backtesting.
    # CHANGED: April 2026 — held-out validation (audit CRITICAL)
    min_coverage_holdout = max(5, int(min_coverage * 0.5))
    validated_strategies = []
    for strat in all_strategies:
        rule_conds = strat.get('condition_key_tuple', [])
        holdout_result = _evaluate_rule_on_holdout(rule_conds, df_holdout, min_coverage_holdout)
        if holdout_result is None:
            continue  # too few holdout trades to trust
        if holdout_result['win_rate'] < min_win_rate:
            continue  # failed holdout win-rate gate
        if holdout_result['avg_pips'] <= 0:
            continue  # unprofitable on holdout
        # Annotate with holdout stats for transparency
        strat['holdout_win_rate'] = round(holdout_result['win_rate'], 4)
        strat['holdout_avg_pips'] = round(holdout_result['avg_pips'], 2)
        strat['holdout_matches'] = holdout_result['matches']
        validated_strategies.append(strat)
    all_strategies = validated_strategies
    print(f"  After holdout validation: {len(all_strategies)} strategies pass")

    elapsed = time.time() - start_time

    # Build output
    output = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'search_mode': mode,
        'search_time_s': round(elapsed, 1),
        'features_tested': len(feature_cols),
        'thresholds_per_feature': num_thresholds,
        'total_singles_tested': len(masks),
        'total_pairs_tested': len(top_singles) * (len(top_singles) - 1) // 2 if max_conditions >= 2 else 0,
        'total_triples_tested': len(pairs[:100]) * len(singles[:200]) if max_conditions == 3 and mode == "full" else 0,
        'strategies_found': len(all_strategies),
        'min_coverage': min_coverage,
        'min_win_rate': min_win_rate,
        'strategies': all_strategies,
    }

    # Save to file
    with open(DEFAULT_OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2)

    print(f"\n  Saved {len(all_strategies)} strategies")
    print(f"  Output: {DEFAULT_OUTPUT}")
    print(f"  Search completed in {elapsed:.0f}s ({elapsed/60:.1f} min)")

    if progress_callback:
        progress_callback(100, 100, "Search complete")

    print(f"\n{'='*70}")
    print(f"SEARCH COMPLETE")
    print(f"{'='*70}\n")

    return output


def main():
    """Command-line entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Search for profitable trading strategies')
    parser.add_argument('--mode', choices=['quick', 'full'], default='quick',
                       help='Search mode (quick=top 20 features, full=all features)')
    parser.add_argument('--timeframes', nargs='+', choices=['M5', 'M15', 'H1', 'H4', 'D1'],
                       help='Timeframes to include (default: all)')
    parser.add_argument('--max-conditions', type=int, default=2, choices=[1, 2, 3],
                       help='Maximum conditions per rule')
    parser.add_argument('--min-coverage', type=int, default=15,
                       help='Minimum trades a rule must match')
    parser.add_argument('--min-win-rate', type=float, default=0.55,
                       help='Minimum win rate (0-1)')
    parser.add_argument('--num-thresholds', type=int, default=5,
                       help='Thresholds to test per feature')

    args = parser.parse_args()

    def _progress(cur, tot, msg):
        pct = int(cur / max(tot, 1) * 100)
        print(f"  [{pct:3d}%] {msg}")

    search_strategies(
        mode=args.mode,
        timeframe_filter=args.timeframes,
        max_conditions=args.max_conditions,
        min_coverage=args.min_coverage,
        min_win_rate=args.min_win_rate,
        num_thresholds=args.num_thresholds,
        progress_callback=_progress,
    )


if __name__ == '__main__':
    main()
