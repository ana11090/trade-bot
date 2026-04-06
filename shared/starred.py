"""
Starred Strategies — mark favorites for quick access across all panels.

WHY: With 36+ strategies in the dropdown, it's hard to find the best ones.
     Starring adds ⭐ to the label and sorts them to the top of every dropdown.
CHANGED: April 2026 — new feature
"""

import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
_STAR_PATH = os.path.join(os.path.dirname(_HERE), 'starred_strategies.json')


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
    try:
        with open(_STAR_PATH, 'w', encoding='utf-8') as f:
            json.dump(starred, f, indent=2)
    except Exception as e:
        print(f"[STARRED] Error saving: {e}")


def make_key(rule_combo, exit_strategy):
    """Create a unique key for a strategy.

    WHY: We need a stable identifier that doesn't change when stats update.
         rule_combo + exit_strategy is unique across backtest results.
    """
    return f"{rule_combo}|{exit_strategy}"


def is_starred(rule_combo, exit_strategy):
    """Check if a strategy is starred."""
    key = make_key(rule_combo, exit_strategy)
    return key in _load()


def star(rule_combo, exit_strategy):
    """Star a strategy."""
    key = make_key(rule_combo, exit_strategy)
    starred = _load()
    if key not in starred:
        starred.append(key)
        _save(starred)
        print(f"[STARRED] Added: {key}")


def unstar(rule_combo, exit_strategy):
    """Remove star from a strategy."""
    key = make_key(rule_combo, exit_strategy)
    starred = _load()
    if key in starred:
        starred.remove(key)
        _save(starred)
        print(f"[STARRED] Removed: {key}")


def toggle(rule_combo, exit_strategy):
    """Toggle star on/off. Returns new state (True = starred)."""
    if is_starred(rule_combo, exit_strategy):
        unstar(rule_combo, exit_strategy)
        return False
    else:
        star(rule_combo, exit_strategy)
        return True


def get_all_starred():
    """Get list of all starred keys."""
    return _load()
