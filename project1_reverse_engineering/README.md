# Project 1: Reverse Engineering Trading Bot

This project reverse engineers a trading bot's strategy using machine learning by analyzing its trade history and market conditions.

## Project Status

✅ All 7 step scripts have been created
✅ Shared utility libraries implemented
✅ Orchestration scripts ready
⚠️ **MISSING: OHLCV Price Data**

## What's Been Implemented

1. **Shared Libraries** (`../shared/`)
   - `data_utils.py` - Data loading and alignment functions
   - `indicator_utils.py` - 119 technical indicators

2. **Step Scripts** (All 7 steps)
   - `step1_align_price.py` - Align trades with candles
   - `step2_compute_indicators.py` - Compute all indicators
   - `step3_label_trades.py` - Label win/loss outcomes
   - `step4_train_model.py` - Train Random Forest model
   - `step5_shap_analysis.py` - SHAP feature importance
   - `step6_extract_rules.py` - Extract IF/THEN rules
   - `step7_validate.py` - Validate rules against history

3. **Orchestration**
   - `run_all_scenarios.py` - Run all 5 scenarios automatically
   - `compare_scenarios.py` - Compare and rank scenarios

## Missing Data Requirements

### CRITICAL: You need OHLCV price data

The scripts are ready but **cannot run without price data**. You need:

1. **XAUUSD Candle Data** for these timeframes:
   - M5 (5-minute candles)
   - M15 (15-minute candles)
   - H1 (1-hour candles)
   - H4 (4-hour candles)

2. **Date Range**: Must cover the same period as your trades (2026-03-04 to 2026-03-10 based on trades_clean.csv)

3. **Format**: CSV files with columns:
   - `timestamp` (or time/datetime)
   - `open`
   - `high`
   - `low`
   - `close`
   - `volume`

4. **Location**: Save files in `../data/` folder:
   ```
   ../data/xauusd_M5.csv
   ../data/xauusd_M15.csv
   ../data/xauusd_H1.csv
   ../data/xauusd_H4.csv
   ```

## How to Get Price Data

### Option 1: Manual Export from MetaTrader 5
1. Open MT5
2. Open XAUUSD chart
3. For each timeframe (M5, M15, H1, H4):
   - Switch to that timeframe
   - Right-click chart → "Save as"
   - Export as CSV
   - Rename to format: `xauusd_M5.csv`, etc.
   - Move to `../data/` folder

### Option 2: Use Data Provider API
- **yfinance**: Can download daily/hourly data for GC=F (Gold Futures)
  - Limited intraday data availability
  - May not have M5/M15 data

- **MT5 Python Library**: Can download from demo account
  - Requires MetaTrader5 installation
  - Free demo account needed

### Option 3: Third-Party Data Provider
- AlphaVantage, Polygon.io, or similar
- May require paid subscription for intraday data

## Current Issues to Resolve

### 1. **Price Data Missing** (BLOCKER)
   - Scripts will fail at step1 without price data files
   - Error: "Candle data file not found: ../data/xauusd_H1.csv"

   **Fix**: Obtain and place OHLCV data files in ../data/

### 2. **Date Range Mismatch**
   - Your trades are from March 2026
   - This is in the future (current date: March 2026)
   - Most historical data sources don't have future data

   **Possible explanations:**
   - Trades might be from a demo/simulation account
   - Dates might be incorrectly formatted
   - Need to verify actual trade dates

   **Fix**: Check if trades_clean.csv dates are correct

### 3. **Dependencies**
   Some Python libraries may need installation:
   ```bash
   pip install pandas numpy yfinance ta scikit-learn shap matplotlib seaborn MetaTrader5 joblib
   ```

## Quick Start (Once Data is Ready)

### Run Single Scenario
```bash
cd project1_reverse_engineering

# Run all steps for H1 timeframe
python step1_align_price.py --scenario H1
python step2_compute_indicators.py --scenario H1
python step3_label_trades.py --scenario H1
python step4_train_model.py --scenario H1
python step5_shap_analysis.py --scenario H1
python step6_extract_rules.py --scenario H1
python step7_validate.py --scenario H1
```

### Run All Scenarios Automatically
```bash
cd project1_reverse_engineering
python run_all_scenarios.py
python compare_scenarios.py
```

This will:
1. Run all 7 steps for all 5 scenarios (M5, M15, H1, H4, H1_M15)
2. Generate comparison report showing which scenario best matches the bot
3. Create validation reports and trading rules

## Expected Output Structure

```
outputs/
├── scenario_M5/
│   ├── trades_with_candles_M5.csv
│   ├── feature_matrix.csv
│   ├── feature_matrix_labeled.csv
│   ├── trained_model.pkl
│   ├── feature_importance.csv
│   ├── shap_importance.csv
│   ├── shap_bar_chart.png
│   ├── shap_summary.png
│   ├── rules_report.txt
│   ├── validation_report.txt
│   └── model_metrics.txt
├── scenario_M15/ (same structure)
├── scenario_H1/ (same structure)
├── scenario_H4/ (same structure)
├── scenario_H1_M15/ (same structure)
├── scenario_comparison.txt
└── scenario_comparison.csv
```

## Understanding Results

After running, check `outputs/scenario_comparison.txt` to see:
- Which scenario has highest match rate
- Model accuracy for each timeframe
- Number of discovered rules
- Recommendation on whether to proceed to backtesting

A **match rate ≥ 70%** means the bot's logic was successfully reverse-engineered.

## Next Steps After Successful Reverse Engineering

1. Review the winning scenario's `rules_report.txt`
2. Check `shap_summary.png` to see which indicators matter most
3. Validate the rules make sense based on trading knowledge
4. Proceed to Project 2 (Backtesting) to test the rules

## Troubleshooting

### "ERROR: Candle data file not found"
- You need to create/download price data files
- See "How to Get Price Data" section above

### "ERROR: Insufficient lookback candles"
- Price data doesn't go back far enough
- Need at least 200 candles before first trade
- Download more historical data

### "All scenarios failed"
- Check that trades_clean.csv exists and is properly formatted
- Verify all dependencies are installed
- Check that price data covers the trade date range

### "Match rate too low (<60%)"
- Wrong timeframe scenario (try all 5 scenarios)
- Data quality issues
- Timezone misalignment
- Bot logic too complex for decision trees

## Questions or Issues?

The implementation follows the comprehensive plan from `reverse_engineering_plan_v4.docx`. All code is complete and ready to run once price data is available.
