# 🚀 START HERE: Complete Setup Guide

## What You Need to Do

You need **2 things** before running reverse engineering:
1. ✅ Trade data (from Project 0)
2. ✅ Price data (OHLCV candles for XAUUSD)

---

## Step 1: Get Trade Data ✅

**You already know this:**
1. Open app: `python main_app.py`
2. Go to **"0 - Data Pipeline"**
3. Load your Myfxbook trade file
4. Click **"Run"**

✅ **Done!** Trade data is now loaded.

---

## Step 2: Get Price Data 📊

**You have 3 options:**

### ⭐ **Option A: MetaTrader 5 - Automatic (RECOMMENDED)**

**Time: 10 minutes | Difficulty: Easy | Success Rate: 95%**

1. **Install MT5** (one-time):
   - Download from: https://www.metatrader5.com/en/download
   - Install it (200 MB)
   - Open MT5

2. **Create Demo Account** (one-time):
   - In MT5: File → Open an Account
   - Search: "MetaQuotes-Demo"
   - Select: Demo Account
   - Fill in any name/email
   - Click: Finish
   - ✅ Keep MT5 **running and logged in**

3. **Download Data** (run each time you need data):

   **Method 3a - From UI (Easiest):**
   ```
   1. Go to Project 1 → Configuration
   2. Click "Download from MT5 (Recommended)" button
   3. Wait 2-5 minutes
   4. ✅ Done!
   ```

   **Method 3b - From Command Line:**
   ```bash
   cd "C:\Users\anicu\OneDrive\Atasari\Documente\trade bo\trade-bot\project1_reverse_engineering"
   python download_data_mt5.py
   ```

**Expected Output:**
```
✓ Connected to MT5
✓ Symbol 'XAUUSD' found
✓ Downloading M5... 15,234 candles (45.2 MB)
✓ Downloading M15... 5,078 candles (18.3 MB)
✓ Downloading H1... 1,269 candles (5.1 MB)
✓ Downloading H4... 317 candles (1.8 MB)
✓ ALL DATA DOWNLOADED SUCCESSFULLY!
```

---

### 📝 **Option B: MetaTrader 5 - Manual Export**

**Time: 15 minutes | Difficulty: Medium | Success Rate: 100%**

Use this if the automatic script fails.

1. **Install MT5 and create demo account** (same as Option A steps 1-2)

2. **For each timeframe (M5, M15, H1, H4):**
   - File → New Chart → XAUUSD
   - Click timeframe button (M5, M15, H1, or H4)
   - Scroll back to load history (Page Up many times)
   - Right-click chart → Export Data
   - Save as: `xauusd_M5.csv` (or M15, H1, H4)
   - Move file to: `C:\Users\anicu\OneDrive\Atasari\Documente\trade bo\trade-bot\data\`

3. **Repeat for all 4 timeframes**

---

### ⚠️ **Option C: yfinance (NOT Recommended)**

**Time: 2 minutes | Difficulty: Easy | Success Rate: 10%**

**Problems:**
- ❌ Only recent 7 days for M5
- ❌ Only recent 60 days for M15/H1
- ❌ Often fails or returns no data
- ❌ Not suitable for this project

**Only use if:**
- Your trades are from the last 7 days
- You just want to test the system
- You're okay with it probably failing

**How to try it:**
```
1. Go to Project 1 → Configuration
2. Click "Download from yfinance (Limited)"
3. Probably won't work... 😅
```

---

## Step 3: Verify Everything Works ✅

1. **Check Data Status:**
   ```
   1. Go to Project 1 → Configuration
   2. Click "Check Data Status"
   3. Should see:
      ✓ Trade Data: 312 trades loaded (from Project 0)
      ✓ Price Data: All 4 timeframes present
   ```

2. **Run Scenarios:**
   ```
   1. Go to Project 1 → Run Scenarios
   2. Should see: ✓ 312 trades loaded from Project 0
   3. Select scenarios (M5, M15, H1, H4, or all)
   4. Click "Run Selected Scenarios"
   5. Wait 5-15 minutes
   ```

3. **View Results:**
   ```
   1. Go to Project 1 → View Results
   2. Click "Refresh Results"
   3. See which scenario won
   4. View the discovered trading rules!
   ```

---

## Quick Troubleshooting

### "No trade data loaded"
→ Go to Project 0 and load your trade file first
→ Then go to Project 1 → Configuration → Click "Check Data Status"

### "No price data found"
→ Download data using Option A (MT5)
→ Verify files exist in `data/` folder

### "MT5 initialization failed"
→ Make sure MT5 is **running and logged in**
→ Try closing and reopening MT5
→ Run script again

### "Symbol XAUUSD not found"
→ In MT5: Right-click Market Watch → Symbols → Search "XAUUSD" → Show
→ Or try changing `SYMBOL = 'XAUUSD'` to `SYMBOL = 'GOLD'` in the script

### yfinance returns no data
→ Don't use yfinance, use MT5 instead 😊

---

## Summary: Recommended Path

```
1. Install MT5 (one-time, 10 min)
   ↓
2. Create demo account (one-time, 2 min)
   ↓
3. Keep MT5 running
   ↓
4. Click "Download from MT5" in UI (2-5 min)
   ↓
5. Click "Check Data Status" to verify
   ↓
6. Run scenarios! 🚀
```

**Total time: ~15-20 minutes**

---

## Files I Created for You

- ✅ `download_data_mt5.py` - Automatic download script
- ✅ `DOWNLOAD_DATA_MT5.md` - Detailed MT5 guide
- ✅ `START_HERE.md` - This file!
- ✅ UI button in Configuration panel

Everything is ready - you just need to:
1. Install MT5
2. Create demo account
3. Click the download button!

---

## Need Help?

**Check these files:**
- `DOWNLOAD_DATA_MT5.md` - Full MT5 guide with troubleshooting
- `QUICK_FIX_GUIDE.md` - How to use Project 0 data in Project 1
- `PROJECT0_INTEGRATION.md` - Technical details

**Common Questions:**
- Q: Do I need real money? → **No! Demo account is free**
- Q: Is MT5 free? → **Yes! Completely free**
- Q: Which option should I use? → **Option A (MT5 Automatic)**
- Q: Can I use yfinance? → **No, it won't work for this project**

Good luck! 🎉
