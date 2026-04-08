"""
PROJECT 2 - COMPUTE STATISTICS
Calculates performance metrics from backtest trade logs
"""

import pandas as pd
import numpy as np
import os
from datetime import datetime

# Paths
INPUT_FOLDER = './outputs/'
INSAMPLE_TRADES = os.path.join(INPUT_FOLDER, 'trade_log_insample.csv')
OUTSAMPLE_TRADES = os.path.join(INPUT_FOLDER, 'trade_log_outsample.csv')

OUTPUT_STATS_SUMMARY = os.path.join(INPUT_FOLDER, 'stats_summary.csv')
OUTPUT_MONTHLY_STATS = os.path.join(INPUT_FOLDER, 'monthly_stats.csv')
OUTPUT_DAILY_STATS = os.path.join(INPUT_FOLDER, 'daily_stats.csv')
OUTPUT_HOURLY_STATS = os.path.join(INPUT_FOLDER, 'hourly_stats.csv')
OUTPUT_DOW_STATS = os.path.join(INPUT_FOLDER, 'dow_stats.csv')


def calculate_summary_stats(trades_df, period_name, starting_capital=10000.0):
    """Calculate headline statistics for a trade log"""
    if len(trades_df) == 0:
        return {
            'period': period_name,
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate_pct': 0,
            'total_profit': 0,
            'total_loss': 0,
            'net_profit': 0,
            'profit_factor': 0,
            'avg_win': 0,
            'avg_loss': 0,
            'largest_win': 0,
            'largest_loss': 0,
            'max_consecutive_wins': 0,
            'max_consecutive_losses': 0,
            'max_drawdown_pct': 0,
            'sharpe_ratio': 0,
            'total_pips': 0,
            'avg_pips_per_trade': 0,
            'final_balance': starting_capital,
            'return_pct': 0
        }

    # Basic counts
    total_trades = len(trades_df)
    winning_trades = len(trades_df[trades_df['net_profit'] > 0])
    losing_trades = len(trades_df[trades_df['net_profit'] < 0])
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

    # Profit metrics
    wins = trades_df[trades_df['net_profit'] > 0]['net_profit']
    losses = trades_df[trades_df['net_profit'] < 0]['net_profit']

    total_profit = wins.sum() if len(wins) > 0 else 0
    total_loss = abs(losses.sum()) if len(losses) > 0 else 0
    net_profit = total_profit - total_loss

    # WHY: Old code returned 0 when there were no losing trades, which
    #      meant lossless strategies got ranked at the BOTTOM instead
    #      of the top when sorting by profit_factor. Return 99.99 as a
    #      sentinel (matches strategy_backtester._safe_pf convention).
    #      99.99 is "basically infinity" for display purposes and
    #      sorts correctly. Zero profit AND zero loss → genuine 0.
    # CHANGED: April 2026 — fix lossless profit_factor (audit family #6)
    if total_loss > 0:
        profit_factor = total_profit / total_loss
    elif total_profit > 0:
        profit_factor = 99.99  # sentinel for "effectively infinite"
    else:
        profit_factor = 0.0    # genuinely zero (no trades / flat)

    avg_win = wins.mean() if len(wins) > 0 else 0
    avg_loss = losses.mean() if len(losses) > 0 else 0

    largest_win = wins.max() if len(wins) > 0 else 0
    largest_loss = losses.min() if len(losses) > 0 else 0

    # Consecutive wins/losses
    trades_df['is_win'] = trades_df['net_profit'] > 0
    consecutive_wins = 0
    consecutive_losses = 0
    max_consecutive_wins = 0
    max_consecutive_losses = 0

    for is_win in trades_df['is_win']:
        if is_win:
            consecutive_wins += 1
            consecutive_losses = 0
            max_consecutive_wins = max(max_consecutive_wins, consecutive_wins)
        else:
            consecutive_losses += 1
            consecutive_wins = 0
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)

    # Drawdown calculation
    trades_df = trades_df.sort_values('entry_time')
    trades_df['cumulative_profit'] = trades_df['net_profit'].cumsum()
    trades_df['running_balance'] = starting_capital + trades_df['cumulative_profit']
    trades_df['peak_balance'] = trades_df['running_balance'].cummax()
    trades_df['drawdown'] = trades_df['running_balance'] - trades_df['peak_balance']
    trades_df['drawdown_pct'] = (trades_df['drawdown'] / trades_df['peak_balance']) * 100

    max_drawdown_pct = abs(trades_df['drawdown_pct'].min())

    # Sharpe ratio (annualized)
    if len(trades_df) > 1:
        daily_returns = trades_df.groupby(trades_df['entry_time'].dt.date)['net_profit'].sum()
        if daily_returns.std() > 0:
            sharpe_ratio = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)
        else:
            sharpe_ratio = 0
    else:
        sharpe_ratio = 0

    # Pips
    total_pips = trades_df['pips'].sum()
    avg_pips_per_trade = trades_df['pips'].mean()

    # Final balance and return
    final_balance = starting_capital + net_profit
    return_pct = (net_profit / starting_capital) * 100

    return {
        'period': period_name,
        'total_trades': total_trades,
        'winning_trades': winning_trades,
        'losing_trades': losing_trades,
        'win_rate_pct': win_rate,
        'total_profit': total_profit,
        'total_loss': total_loss,
        'net_profit': net_profit,
        'profit_factor': profit_factor,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'largest_win': largest_win,
        'largest_loss': largest_loss,
        'max_consecutive_wins': max_consecutive_wins,
        'max_consecutive_losses': max_consecutive_losses,
        'max_drawdown_pct': max_drawdown_pct,
        'sharpe_ratio': sharpe_ratio,
        'total_pips': total_pips,
        'avg_pips_per_trade': avg_pips_per_trade,
        'final_balance': final_balance,
        'return_pct': return_pct
    }


