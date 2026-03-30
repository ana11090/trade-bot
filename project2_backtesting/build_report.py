"""
PROJECT 2 - BUILD HTML REPORT
Generates visual dashboard from backtest statistics
"""

import pandas as pd
import os
from datetime import datetime

# Paths
INPUT_FOLDER = './outputs/'
STATS_SUMMARY = os.path.join(INPUT_FOLDER, 'stats_summary.csv')
MONTHLY_STATS = os.path.join(INPUT_FOLDER, 'monthly_stats.csv')
DAILY_STATS = os.path.join(INPUT_FOLDER, 'daily_stats.csv')
HOURLY_STATS = os.path.join(INPUT_FOLDER, 'hourly_stats.csv')
DOW_STATS = os.path.join(INPUT_FOLDER, 'dow_stats.csv')
INSAMPLE_TRADES = os.path.join(INPUT_FOLDER, 'trade_log_insample.csv')
OUTSAMPLE_TRADES = os.path.join(INPUT_FOLDER, 'trade_log_outsample.csv')

OUTPUT_REPORT = os.path.join(INPUT_FOLDER, 'backtest_report.html')


def generate_html_report():
    """Generate complete HTML report"""

    # Load all data
    print("[BUILD REPORT] Loading statistics files...")

    summary_df = pd.read_csv(STATS_SUMMARY) if os.path.exists(STATS_SUMMARY) else pd.DataFrame()
    monthly_df = pd.read_csv(MONTHLY_STATS) if os.path.exists(MONTHLY_STATS) else pd.DataFrame()
    daily_df = pd.read_csv(DAILY_STATS) if os.path.exists(DAILY_STATS) else pd.DataFrame()
    hourly_df = pd.read_csv(HOURLY_STATS) if os.path.exists(HOURLY_STATS) else pd.DataFrame()
    dow_df = pd.read_csv(DOW_STATS) if os.path.exists(DOW_STATS) else pd.DataFrame()

    insample_trades = pd.read_csv(INSAMPLE_TRADES) if os.path.exists(INSAMPLE_TRADES) else pd.DataFrame()
    outsample_trades = pd.read_csv(OUTSAMPLE_TRADES) if os.path.exists(OUTSAMPLE_TRADES) else pd.DataFrame()

    # Generate HTML
    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Backtest Report - Project 2</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            overflow: hidden;
        }}

        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }}

        .header h1 {{
            font-size: 36px;
            margin-bottom: 10px;
        }}

        .header p {{
            font-size: 16px;
            opacity: 0.9;
        }}

        .content {{
            padding: 40px;
        }}

        .section {{
            margin-bottom: 50px;
        }}

        .section h2 {{
            font-size: 28px;
            color: #333;
            margin-bottom: 20px;
            border-bottom: 3px solid #667eea;
            padding-bottom: 10px;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}

        .stat-card {{
            background: #f8f9fa;
            border-radius: 8px;
            padding: 20px;
            border-left: 4px solid #667eea;
        }}

        .stat-card.positive {{
            border-left-color: #28a745;
        }}

        .stat-card.negative {{
            border-left-color: #dc3545;
        }}

        .stat-card .label {{
            font-size: 14px;
            color: #666;
            margin-bottom: 8px;
        }}

        .stat-card .value {{
            font-size: 28px;
            font-weight: bold;
            color: #333;
        }}

        .stat-card.positive .value {{
            color: #28a745;
        }}

        .stat-card.negative .value {{
            color: #dc3545;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 30px;
            background: white;
        }}

        table th {{
            background: #667eea;
            color: white;
            padding: 12px;
            text-align: left;
            font-weight: 600;
        }}

        table td {{
            padding: 12px;
            border-bottom: 1px solid #e0e0e0;
        }}

        table tr:hover {{
            background: #f8f9fa;
        }}

        .positive-value {{
            color: #28a745;
            font-weight: bold;
        }}

        .negative-value {{
            color: #dc3545;
            font-weight: bold;
        }}

        .chart {{
            margin: 30px 0;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 8px;
        }}

        .bar-chart {{
            display: flex;
            align-items: flex-end;
            height: 300px;
            gap: 8px;
            padding: 20px;
            background: white;
            border-radius: 8px;
        }}

        .bar {{
            flex: 1;
            background: #667eea;
            min-height: 5px;
            position: relative;
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            align-items: center;
        }}

        .bar.positive {{
            background: #28a745;
        }}

        .bar.negative {{
            background: #dc3545;
        }}

        .bar-label {{
            font-size: 11px;
            color: #666;
            margin-top: 8px;
            text-align: center;
        }}

        .bar-value {{
            font-size: 10px;
            color: white;
            padding: 4px;
            font-weight: bold;
        }}

        .period-tabs {{
            display: flex;
            gap: 10px;
            margin-bottom: 30px;
        }}

        .period-tab {{
            flex: 1;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 8px;
            text-align: center;
            border: 2px solid transparent;
        }}

        .period-tab.insample {{
            border-color: #667eea;
        }}

        .period-tab.outsample {{
            border-color: #28a745;
        }}

        .period-tab h3 {{
            font-size: 18px;
            color: #333;
            margin-bottom: 10px;
        }}

        .comparison-table {{
            margin-top: 30px;
        }}

        .footer {{
            background: #f8f9fa;
            padding: 20px;
            text-align: center;
            color: #666;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Backtest Report</h1>
            <p>Project 2 - Trading Strategy Performance Analysis</p>
            <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>

        <div class="content">
"""

    # Summary statistics section
    html += """
            <div class="section">
                <h2>Performance Summary</h2>
                <div class="period-tabs">
"""

    for _, row in summary_df.iterrows():
        period = row['period']
        tab_class = 'insample' if period == 'IN-SAMPLE' else 'outsample'

        html += f"""
                    <div class="period-tab {tab_class}">
                        <h3>{period}</h3>
                        <div class="stats-grid">
"""

        # Add key metrics
        net_profit = row['net_profit']
        profit_class = 'positive' if net_profit > 0 else 'negative'

        html += f"""
                            <div class="stat-card {profit_class}">
                                <div class="label">Net Profit</div>
                                <div class="value">${net_profit:,.2f}</div>
                            </div>
                            <div class="stat-card">
                                <div class="label">Total Trades</div>
                                <div class="value">{int(row['total_trades'])}</div>
                            </div>
                            <div class="stat-card">
                                <div class="label">Win Rate</div>
                                <div class="value">{row['win_rate_pct']:.1f}%</div>
                            </div>
                            <div class="stat-card">
                                <div class="label">Profit Factor</div>
                                <div class="value">{row['profit_factor']:.2f}</div>
                            </div>
                            <div class="stat-card {profit_class}">
                                <div class="label">Return</div>
                                <div class="value">{row['return_pct']:.1f}%</div>
                            </div>
                            <div class="stat-card">
                                <div class="label">Max Drawdown</div>
                                <div class="value">{row['max_drawdown_pct']:.1f}%</div>
                            </div>
"""

        html += """
                        </div>
                    </div>
"""

    html += """
                </div>
            </div>
"""

    # Detailed statistics table
    html += """
            <div class="section">
                <h2>Detailed Statistics</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Metric</th>
                            <th>IN-SAMPLE</th>
                            <th>OUT-OF-SAMPLE</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    # Create comparison table
    metrics = [
        ('Total Trades', 'total_trades', ''),
        ('Winning Trades', 'winning_trades', ''),
        ('Losing Trades', 'losing_trades', ''),
        ('Win Rate', 'win_rate_pct', '%'),
        ('Total Profit', 'total_profit', '$'),
        ('Total Loss', 'total_loss', '$'),
        ('Net Profit', 'net_profit', '$'),
        ('Profit Factor', 'profit_factor', ''),
        ('Average Win', 'avg_win', '$'),
        ('Average Loss', 'avg_loss', '$'),
        ('Largest Win', 'largest_win', '$'),
        ('Largest Loss', 'largest_loss', '$'),
        ('Max Consecutive Wins', 'max_consecutive_wins', ''),
        ('Max Consecutive Losses', 'max_consecutive_losses', ''),
        ('Max Drawdown', 'max_drawdown_pct', '%'),
        ('Sharpe Ratio', 'sharpe_ratio', ''),
        ('Total Pips', 'total_pips', ''),
        ('Avg Pips Per Trade', 'avg_pips_per_trade', ''),
        ('Final Balance', 'final_balance', '$'),
        ('Return', 'return_pct', '%'),
    ]

    insample_row = summary_df[summary_df['period'] == 'IN-SAMPLE'].iloc[0] if len(summary_df[summary_df['period'] == 'IN-SAMPLE']) > 0 else None
    outsample_row = summary_df[summary_df['period'] == 'OUT-OF-SAMPLE'].iloc[0] if len(summary_df[summary_df['period'] == 'OUT-OF-SAMPLE']) > 0 else None

    for metric_name, metric_key, symbol in metrics:
        insample_val = insample_row[metric_key] if insample_row is not None else 0
        outsample_val = outsample_row[metric_key] if outsample_row is not None else 0

        # Format values
        if symbol == '$':
            insample_str = f"${insample_val:,.2f}"
            outsample_str = f"${outsample_val:,.2f}"
            insample_class = 'positive-value' if insample_val > 0 else 'negative-value' if insample_val < 0 else ''
            outsample_class = 'positive-value' if outsample_val > 0 else 'negative-value' if outsample_val < 0 else ''
        elif symbol == '%':
            insample_str = f"{insample_val:.1f}%"
            outsample_str = f"{outsample_val:.1f}%"
            insample_class = ''
            outsample_class = ''
        else:
            insample_str = f"{insample_val:.2f}" if isinstance(insample_val, float) else f"{int(insample_val)}"
            outsample_str = f"{outsample_val:.2f}" if isinstance(outsample_val, float) else f"{int(outsample_val)}"
            insample_class = ''
            outsample_class = ''

        html += f"""
                        <tr>
                            <td><strong>{metric_name}</strong></td>
                            <td class="{insample_class}">{insample_str}</td>
                            <td class="{outsample_class}">{outsample_str}</td>
                        </tr>
"""

    html += """
                    </tbody>
                </table>
            </div>
"""

    # Monthly performance chart
    if len(monthly_df) > 0:
        html += """
            <div class="section">
                <h2>Monthly Performance</h2>
                <div class="chart">
                    <div class="bar-chart">
"""

        # Create bars for each month
        max_profit = monthly_df['net_profit'].abs().max()

        for _, row in monthly_df.iterrows():
            profit = row['net_profit']
            height_pct = (abs(profit) / max_profit * 100) if max_profit > 0 else 5
            bar_class = 'positive' if profit > 0 else 'negative'

            html += f"""
                        <div class="bar {bar_class}" style="height: {height_pct}%;">
                            <span class="bar-value">${profit:,.0f}</span>
                        </div>
"""

        html += """
                    </div>
                    <div style="display: flex; gap: 8px; padding: 0 20px;">
"""

        for _, row in monthly_df.iterrows():
            html += f"""
                        <div class="bar-label" style="flex: 1;">{row['year_month']}</div>
"""

        html += """
                    </div>
                </div>
            </div>
"""

    # Day of week analysis
    if len(dow_df) > 0:
        html += """
            <div class="section">
                <h2>Day of Week Analysis</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Day</th>
                            <th>Period</th>
                            <th>Trade Count</th>
                            <th>Net Profit</th>
                            <th>Avg Profit/Trade</th>
                            <th>Win Rate</th>
                            <th>Total Pips</th>
                        </tr>
                    </thead>
                    <tbody>
"""

        for _, row in dow_df.iterrows():
            profit = row['net_profit']
            profit_class = 'positive-value' if profit > 0 else 'negative-value' if profit < 0 else ''

            html += f"""
                        <tr>
                            <td><strong>{row['day_of_week']}</strong></td>
                            <td>{row['period']}</td>
                            <td>{int(row['trade_count'])}</td>
                            <td class="{profit_class}">${profit:,.2f}</td>
                            <td class="{profit_class}">${row['avg_profit_per_trade']:,.2f}</td>
                            <td>{row['win_rate_pct']:.1f}%</td>
                            <td>{row['total_pips']:.1f}</td>
                        </tr>
"""

        html += """
                    </tbody>
                </table>
            </div>
"""

    # Recent trades preview
    if len(insample_trades) > 0:
        html += """
            <div class="section">
                <h2>Recent Trades (IN-SAMPLE) - Last 10</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Entry Time</th>
                            <th>Direction</th>
                            <th>Entry Price</th>
                            <th>Exit Price</th>
                            <th>Exit Reason</th>
                            <th>Pips</th>
                            <th>Net Profit</th>
                        </tr>
                    </thead>
                    <tbody>
"""

        recent_trades = insample_trades.tail(10)
        for _, trade in recent_trades.iterrows():
            profit = trade['net_profit']
            profit_class = 'positive-value' if profit > 0 else 'negative-value'

            html += f"""
                        <tr>
                            <td>{trade['entry_time']}</td>
                            <td>{trade['direction']}</td>
                            <td>{trade['entry_price']:.2f}</td>
                            <td>{trade['exit_price']:.2f}</td>
                            <td>{trade['exit_reason']}</td>
                            <td>{trade['pips']:.1f}</td>
                            <td class="{profit_class}">${profit:,.2f}</td>
                        </tr>
"""

        html += """
                    </tbody>
                </table>
            </div>
"""

    # Footer
    html += f"""
        </div>

        <div class="footer">
            <p>Backtest Report Generated by Project 2 - Backtesting Engine</p>
            <p>Report generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
    </div>
</body>
</html>
"""

    return html


def main():
    """Main entry point"""
    print("=" * 60)
    print("PROJECT 2 - BUILD HTML REPORT")
    print("=" * 60)

    print("[BUILD REPORT] Generating HTML report...")
    html = generate_html_report()

    # Save report
    with open(OUTPUT_REPORT, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"[BUILD REPORT] Report saved: {OUTPUT_REPORT}")
    print(f"[BUILD REPORT] File size: {os.path.getsize(OUTPUT_REPORT) / 1024:.1f} KB")

    print("=" * 60)
    print("HTML REPORT GENERATION COMPLETE")
    print("=" * 60)
    print(f"Open in browser: {os.path.abspath(OUTPUT_REPORT)}")


if __name__ == '__main__':
    main()
