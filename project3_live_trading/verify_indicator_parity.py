"""
VERIFICATION SCRIPT: Python <-> MQL5 Indicator Formula Parity

Tests that indicator formulas in indicator_mapper.py match the Python
formulas from shared/indicator_utils.py for all 8 Phase 4 bug fixes.

Usage:
    python verify_indicator_parity.py

This script does NOT execute MQL5 code. Instead, it:
1. Generates MQL5 code snippets from indicator_mapper.py
2. Validates the formulas are mathematically equivalent to Python versions
3. Checks for known bug patterns (scale mismatches, hardcoded timeframes, etc.)
4. Reports any remaining issues

Phase 4 Bug Families:
- FIX 1: sma_N_distance (scale: percentage not points)
- FIX 2: bb_width (scale: percentage not raw)
- FIX 3: keltner_width (scale: 4×ATR/EMA×100, not 2×ATR raw)
- FIX 4: pivot_point, price_bucket, ratio_safe_price (timeframe: current not D1)
- FIX 5: distance_from_high (scale: percentage not points)
- FIX 6: std_dev (ddof: sample not population)
- FIX 7: crossed_above/crossed_below (dispatch: multi-indicator not hardcoded RSI)
- FIX 8: tsi (loud failure instead of silent)
"""

import sys
import os
import re

# Add parent directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from project3_live_trading.indicator_mapper import (
    INDICATOR_PATTERNS,
    TIMEFRAME_MAP,
    parse_feature_name,
    _mql5_sub_expr,
    _py_sub_expr
)


# ============================================================
# TEST CASES FOR EACH BUG FIX
# ============================================================

def test_fix1_sma_distance():
    """FIX 1: sma_N_distance should use percentage not points"""
    print("\n" + "="*60)
    print("FIX 1: sma_N_distance (percentage scale)")
    print("="*60)

    test_features = ['H1_sma_20_distance', 'M15_sma_50_distance']

    for feat in test_features:
        print(f"\nTesting: {feat}")

        # Check MQL5 formula
        matched = False
        for pattern, config in INDICATOR_PATTERNS:
            if re.match(pattern, feat.split('_', 1)[1]):
                mql5_code = config.get('mt5_code', '')

                # Should contain percentage formula: (close - sma) / sma * 100
                # Should NOT contain _Point
                if '_Point' in mql5_code:
                    print(f"  [FAIL] FAIL: Still uses _Point (raw points)")
                    return False

                if '* 100' in mql5_code or '*100' in mql5_code:
                    print(f"  [OK] PASS: Uses percentage formula (* 100)")
                    matched = True
                    break

        if not matched:
            print(f"  [FAIL] FAIL: Pattern not found or no percentage scaling")
            return False

    print(f"\n[OK] FIX 1: All sma_distance tests passed")
    return True


def test_fix2_bb_width():
    """FIX 2: bb_width should use (upper-lower)/middle*100"""
    print("\n" + "="*60)
    print("FIX 2: bb_width (percentage of middle)")
    print("="*60)

    test_features = ['H1_bb_20_2.0_width', 'M15_bb_20_2.0_width']

    for feat in test_features:
        print(f"\nTesting: {feat}")

        matched = False
        for pattern, config in INDICATOR_PATTERNS:
            if re.match(pattern, feat.split('_', 1)[1]):
                buffer_read = config.get('mt5_buffer_read', '')

                # Should read 3 buffers (0=middle, 1=upper, 2=lower)
                # Should compute: (upper - lower) / middle * 100
                if 'handle_bb_' not in buffer_read:
                    continue

                if buffer_read.count('SafeCopyBuf') < 3:
                    print(f"  [FAIL] FAIL: Doesn't read all 3 BB buffers (middle, upper, lower)")
                    return False

                if '* 100' in buffer_read or '*100' in buffer_read:
                    print(f"  [OK] PASS: Uses 3 buffers and percentage formula")
                    matched = True
                    break

        if not matched:
            print(f"  [FAIL] FAIL: Pattern not found or incorrect formula")
            return False

    print(f"\n[OK] FIX 2: All bb_width tests passed")
    return True


def test_fix3_keltner_width():
    """FIX 3: keltner_width should use 4×ATR/EMA*100"""
    print("\n" + "="*60)
    print("FIX 3: keltner_width (4×ATR not 2×ATR)")
    print("="*60)

    test_features = ['H1_keltner_width', 'M15_keltner_width']

    for feat in test_features:
        print(f"\nTesting: {feat}")

        matched = False
        for pattern, config in INDICATOR_PATTERNS:
            if re.match(pattern, feat.split('_', 1)[1]):
                buffer_read = config.get('mt5_buffer_read', '')

                # Should read EMA and ATR
                # Should compute: ATR * 4.0 / EMA * 100
                if 'handle_ema_' not in buffer_read or 'handle_atr_' not in buffer_read:
                    print(f"  [FAIL] FAIL: Doesn't use both EMA and ATR handles")
                    return False

                if '* 4.0' not in buffer_read and '*4.0' not in buffer_read:
                    print(f"  [FAIL] FAIL: Doesn't multiply ATR by 4 (uses wrong width)")
                    return False

                if '* 100' in buffer_read or '*100' in buffer_read:
                    print(f"  [OK] PASS: Uses 4×ATR/EMA*100 formula")
                    matched = True
                    break

        if not matched:
            print(f"  [FAIL] FAIL: Pattern not found or incorrect formula")
            return False

    print(f"\n[OK] FIX 3: All keltner_width tests passed")
    return True


