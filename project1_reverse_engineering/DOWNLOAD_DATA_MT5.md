# How to Download Price Data from MetaTrader 5

## Why MetaTrader 5?

**yfinance limitations:**
- ❌ Only recent 7 days for M5 data
- ❌ Only recent 60 days for M15/H1 data
- ❌ Data often lags or is incomplete
- ❌ Not reliable for XAUUSD (Gold)

**MetaTrader 5 advantages:**
- ✅ Complete historical data (years of data)
- ✅ Accurate tick-by-tick data
- ✅ Free demo account (no money needed)
- ✅ All timeframes (M1, M5, M15, H1, H4, D1)
- ✅ Reliable and used by professional traders

---

## Option 1: Download Using Python Script (Recommended)

This is the easiest way - automatic download using MT5 Python library.

### Step 1: Install MetaTrader 5

1. **Download MT5**: https://www.metatrader5.com/en/download
2. **Install** it (Windows only, ~200 MB)
3. **Open MT5** application

### Step 2: Create Free Demo Account

1. In MT5, click **File → Open an Account**
2. Search for **"MetaQuotes-Demo"** or any broker (e.g., "Admirals", "IC Markets")
3. Select **Demo Account**
4. Fill in any name/email (doesn't need to be real)
5. Choose account type: **Standard** or **Cent**
6. Click **Finish**
7. ✅ MT5 will create account and show credentials

**Important:** Keep MT5 **open and connected** when running the Python script!

### Step 3: Run the Download Script

I'll create a script for you. Run this:

```bash
cd "C:\Users\anicu\OneDrive\Atasari\Documente\trade bo\trade-bot\project1_reverse_engineering"
python download_data_mt5.py
```

The script will:
1. Connect to your open MT5 terminal
2. Download M5, M15, H1, H4 data for XAUUSD
3. Save to `../data/` folder
4. Show progress for each timeframe

---

## Option 2: Manual Export from MT5 (Backup Method)

If the Python script doesn't work, you can manually export data from MT5.

### Step 1: Open MT5 and Log In

1. Open MetaTrader 5
2. Log in to your demo account
3. Make sure you're connected (bottom right should show green connection)

### Step 2: Export Data for Each Timeframe

**For M5 (5-minute):**
1. Open a chart: **File → New Chart → XAUUSD**
2. Set timeframe: Click **M5** on toolbar
3. Scroll back to load more history (hold Page Up until it stops loading)
4. Right-click chart → **Export Data**
5. Save as: `xauusd_M5.csv`
6. Move file to: `C:\Users\anicu\OneDrive\Atasari\Documente\trade bo\trade-bot\data\`

**Repeat for M15, H1, H4:**
- M15: Set chart to 15-minute, export as `xauusd_M15.csv`
- H1: Set chart to 1-hour, export as `xauusd_H1.csv`
- H4: Set chart to 4-hour, export as `xauusd_H4.csv`

### Step 3: Verify Files

Check that you have:
```
data/
  ├── xauusd_M5.csv
  ├── xauusd_M15.csv
  ├── xauusd_H1.csv
  └── xauusd_H4.csv
```

---

## Required Data Range

Your trades are from **March 2026**, so you need data covering:
- **Start:** February 15, 2026 (for 200-candle lookback)
- **End:** March 15, 2026 (after last trade)

**MT5 will automatically include this range** when you download recent data.

---

## Troubleshooting

### Problem: "MetaTrader5 module not found"

**Solution:**
```bash
pip install MetaTrader5
```

### Problem: "MT5 initialization failed"

**Solutions:**
1. Make sure MT5 application is **running and logged in**
2. Try closing and reopening MT5
3. Make sure you're logged into a demo account (green connection indicator)
4. On Windows, run Python as Administrator

### Problem: "Symbol XAUUSD not found"

**Solutions:**
1. In MT5, right-click **Market Watch** → **Symbols**
2. Search for **XAUUSD** or **GOLD**
3. Click **Show** to enable it
4. Some brokers use **GOLD** instead of **XAUUSD** - check the symbol name

### Problem: Manual export doesn't show "Export Data" option

**Solution:**
1. Right-click the **chart area** (not the toolbar)
2. Or try: **Tools → History Center → XAUUSD → Select timeframe → Export**

### Problem: Downloaded data is in wrong format

**Expected CSV format:**
```
timestamp,open,high,low,close,volume
2026-02-15 00:00:00,2050.50,2051.20,2049.80,2050.90,1234
2026-02-15 00:05:00,2050.90,2051.50,2050.60,2051.10,2345
...
```

If your format is different, let me know and I'll create a converter.

---

## Data Size Expectations

Approximate file sizes:
- **M5**: ~50-100 MB (very detailed)
- **M15**: ~20-40 MB
- **H1**: ~5-10 MB
- **H4**: ~2-5 MB

If files are much smaller, you might not have enough historical data.

---

## Next Steps After Download

1. ✅ Verify files exist in `data/` folder
2. ✅ Go to Project 1 → Configuration
3. ✅ Click "Check Data Status"
4. ✅ Should show: "✓ Price Data: All 4 timeframes present"
5. ✅ Go to Run Scenarios and execute!

---

## Summary

**Recommended Path:**
1. Install MT5 (5 minutes)
2. Create demo account (2 minutes)
3. Run Python download script (5 minutes)
4. Start reverse engineering! 🚀

**If Python script fails:**
- Use manual export method
- Takes 10-15 minutes but always works

Let me know which method you prefer and I'll create the exact script you need!
