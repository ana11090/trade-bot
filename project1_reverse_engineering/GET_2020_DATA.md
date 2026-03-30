# How to Get XAUUSD Data from 2020

You currently have:
- **M5**: Oct 2024 - Mar 2026 (1.5 years) ⚠️
- **M15**: Feb 2024 - Mar 2026 (2 years) ⚠️
- **H1**: 2015 - 2026 (11 years) ✅
- **H4**: 2010 - 2026 (16 years) ✅

To get M5/M15 data from 2020, here are your options:

---

## Option 1: Free API Services (BEST OPTION)

### Alpha Vantage (Recommended - Free)

1. **Get Free API Key** (takes 30 seconds):
   - Go to: https://www.alphavantage.co/support/#api-key
   - Enter your email
   - Get instant API key (free forever, 500 calls/day)

2. **Download Data**:
   ```bash
   cd "C:\Users\anicu\OneDrive\Atasari\Documente\trade bo\trade-bot\project1_reverse_engineering"

   # Download M5 data
   python download_with_api_key.py --provider alphavantage --apikey YOUR_KEY_HERE --timeframe M5

   # Download M15 data
   python download_with_api_key.py --provider alphavantage --apikey YOUR_KEY_HERE --timeframe M15
   ```

3. **What You'll Get**:
   - Up to 5,000 recent candles per request
   - For M5: ~2 weeks of data
   - For M15: ~2 months of data
   - **Note**: Alpha Vantage doesn't have full 2020 history for intraday

### Twelve Data (Alternative - Free)

1. **Get Free API Key**:
   - Go to: https://twelvedata.com/pricing
   - Sign up for free tier (800 calls/day)

2. **Download**:
   ```bash
   python download_with_api_key.py --provider twelvedata --apikey YOUR_KEY --timeframe M5
   ```

---

## Option 2: Different MT5 Broker

Some brokers have deeper history than MetaQuotes-Demo:

### Recommended Brokers with Deep History:
1. **IC Markets** - Usually has 3-5 years of M5 data
2. **Pepperstone** - Usually has 2-3 years of M5 data
3. **FXCM** - Usually has 2+ years of M5 data

### How to Do This:
1. In MT5, go to **File → Open an Account**
2. Search for "IC Markets" or "Pepperstone"
3. Create demo account
4. Run: `python download_data_mt5.py`
5. You'll get much more historical data!

---

## Option 3: Manual Download from Websites

### Investing.com
1. Go to: https://www.investing.com/commodities/gold-historical-data
2. Set date range: 01/01/2020 to today
3. Set timeframe: 5 minute or 15 minute
4. Download CSV
5. Save to data folder

### HistData.com
1. Go to: http://www.histdata.com/download-free-forex-data/
2. Look for XAUUSD or GOLD
3. Download monthly ZIP files
4. Extract and combine

---

## Option 4: Premium Data Provider

If you need professional-grade data:

- **Dukascopy** (~$100/year) - Tick-level data from 2003
- **Polygon.io** ($200/mo) - Complete intraday history
- **IQFeed** - Real-time + historical

---

## Reality Check: Do You Really Need Data from 2020?

### What you currently have is actually EXCELLENT:

✅ **For backtesting strategies:**
- 1.5 years of M5 data = plenty for testing
- 2 years of M15 data = excellent sample size
- Most professional traders backtest on 6-12 months

✅ **For machine learning:**
- 100k+ candles = good training dataset
- Recent data is more relevant (market conditions change)

✅ **For analysis:**
- Your data covers different market conditions
- Includes recent volatility and trends

### When you DON'T need 2020 data:
- Testing short-term strategies (days/weeks)
- Recent market behavior analysis
- Quick strategy validation

### When you DO need 2020 data:
- Long-term trend analysis (multi-year)
- Economic cycle backtesting
- Academic research

---

## My Recommendation

**For most trading strategies, your current data is sufficient!**

But if you really want more:
1. Get Alpha Vantage API key (30 seconds, free)
2. Try different MT5 broker (5 minutes, free)
3. If still not enough, consider premium data

---

## Quick Start

Want to just get started? Run this:

```bash
# Check what you have
cd "C:\Users\anicu\OneDrive\Atasari\Documente\trade bo\trade-bot"
python main_app.py
# Go to Project 1 → Configuration → Check Data Status

# Should show: ✅ All 4 timeframes present
# That means you're ready to start your analysis!
```

Your data is good enough for 95% of trading strategies. Don't let perfect be the enemy of good! 🚀
