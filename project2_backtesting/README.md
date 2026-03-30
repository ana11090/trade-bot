# Project 2 - Backtesting

## Overview

Project 2 validates the trading rules discovered in Project 1 by simulating trades on both **in-sample** (historical bot period) and **out-of-sample** (new data) periods.

This answers the critical question: **Do the discovered rules represent a real, repeatable edge, or just overfitting to historical data?**

---

## What Project 2 Does

1. **Loads rules** from Project 1 output (`rules_report_H1.txt`)
2. **Simulates trades** on historical price data using those rules
3. **Calculates performance metrics** (win rate, profit factor, drawdown, etc.)
4. **Generates HTML report** with visual charts and statistics
5. **Compares in-sample vs out-of-sample** performance

---

## Files Structure

```
project2_backtesting/
├── backtest_engine.py       # Main simulation engine
├── compute_stats.py         # Calculate performance metrics
├── build_report.py          # Generate HTML report
├── run_backtest.py          # Orchestrator script
├── panels/                  # UI panels
│   ├── configuration.py     # Configuration panel
│   ├── run_backtest_panel.py # Run backtest panel
│   └── view_results.py      # View results panel
├── outputs/                 # Generated results
│   ├── trade_log_insample.csv
│   ├── trade_log_outsample.csv
│   ├── stats_summary.csv
│   ├── monthly_stats.csv
│   ├── daily_stats.csv
│   ├── hourly_stats.csv
│   ├── dow_stats.csv
│   └── backtest_report.html # Main visual report
└── README.md
```

---

## How to Use

### Method 1: Using the UI (Recommended)

1. **Open the application**
   ```bash
   python main_app.py
   ```

2. **Navigate to Project 2**
   - Click "2 - Backtesting" in the sidebar
   - Three sub-panels will appear

3. **Check Prerequisites** (Configuration panel)
   - Verify rules file exists (from Project 1)
   - Verify price data exists (XAUUSD H1 candles)

4. **Run Backtest** (Run Backtest panel)
   - Click "Run Backtest" button
   - Monitor progress (2-5 minutes)
   - Wait for completion message

5. **View Results** (View Results panel)
   - Review summary statistics
   - Click "Open HTML Report" to see visual dashboard
   - Click "Open Outputs Folder" to access raw CSV files

### Method 2: Command Line

```bash
cd project2_backtesting

# Run all steps at once
python run_backtest.py

# Or run steps individually
python backtest_engine.py
python compute_stats.py
python build_report.py

# Open the HTML report
# Windows: start outputs/backtest_report.html
# Mac: open outputs/backtest_report.html
# Linux: xdg-open outputs/backtest_report.html
```

---

## Configuration

Edit `backtest_engine.py` to modify settings:

```python
# Date ranges
INSAMPLE_START = '2022-01-01'   # First date of bot's trade history
INSAMPLE_END = '2023-12-31'     # Last date of bot's trade history
OUTSAMPLE_START = '2024-01-01'  # First date of fresh data
OUTSAMPLE_END = '2024-12-31'    # Last date of fresh data

# Capital and risk
STARTING_CAPITAL = 10000.0      # USD - starting account balance
RISK_PER_TRADE_PCT = 0.01       # 1% of current balance per trade

# Stop loss and take profit (ATR multipliers)
SL_ATR_MULTIPLIER = 1.5
TP1_ATR_MULTIPLIER = 1.5        # 50% of position closed at TP1
TP2_ATR_MULTIPLIER = 3.0        # remaining 50% closed at TP2

# Costs
COMMISSION_PER_LOT = 4.0        # USD round trip
SPREAD_PIPS = 0.3               # estimated spread in pips
```

---

## How the Backtest Works

### 1. Rule Parsing

The engine reads rules from `rules_report_H1.txt`:

```
RULE #1 (confidence: 74%, covers: 38 trades, direction: BUY)
  CONDITION: rsi_14 < 32.5
  CONDITION: ema_50_distance > 0.0
  CONDITION: atr_14 > 5.0
  CONDITION: is_london_session == 1
  ENTRY: BUY
  STOP_LOSS: ATR * 1.5
  TAKE_PROFIT: ATR * 3.0
```

### 2. Trade Simulation

For each candle chronologically:
- Check if all rule conditions are met
- If yes, open a trade with:
  - Entry price: Current close
  - Stop loss: Entry ± (ATR × 1.5)
  - Take profit 1: Entry ± (ATR × 1.5) - close 50% of position
  - Take profit 2: Entry ± (ATR × 3.0) - close remaining 50%
- Scan forward candles to check if SL/TP hit
- Record trade with all details

### 3. Statistics Calculation

Calculate comprehensive metrics:
- **Performance**: Win rate, profit factor, net profit
- **Risk**: Max drawdown, Sharpe ratio
- **Temporal**: Monthly, daily, hourly, day-of-week analysis
- **Trade**: Average win/loss, largest win/loss, consecutive wins/losses

### 4. HTML Report Generation

Generate visual dashboard with:
- Summary cards (net profit, win rate, etc.)
- Comparison table (in-sample vs out-of-sample)
- Monthly performance bar chart
- Day-of-week analysis table
- Recent trades preview

---

## Interpreting Results