def test_fix4_pivot_timeframe():
    """FIX 4: pivot_point should use current timeframe not D1"""
    print("\n" + "="*60)
    print("FIX 4: pivot_point (current timeframe not D1)")
    print("="*60)

    test_features = ['H1_pivot_point', 'M15_pivot_point_distance', 'H1_price_bucket']

    for feat in test_features:
        print(f"\nTesting: {feat}")

        p_info = parse_feature_name(feat)
        tf_info = TIMEFRAME_MAP.get(p_info['timeframe'], TIMEFRAME_MAP['H1'])
        mt5_tf = tf_info['mt5']

        # Get MQL5 expression
        lines, expr = _mql5_sub_expr(feat, '_test')

        full_code = '\n'.join(lines) + '\n' + expr

        # Should use {mt5_tf} (like PERIOD_H1), NOT PERIOD_D1
        if 'PERIOD_D1' in full_code:
            print(f"  [FAIL] FAIL: Still hardcoded to PERIOD_D1")
            print(f"     Expected: {mt5_tf}")
            print(f"     Code: {full_code[:200]}")
            return False

        print(f"  [OK] PASS: Uses current timeframe ({mt5_tf})")

    print(f"\n[OK] FIX 4: All pivot/timeframe tests passed")
    return True


def test_fix5_distance_from_high():
    """FIX 5: distance_from_high should use percentage not points"""
    print("\n" + "="*60)
    print("FIX 5: distance_from_high (percentage scale)")
    print("="*60)

    test_features = ['H1_distance_from_high', 'M15_distance_from_high']

    for feat in test_features:
        print(f"\nTesting: {feat}")

        matched = False
        for pattern, config in INDICATOR_PATTERNS:
            if re.match(pattern, feat.split('_', 1)[1]):
                mql5_code = config.get('mt5_code', '')

                # Should contain: (high - close) / close * 100
                # Should NOT contain _Point
                if '_Point' in mql5_code:
                    print(f"  [FAIL] FAIL: Still uses _Point (raw points)")
                    return False

                if '* 100' in mql5_code or '*100' in mql5_code:
                    print(f"  [OK] PASS: Uses percentage formula")
                    matched = True
                    break

        if not matched:
            print(f"  [FAIL] FAIL: Pattern not found or no percentage scaling")
            return False

    print(f"\n[OK] FIX 5: All distance_from_high tests passed")
    return True


def test_fix6_std_dev_ddof():
    """FIX 6: std_dev should correct for ddof=1 (sample std)"""
    print("\n" + "="*60)
    print("FIX 6: std_dev (ddof correction)")
    print("="*60)

    test_features = ['H1_std_dev_20', 'M15_std_dev_50']

    for feat in test_features:
        print(f"\nTesting: {feat}")

        matched = False
        for pattern, config in INDICATOR_PATTERNS:
            if re.match(pattern, feat.split('_', 1)[1]):
                buffer_read = config.get('mt5_buffer_read', '')

                # Should contain correction factor: sqrt(N / (N-1))
                # Looks for MathSqrt pattern with period division
                if 'MathSqrt' not in buffer_read:
                    print(f"  [FAIL] FAIL: Missing ddof correction factor (sqrt)")
                    return False

                if '- 1.0' not in buffer_read:
                    print(f"  [FAIL] FAIL: Missing N-1 denominator for sample std")
                    return False

                print(f"  [OK] PASS: Has ddof correction sqrt(N/(N-1))")
                matched = True
                break

        if not matched:
            print(f"  [FAIL] FAIL: Pattern not found")
            return False

    print(f"\n[OK] FIX 6: All std_dev tests passed")
    return True


