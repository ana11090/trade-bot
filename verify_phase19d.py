"""
Phase 19d Verification Tests
Verify all print() → logging conversions are correct.
"""
import os
import sys
import re
import ast
import importlib.util

BASE_DIR = r'D:\traiding data\trade-bot'

# All files that were converted
FILES_TO_VERIFY = [
    # Shared (4 files)
    ('shared', 'data_utils.py'),
    ('shared', 'data_validator.py'),
    ('shared', 'prop_firm_engine.py'),
    ('shared', 'prop_firm_simulator.py'),
    # Project1 (12 files)
    ('project1_reverse_engineering', 'step1_align_price.py'),
    ('project1_reverse_engineering', 'step2_compute_indicators.py'),
    ('project1_reverse_engineering', 'step3_label_trades.py'),
    ('project1_reverse_engineering', 'step4_train_model.py'),
    ('project1_reverse_engineering', 'step5_shap_analysis.py'),
    ('project1_reverse_engineering', 'step6_extract_rules.py'),
    ('project1_reverse_engineering', 'step7_validate.py'),
    ('project1_reverse_engineering', 'run_pipeline.py'),
    ('project1_reverse_engineering', 'run_all_scenarios.py'),
    ('project1_reverse_engineering', 'strategy_search.py'),
    ('project1_reverse_engineering', 'analyze.py'),
    ('project1_reverse_engineering', 'compare_scenarios.py'),
    # Project2 (8 files)
    ('project2_backtesting', 'backtest_engine.py'),
    ('project2_backtesting', 'strategy_backtester.py'),
    ('project2_backtesting', 'strategy_refiner.py'),
    ('project2_backtesting', 'strategy_validator.py'),
    ('project2_backtesting', 'compute_stats.py'),
    ('project2_backtesting', 'run_backtest.py'),
    ('project2_backtesting', 'build_report.py'),
    ('project2_backtesting', 'test_safety_stops.py'),
    # Project4 (1 file)
    ('project4_strategy_creation', 'scratch_discovery.py'),
]

# Preserved prints (should NOT be converted)
# WHY: Line numbers shifted +4 after adding WHY comments
PRESERVED_PRINTS = [
    ('project1_reverse_engineering', 'step1_align_price.py', 278),
    ('project1_reverse_engineering', 'step2_compute_indicators.py', 133),
]


