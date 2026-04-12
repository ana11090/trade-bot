"""
Phase 73 Verification Tests — saved_rules, starred, feature_toggles HIGHs
Verifies:
  - Fix 40: Thread safety for saved_rules operations (save/delete lock)
  - Fix 44: Thread safety for starred operations (star/unstar lock)
  - Fix 45: Atomic writes for starred via tempfile
  - Fix 46: Serialized star operations (load-modify-save in lock)
  - Fix 47: Thread safety for feature_toggles (settings lock)
  - Fix 48: Atomic writes for feature_toggles via tempfile
"""

import sys
import os
import json
import tempfile
import threading
import time

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'shared'))

from saved_rules import save_rule, delete_rule, load_all as load_rules
from starred import star, unstar, is_starred, _load as load_starred
from feature_toggles import save as save_settings, load as load_settings


def test_fix40_saved_rules_thread_safety():
    """
    V1: Verify saved_rules operations are thread-safe.

    Scenario: Concurrently save 10 rules from 3 threads (30 total)
    Expected: All 30 rules saved without corruption or lost writes
    """
    print("\n=== V1: Fix 40 — saved_rules thread safety ===")

    # Clear existing rules
    import shared.saved_rules as sr
    with sr._save_lock:
        sr._atomic_write_json([], sr._SAVE_PATH)

    saved_ids = []
    lock = threading.Lock()

    def _save_worker(thread_id):
        for i in range(10):
            rule = {
                'conditions': [{'feature': f'thread{thread_id}_rule{i}', 'operator': '>', 'value': 0}],
                'win_rate': 0.65,
                'avg_pips': 10
            }
            rule_id = save_rule(rule, source=f"Thread{thread_id}", notes=f"Rule{i}")
            with lock:
                saved_ids.append(rule_id)
            time.sleep(0.001)  # Small delay to increase race likelihood

    threads = []
    for t_id in range(3):
        t = threading.Thread(target=_save_worker, args=(t_id,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    all_rules = load_rules()

    print(f"  Threads launched: 3 (10 saves each = 30 total)")
    print(f"  Rules saved successfully: {len(saved_ids)}")
    print(f"  Rules in JSON: {len(all_rules)}")
    print(f"  All IDs unique: {len(saved_ids) == len(set(saved_ids))}")

    assert len(saved_ids) == 30, f"Expected 30 saves, got {len(saved_ids)}"
    assert len(all_rules) == 30, f"Expected 30 rules in JSON, got {len(all_rules)}"
    assert len(saved_ids) == len(set(saved_ids)), "Duplicate rule IDs detected"

    # Verify all rules have unique features (no clobbering)
    features = [r['rule']['conditions'][0]['feature'] for r in all_rules]
    assert len(features) == len(set(features)), "Duplicate features — some writes were clobbered"

    print("  [OK] Fix 40 verified: saved_rules operations are thread-safe")


def test_fix44_45_46_starred_thread_safety_and_atomic():
    """
    V2: Verify starred operations are thread-safe and use atomic writes.

    Scenario: Concurrently star/unstar 10 strategies from 3 threads
    Expected: All operations complete without corruption, file uses atomic writes
    """
    print("\n=== V2: Fix 44, 45, 46 — starred thread safety + atomic writes ===")

    # Clear existing stars
    import shared.starred as st
    with st._star_lock:
        st._save([])

    star_count = [0]
    lock = threading.Lock()

    def _star_worker(thread_id):
        for i in range(10):
            rule_combo = f"thread{thread_id}_rule{i}"
            exit_strat = "EXIT1"
            star(rule_combo, exit_strat)
            with lock:
                star_count[0] += 1
            time.sleep(0.001)

    threads = []
    for t_id in range(3):
        t = threading.Thread(target=_star_worker, args=(t_id,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    all_starred = load_starred()

    print(f"  Threads launched: 3 (10 stars each = 30 total)")
    print(f"  Star operations completed: {star_count[0]}")
    print(f"  Stars in JSON: {len(all_starred)}")

    assert star_count[0] == 30, f"Expected 30 star operations, got {star_count[0]}"
    assert len(all_starred) == 30, f"Expected 30 stars in JSON, got {len(all_starred)}"
    assert len(all_starred) == len(set(all_starred)), "Duplicate starred keys detected"

    # Verify atomic write: check that _save uses tempfile (Fix 45)
    # We can't directly test atomic behavior without crashing mid-write,
    # but we can verify the code path exists
    import inspect
    save_source = inspect.getsource(st._save)
    assert 'tempfile.mkstemp' in save_source, "Fix 45: _save should use tempfile.mkstemp for atomic writes"
    assert 'os.replace' in save_source, "Fix 45: _save should use os.replace for atomic writes"

    print("  [OK] Fix 44, 45, 46 verified: starred operations are thread-safe with atomic writes")


def test_fix47_48_feature_toggles_thread_safety_and_atomic():
    """
    V3: Verify feature_toggles operations are thread-safe and use atomic writes.

    Scenario: Concurrently toggle features from 3 threads
    Expected: All operations complete without corruption, file uses atomic writes
    """
    print("\n=== V3: Fix 47, 48 — feature_toggles thread safety + atomic writes ===")

    # Reset to defaults
    import shared.feature_toggles as ft
    with ft._settings_lock:
        ft._current = dict(ft._DEFAULTS)

    toggle_count = [0]
    lock = threading.Lock()

    def _toggle_worker(thread_id):
        for i in range(10):
            # Alternate between True/False
            smart_val = (thread_id + i) % 2 == 0
            regime_val = (thread_id + i) % 2 == 1
            save_settings(smart_features=smart_val, regime_features=regime_val)
            with lock:
                toggle_count[0] += 1
            time.sleep(0.001)

    threads = []
    for t_id in range(3):
        t = threading.Thread(target=_toggle_worker, args=(t_id,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    final_settings = load_settings()

    print(f"  Threads launched: 3 (10 saves each = 30 total)")
    print(f"  Save operations completed: {toggle_count[0]}")
    print(f"  Final settings: {final_settings}")

    assert toggle_count[0] == 30, f"Expected 30 save operations, got {toggle_count[0]}"
    assert 'smart_features' in final_settings, "smart_features missing from settings"
    assert 'regime_features' in final_settings, "regime_features missing from settings"

    # Verify atomic write: check that save uses tempfile (Fix 48)
    import inspect
    save_source = inspect.getsource(save_settings)
    assert 'tempfile.mkstemp' in save_source, "Fix 48: save should use tempfile.mkstemp for atomic writes"
    assert 'os.replace' in save_source, "Fix 48: save should use os.replace for atomic writes"

    # Verify locking: check that save and load use _settings_lock (Fix 47)
    assert 'with _settings_lock:' in save_source or 'with ft._settings_lock:' in save_source, \
        "Fix 47: save should acquire _settings_lock"

    load_source = inspect.getsource(load_settings)
    assert 'with _settings_lock:' in load_source or 'with ft._settings_lock:' in load_source, \
        "Fix 47: load should acquire _settings_lock"

    print("  [OK] Fix 47, 48 verified: feature_toggles operations are thread-safe with atomic writes")


def test_atomic_write_corruption_resistance():
    """
    V4: Verify atomic writes prevent corruption even with simulated failure.

    Scenario: Write to a file, then simulate a failure mid-write
    Expected: Original file remains intact (atomic replace not triggered)
    """
    print("\n=== V4: Atomic write corruption resistance ===")

    # Test with starred._save as it has atomic writes
    import shared.starred as st

    # Save initial state
    initial_stars = ["test1|EXIT1", "test2|EXIT2"]
    with st._star_lock:
        st._save(initial_stars)

    # Verify file exists and has content
    assert os.path.exists(st._STAR_PATH), "Star file should exist"
    with open(st._STAR_PATH, 'r') as f:
        saved = json.load(f)
    assert saved == initial_stars, "Initial stars should be saved"

    # Try to save with a simulated failure (can't easily force tempfile failure,
    # but we can verify that if _save raises, the original file is intact)
    try:
        # Temporarily break json.dump by passing non-serializable data
        # This will cause an exception after tempfile is created but before os.replace
        class BadObject:
            pass

        with st._star_lock:
            try:
                st._save([BadObject()])
            except TypeError:
                pass  # Expected — json.dump can't serialize BadObject
    except Exception as e:
        print(f"  Simulated failure: {e}")

    # Verify original file is still intact
    with open(st._STAR_PATH, 'r') as f:
        saved_after = json.load(f)

    print(f"  Original stars: {initial_stars}")
    print(f"  Stars after simulated failure: {saved_after}")

    assert saved_after == initial_stars, \
        "Original file should be intact after failed write (atomic protection)"

    print("  [OK] Atomic writes prevent corruption on failure")


if __name__ == '__main__':
    print("Phase 73 Verification Tests — saved_rules, starred, feature_toggles HIGHs")
    print("=" * 70)

    try:
        test_fix40_saved_rules_thread_safety()
        test_fix44_45_46_starred_thread_safety_and_atomic()
        test_fix47_48_feature_toggles_thread_safety_and_atomic()
        test_atomic_write_corruption_resistance()

        print("\n" + "=" * 70)
        print("[SUCCESS] All Phase 73 verification tests passed!")
        print("=" * 70)

    except AssertionError as e:
        print(f"\n[FAIL] Verification failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
