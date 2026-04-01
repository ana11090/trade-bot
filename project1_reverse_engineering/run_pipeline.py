"""
Run the Project 1 pipeline: Align trades → Compute features.
This produces the feature_matrix.csv that all analysis depends on.

Usage: python run_pipeline.py
"""
import sys
import os
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from step1_align_price import align_all_timeframes
from step2_compute_indicators import compute_features

print("=" * 70)
print("PROJECT 1 — REVERSE ENGINEERING PIPELINE")
print("=" * 70)

start = time.time()

print("\n[STEP 1/2] Aligning trades to candles...\n")
aligned = align_all_timeframes()
if aligned is None:
    print("STEP 1 FAILED — cannot continue")
    sys.exit(1)

print(f"\n[STEP 2/2] Computing indicators...\n")
features = compute_features()
if features is None:
    print("STEP 2 FAILED")
    sys.exit(1)

elapsed = time.time() - start
print(f"\n{'=' * 70}")
print(f"PIPELINE COMPLETE in {elapsed:.0f}s ({elapsed/60:.1f} minutes)")
print(f"Feature matrix: {len(features)} trades × {len(features.columns)} features")
print(f"Output: project1_reverse_engineering/outputs/feature_matrix.csv")
print(f"{'=' * 70}")