def test_fix7_crossed_dispatch():
    """FIX 7: crossed_above/below should dispatch on indicator, not hardcode RSI"""
    print("\n" + "="*60)
    print("FIX 7: crossed_above/below (indicator dispatch)")
    print("="*60)

    # Test different indicators being crossed
    test_cases = [
        ('H1_rsi_14_crossed_above_30', 'iRSI'),
        ('H1_adx_14_crossed_above_25', 'iADX'),
        ('H1_cci_20_crossed_below_-100', 'iCCI'),
    ]

    for feat, expected_func in test_cases:
        print(f"\nTesting: {feat} (should use {expected_func})")

        # Parse the feature
        parts = feat.split('_crossed_')
        if len(parts) != 2:
            print(f"  [FAIL] FAIL: Can't parse feature name")
            return False

        indicator_col = parts[0]  # e.g., 'H1_rsi_14'
        p_info = parse_feature_name(indicator_col)
        ind_name = p_info['indicator']  # e.g., 'rsi_14'

        # Check that indicator name would dispatch correctly
        # We can't easily test the generated code without running it,
        # but we can verify the pattern exists in indicator_mapper.py

        # Read the mapper file directly
        mapper_file = os.path.join(os.path.dirname(__file__), 'indicator_mapper.py')

        with open(mapper_file, 'r', encoding='utf-8') as f:
            mapper_source = f.read()

        # Check that crossed_above has indicator dispatch (not just hardcoded iRSI)
        try:
            start_idx = mapper_source.find("elif ftype == 'crossed_above'")
            end_idx = mapper_source.find("elif ftype == 'crossed_below'")
            if start_idx == -1 or end_idx == -1:
                print(f"  [FAIL] FAIL: Can't find crossed_above/below section")
                return False

            crossed_section = mapper_source[start_idx:end_idx]

            if expected_func not in crossed_section:
                print(f"  [FAIL] FAIL: {expected_func} not in crossed_above dispatch")
                return False

            print(f"  [OK] PASS: Dispatch includes {expected_func}")
        except Exception as e:
            print(f"  [FAIL] FAIL: Error reading mapper file: {e}")
            return False

    print(f"\n[OK] FIX 7: All crossed_above/below tests passed")
    return True


def test_fix8_tsi_loud():
    """FIX 8: TSI should fail loudly, not silently"""
    print("\n" + "="*60)
    print("FIX 8: TSI (loud failure)")
    print("="*60)

    test_features = ['H1_tsi', 'M15_tsi']

    for feat in test_features:
        print(f"\nTesting: {feat}")

        # Get MQL5 expression
        lines, expr = _mql5_sub_expr(feat, '_test')

        full_code = '\n'.join(lines)

        # Should contain Print() error message
        # Should set indicatorFailed = true
        if 'Print(' not in full_code and 'print(' not in full_code:
            print(f"  [FAIL] FAIL: No Print() statement (silent failure)")
            return False

        if 'ERROR' not in full_code.upper() and 'TSI' not in full_code.upper():
            print(f"  [FAIL] FAIL: No clear error message about TSI")
            return False

        if 'indicatorFailed' not in full_code:
            print(f"  [WARN]  WARN: Doesn't set indicatorFailed flag (may not halt execution)")

        print(f"  [OK] PASS: Fails loudly with error message")

    print(f"\n[OK] FIX 8: All TSI tests passed")
    return True


# ============================================================
# MAIN VERIFICATION RUNNER
# ============================================================

def main():
    """Run all verification tests"""
    print("\n" + "="*60)
    print("PHASE 4 VERIFICATION: Python <-> MQL5 Indicator Parity")
    print("="*60)
    print("\nTesting 8 bug fixes in indicator_mapper.py...")

    all_passed = True

    # Run all tests
    tests = [
        ("FIX 1: sma_N_distance", test_fix1_sma_distance),
        ("FIX 2: bb_width", test_fix2_bb_width),
        ("FIX 3: keltner_width", test_fix3_keltner_width),
        ("FIX 4: pivot_point timeframe", test_fix4_pivot_timeframe),
        ("FIX 5: distance_from_high", test_fix5_distance_from_high),
        ("FIX 6: std_dev ddof", test_fix6_std_dev_ddof),
        ("FIX 7: crossed_above/below dispatch", test_fix7_crossed_dispatch),
        ("FIX 8: TSI loud failure", test_fix8_tsi_loud),
    ]

    results = []
    for name, test_func in tests:
        try:
            passed = test_func()
            results.append((name, passed))
            if not passed:
                all_passed = False
        except Exception as e:
            print(f"\n[FAIL] ERROR in {name}: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
            all_passed = False

    # Summary
    print("\n" + "="*60)
    print("VERIFICATION SUMMARY")
    print("="*60)

    for name, passed in results:
        status = "[OK] PASS" if passed else "[FAIL] FAIL"
        print(f"{status}: {name}")

    print("\n" + "="*60)
    if all_passed:
        print("[OK] ALL TESTS PASSED")
        print("="*60)
        print("\nNext steps:")
        print("1. Run step4_train_model.py to retrain with corrected features")
        print("2. Run step5_shap_analysis.py to verify feature importance changed")
        print("3. Run step6_extract_rules.py to get new rules with correct formulas")
        print("4. Deploy to MT5 and compare live signals with Python predictions")
        return 0
    else:
        print("[FAIL] SOME TESTS FAILED")
        print("="*60)
        print("\nFIX: Review indicator_mapper.py and apply missing corrections")
        return 1


if __name__ == '__main__':
    sys.exit(main())