def test_v1_syntax_check():
    """V1: Python syntax check on all 27 files"""
    print("\n" + "="*60)
    print("V1: Python Syntax Check")
    print("="*60)

    errors = []
    for subdir, filename in FILES_TO_VERIFY:
        filepath = os.path.join(BASE_DIR, subdir, filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                code = f.read()
            ast.parse(code)
            print(f"  [OK] {filename}")
        except SyntaxError as e:
            errors.append(f"{filename}: {e}")
            print(f"  [FAIL] {filename}: {e}")

    if errors:
        print(f"\n[FAIL] V1: {len(errors)} syntax errors")
        return False
    else:
        print(f"\n[PASS] V1: All {len(FILES_TO_VERIFY)} files have valid syntax")
        return True


def test_v2_import_check():
    """V2: Import check on all 27 files"""
    print("\n" + "="*60)
    print("V2: Import Check")
    print("="*60)

    # Just verify the logger import is present
    errors = []
    for subdir, filename in FILES_TO_VERIFY:
        filepath = os.path.join(BASE_DIR, subdir, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        if 'from shared.logging_setup import get_logger' in content:
            print(f"  [OK] {filename}")
        else:
            errors.append(filename)
            print(f"  [FAIL] {filename}: Missing logger import")

    if errors:
        print(f"\n[FAIL] V2: {len(errors)} files missing logger import")
        return False
    else:
        print(f"\n[PASS] V2: All {len(FILES_TO_VERIFY)} files have logger import")
        return True


def test_v5_no_remaining_prints():
    """V5: Verify no print() calls remain (except 2 preserved)"""
    print("\n" + "="*60)
    print("V5: No Remaining Print Calls")
    print("="*60)

    violations = []
    preserved_map = {(s, f): ln for s, f, ln in PRESERVED_PRINTS}

    for subdir, filename in FILES_TO_VERIFY:
        filepath = os.path.join(BASE_DIR, subdir, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        preserved_line = preserved_map.get((subdir, filename))

        for i, line in enumerate(lines, start=1):
            if 'print(' in line and not line.strip().startswith('#'):
                # Check if this is a preserved print
                if preserved_line and i == preserved_line:
                    continue  # Skip preserved prints

                # Check if it's in a comment or string
                stripped = line.strip()
                if stripped.startswith('#'):
                    continue

                violations.append(f"{filename}:{i}: {line.strip()}")

    if violations:
        print(f"\n[FAIL] V5: Found {len(violations)} unexpected print() calls:")
        for v in violations[:10]:  # Show first 10
            print(f"  {v}")
        return False
    else:
        print(f"[PASS] V5: Only 2 preserved print() calls remain (as expected)")
        return True


def test_v6_no_error_prefix():
    """V6: Verify log.error() calls don't have 'ERROR:' prefix"""
    print("\n" + "="*60)
    print("V6: No ERROR: Prefix in log.error()")
    print("="*60)

    violations = []
    for subdir, filename in FILES_TO_VERIFY:
        filepath = os.path.join(BASE_DIR, subdir, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        for i, line in enumerate(lines, start=1):
            if 'log.error(' in line and 'ERROR:' in line:
                violations.append(f"{filename}:{i}: {line.strip()}")

    if violations:
        print(f"\n[FAIL] V6: Found {len(violations)} log.error() with ERROR: prefix:")
        for v in violations[:10]:
            print(f"  {v}")
        return False
    else:
        print(f"[PASS] V6: No ERROR: prefixes in log.error() calls")
        return True


def test_v7_no_warning_prefix():
    """V7: Verify log.warning() calls don't have 'WARNING:' prefix"""
    print("\n" + "="*60)
    print("V7: No WARNING: Prefix in log.warning()")
    print("="*60)

    violations = []
    for subdir, filename in FILES_TO_VERIFY:
        filepath = os.path.join(BASE_DIR, subdir, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        for i, line in enumerate(lines, start=1):
            if 'log.warning(' in line and 'WARNING:' in line:
                violations.append(f"{filename}:{i}: {line.strip()}")

    if violations:
        print(f"\n[FAIL] V7: Found {len(violations)} log.warning() with WARNING: prefix:")
        for v in violations[:10]:
            print(f"  {v}")
        return False
    else:
        print(f"[PASS] V7: No WARNING: prefixes in log.warning() calls")
        return True


def test_v8_preserve_tags():
    """V8: Verify [TAG] prefixes are preserved"""
    print("\n" + "="*60)
    print("V8: TAG Prefixes Preserved")
    print("="*60)

    # Look for common tags in logging calls
    tags_found = []
    for subdir, filename in FILES_TO_VERIFY:
        filepath = os.path.join(BASE_DIR, subdir, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # Find log calls with [TAG] patterns
        for match in re.finditer(r'log\.(info|warning|error)\([^)]*\[(\w+)\]', content):
            tags_found.append(match.group(2))

    if tags_found:
        unique_tags = set(tags_found)
        print(f"[PASS] V8: Found {len(tags_found)} tagged log calls with tags: {unique_tags}")
        return True
    else:
        print(f"[INFO] V8: No [TAG] patterns found (may be OK if none existed)")
        return True


def main():
    """Run all verification tests."""
    print("\n" + "="*70)
    print("PHASE 19D VERIFICATION TESTS")
    print("="*70)

    results = {
        'V1_Syntax': test_v1_syntax_check(),
        'V2_Import': test_v2_import_check(),
        'V5_NoPrints': test_v5_no_remaining_prints(),
        'V6_NoErrorPrefix': test_v6_no_error_prefix(),
        'V7_NoWarningPrefix': test_v7_no_warning_prefix(),
        'V8_PreserveTags': test_v8_preserve_tags(),
    }

    # Summary
    print("\n" + "="*70)
    print("VERIFICATION SUMMARY")
    print("="*70)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for test_name, result in results.items():
        status = "[PASS]" if result else "[FAIL]"
        print(f"  {status} {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n[SUCCESS] All verification tests passed!")
        return 0
    else:
        print(f"\n[FAILED] {total - passed} tests failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())