def calculate_monthly_stats(trades_df, period_name):
    """Calculate month-by-month statistics"""
    if len(trades_df) == 0:
        return pd.DataFrame()

    trades_df['entry_time'] = pd.to_datetime(trades_df['entry_time'])
    trades_df['year_month'] = trades_df['entry_time'].dt.to_period('M')

    monthly = trades_df.groupby('year_month').agg({
        'trade_id': 'count',
        'net_profit': ['sum', 'mean'],
        'pips': 'sum'
    }).reset_index()

    monthly.columns = ['year_month', 'trade_count', 'net_profit', 'avg_profit_per_trade', 'total_pips']
    monthly['period'] = period_name
    monthly['year_month'] = monthly['year_month'].astype(str)

    # Add win rate
    win_rate_by_month = trades_df[trades_df['net_profit'] > 0].groupby('year_month').size()
    total_by_month = trades_df.groupby('year_month').size()
    monthly['win_rate_pct'] = (win_rate_by_month / total_by_month * 100).values

    return monthly


def calculate_daily_stats(trades_df, period_name):
    """Calculate day-by-day statistics"""
    if len(trades_df) == 0:
        return pd.DataFrame()

    trades_df['entry_time'] = pd.to_datetime(trades_df['entry_time'])
    trades_df['date'] = trades_df['entry_time'].dt.date

    daily = trades_df.groupby('date').agg({
        'trade_id': 'count',
        'net_profit': 'sum',
        'pips': 'sum'
    }).reset_index()

    daily.columns = ['date', 'trade_count', 'net_profit', 'total_pips']
    daily['period'] = period_name

    return daily


def calculate_hourly_stats(trades_df, period_name):
    """Calculate hour-by-hour statistics (UTC)"""
    if len(trades_df) == 0:
        return pd.DataFrame()

    trades_df['entry_time'] = pd.to_datetime(trades_df['entry_time'])
    trades_df['hour'] = trades_df['entry_time'].dt.hour

    hourly = trades_df.groupby('hour').agg({
        'trade_id': 'count',
        'net_profit': 'sum',
        'pips': 'sum'
    }).reset_index()

    hourly.columns = ['hour', 'trade_count', 'net_profit', 'total_pips']
    hourly['period'] = period_name

    return hourly


def calculate_dow_stats(trades_df, period_name):
    """Calculate day-of-week statistics"""
    if len(trades_df) == 0:
        return pd.DataFrame()

    trades_df['entry_time'] = pd.to_datetime(trades_df['entry_time'])
    trades_df['day_of_week'] = trades_df['entry_time'].dt.day_name()

    dow = trades_df.groupby('day_of_week').agg({
        'trade_id': 'count',
        'net_profit': ['sum', 'mean'],
        'pips': 'sum'
    }).reset_index()

    dow.columns = ['day_of_week', 'trade_count', 'net_profit', 'avg_profit_per_trade', 'total_pips']
    dow['period'] = period_name

    # Calculate win rate
    wins_by_dow = trades_df[trades_df['net_profit'] > 0].groupby('day_of_week').size()
    total_by_dow = trades_df.groupby('day_of_week').size()
    dow['win_rate_pct'] = (wins_by_dow / total_by_dow * 100).values

    # Order by day of week
    day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    dow['day_of_week'] = pd.Categorical(dow['day_of_week'], categories=day_order, ordered=True)
    dow = dow.sort_values('day_of_week')

    return dow


