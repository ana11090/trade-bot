"""
Configuration file for trade-bot
"""

import os

# Data paths
DATA_ROOT = r'D:\traiding data\xauusd'
TICK_DATA_PATH = os.path.join(DATA_ROOT, 'ticks')
TIMEFRAME_DATA_PATH = os.path.join(DATA_ROOT, 'timeframes')

# Local data cache (for small aggregated files)
LOCAL_DATA_PATH = os.path.join(os.path.dirname(__file__), 'data')

# Ensure directories exist
os.makedirs(TICK_DATA_PATH, exist_ok=True)
os.makedirs(TIMEFRAME_DATA_PATH, exist_ok=True)
os.makedirs(LOCAL_DATA_PATH, exist_ok=True)

# Data settings
SYMBOL = 'XAUUSD'
DEFAULT_TIMEFRAMES = ['M1', 'M5', 'M15', 'H1', 'H4', 'D1', 'W1', 'MN']

# Date range
START_DATE = '2005-01-01'
END_DATE = '2026-03-30'

print(f"Config loaded: Tick data at {TICK_DATA_PATH}")
