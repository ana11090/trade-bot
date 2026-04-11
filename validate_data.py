"""
Validate candle data quality.
Run after build_candles_from_ticks.py to check the output.

Usage: python validate_data.py [history_id] [symbol] [timeframe]
       python validate_data.py                    # use active history, XAUUSD, H1
       python validate_data.py my_bot             # use my_bot history, XAUUSD, H1
       python validate_data.py my_bot EURUSD M15  # specific everything

CHANGED: April 2026 — active history detection + CLI args (audit LOW #75, Phase 21)
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
# WHY: Old code hardcoded "original_bot" + "xauusd_H1" — only worked
#      for one specific user setup. Phase 21 detects the active
#      trade history from trade_history_manager and accepts CLI
#      overrides for symbol and timeframe.

# Parse CLI args
history_id = sys.argv[1] if len(sys.argv) > 1 else None
symbol     = sys.argv[2] if len(sys.argv) > 2 else "XAUUSD"
tf         = sys.argv[3] if len(sys.argv) > 3 else "H1"

# Determine history_id
if history_id is None:
    try:
        from shared.trade_history_manager import get_active_history
        active = get_active_history()
        if active:
            history_id = active.get("history_id")
            print(f"Using active history: {history_id}")
        else:
            history_id = "original_bot"  # legacy default
            print(f"No active history set; defaulting to: {history_id}")
    except Exception as e:
        history_id = "original_bot"
        print(f"Could not detect active history ({e}); defaulting to: {history_id}")

trades_path = os.path.join("trade_histories", history_id, "trades_clean.csv")
# Build candles path from symbol + tf (lowercase for filename consistency)
candles_filename = f"{symbol.lower()}_{tf}.csv"
candles_path = os.path.join(data_dir, candles_filename)

if os.path.exists(trades_path) and os.path.exists(candles_path):
    print("=" * 70)
    print(f"TRADE vs CANDLE CROSS-CHECK ({history_id} | {symbol} {tf})")
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
        print(f"Note: Candles file not found at {candles_path}, skipping cross-check")