def main():
    """Main entry point"""
    print("=" * 60)
    print("PROJECT 2 - COMPUTE STATISTICS")
    print("=" * 60)

    # Load trade logs
    print(f"[COMPUTE STATS] Loading trade logs...")

    insample_df = pd.DataFrame()
    outsample_df = pd.DataFrame()

    if os.path.exists(INSAMPLE_TRADES):
        insample_df = pd.read_csv(INSAMPLE_TRADES)
        print(f"[COMPUTE STATS] Loaded in-sample: {len(insample_df)} trades")

    if os.path.exists(OUTSAMPLE_TRADES):
        outsample_df = pd.read_csv(OUTSAMPLE_TRADES)
        print(f"[COMPUTE STATS] Loaded out-of-sample: {len(outsample_df)} trades")

    if len(insample_df) == 0 and len(outsample_df) == 0:
        print("[COMPUTE STATS] ERROR: No trade logs found. Run backtest_engine.py first.")
        return

    # Calculate summary stats
    print(f"[COMPUTE STATS] Calculating summary statistics...")
    summary_stats = []

    if len(insample_df) > 0:
        insample_stats = calculate_summary_stats(insample_df, 'IN-SAMPLE')
        summary_stats.append(insample_stats)

    if len(outsample_df) > 0:
        outsample_stats = calculate_summary_stats(outsample_df, 'OUT-OF-SAMPLE')
        summary_stats.append(outsample_stats)

    summary_df = pd.DataFrame(summary_stats)
    summary_df.to_csv(OUTPUT_STATS_SUMMARY, index=False)
    print(f"[COMPUTE STATS] Saved: {OUTPUT_STATS_SUMMARY}")

    # Calculate monthly stats
    print(f"[COMPUTE STATS] Calculating monthly statistics...")
    monthly_frames = []

    if len(insample_df) > 0:
        monthly_frames.append(calculate_monthly_stats(insample_df, 'IN-SAMPLE'))

    if len(outsample_df) > 0:
        monthly_frames.append(calculate_monthly_stats(outsample_df, 'OUT-OF-SAMPLE'))

    if monthly_frames:
        monthly_df = pd.concat(monthly_frames, ignore_index=True)
        monthly_df.to_csv(OUTPUT_MONTHLY_STATS, index=False)
        print(f"[COMPUTE STATS] Saved: {OUTPUT_MONTHLY_STATS} ({len(monthly_df)} months)")

    # Calculate daily stats
    print(f"[COMPUTE STATS] Calculating daily statistics...")
    daily_frames = []

    if len(insample_df) > 0:
        daily_frames.append(calculate_daily_stats(insample_df, 'IN-SAMPLE'))

    if len(outsample_df) > 0:
        daily_frames.append(calculate_daily_stats(outsample_df, 'OUT-OF-SAMPLE'))

    if daily_frames:
        daily_df = pd.concat(daily_frames, ignore_index=True)
        daily_df.to_csv(OUTPUT_DAILY_STATS, index=False)
        print(f"[COMPUTE STATS] Saved: {OUTPUT_DAILY_STATS} ({len(daily_df)} days)")

    # Calculate hourly stats
    print(f"[COMPUTE STATS] Calculating hourly statistics...")
    hourly_frames = []

    if len(insample_df) > 0:
        hourly_frames.append(calculate_hourly_stats(insample_df, 'IN-SAMPLE'))

    if len(outsample_df) > 0:
        hourly_frames.append(calculate_hourly_stats(outsample_df, 'OUT-OF-SAMPLE'))

    if hourly_frames:
        hourly_df = pd.concat(hourly_frames, ignore_index=True)
        hourly_df.to_csv(OUTPUT_HOURLY_STATS, index=False)
        print(f"[COMPUTE STATS] Saved: {OUTPUT_HOURLY_STATS}")

    # Calculate day-of-week stats
    print(f"[COMPUTE STATS] Calculating day-of-week statistics...")
    dow_frames = []

    if len(insample_df) > 0:
        dow_frames.append(calculate_dow_stats(insample_df, 'IN-SAMPLE'))

    if len(outsample_df) > 0:
        dow_frames.append(calculate_dow_stats(outsample_df, 'OUT-OF-SAMPLE'))

    if dow_frames:
        dow_df = pd.concat(dow_frames, ignore_index=True)
        dow_df.to_csv(OUTPUT_DOW_STATS, index=False)
        print(f"[COMPUTE STATS] Saved: {OUTPUT_DOW_STATS}")

    print("=" * 60)
    print("STATISTICS COMPUTATION COMPLETE")
    print("=" * 60)
    print(f"Next step: Run build_report.py to generate HTML report")


if __name__ == '__main__':
    main()
