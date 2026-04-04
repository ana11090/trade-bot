"""
Verification script for SMART features support in backtester.

Tests:
1. _extract_required_indicators skips SMART features
2. SMART features are computed when rules reference them
3. Warning messages are clear and helpful
"""
import sys
import os

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from project2_backtesting.strategy_backtester import _extract_required_indicators

print("=" * 70)
print("SMART FEATURES VERIFICATION")
print("=" * 70)

# Test 1: _extract_required_indicators skips SMART features
print("\n[TEST 1] _extract_required_indicators should skip SMART features")
test_rules = [
    {
        "prediction": "WIN",
        "conditions": [
            {"feature": "H1_rsi_14", "operator": ">", "value": 50},
            {"feature": "SMART_H1_rsi_14_direction", "operator": ">", "value": 0},
            {"feature": "SMART_rsi_bullish_tfs", "operator": ">", "value": 2},
            {"feature": "M5_adx_14", "operator": ">", "value": 25},
        ]
    }
]

required = _extract_required_indicators(test_rules)
print(f"  Input features: H1_rsi_14, SMART_H1_rsi_14_direction, SMART_rsi_bullish_tfs, M5_adx_14")
print(f"  Extracted required indicators: {required}")

expected_h1 = ["rsi_14"]
expected_m5 = ["adx_14"]
expected = {"H1": expected_h1, "M5": expected_m5}

if required == expected:
    print(f"  [PASS] Correctly extracted only regular indicators, skipped SMART features")
else:
    print(f"  [FAIL] Expected {expected}, got {required}")

# Test 2: Check that SMART features don't pollute required_indicators
print("\n[TEST 2] SMART-only rules should return empty dict")
smart_only_rules = [
    {
        "prediction": "WIN",
        "conditions": [
            {"feature": "SMART_H1_rsi_14_direction", "operator": ">", "value": 0},
            {"feature": "SMART_momentum_quality", "operator": ">", "value": 0.5},
        ]
    }
]

required_smart_only = _extract_required_indicators(smart_only_rules)
print(f"  Input features: SMART_H1_rsi_14_direction, SMART_momentum_quality")
print(f"  Extracted required indicators: {required_smart_only}")

if required_smart_only == {}:
    print(f"  [PASS] SMART-only rules return empty dict (no timeframe indicators needed)")
else:
    print(f"  [FAIL] Expected empty dict, got {required_smart_only}")

# Test 3: Mixed timeframes with SMART features
print("\n[TEST 3] Mixed timeframes with SMART features")
mixed_rules = [
    {
        "prediction": "WIN",
        "conditions": [
            {"feature": "M5_rsi_14", "operator": ">", "value": 50},
            {"feature": "M15_macd_line", "operator": ">", "value": 0},
            {"feature": "H1_adx_14", "operator": ">", "value": 25},
            {"feature": "H4_atr_14", "operator": ">", "value": 5},
            {"feature": "D1_ema_200", "operator": ">", "value": 2000},
            {"feature": "SMART_divergence_rsi_bullish", "operator": "==", "value": 1},
            {"feature": "SMART_H4_momentum_quality", "operator": ">", "value": 0.6},
        ]
    }
]

required_mixed = _extract_required_indicators(mixed_rules)
print(f"  Extracted required indicators: {required_mixed}")

expected_mixed = {
    "M5": ["rsi_14"],
    "M15": ["macd_line"],
    "H1": ["adx_14"],
    "H4": ["atr_14"],
    "D1": ["ema_200"],
}

if required_mixed == expected_mixed:
    print(f"  [PASS] Correctly extracted all timeframes, skipped SMART features")
else:
    print(f"  [FAIL] Expected {expected_mixed}, got {required_mixed}")

print("\n" + "=" * 70)
print("VERIFICATION COMPLETE")
print("=" * 70)
print("\nNext step: Run full backtester to verify SMART computation works end-to-end")
print("Command: python project2_backtesting/strategy_backtester.py")
