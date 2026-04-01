"""
Validate candle data quality.
Run after build_candles_from_ticks.py to check the output.

Usage: python validate_data.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.data_validator import validate_all_candles, cross_check_trades_vs_candles

print("=" * 70)
print("DATA VALIDATION REPORT")
print("=" * 70)

data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
results = validate_all_candles(data_dir)

print()
# Cross-check trades vs candles if trade history exists
trades_path = os.path.join("trade_histories", "original_bot", "trades_clean.csv")
candles_path = os.path.join(data_dir, "xauusd_H1.csv")
if os.path.exists(trades_path) and os.path.exists(candles_path):
    print("=" * 70)
    print("TRADE vs CANDLE CROSS-CHECK")
    print("=" * 70)
    mismatches = cross_check_trades_vs_candles(trades_path, candles_path)
    if mismatches:
        print(f"  {len(mismatches)} mismatches found (first 5):")
        for m in mismatches[:5]:
            print(f"    Trade: {m['trade_time']} @ ${m['trade_price']:.2f}")
            print(f"    Candle: {m['candle_time']} range ${m['candle_low']:.2f}-${m['candle_high']:.2f}")
            print()
    else:
        print("  All trade prices match candle data. OK")
else:
    if not os.path.exists(trades_path):
        print(f"Note: Trade history not found at {trades_path}, skipping cross-check")
    if not os.path.exists(candles_path):
        print(f"Note: Candle file not found at {candles_path}, skipping cross-check")
