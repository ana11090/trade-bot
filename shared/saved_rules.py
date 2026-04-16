"""
Saved Rules — bookmark any rule from anywhere in the app.
Rules are saved to saved_rules.json and can be loaded, deleted, or sent to backtester.
"""

import os
import json
import tempfile
import threading
from datetime import datetime

_SAVE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'saved_rules.json')

# WHY (Phase 73 Fix 40): Two concurrent save_rule or delete_rule operations
#      race the JSON write. User bookmarks rule A from backtest panel and
#      rule B from analysis panel simultaneously — only one write completes
#      (the other is clobbered). Use a write lock.
# CHANGED: April 2026 — Phase 73 Fix 40 — save/delete write lock
#          (audit Part F HIGH #40)
_save_lock = threading.Lock()


# WHY (Phase A.40a.1): When a rule is saved or deleted, other panels
#      displaying library counts (Run Backtest source dropdown, etc.)
#      need to refresh. Listeners register here; every mutation notifies
#      them. Kept simple (list of callables) to avoid importing UI
#      frameworks in a data-layer module.
#
#      Listeners MUST be fast (they fire inside the write path) and
#      MUST NOT block or raise. If a listener wraps widget updates, it
#      MUST marshal to the UI main thread itself (e.g. tk.after(0,...))
#      — save/delete can be called from worker threads and touching
#      widgets directly from a worker thread will crash Tk.
# CHANGED: April 2026 — Phase A.40a.1
_change_listeners = []
_listeners_lock = threading.Lock()


def register_change_listener(cb):
    """Register a callable to be invoked after any library mutation.

    The callback receives two args: event ('save' | 'delete' | 'delete_all')
    and a payload dict ({'id': int} for save/delete, {} for delete_all).

    Returns the callable itself so callers can hold a ref for later
    unregistration. Silently ignores duplicate registrations.
    """
    with _listeners_lock:
        if cb not in _change_listeners:
            _change_listeners.append(cb)
    return cb


def unregister_change_listener(cb):
    """Remove a previously-registered listener. No-op if not found."""
    with _listeners_lock:
        try:
            _change_listeners.remove(cb)
        except ValueError:
            pass


def _notify_change(event, payload):
    """Fire all registered listeners. Never raises — listener failures
    are swallowed so they can't break the save/delete write path."""
    with _listeners_lock:
        listeners = list(_change_listeners)
    for cb in listeners:
        try:
            cb(event, dict(payload))
        except Exception:
            # Listeners must be safe; we don't propagate their failures
            # but we also don't let them spam our logs. If a listener
            # is broken, it silently stops updating. A bug caught at
            # develop time, not a runtime failure mode for users.
            pass


