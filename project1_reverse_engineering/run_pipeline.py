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

# CHANGED: April 2026 — UI-safe logging (Phase 19d)
from shared.logging_setup import get_logger
log = get_logger(__name__)

log.info("=" * 70)
log.info("PROJECT 1 — REVERSE ENGINEERING PIPELINE")
log.info("=" * 70)

start = time.time()

log.info("\n[STEP 1/2] Aligning trades to candles...\n")
aligned = align_all_timeframes()
if aligned is None:
    log.info("STEP 1 FAILED — cannot continue")
    sys.exit(1)

log.info(f"\n[STEP 2/2] Computing indicators...\n")
features = compute_features()
if features is None:
    log.info("STEP 2 FAILED")
    sys.exit(1)

elapsed = time.time() - start
log.info(f"\n{'=' * 70}")
log.info(f"PIPELINE COMPLETE in {elapsed:.0f}s ({elapsed/60:.1f} minutes)")
log.info(f"Feature matrix: {len(features)} trades × {len(features.columns)} features")
log.info(f"Output: project1_reverse_engineering/outputs/feature_matrix.csv")
log.info(f"{'=' * 70}")
