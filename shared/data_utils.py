"""
DATA UTILITIES
Shared functions for loading and processing trade and price data.
Used by all project1_reverse_engineering scripts.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone
import pytz


def load_trades_csv(filepath):
    """
    Load trades from CSV file exported from Myfxbook.

    Args:
        filepath: Path to trades_clean.csv

    Returns:
        DataFrame with trades, datetime columns parsed
    """
    print(f"  Loading trades from: {filepath}")

    df = pd.read_csv(filepath)

    # Parse date columns - handle DD/MM/YYYY HH:MM format
    df['Open Date'] = pd.to_datetime(df['Open Date'], format='%d/%m/%Y %H:%M')
    df['Close Date'] = pd.to_datetime(df['Close Date'], format='%d/%m/%Y %H:%M')

    # Rename columns to snake_case for easier coding
    df = df.rename(columns={
        'Open Date': 'open_time',
        'Close Date': 'close_time',
        'Symbol': 'symbol',
        'Action': 'action',
        'Lots': 'lots',
        'SL': 'sl',
        'TP': 'tp',
        'Open Price': 'open_price',
        'Close Price': 'close_price',
        'Pips': 'pips',
        'Profit': 'profit',
        'Duration (DDHHMMSS)': 'duration',
        'Change %': 'change_pct'
    })

    print(f"  Loaded {len(df)} trades. Date range: {df['open_time'].min()} to {df['open_time'].max()}")

    return df


def load_trades_from_state(state_module):
    """
    Load trades from state.loaded_data (Project 0 grid data).

    Args:
        state_module: The state module containing loaded_data

    Returns:
        DataFrame with trades, datetime columns parsed
    """
    if state_module.loaded_data is None:
        raise ValueError("No data loaded in Project 0. Please load trade data first.")

    print(f"  Loading trades from Project 0 grid data...")

    df = state_module.loaded_data.copy()

    # Parse date columns - handle DD/MM/YYYY HH:MM format
    # Column names might already be in proper format or need parsing
    col_names = df.columns.tolist()

    # Handle different possible column name formats
    date_col_map = {}
    for col in col_names:
        col_lower = col.lower().strip()
        if 'open' in col_lower and 'date' in col_lower:
            date_col_map['Open Date'] = col
        elif 'close' in col_lower and 'date' in col_lower:
            date_col_map['Close Date'] = col

    # If columns already have 'Open Date' format, use them
    if 'Open Date' not in date_col_map and 'Open Date' in df.columns:
        date_col_map['Open Date'] = 'Open Date'
    if 'Close Date' not in date_col_map and 'Close Date' in df.columns:
        date_col_map['Close Date'] = 'Close Date'

    # Parse dates
    if 'Open Date' in date_col_map:
        df['Open Date'] = pd.to_datetime(df[date_col_map['Open Date']], format='%d/%m/%Y %H:%M', errors='coerce')
    if 'Close Date' in date_col_map:
        df['Close Date'] = pd.to_datetime(df[date_col_map['Close Date']], format='%d/%m/%Y %H:%M', errors='coerce')

    # Rename columns to snake_case for easier coding
    df = df.rename(columns={
        'Open Date': 'open_time',
        'Close Date': 'close_time',
        'Symbol': 'symbol',
        'Action': 'action',
        'Lots': 'lots',
        'SL': 'sl',
        'TP': 'tp',
        'Open Price': 'open_price',
        'Close Price': 'close_price',
        'Pips': 'pips',
        'Profit': 'profit',
        'Duration (DDHHMMSS)': 'duration',
        'Change %': 'change_pct'
    })

    print(f"  Loaded {len(df)} trades from Project 0 grid.")
    print(f"  Date range: {df['open_time'].min()} to {df['open_time'].max()}")

    return df


def load_ohlcv_csv(filepath, timeframe_name):
    """
    Load OHLCV candle data from CSV file.

    Args:
        filepath: Path to candle data CSV (e.g., '../data/xauusd_H1.csv')
        timeframe_name: Name for logging (e.g., 'H1')

    Returns:
        DataFrame with OHLCV data, timestamp column parsed
    """
    print(f"  Loading {timeframe_name} candle data from: {filepath}")

    df = pd.read_csv(filepath)

    # Parse timestamp column - assume column is named 'timestamp' or 'time' or 'datetime'
    time_col = None
    for col in ['timestamp', 'time', 'datetime', 'Datetime', 'Time']:
        if col in df.columns:
            time_col = col
            break

    if time_col is None:
        raise ValueError(f"No timestamp column found in {filepath}. Columns: {df.columns.tolist()}")

    df[time_col] = pd.to_datetime(df[time_col])
    df = df.rename(columns={time_col: 'timestamp'})

    # Ensure standard OHLCV column names (case-insensitive matching)
    column_mapping = {}
    for col in df.columns:
        col_lower = col.lower()
        if col_lower == 'open':
            column_mapping[col] = 'open'
        elif col_lower == 'high':
            column_mapping[col] = 'high'
        elif col_lower == 'low':
            column_mapping[col] = 'low'
        elif col_lower == 'close':
            column_mapping[col] = 'close'
        elif col_lower == 'volume':
            column_mapping[col] = 'volume'

    df = df.rename(columns=column_mapping)

    # Sort by timestamp
    df = df.sort_values('timestamp').reset_index(drop=True)

    print(f"  Loaded {len(df)} {timeframe_name} candles. Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")

    return df


def convert_to_utc(df, timestamp_col, source_timezone='EET'):
    """
    Convert timestamps from source timezone to UTC.

    Args:
        df: DataFrame containing timestamps
        timestamp_col: Name of the timestamp column
        source_timezone: Source timezone string (e.g., 'EET', 'GMT', 'US/Eastern')

    Returns:
        DataFrame with timestamps converted to UTC (timezone-aware)
    """
    print(f"  Converting {timestamp_col} from {source_timezone} to UTC...")

    # If already timezone-aware, convert to UTC
    if df[timestamp_col].dt.tz is not None:
        df[timestamp_col] = df[timestamp_col].dt.tz_convert('UTC')
    else:
        # Localize to source timezone, then convert to UTC
        # nonexistent/ambiguous='NaT' avoids crashes on DST boundary times
        source_tz = pytz.timezone(source_timezone)
        df[timestamp_col] = (df[timestamp_col]
                             .dt.tz_localize(source_tz, ambiguous='NaT', nonexistent='NaT')
                             .dt.tz_convert('UTC'))

    return df


def align_trades_to_candles(trades_df, candles_df, lookback_candles=200):
    """
    For each trade, find the last closed candle before trade open time.
    Also extract the lookback window of candles for indicator computation.

    Args:
        trades_df: DataFrame with trades (must have 'open_time' column in UTC)
        candles_df: DataFrame with candles (must have 'timestamp' column in UTC)
        lookback_candles: Number of candles to extract before each trade

    Returns:
        Tuple of (aligned_trades_df, trades_dropped_count)
        aligned_trades_df has 'aligned_candle_idx' column added
    """
    print(f"  Aligning {len(trades_df)} trades to nearest candle...")

    # Drop rows where open_time could not be parsed (NaT) — merge_asof rejects nulls
    null_count = trades_df['open_time'].isna().sum()
    if null_count > 0:
        print(f"  WARNING: Dropping {null_count} trades with unparseable open_time (NaT)")
        trades_df = trades_df.dropna(subset=['open_time']).copy()

    # Ensure both are sorted by time
    trades_df = trades_df.sort_values('open_time').reset_index(drop=True)
    candles_df = candles_df.sort_values('timestamp').reset_index(drop=True)

    # Use merge_asof to find the last candle before each trade
    # direction='backward' means: find the most recent candle where candle_time <= trade_time
    aligned = pd.merge_asof(
        trades_df,
        candles_df[['timestamp']].reset_index().rename(columns={'index': 'aligned_candle_idx'}),
        left_on='open_time',
        right_on='timestamp',
        direction='backward'
    )

    # Drop trades where we don't have enough lookback candles
    # If aligned_candle_idx < lookback_candles, we can't compute indicators
    initial_count = len(aligned)
    aligned = aligned[aligned['aligned_candle_idx'] >= lookback_candles].copy()
    dropped_count = initial_count - len(aligned)

    if dropped_count > 0:
        print(f"  WARNING: Dropped {dropped_count} trades due to insufficient lookback candles (need {lookback_candles})")

    print(f"  Alignment complete. {len(aligned)} trades aligned.")

    return aligned, dropped_count


def verify_alignment(trades_df, candles_df, tolerance_pips=5.0):
    """
    Verify that trade open prices fall within the candle's high/low range.

    Args:
        trades_df: Aligned trades with 'aligned_candle_idx' column
        candles_df: Candle data
        tolerance_pips: How many pips outside the candle range is acceptable

    Returns:
        Number of trades that failed verification
    """
    print(f"  Verifying alignment: checking trade open prices vs candle ranges...")

    misaligned_count = 0

    for idx, trade in trades_df.iterrows():
        candle_idx = int(trade['aligned_candle_idx'])
        candle = candles_df.iloc[candle_idx]

        trade_price = trade['open_price']
        candle_high = candle['high']
        candle_low = candle['low']

        # Check if trade price is within candle range (with tolerance)
        if trade_price < (candle_low - tolerance_pips) or trade_price > (candle_high + tolerance_pips):
            misaligned_count += 1

    if misaligned_count > 0:
        print(f"  WARNING: {misaligned_count} trades have open prices outside candle range (tolerance: {tolerance_pips} pips)")
    else:
        print(f"  Alignment verified: all trades within candle ranges")

    return misaligned_count


def get_candle_lookback(candles_df, candle_idx, lookback_count):
    """
    Extract lookback window of candles ending at candle_idx (inclusive).

    Args:
        candles_df: Full candle DataFrame
        candle_idx: Index of the reference candle
        lookback_count: Number of candles to extract (including reference candle)

    Returns:
        DataFrame slice of candles
    """
    start_idx = max(0, candle_idx - lookback_count + 1)
    end_idx = candle_idx + 1

    return candles_df.iloc[start_idx:end_idx].copy()


def save_dataframe(df, filepath, description="data"):
    """
    Save DataFrame to CSV with logging.

    Args:
        df: DataFrame to save
        filepath: Output path
        description: Description for logging
    """
    df.to_csv(filepath, index=False)
    print(f"  Saved {description}: {filepath}")
