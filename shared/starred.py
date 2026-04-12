"""
Starred Strategies — mark favorites for quick access across all panels.

WHY: With 36+ strategies in the dropdown, it's hard to find the best ones.
     Starring adds ⭐ to the label and sorts them to the top of every dropdown.
CHANGED: April 2026 — new feature
"""

import os
import json
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_STAR_PATH = os.path.join(os.path.dirname(_HERE), 'starred_strategies.json')

# WHY (Phase 73 Fix 44): Two concurrent star/unstar operations race the JSON
#      write. User clicks star on strategy A and B simultaneously, only one
#      write completes (the other is clobbered). Use a write lock.
# CHANGED: April 2026 — Phase 73 Fix 44 — star/unstar write lock
#          (audit Part F HIGH #44)
_star_lock = threading.Lock()


def _load():
    """Load starred strategy keys from disk."""
    if os.path.exists(_STAR_PATH):
        try:
            with open(_STAR_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save(starred):
    """Save starred strategy keys to disk."""
    # WHY (Phase 73 Fix 45): Old code used open('w') which truncates the file
    #      immediately. A crash between truncate and json.dump completing left
    #      starred_strategies.json empty — all stars lost. Write to tempfile
    #      in same directory, then os.replace (atomic on POSIX + Windows 3.3+).
    # CHANGED: April 2026 — Phase 73 Fix 45 — atomic write via tempfile
    #          (audit Part F HIGH #45)
    dir_name = os.path.dirname(_STAR_PATH) or '.'
    fd, tmp_path = tempfile.mkstemp(
        suffix='.json', prefix='.tmp_starred_', dir=dir_name
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(starred, fh, indent=2)
        os.replace(tmp_path, _STAR_PATH)
    except Exception as e:
        print(f"[STARRED] Error saving: {e}")
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def make_key(rule_combo, exit_strategy, entry_tf=''):
    """Create a unique key for a strategy.

    WHY: We need a stable identifier that doesn't change when stats update.
         rule_combo + exit_strategy is unique across backtest results.
         entry_tf included when provided so multi-TF runs get separate star slots.
    CHANGED: April 2026 — multi-TF support (entry_tf optional for backwards compat)
    """
    if entry_tf:
        return f"{rule_combo}|{exit_strategy}|{entry_tf}"
    return f"{rule_combo}|{exit_strategy}"


def is_starred(rule_combo, exit_strategy, entry_tf=''):
    """Check if a strategy is starred."""
    key = make_key(rule_combo, exit_strategy, entry_tf)
    # Also check old key format (without TF) for backwards compatibility
    old_key = f"{rule_combo}|{exit_strategy}"
    loaded = _load()
    return key in loaded or (entry_tf and old_key in loaded)


def star(rule_combo, exit_strategy, entry_tf=''):
    """Star a strategy."""
    # WHY (Phase 73 Fix 46): Old code didn't lock around load-modify-save.
    #      Two concurrent star() calls both loaded the same list, appended
    #      their keys, but the second _save() clobbered the first. Wrap in lock.
    # CHANGED: April 2026 — Phase 73 Fix 46 — serialize star operations
    #          (audit Part F HIGH #46)
    key = make_key(rule_combo, exit_strategy, entry_tf)
    with _star_lock:
        starred = _load()
        if key not in starred:
            starred.append(key)
            _save(starred)
            print(f"[STARRED] Added: {key}")


def unstar(rule_combo, exit_strategy, entry_tf=''):
    """Remove star from a strategy."""
    # Phase 73 Fix 46: Wrap load-modify-save in lock
    key = make_key(rule_combo, exit_strategy, entry_tf)
    with _star_lock:
        starred = _load()
        if key in starred:
            starred.remove(key)
            _save(starred)
            print(f"[STARRED] Removed: {key}")
        # Also remove old key format if present
        old_key = f"{rule_combo}|{exit_strategy}"
        if entry_tf and old_key in starred:
            starred.remove(old_key)
            _save(starred)


def toggle(rule_combo, exit_strategy, entry_tf=''):
    """Toggle star on/off. Returns new state (True = starred)."""
    if is_starred(rule_combo, exit_strategy, entry_tf):
        unstar(rule_combo, exit_strategy, entry_tf)
        return False
    else:
        star(rule_combo, exit_strategy, entry_tf)
        return True


def get_all_starred():
    """Get list of all starred keys."""
    return _load()
