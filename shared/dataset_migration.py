"""
shared/dataset_migration.py

First-launch migration from the legacy flat data/xauusd_*.csv layout
into the new datasets/ registry. Runs automatically from config.py at
import time. Idempotent — safe to call on every launch.

WHY: Users (including the repo owner) already have data/ folders full
     of candle CSVs. We cannot require them to manually move files.
     Migration copies (not moves) the legacy files into
     datasets/xauusd_default/candles/ on first launch, detects Git LFS
     pointer stubs and marks status=candles_missing so Phase B's Build
     button can regenerate them from ticks.
CHANGED: April 2026 — Phase A — new module
"""

import os
import shutil
import logging

from shared import dataset_registry as reg

log = logging.getLogger(__name__)

DEFAULT_DATASET_ID = 'xauusd_default'
DEFAULT_DISPLAY_LABEL = 'XAUUSD (default, migrated from data/)'


def run_migration(project_root, legacy_data_dir, legacy_tick_root):
    """Create datasets/xauusd_default/ if it doesn't exist and copy
    any legacy candle CSVs into it.

    WHY: Copy, not move — for one release cycle the legacy data/ folder
         stays as a fallback. A later phase will delete it after we're
         confident migration is solid.
    CHANGED: April 2026 — Phase A
    """
    # If default dataset already exists, migration was done before — skip.
    if reg.get_dataset(DEFAULT_DATASET_ID) is not None:
        log.debug("migration: %s already registered, skipping", DEFAULT_DATASET_ID)
        if reg.get_active_dataset_id() is None:
            reg.set_active_dataset_id(DEFAULT_DATASET_ID)
        return

    log.info("migration: creating default dataset %s", DEFAULT_DATASET_ID)
    reg.register_dataset(
        dataset_id=DEFAULT_DATASET_ID,
        symbol='XAUUSD',
        display_label=DEFAULT_DISPLAY_LABEL,
        tick_root=legacy_tick_root,
        pip_value=1.0,  # $1/pip/lot per project spec
        contract_spec={'instrument_type': 'metal', 'pip_size': 0.1},
        status='candles_missing',
    )

    # Copy legacy candle files if present, detecting LFS stubs.
    dst_dir = reg.dataset_candles_dir(DEFAULT_DATASET_ID)
    copied = 0
    stub_count = 0

    if os.path.isdir(legacy_data_dir):
        try:
            entries = os.listdir(legacy_data_dir)
        except OSError as e:
            log.warning("migration: cannot list %s: %s", legacy_data_dir, e)
            entries = []

        for fname in entries:
            # WHY: only copy files that look like candle CSVs for this symbol
            # CHANGED: April 2026 — Phase A
            if not fname.lower().startswith('xauusd_') or not fname.endswith('.csv'):
                continue
            src = os.path.join(legacy_data_dir, fname)
            if not os.path.isfile(src):
                continue
            try:
                if reg.is_lfs_stub(src):
                    stub_count += 1
                    log.info("migration: %s is an LFS stub — not copying", fname)
                    continue
                dst = os.path.join(dst_dir, fname)
                shutil.copy2(src, dst)
                copied += 1
            except OSError as e:
                # WHY: per-row try/except per user rule #5
                # CHANGED: April 2026 — Phase A
                log.warning("migration: failed to copy %s: %s", fname, e)
                continue

    log.info(
        "migration: copied %d candle files, skipped %d LFS stubs",
        copied, stub_count,
    )

    # If we actually copied real candles, compute hash and flip to ready.
    if copied > 0 and stub_count == 0:
        candle_hash = reg.compute_candle_hash(dst_dir)
        reg.update_dataset(
            DEFAULT_DATASET_ID,
            status='ready',
            candle_hash=candle_hash,
        )
    else:
        # Status stays candles_missing — Phase B's Build button will fix it.
        log.info(
            "migration: dataset %s marked candles_missing "
            "(use Dataset Manager → Build in the UI)",
            DEFAULT_DATASET_ID,
        )

    reg.set_active_dataset_id(DEFAULT_DATASET_ID)
