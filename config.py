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
