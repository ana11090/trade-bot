# MT5 Backtest Issue - Root Cause Analysis

**Date:** 2026-04-26
**Issue:** Great backtest results in Python app, terrible results in MT5

## Root Cause

**10x pip calculation error** in MT5 EA configuration

### Python Backtest (Correct)
- Uses ATR-based SL/TP (1.5x ATR for stop loss)
- Typical ATR on XAUUSD H1: 9-15 points
- **Actual SL distance: ~13-15 points** (1,300-1,500 pips)
- Results: 66.5% win rate, 3.74 profit factor

### MT5 EA (Incorrect)
- **Current config: SLPips = 150**
- XAUUSD pip size = 0.01 (2-digit pricing)
- **Actual SL distance: 150 × 0.01 = 1.5 points** ❌
- **This is 10x too small!**
- Result: Every single trade hits stop loss immediately

## Evidence from MT5 Logs

All trades stopped out within minutes:
```
BUY @ 4379.53, SL @ 4378.59 → Loss of 0.94 points
BUY @ 4377.49, SL @ 4375.98 → Loss of 1.51 points
BUY @ 4385.36, SL @ 4383.84 → Loss of 1.52 points
```

Final balance: $9,725.89 (started at $10,000) = -$274.11
Daily DD buffer hit: 2.7%

## Evidence from Python Backtest

Sample trades from backtest_trades_H1.json:
```json
Loss: -1352.3 pips = -13.52 points (SL hit)
Win: +2404.4 pips = +24.04 points (TP hit)
Win: +3191.5 pips = +31.91 points (TP hit)
```

Average SL distance: ~13-15 points (based on 1.5x ATR)

## The Solution

**Update MT5 EA parameters to match Python backtest:**

### Current (Wrong):
```mql5
input double SLPips = 150;    // 1.5 points
input double TPPips = 750;    // 7.5 points
```

### Should Be:
```mql5
input double SLPips = 1500;   // 15 points (10x bigger)
input double TPPips = 7500;   // 75 points (10x bigger)
```

Or better yet, **implement ATR-based SL/TP** in MT5 to match the Python backtest exactly:
```mql5
// Calculate ATR
double atr = iATR(_Symbol, PERIOD_H1, 14, 0);
double slDistance = atr * 1.5;  // in price points
double tpDistance = atr * 5.0;  // in price points
```

## Additional Discrepancies Found

| Setting | Python App | MT5 EA | Notes |
|---------|------------|--------|-------|
| Starting Capital | $10,000 | $25,000 | Different account size |
| Risk % | 1.0% | 0.3% | Different risk per trade |
| Spread | 25 pips (2.5 pts) | 5 pips max | Different spread assumptions |
| Exit Strategy | ATR-based | Fixed pips + Trailing | Different exit logic |

## Recommended Actions

1. **Immediate Fix:** Update SLPips to 1500 and TPPips to 7500 in MT5 EA
2. **Better Fix:** Implement ATR-based SL/TP calculation in MT5 EA to match Python exactly
3. **Validation:** Align all parameters (capital, risk%, spread) between Python and MT5
4. **Testing:** Run new MT5 backtest and compare results to Python backtest

## Files Analyzed

- MT5 EA: `C:\Users\anicu\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Experts\number216.mq5`
- MT5 Logs: `C:\Users\anicu\Downloads\logs mt5.txt`
- Python Backtest Engine: `D:\traiding data\trade-bot\project2_backtesting\backtest_engine.py`
- Python Backtest Config: `D:\traiding data\trade-bot\project2_backtesting\backtest_config.json`
- Python Backtest Results: `D:\traiding data\trade-bot\project2_backtesting\outputs\backtest_trades_H1.json`
