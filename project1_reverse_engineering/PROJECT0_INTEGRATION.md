# Project 0 ↔ Project 1 Integration

## Overview

Project 1 (Reverse Engineering) now **automatically uses the trade data loaded in Project 0** instead of reading from a separate CSV file. This creates a seamless workflow between the two projects.

## How It Works

### Data Flow:

```
Project 0 (Data Pipeline)
    ↓
User loads trade file
    ↓
Data stored in state.loaded_data
    ↓
Project 1 (Reverse Engineering)
    ↓
Automatically uses state.loaded_data
    ↓
Runs reverse engineering analysis
```

## Step-by-Step Workflow

### 1. Load Trade Data in Project 0

**First, you must load your trade data:**

1. Click **"0 - Data Pipeline"** in the sidebar
2. **Step 1**: Click "Browse" and select your Myfxbook trade file (`.txt` file)
3. **Step 2**: Click "Run" to load the data
4. ✓ You should see trades displayed in the grid
5. (Optional) Click "Clean" to clean the data

**Important**: The data loaded in Project 0's grid is now available to Project 1!

### 2. Use the Data in Project 1

**Now you can use Project 1:**

1. Click **"1 - Reverse Engineer"** in the sidebar
2. Click **"⚙️ Configuration & Data"**
3. You'll see: **"✓ Trade Data: XX trades loaded (from Project 0)"**
4. Download price data if needed
5. Go to **"🚀 Run Scenarios"**
6. You'll see: **"✓ XX trades loaded from Project 0"**
7. Select scenarios and run!

## Visual Indicators

### In Configuration Panel:
```
Data Status:
  ✓ Trade Data: 312 trades loaded (from Project 0)     [GREEN]
  ✓ Price Data: All 4 timeframes present               [GREEN]
```

Or if no data:
```
Data Status:
  ⚠️ Trade Data: No data loaded in Project 0           [RED]
  ⚠️ Price Data: No files found                        [RED]
```

### In Run Scenarios Panel:
```
┌─────────────────────────────────────────────┐
│ ✓ 312 trades loaded from Project 0          │  [BLUE BOX]
└─────────────────────────────────────────────┘
```

Or:
```
┌─────────────────────────────────────────────┐
│ ⚠️ No trade data loaded - Load in Project 0 │  [BLUE BOX]
└─────────────────────────────────────────────┘
```

## What Changed

### Before (Old Behavior):
- Project 1 read trades from hardcoded CSV path
- Required `trades_clean.csv` file to exist
- No connection between Project 0 and Project 1
- Could have different data in each project

### After (New Behavior):
- Project 1 uses `state.loaded_data` from Project 0
- No need for separate CSV file (but works as fallback)
- **One source of truth** for trade data
- Consistent data across all projects

## Technical Details

### For Developers:

**Modified Files:**

1. **`shared/data_utils.py`**
   - Added `load_trades_from_state()` function
   - Reads from `state.loaded_data` instead of CSV
   - Handles column name mapping and date parsing

2. **`project1_reverse_engineering/step1_align_price.py`**
   - Now checks `state.loaded_data` first
   - Falls back to CSV if no state data available
   - Prints source of data in console

3. **`project1_reverse_engineering/panels/configuration.py`**
   - Added trade data status indicator
   - Shows number of trades from Project 0
   - Warns if no data is loaded

4. **`project1_reverse_engineering/panels/run_scenarios.py`**
   - Added validation before running
   - Shows error if no trade data loaded
   - Displays data status in blue box

### Code Example:

```python
# In step1_align_price.py
if state.loaded_data is not None:
    print("  Using trade data from Project 0 grid...")
    trades_df = data_utils.load_trades_from_state(state)
else:
    print("  No data in Project 0 grid, loading from CSV...")
    trades_df = data_utils.load_trades_csv(TRADES_CSV_PATH)
```

## Error Prevention

### If you try to run Project 1 without loading data:

**Error Message:**
```
❌ No Trade Data

No trade data loaded!

Please go to Project 0 → Data Pipeline and
load your trade data first.

Steps:
1. Click '0 - Data Pipeline' in sidebar
2. Select your trade file
3. Click 'Run' to load the data
4. Return to Project 1 and try again
```

This prevents you from accidentally running analysis with no data.

## Fallback Behavior

If you run Project 1 **from command line** (not UI):

```bash
python step1_align_price.py --scenario H1
```

The script will:
1. Check if `state.loaded_data` exists
2. If not, look for CSV file at: `../project0_data_pipeline/Data Files for data mining/trades_clean.csv`
3. Load from CSV if found
4. Error if neither source is available

This ensures command-line usage still works!

## Benefits

### 1. **Single Source of Truth**
- Load data once in Project 0
- Use it everywhere (Project 0, 1, 2, 3)
- No data synchronization issues

### 2. **Seamless Workflow**
- Natural progression: Load → Analyze → Backtest → Deploy
- No file exports needed between projects
- Data flows automatically

### 3. **Better UX**
- Clear visual indicators
- Error prevention
- Helpful error messages

### 4. **Consistency**
- Same trades used in Project 0 analysis and Project 1 reverse engineering
- If you clean data in Project 0, Project 1 uses clean data
- No risk of using different datasets

## FAQ

### Q: What if I want to use different trade data in Project 1?
**A:** Load the new data in Project 0 first. Project 1 will automatically pick it up.

### Q: Can I still use CSV files directly?
**A:** Yes! If `state.loaded_data` is None, the scripts fallback to reading from CSV files. This is useful for command-line usage.

### Q: What happens if I load new data in Project 0?
**A:** Project 1 will immediately use the new data. The status indicators will update when you click "Check Data Status" or navigate to the panels.

### Q: Do I need to reload the app?
**A:** No! Data is shared in memory via the `state` module. Just load data in Project 0 and switch to Project 1.

### Q: What if the date format is wrong?
**A:** Project 0 handles date parsing. Once loaded there, Project 1 receives already-parsed dates. Make sure Project 0 loads correctly first.

## Summary

✅ **Project 1 now uses Project 0 data automatically**
✅ **No separate CSV files needed**
✅ **Clear status indicators show data availability**
✅ **Error messages guide you if data is missing**
✅ **Fallback to CSV still works for command-line use**

This integration makes the Trade Bot application feel like a unified system rather than separate projects! 🎉
