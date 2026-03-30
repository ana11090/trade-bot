"""
PROJECT 2 - BACKTESTING ENGINE
Simulates trades based on rules discovered in Project 1
"""

import pandas as pd
import numpy as np
import sys
import os
from datetime import datetime
import re

# Add parent directory to path for shared utilities
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from shared import indicator_utils

# ============================================================
# CONFIGURATION - change these values, nothing else
# ============================================================
RULES_FILE = '../project1_reverse_engineering/outputs/scenario_H1/rules_report_H1.txt'
PRICE_DATA_FILE = '../data/xauusd_H1.csv'
ORIGINAL_TRADES_FILE = '../project0_data_pipeline/trades_clean.csv'
OUTPUT_FOLDER = './outputs/'
WINNING_SCENARIO = 'H1'  # which scenario won in Project 1

# Date ranges
INSAMPLE_START = '2022-01-01'  # first date of bot's trade history
INSAMPLE_END = '2023-12-31'    # last date of bot's trade history
OUTSAMPLE_START = '2024-01-01'  # first date of fresh data
OUTSAMPLE_END = '2024-12-31'    # last date of fresh data

# Capital and risk
STARTING_CAPITAL = 10000.0  # USD - starting account balance
RISK_PER_TRADE_PCT = 0.01   # 1% of current balance per trade
LOT_SIZE_CALCULATION = 'DYNAMIC'  # 'DYNAMIC' or 'FIXED'
FIXED_LOT_SIZE = 0.01  # only used if DYNAMIC = False

# Stop loss and take profit (ATR multipliers)
SL_ATR_MULTIPLIER = 1.5
TP1_ATR_MULTIPLIER = 1.5  # 50% of position closed at TP1
TP2_ATR_MULTIPLIER = 3.0  # remaining 50% closed at TP2
HARD_CLOSE_HOUR_UTC = 21  # close all trades at 21:00 UTC

# Costs
COMMISSION_PER_LOT = 4.0  # USD round trip - Leveraged prop firm
SPREAD_PIPS = 0.3  # estimated spread in pips
PIP_VALUE_PER_LOT = 10.0  # USD per pip per standard lot XAUUSD

# Engine settings
MAX_ONE_TRADE_OPEN = True  # never open second trade while one is open
WARMUP_CANDLES = 200  # skip first N candles for indicator warmup
SAME_CANDLE_SL_RULE = 'LOSS'  # if SL and TP hit same candle: LOSS or WIN


class Rule:
    """Represents a trading rule parsed from rules_report.txt"""
    def __init__(self, rule_id, confidence, covers, direction):
        self.rule_id = rule_id
        self.confidence = confidence
        self.covers = covers
        self.direction = direction  # 'BUY' or 'SELL'
        self.conditions = []  # list of (feature, operator, value) tuples
        self.hard_close_hour = None

    def add_condition(self, feature, operator, value):
        """Add a condition to this rule"""
        self.conditions.append((feature, operator, value))

    def check_conditions(self, indicators_row):
        """Check if all conditions are met for this candle"""
        for feature, operator, value in self.conditions:
            if feature not in indicators_row:
                return False

            indicator_value = indicators_row[feature]

            # Handle NaN
            if pd.isna(indicator_value):
                return False

            # Evaluate condition
            if operator == '<':
                if not (indicator_value < value):
                    return False
            elif operator == '<=':
                if not (indicator_value <= value):
                    return False
            elif operator == '>':
                if not (indicator_value > value):
                    return False
            elif operator == '>=':
                if not (indicator_value >= value):
                    return False
            elif operator == '==':
                if not (abs(indicator_value - value) < 0.001):  # float equality
                    return False
            elif operator == '!=':
                if not (abs(indicator_value - value) >= 0.001):
                    return False

        return True


