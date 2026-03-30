# Project 1 UI Integration Guide

## ✅ Integration Complete

Project 1 Reverse Engineering is now fully integrated into the Trade Bot UI!

## How to Access Project 1

### 1. Start the Application

```bash
cd "C:\Users\anicu\OneDrive\Atasari\Documente\trade bo\trade-bot"
python main_app.py
```

### 2. Navigate to Project 1

- Click on **"1 - Reverse Engineer"** button in the left sidebar
- This will expand to show 3 sub-buttons:
  - ⚙️ **Configuration & Data**
  - 🚀 **Run Scenarios**
  - 📊 **View Results**

## Features of Each Panel

### ⚙️ Configuration & Data

**Purpose**: Set up parameters and download price data

**Features**:
- Configure all reverse engineering parameters (symbol, timeframes, ML settings)
- Download OHLCV price data using yfinance
- Check status of existing data files
- Real-time console output showing download progress

**What You Can Do**:
1. Review and adjust parameters (defaults are pre-filled from the plan document)
2. Click "Download Data (yfinance)" to attempt automatic download
3. Click "Check Existing Data" to verify data files exist
4. Status indicator shows: ✓ All data files present / ⚠️ Missing data

**Important Notes**:
- yfinance has limited intraday data availability
- For best results, use MetaTrader 5 to export data manually
- The panel will warn you if data is insufficient

---

### 🚀 Run Scenarios

**Purpose**: Execute the 7-step reverse engineering pipeline

**Features**:
- Select which timeframe scenarios to run (M5, M15, H1, H4, H1_M15)
- Run all 7 steps automatically for each selected scenario
- Real-time console output showing progress
- "Select All" / "Deselect All" buttons for convenience

**The 7 Steps**:
Each selected scenario will run:
1. Align price data
2. Compute indicators
3. Label trades
4. Train ML model
5. SHAP analysis
6. Extract rules
7. Validate results

**What You Can Do**:
1. Check the scenarios you want to run
2. Click "Run Selected Scenarios"
3. Watch the console output for progress
4. Wait for completion (5-15 minutes per scenario)
5. See success/failure summary at the end

**Important Notes**:
- All scenarios run in a background thread (UI stays responsive)
- Output is captured and displayed in the console
- If a step fails, that scenario is skipped but others continue
- Results are saved to `outputs/scenario_XXX/` folders

---

### 📊 View Results

**Purpose**: Compare scenarios and view discovered trading rules

**Features**:
- **Scenario Comparison Table**: Shows accuracy, match rate, and composite score for all scenarios
- **Winner Display**: Highlights the best-performing scenario
- **Rules Viewer**: View discovered IF/THEN trading rules for any scenario
- **Validation Report**: See match rate and recommendation

**What You Can Do**:
1. Click "Refresh Results" to reload latest data
2. See which scenario won (highest composite score)
3. Select a scenario from dropdown and click "View Rules"
4. Read the discovered trading rules and validation report
5. Check if match rate ≥ 70% (success threshold)

**Composite Score**:
- Formula: `0.4 × Test Accuracy + 0.6 × Match Rate`
- Higher score = better reverse engineering
- Winner is the scenario with highest score

**Status Indicators**:
- ✓ SUCCESS: Match rate ≥ 70% → Proceed to backtesting
- ⚠️ MARGINAL: Match rate 60-70% → Review before proceeding
- ✗ INSUFFICIENT: Match rate < 60% → Try different scenario or improve data

---

## Typical Workflow

### First Time Setup:
1. Open app: `python main_app.py`
2. Click "1 - Reverse Engineer" → "Configuration & Data"
3. Download price data (or place manually in `data/` folder)
4. Verify all 4 timeframe files are present

### Running Analysis:
5. Go to "Run Scenarios"
6. Select scenarios to test (or "Select All")
7. Click "Run Selected Scenarios"
8. Wait for completion (watch console output)

### Viewing Results:
9. Go to "View Results"
10. Check which scenario won
11. View rules for the winner
12. Check validation report
13. If match rate ≥ 70%: **You've successfully reverse-engineered the bot!**

---

## UI vs Command Line

Both approaches work identically:

| Feature | UI (Tkinter) | Command Line |
|---------|--------------|--------------|
| Configuration | Set in UI panel | Edit script constants |
| Download Data | Click button | `python download_price_data.py` |
| Run Scenarios | Select & click | `python run_all_scenarios.py` |
| View Results | Results panel | Open .txt files in `outputs/` |
| Real-time Output | Console widget | Terminal output |

**Advantages of UI**:
- More visual and user-friendly
- Easy to switch between scenarios
- All features in one place
- Progress indicators
- Color-coded status

**Advantages of Command Line**:
- Faster for batch operations
- Better for debugging
- Can redirect output to files
- Scriptable/automatable

---

## Troubleshooting

### "No price data found" in Configuration panel
- Download data using the button, OR
- Manually place CSV files in `../data/` folder
- Files must be named: `xauusd_M5.csv`, `xauusd_M15.csv`, `xauusd_H1.csv`, `xauusd_H4.csv`

### "No comparison results found" in Results panel
- You need to run scenarios first
- Go to "Run Scenarios" and execute at least one scenario
- Results are saved to `outputs/scenario_comparison.txt`

### Scenarios fail with "Candle data file not found"
- Price data files are missing
- Go back to Configuration panel and download/verify data

### UI freezes during execution
- This shouldn't happen (runs in background thread)
- If it does, close and restart the app
- Use command line as alternative: `python run_all_scenarios.py`

---

## Files Created by the UI

When you run scenarios, these files are created:

```
outputs/
├── scenario_M5/
│   ├── trades_with_candles_M5.csv
│   ├── feature_matrix.csv
│   ├── feature_matrix_labeled.csv
│   ├── trained_model.pkl
│   ├── feature_importance.csv
│   ├── shap_importance.csv
│   ├── shap_bar_chart.png          ← Visualization
│   ├── shap_summary.png            ← Visualization
│   ├── rules_report.txt            ← READ THIS!
│   ├── validation_report.txt       ← READ THIS!
│   └── model_metrics.txt
├── scenario_M15/ (same structure)
├── scenario_H1/ (same structure)
├── scenario_H4/ (same structure)
├── scenario_H1_M15/ (same structure)
├── scenario_comparison.txt         ← READ THIS FIRST!
└── scenario_comparison.csv
```

---

## Next Steps After Success

If you achieve match rate ≥ 70%:

1. **Review the Rules**: Read `rules_report.txt` for the winning scenario
2. **Understand Indicators**: Check `shap_summary.png` to see which indicators matter most
3. **Validate Logic**: Ensure the rules make sense from a trading perspective
4. **Proceed to Project 2**: Begin backtesting the discovered rules
5. **Implement in MT5**: Convert rules to Expert Advisor code (Project 3)

---

## Summary

✅ **Project 1 is now fully integrated into the UI**
✅ **All functionality accessible via sidebar buttons**
✅ **Real-time progress and output display**
✅ **Easy-to-use visual interface**
✅ **Same functionality as command-line scripts**

Enjoy using the Trade Bot Reverse Engineering system! 🚀
