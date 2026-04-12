"""
Configuration file for trade-bot
"""

import os
from datetime import datetime, timezone

# Data paths
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_ROOT = os.path.join(PROJECT_ROOT, 'xauusd')
TICK_DATA_PATH = os.path.join(DATA_ROOT, 'ticks')
TIMEFRAME_DATA_PATH = os.path.join(DATA_ROOT, 'timeframes')

# Local data cache (for small aggregated files)
LOCAL_DATA_PATH = os.path.join(os.path.dirname(__file__), 'data')


def ensure_data_dirs():
    """Create data directories if they don't exist.

    WHY: Old code ran os.makedirs() at module import time as a side
         effect. Importing config wrote to disk, which made tests
         harder and created dirs on read-only machines. This function
         lets callers explicitly opt in.
    CHANGED: April 2026 — explicit dir creation (audit MED #69)
    """
    os.makedirs(TICK_DATA_PATH, exist_ok=True)
    os.makedirs(TIMEFRAME_DATA_PATH, exist_ok=True)
    os.makedirs(LOCAL_DATA_PATH, exist_ok=True)


# WHY: Preserve old import-time behavior so existing scripts that
#      assume the dirs exist don't break. New callers should call
#      ensure_data_dirs() explicitly.
# CHANGED: April 2026 — wrapped in function but still called at
#          import for backward compat (audit MED #69)
ensure_data_dirs()

# Data settings
SYMBOL = 'XAUUSD'
DEFAULT_TIMEFRAMES = ['M1', 'M5', 'M15', 'H1', 'H4', 'D1', 'W1', 'MN']

# Date range
# WHY: Old version had END_DATE = '2026-03-30' hardcoded. The pipeline
#      silently lagged the calendar — every day past that date,
#      backtests were missing one more day of data without warning.
#      Default to today's date so the pipeline always covers up to
#      "now". Users who want a fixed end date can override after import.
# CHANGED: April 2026 — dynamic END_DATE (audit LOW #70)
START_DATE = '2005-01-01'
END_DATE = datetime.now(timezone.utc).strftime('%Y-%m-%d')

print(f"Config loaded: Tick data at {TICK_DATA_PATH}")

# -------- Phase A: dataset registry -----------------------------------
# WHY: Introduces first-class Dataset abstraction. Active dataset ID
#      replaces the single global SYMBOL as the source of truth for
#      which data the pipeline is operating on. Phase C will plumb
#      this through step1/step2/refiner/validator.
# CHANGED: April 2026 — Phase A
DATASETS_ROOT = os.path.join(PROJECT_ROOT, 'datasets')

from shared import dataset_registry as _dataset_registry  # noqa: E402
from shared import dataset_migration as _dataset_migration  # noqa: E402

_dataset_registry.set_datasets_root(DATASETS_ROOT)

try:
    _dataset_migration.run_migration(
        project_root=PROJECT_ROOT,
        legacy_data_dir=LOCAL_DATA_PATH,
        legacy_tick_root=TICK_DATA_PATH,
    )
except Exception as _mig_err:
    # WHY: Never let migration break app startup. Log and continue.
    # CHANGED: April 2026 — Phase A
    import logging as _logging
    _logging.getLogger(__name__).exception(
        "Phase A migration failed: %s", _mig_err,
    )

ACTIVE_DATASET_ID = _dataset_registry.get_active_dataset_id()
print(f"Active dataset: {ACTIVE_DATASET_ID}")