def parse_rules_file(rules_file_path):
    """Parse rules from rules_report.txt"""
    print(f"[BACKTEST ENGINE] Loading rules from: {rules_file_path}")

    if not os.path.exists(rules_file_path):
        raise FileNotFoundError(f"Rules file not found: {rules_file_path}")

    with open(rules_file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    rules = []
    current_rule = None

    lines = content.split('\n')
    for line in lines:
        line = line.strip()

        # Parse rule header
        if line.startswith('RULE #'):
            # Extract rule number and metadata
            match = re.search(r'RULE #(\d+).*confidence:\s*([\d.]+)%.*covers:\s*(\d+).*direction:\s*(\w+)', line)
            if match:
                rule_id = int(match.group(1))
                confidence = float(match.group(2))
                covers = int(match.group(3))
                direction = match.group(4)

                current_rule = Rule(rule_id, confidence, covers, direction)
                rules.append(current_rule)

        # Parse conditions
        elif line.startswith('CONDITION:') and current_rule:
            # Extract feature, operator, value
            condition_text = line.replace('CONDITION:', '').strip()

            # Try different operators
            for op in ['<=', '>=', '==', '!=', '<', '>']:
                if op in condition_text:
                    parts = condition_text.split(op)
                    if len(parts) == 2:
                        feature = parts[0].strip()
                        value = float(parts[1].strip())
                        current_rule.add_condition(feature, op, value)
                        break

        # Parse hard close hour
        elif line.startswith('HARD_CLOSE_HOUR:') and current_rule:
            match = re.search(r'HARD_CLOSE_HOUR:\s*(\d+)', line)
            if match:
                current_rule.hard_close_hour = int(match.group(1))

    print(f"[BACKTEST ENGINE] Parsed {len(rules)} rules successfully.")
    return rules


def calculate_lot_size(balance, risk_pct, sl_distance_usd):
    """Calculate lot size based on risk percentage"""
    if LOT_SIZE_CALCULATION == 'FIXED':
        return FIXED_LOT_SIZE

    # Dynamic: risk X% of balance
    risk_amount = balance * risk_pct

    # Prevent division by zero
    if sl_distance_usd <= 0:
        return FIXED_LOT_SIZE

    lot_size = risk_amount / sl_distance_usd

    # Clamp to reasonable range (0.01 to 10.0 lots)
    lot_size = max(0.01, min(10.0, lot_size))

    return round(lot_size, 2)


def simulate_trade(rule, entry_candle, indicators_df, candles_df, balance, start_idx):
    """
    Simulate a single trade from entry to exit
    Returns: trade dictionary with all details
    """
    entry_price = entry_candle['close']
    entry_time = entry_candle['timestamp']
    atr_value = indicators_df.loc[entry_candle.name, 'atr_14']

    # Calculate SL and TP levels
    if rule.direction == 'BUY':
        sl_price = entry_price - (atr_value * SL_ATR_MULTIPLIER)
        tp1_price = entry_price + (atr_value * TP1_ATR_MULTIPLIER)
        tp2_price = entry_price + (atr_value * TP2_ATR_MULTIPLIER)
    else:  # SELL
        sl_price = entry_price + (atr_value * SL_ATR_MULTIPLIER)
        tp1_price = entry_price - (atr_value * TP1_ATR_MULTIPLIER)
        tp2_price = entry_price - (atr_value * TP2_ATR_MULTIPLIER)

    # Calculate lot size
    sl_distance_pips = abs(entry_price - sl_price) / 0.01  # XAUUSD: 1 pip = $0.01
    sl_distance_usd = sl_distance_pips * PIP_VALUE_PER_LOT * FIXED_LOT_SIZE
    lot_size = calculate_lot_size(balance, RISK_PER_TRADE_PCT, sl_distance_usd)

    # Track trade state
    position_size = lot_size
    exit_price = None
    exit_time = None
    exit_reason = None

    # Scan forward through candles
    for i in range(start_idx + 1, len(candles_df)):
        candle = candles_df.iloc[i]

        # Check hard close hour
        if rule.hard_close_hour is not None:
            if candle['timestamp'].hour >= rule.hard_close_hour:
                exit_price = candle['close']
                exit_time = candle['timestamp']
                exit_reason = 'HARD_CLOSE'
                break

        # Check if SL or TP hit
        if rule.direction == 'BUY':
            # Check SL
            if candle['low'] <= sl_price:
                exit_price = sl_price
                exit_time = candle['timestamp']
                exit_reason = 'STOP_LOSS'

                # Same candle SL/TP check
                if candle['high'] >= tp1_price and SAME_CANDLE_SL_RULE == 'WIN':
                    exit_price = tp1_price
                    exit_reason = 'TAKE_PROFIT_1'

                break

            # Check TP2
            elif candle['high'] >= tp2_price:
                exit_price = tp2_price
                exit_time = candle['timestamp']
                exit_reason = 'TAKE_PROFIT_2'
                break

            # Check TP1
            elif candle['high'] >= tp1_price:
                # Close 50% at TP1, let 50% run to TP2
                # For simplicity, we'll record as TP1 and adjust profit
                exit_price = tp1_price
                exit_time = candle['timestamp']
                exit_reason = 'TAKE_PROFIT_1'
                position_size = lot_size * 0.5  # only 50% closed
                break

        else:  # SELL
            # Check SL
            if candle['high'] >= sl_price:
                exit_price = sl_price
                exit_time = candle['timestamp']
                exit_reason = 'STOP_LOSS'

                # Same candle SL/TP check
                if candle['low'] <= tp1_price and SAME_CANDLE_SL_RULE == 'WIN':
                    exit_price = tp1_price
                    exit_reason = 'TAKE_PROFIT_1'

                break

            # Check TP2
            elif candle['low'] <= tp2_price:
                exit_price = tp2_price
                exit_time = candle['timestamp']
                exit_reason = 'TAKE_PROFIT_2'
                break

            # Check TP1
            elif candle['low'] <= tp1_price:
                exit_price = tp1_price
                exit_time = candle['timestamp']
                exit_reason = 'TAKE_PROFIT_1'
                position_size = lot_size * 0.5
                break

    # If no exit found, force close at last candle
    if exit_price is None:
        last_candle = candles_df.iloc[-1]
        exit_price = last_candle['close']
        exit_time = last_candle['timestamp']
        exit_reason = 'END_OF_DATA'

    # Calculate P&L
    if rule.direction == 'BUY':
        pips = (exit_price - entry_price) / 0.01
    else:
        pips = (entry_price - exit_price) / 0.01

    gross_profit = pips * PIP_VALUE_PER_LOT * position_size
    spread_cost = SPREAD_PIPS * PIP_VALUE_PER_LOT * position_size
    commission = COMMISSION_PER_LOT * position_size
    net_profit = gross_profit - spread_cost - commission

    # Build trade record
    trade = {
        'trade_id': None,  # will be set later
        'rule_id': rule.rule_id,
        'entry_time': entry_time,
        'exit_time': exit_time,
        'direction': rule.direction,
        'entry_price': entry_price,
        'exit_price': exit_price,
        'sl_price': sl_price,
        'tp1_price': tp1_price,
        'tp2_price': tp2_price,
        'lot_size': lot_size,
        'position_closed': position_size,
        'pips': pips,
        'gross_profit': gross_profit,
        'spread_cost': spread_cost,
        'commission': commission,
        'net_profit': net_profit,
        'exit_reason': exit_reason,
        'balance_before': balance,
        'balance_after': balance + net_profit
    }

    return trade


def run_backtest(candles_df, indicators_df, rules, period_start, period_end, period_name):
    """Run backtest for a specific period"""
    print(f"[BACKTEST ENGINE] Starting {period_name} backtest ({period_start} to {period_end})...")

    # Filter candles to period
    period_candles = candles_df[
        (candles_df['timestamp'] >= period_start) &
        (candles_df['timestamp'] <= period_end)
    ].reset_index(drop=True)

    if len(period_candles) == 0:
        print(f"[BACKTEST ENGINE] WARNING: No candles found in {period_name} period")
        return []

    # Initialize
    balance = STARTING_CAPITAL
    trades = []
    open_trade = None

    # Main loop
    for i in range(WARMUP_CANDLES, len(period_candles)):
        candle = period_candles.iloc[i]
        candle_idx = candle.name

        # Skip if we have an open trade
        if MAX_ONE_TRADE_OPEN and open_trade is not None:
            continue

        # Check each rule
        for rule in rules:
            # Get indicator values for this candle
            if candle_idx not in indicators_df.index:
                continue

            indicators_row = indicators_df.loc[candle_idx]

            # Check if rule conditions are met
            if rule.check_conditions(indicators_row):
                # Fire trade!
                trade = simulate_trade(rule, candle, indicators_df, period_candles, balance, i)

                if trade:
                    trade['trade_id'] = len(trades) + 1
                    trades.append(trade)

                    # Update balance
                    balance = trade['balance_after']

                    # If max one trade, mark as open
                    if MAX_ONE_TRADE_OPEN:
                        open_trade = trade

                    # Print progress
                    if len(trades) % 10 == 0:
                        print(f"[BACKTEST ENGINE]   Trade #{len(trades)}: {trade['direction']} "
                              f"at {trade['entry_price']:.2f}, exit {trade['exit_reason']}, "
                              f"P&L: ${trade['net_profit']:.2f}")

                    break  # Only one rule can fire per candle

        # Clear open trade flag if trade is closed
        if open_trade and candle['timestamp'] >= open_trade['exit_time']:
            open_trade = None

    # Summary
    if len(trades) > 0:
        winning_trades = [t for t in trades if t['net_profit'] > 0]
        win_rate = len(winning_trades) / len(trades) * 100
        total_profit = sum(t['net_profit'] for t in trades)

        gross_wins = sum(t['gross_profit'] for t in trades if t['gross_profit'] > 0)
        gross_losses = abs(sum(t['gross_profit'] for t in trades if t['gross_profit'] < 0))
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else 0

        print(f"[BACKTEST ENGINE] {period_name} complete: {len(trades)} trades. "
              f"Win rate: {win_rate:.1f}%. Profit factor: {profit_factor:.2f}. "
              f"Net P&L: ${total_profit:.2f}")
    else:
        print(f"[BACKTEST ENGINE] {period_name} complete: 0 trades (no signals)")

    return trades


def main():
    """Main entry point"""
    print("=" * 60)
    print("PROJECT 2 - BACKTESTING ENGINE")
    print("=" * 60)

    # Create output folder
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    # Load price data
    print(f"[BACKTEST ENGINE] Loading price data: {PRICE_DATA_FILE}")

    if not os.path.exists(PRICE_DATA_FILE):
        print(f"ERROR: Price data file not found: {PRICE_DATA_FILE}")
        print("Please download price data first using download_data_mt5.py")
        return

    candles_df = pd.read_csv(PRICE_DATA_FILE)
    candles_df['timestamp'] = pd.to_datetime(candles_df['timestamp'])

    print(f"[BACKTEST ENGINE] Loaded {len(candles_df)} {WINNING_SCENARIO} candles "
          f"({candles_df['timestamp'].min()} to {candles_df['timestamp'].max()})")

    # Parse rules
    rules = parse_rules_file(RULES_FILE)

    # Compute indicators on full dataset
    print(f"[BACKTEST ENGINE] Computing indicators on full dataset...")
    indicators_df = indicator_utils.compute_all_indicators(candles_df)
    print(f"[BACKTEST ENGINE] Indicators computed: {len(indicators_df.columns)} features")

    # Run in-sample backtest
    insample_trades = run_backtest(
        candles_df, indicators_df, rules,
        INSAMPLE_START, INSAMPLE_END, 'IN-SAMPLE'
    )

    # Run out-of-sample backtest
    outsample_trades = run_backtest(
        candles_df, indicators_df, rules,
        OUTSAMPLE_START, OUTSAMPLE_END, 'OUT-OF-SAMPLE'
    )

    # Save trade logs
    if len(insample_trades) > 0:
        insample_df = pd.DataFrame(insample_trades)
        insample_path = os.path.join(OUTPUT_FOLDER, 'trade_log_insample.csv')
        insample_df.to_csv(insample_path, index=False)
        print(f"[BACKTEST ENGINE] Saved: {insample_path}")

    if len(outsample_trades) > 0:
        outsample_df = pd.DataFrame(outsample_trades)
        outsample_path = os.path.join(OUTPUT_FOLDER, 'trade_log_outsample.csv')
        outsample_df.to_csv(outsample_path, index=False)
        print(f"[BACKTEST ENGINE] Saved: {outsample_path}")

    print("=" * 60)
    print("BACKTEST ENGINE COMPLETE")
    print("=" * 60)
    print(f"Next step: Run compute_stats.py to calculate performance metrics")


if __name__ == '__main__':
    main()