### ✅ **Good Signs** (Strategy may be viable)

- **Out-of-sample win rate ≥ 50%**
- **Out-of-sample profit factor ≥ 1.5**
- **Out-of-sample net profit > 0**
- **Max drawdown < 20%**
- **Similar performance in both periods**

### ⚠️ **Warning Signs** (Likely overfitting)

- **In-sample great, out-of-sample terrible**
  - Example: 70% win rate in-sample, 40% out-of-sample
  - Diagnosis: Rules are too specific to historical data

- **Profit factor drops significantly**
  - Example: 2.5 in-sample, 0.8 out-of-sample
  - Diagnosis: Rules don't generalize to new data

- **Max drawdown > 30%**
  - Risk management may need adjustment

### ❌ **Red Flags** (Strategy not viable)

- **Out-of-sample negative net profit**
- **Out-of-sample win rate < 40%**
- **Out-of-sample profit factor < 1.0**
- **Extreme performance difference between periods**

---

## What To Do Next

### Scenario 1: Out-of-sample performs well ✅
**Action**: Strategy may be robust!
- Proceed to Project 3 (Forward Testing)
- Test on demo account
- Consider live trading (with caution)

### Scenario 2: Out-of-sample fails ❌
**Action**: Return to Project 1
- Try different scenarios (M15, H4, combined timeframes)
- Adjust SHAP threshold to find more general rules
- Consider simplifying rules (fewer conditions)

### Scenario 3: Both periods fail ❌
**Action**: Rules may be wrong
- Review SHAP feature importance
- Check if correct indicators were identified
- Verify timeframe matches bot's actual timeframe
- Consider that bot may use non-indicator logic

---

## Outputs Explained

### Trade Logs

**trade_log_insample.csv** / **trade_log_outsample.csv**
- Every simulated trade
- Columns: entry_time, exit_time, direction, prices, P&L, etc.
- Use for detailed trade-by-trade analysis

### Statistics Files

**stats_summary.csv**
- Headline metrics for both periods
- One row per period (in-sample, out-of-sample)

**monthly_stats.csv**
- Performance by calendar month
- Identify seasonal patterns

**daily_stats.csv**
- Performance by trading day
- Identify problematic days

**hourly_stats.csv**
- Performance by hour of day (UTC)
- Verify session-based strategies

**dow_stats.csv**
- Performance by day of week
- Check if strategy works all days

### HTML Report

**backtest_report.html**
- Self-contained visual dashboard
- Open in any browser
- All charts embedded (no internet needed)
- Professional presentation for sharing

---

## Technical Details

### Trade Execution Logic

1. **Entry**: Close price of candle when rules fire
2. **Stop Loss**: Fixed distance based on ATR
3. **Take Profit**: Two levels (TP1 and TP2)
   - TP1 hit: Close 50% of position
   - TP2 hit: Close remaining 50%
4. **Same Candle SL/TP**: If both hit same candle, SL takes priority (configurable)
5. **Hard Close Hour**: Close all trades at specified hour (default: 21:00 UTC)

### Risk Management

- **Dynamic Lot Sizing**: Risk X% of current balance per trade
- **ATR-Based Stops**: Adapts to market volatility
- **Commission & Spread**: Realistic cost modeling
- **No Pyramiding**: Max one open trade at a time (configurable)

### Indicators

All 124 indicators from Project 1 are computed on the full dataset:
- RSI (multiple periods)
- EMAs (9, 20, 50, 100, 200)
- MACD (standard and fast)
- ATR (multiple periods)
- Bollinger Bands
- ADX, Stochastic, CCI, Williams %R
- Volume indicators
- Ichimoku Cloud
- Parabolic SAR, VWAP, Supertrend
- And many more...

The backtest engine uses these indicators to evaluate rule conditions at each candle.

---

## Troubleshooting

### Error: "Rules file not found"
**Solution**: Run Project 1 first to generate rules

### Error: "Price data file not found"
**Solution**: Download XAUUSD H1 data using Project 1 tools

### Warning: "No trades generated"
**Solution**:
- Rules may be too restrictive
- Check date ranges match available data
- Verify indicator calculations are correct

### HTML report shows zero trades
**Solution**:
- Check if rules file is correctly formatted
- Verify price data covers the backtest period
- Review warmup period (first 200 candles skipped)

---

## Performance Notes

- **Backtest Duration**: 2-5 minutes for typical dataset
- **Memory Usage**: ~500 MB for 2 years of H1 data
- **Output Size**: HTML report ~2-3 MB

---

## Next Steps

1. ✅ Run backtest
2. ✅ Analyze HTML report
3. ✅ Compare in-sample vs out-of-sample
4. 📊 Decide if strategy is viable
5. 🚀 If viable: Proceed to Project 3 (Forward Testing)
6. 🔄 If not: Return to Project 1 and iterate

---

## Support

For issues or questions:
- Review the `project2_backtesting_plan.docx` for detailed specifications
- Check the HTML report for diagnostic information
- Examine raw CSV files in `outputs/` folder
- Review terminal output for error messages

---

**Project 2 Complete!** ✅

You now have a fully functional backtesting engine that validates your discovered trading rules with transparency and comprehensive statistics.