# WHY: All write paths previously used open(w) which truncates the file
#      immediately. A crash between truncate and json.dump completing
#      leaves saved_rules.json empty or partial — all saved rules lost.
#      Fix: write to a tempfile in the same directory, then os.replace
#      which is atomic (POSIX-atomic on Unix, atomic on Windows since
#      Python 3.3). The source file is never in a partial state.
# CHANGED: April 2026 — atomic writes (audit MED #67)
def _atomic_write_json(data, path):
    """Write JSON data to `path` atomically via tempfile + rename."""
    dir_name = os.path.dirname(path) or '.'
    fd, tmp_path = tempfile.mkstemp(
        suffix='.json', prefix='.tmp_saved_rules_', dir=dir_name
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2, default=str)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_all():
    """Load all saved rules. Returns list of dicts."""
    if os.path.exists(_SAVE_PATH):
        try:
            with open(_SAVE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_rule(rule, source="unknown", notes=""):
    """
    Save a rule for later.

    Args:
        rule: dict with 'conditions', 'prediction', 'win_rate', etc.
        source: where it came from ("Robot Analysis", "XGBoost", "Scratch", "Backtest Result", etc.)
        notes: optional user note
    """
    # Phase 73 Fix 40: Wrap load-modify-save in lock to prevent concurrent write races
    with _save_lock:
        all_rules = load_all()

        # WHY: Old code used id = len(all_rules) + 1 which produces DUPLICATE
        #      IDs after a middle-delete:
        #        1. save A→id=1, B→id=2, C→id=3  (list: [A,B,C])
        #        2. delete_rule(2) → [A,C] (len=2)
        #        3. save D → id = len+1 = 3  ← COLLISION with C.id=3
        #        4. delete_rule(3) → removes BOTH C and D (silent data loss)
        #      Fix: use max(existing_ids)+1. Monotonic growth means IDs are
        #      never reused even after deletes.
        # CHANGED: April 2026 — unique IDs via max+1 (audit MED)
        existing_ids = [r.get("id", 0) for r in all_rules if isinstance(r.get("id"), int)]
        new_id       = max(existing_ids, default=0) + 1

        entry = {
            "id": new_id,
            "saved_at": datetime.now().isoformat(),
            "source": source,
            "notes": notes,
            "rule": rule,
        }

        all_rules.append(entry)

        # CHANGED: April 2026 — atomic write (audit MED #67)
        _atomic_write_json(all_rules, _SAVE_PATH)

    # WHY (Phase A.40a.1): Notify listeners OUTSIDE the _save_lock so a
    #      slow listener can't block other save/delete operations. The
    #      library state is already persisted by this point — listeners
    #      observe committed state.
    # CHANGED: April 2026 — Phase A.40a.1
    _notify_change('save', {'id': entry["id"]})

    return entry["id"]


def delete_rule(rule_id):
    """Delete a saved rule by ID."""
    # Phase 73 Fix 40: Wrap load-modify-save in lock
    with _save_lock:
        all_rules = load_all()
        all_rules = [r for r in all_rules if r.get("id") != rule_id]

        # CHANGED: April 2026 — atomic write (audit MED #67)
        _atomic_write_json(all_rules, _SAVE_PATH)

    # WHY (Phase A.40a.1): notify listeners AFTER the lock is released
    #      so they observe committed state without blocking other writes.
    # CHANGED: April 2026 — Phase A.40a.1
    _notify_change('delete', {'id': rule_id})


def delete_all():
    """Delete all saved rules."""
    # Phase 73 Fix 40: Wrap write in lock
    with _save_lock:
        # CHANGED: April 2026 — atomic write (audit MED #67)
        _atomic_write_json([], _SAVE_PATH)

    # WHY (Phase A.40a.1): notify listeners AFTER the lock is released.
    # CHANGED: April 2026 — Phase A.40a.1
    _notify_change('delete_all', {})


def export_to_report(rule_ids=None):
    """
    Export saved rules to analysis_report.json format.
    If rule_ids is None, exports all saved rules.
    """
    all_rules = load_all()

    if rule_ids:
        selected = [r for r in all_rules if r.get("id") in rule_ids]
    else:
        selected = all_rules

    return [r["rule"] for r in selected]


def build_save_button(parent, rule, source="unknown", bg="#ffffff"):
    """
    Create a small save/bookmark button.
    Place next to any rule display in the app.

    Args:
        parent: parent tk widget
        rule: the rule dict to save
        source: label for where it came from
        bg: background color

    Returns: the button widget
    """
    import tkinter as tk
    from tkinter import messagebox, simpledialog

    def _save():
        notes = simpledialog.askstring("Save Rule", "Add a note (optional):", parent=parent)
        if notes is None:
            notes = ""
        rule_id = save_rule(rule, source=source, notes=notes)
        messagebox.showinfo("Saved", f"Rule saved! (ID: {rule_id})\n\nView in saved rules panel.")
        btn.config(text="💾 ✓", state="disabled")

    btn = tk.Button(
        parent, text="💾 Save",
        font=("Arial", 8, "bold"),
        bg="#667eea", fg="white",
        relief=tk.FLAT, cursor="hand2",
        padx=6, pady=1,
        command=_save,
    )

    return btn
