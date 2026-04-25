"""
Saved Rules — bookmark any rule from anywhere in the app.
Rules are saved to saved_rules.json and can be loaded, deleted, or sent to backtester.
"""

import os
import json
import tempfile
import threading
import hashlib
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


def _normalize_rule(rule):
    """Consolidate duplicate keys in rule dict.

    WHY: Rules from different sources use different key names for
         the same data (exit_class vs exit_name, entry_tf vs
         entry_timeframe, etc.). Normalize to canonical names.
    CHANGED: April 2026 — duplicate key consolidation
    """
    # Canonical key names
    if 'exit_class' in rule and 'exit_name' not in rule:
        rule['exit_name'] = rule.pop('exit_class')
    elif 'exit_class' in rule:
        del rule['exit_class']

    if 'exit_strategy_params' in rule and 'exit_params' not in rule:
        rule['exit_params'] = rule.pop('exit_strategy_params')
    elif 'exit_strategy_params' in rule:
        del rule['exit_strategy_params']

    if 'entry_tf' in rule and 'entry_timeframe' not in rule:
        rule['entry_timeframe'] = rule.pop('entry_tf')
    elif 'entry_tf' in rule:
        del rule['entry_tf']

    if 'action' in rule and 'direction' not in rule:
        rule['direction'] = rule.pop('action')
    elif 'action' in rule:
        del rule['action']

    return rule


def update_rule_field(rule_id, field, value):
    """Update a single field in a saved rule's rule dict.

    WHY: Status/grade/score updates need atomic read-modify-write.
         Used to track lifecycle: discovered → backtested → validated → deployed
    CHANGED: April 2026 — lifecycle status tracking
    """
    with _save_lock:
        all_rules = load_all()
        for entry in all_rules:
            # Support both numeric and string IDs
            if entry.get('id') == rule_id or entry.get('rule_id') == rule_id:
                entry['rule'][field] = value
                break
        _atomic_write_json(all_rules, _SAVE_PATH)

    # Notify listeners after successful update
    _notify_change('update', {'id': rule_id, 'field': field})


def _generate_rule_id(rule):
    """Generate a descriptive, unique rule ID.

    Format: {direction}_{timeframe}_{N}c_{MMDD}_{hash8}
    Example: BUY_M5_5c_0420_a7f3d9e2

    WHY: Sequential IDs (#24) tell you nothing. This format
         shows direction, timeframe, complexity, date, and
         a unique hash — all at a glance.

    WHY: Extended hash from 4 to 8 characters for better uniqueness.
         With 4 hex chars (16^4 = 65K combinations), collision risk
         was too high when saving hundreds of similar strategies.
         8 chars gives 16^8 = 4.3 billion combinations.
    CHANGED: April 2026 — descriptive rule IDs
    CHANGED: April 2026 — 8-char hash for uniqueness
    """
    # Direction
    direction = rule.get('direction', rule.get('action', rule.get('prediction', 'UNK')))
    if direction == 'WIN':
        direction = 'BUY'  # legacy format
    direction = direction.upper()[:4]

    # Timeframe
    tf = rule.get('entry_timeframe', rule.get('entry_tf', 'XX'))

    # Number of conditions
    conditions = rule.get('conditions', [])
    n_conds = len(conditions) if isinstance(conditions, list) else 0

    # Date (MMDD)
    # Use saved_at date if available (for backfill accuracy)
    mmdd = datetime.now().strftime('%m%d')
    saved_at = rule.get('saved_at', '')
    if saved_at:
        try:
            _parsed = datetime.fromisoformat(saved_at.replace('Z', '+00:00'))
            mmdd = _parsed.strftime('%m%d')
        except Exception:
            pass

    # Extended hash from conditions + exit strategy + timestamp (8 hex chars)
    # WHY: Same rule with different exit strategies must get different IDs.
    # WHY: Add timestamp component to ensure even identical rules saved
    #      at different times get unique IDs.
    # CHANGED: April 2026 — include exit strategy in hash
    # CHANGED: April 2026 — 8-char hash with timestamp for uniqueness
    import time
    cond_str = str(sorted(str(c) for c in conditions)) if conditions else ''
    exit_str = rule.get('exit_name', rule.get('exit_class', ''))
    exit_params = rule.get('exit_params', rule.get('exit_strategy_params', {}))
    if exit_params:
        exit_str += str(sorted(exit_params.items()) if isinstance(exit_params, dict) else str(exit_params))

    # Include high-precision timestamp to ensure uniqueness
    timestamp_str = str(time.time())
    hash_input = cond_str + exit_str + timestamp_str if (cond_str or exit_str) else str(rule) + timestamp_str
    hash8 = hashlib.md5(hash_input.encode()).hexdigest()[:8]

    return f"{direction}_{tf}_{n_conds}c_{mmdd}_{hash8}"


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

        # WHY: Descriptive IDs like BUY_M5_5c_0420_a7f3d9e2 are more useful
        #      than sequential numbers. Keep numeric id for backward compat
        #      but add descriptive rule_id as the display name.
        # WHY: With 8-char hash + timestamp, collisions are nearly impossible
        #      (4.3 billion combinations). Duplicate check kept as safety net.
        # CHANGED: April 2026 — descriptive rule IDs
        # CHANGED: April 2026 — 8-char hash makes duplicates extremely rare
        rule_id = _generate_rule_id(rule)
        # Handle duplicates (extremely rare with 8-char hash + timestamp)
        existing_rule_ids = [r.get("rule_id", "") for r in all_rules]
        if rule_id in existing_rule_ids:
            # Append counter: BUY_M5_5c_0420_a7f3d9e2_2
            counter = 2
            while f"{rule_id}_{counter}" in existing_rule_ids:
                counter += 1
            rule_id = f"{rule_id}_{counter}"

        # WHY: rule_combo is used for display in refiner dropdown.
        #      If empty or generic ("?"), build a readable one from conditions.
        # CHANGED: April 2026 — readable rule_combo
        _existing_combo = rule.get('rule_combo', '')
        if not _existing_combo or _existing_combo in ('?', '', 'Unknown'):
            _combo_parts = []
            _combo_dir = rule.get('direction', rule.get('action', 'BUY'))
            _combo_exit = rule.get('exit_name', rule.get('exit_class', ''))
            _combo_conds = rule.get('conditions', [])
            _combo_parts.append(_combo_dir)
            if _combo_conds:
                # Show first 2 condition features
                _combo_feats = [c.get('feature', '?').split('_', 1)[-1][:15] for c in _combo_conds[:2]]
                _combo_parts.append('+'.join(_combo_feats))
            if _combo_exit and _combo_exit not in ('?', 'Default', ''):
                _combo_parts.append(_combo_exit)
            rule['rule_combo'] = ' | '.join(_combo_parts)

        # WHY: Every rule must carry firm info and lifecycle status.
        #      Fill missing fields from P1 config at save time.
        # CHANGED: April 2026 — auto-enrich rules at save
        _defaults = {
            'status': 'discovered',  # discovered → backtested → validated → deployed
            'grade': '',
            'score': 0,
            'prop_firm_name': '',
            'leverage': 0,
            'pip_value_per_lot': 1.0,
            'spread_pips': 2.5,
            'risk_pct': 0,
            'risk_pct_firm': 0,
            'dd_daily_pct': 0,
            'dd_total_pct': 0,
            'account_size': 10000,
        }
        try:
            from project1_reverse_engineering.config_loader import ConfigLoader
            _p1 = ConfigLoader('p1_config').load()
            _defaults['prop_firm_name'] = _p1.get('prop_firm_name', '')
            _defaults['leverage'] = int(_p1.get('prop_firm_leverage', 0))
            _defaults['pip_value_per_lot'] = float(_p1.get('pip_value_per_lot', 1.0))
            _defaults['spread_pips'] = float(_p1.get('spread', 2.5))
            try:
                _defaults['risk_pct'] = float(_p1.get('risk_pct', 0))
            except (TypeError, ValueError):
                pass
            try:
                _defaults['risk_pct_firm'] = float(_p1.get('risk_pct_firm', 0))
            except (TypeError, ValueError):
                pass
            try:
                _defaults['dd_daily_pct'] = float(_p1.get('dd_daily_pct', 0))
            except (TypeError, ValueError):
                pass
            try:
                _defaults['dd_total_pct'] = float(_p1.get('dd_total_pct', 0))
            except (TypeError, ValueError):
                pass
            try:
                _defaults['account_size'] = float(_p1.get('prop_firm_account', 10000))
            except (TypeError, ValueError):
                pass
        except Exception:
            pass

        # Normalize duplicate keys first
        rule = _normalize_rule(rule)

        # Apply defaults only if missing
        for k, v in _defaults.items():
            if k not in rule or not rule.get(k):
                rule[k] = v

        # WHY: Rules without direction produce 0 trades in backtester.
        #      Also, some rules have prediction=BUY/SELL instead of WIN.
        #      Normalize to WIN + direction format.
        # CHANGED: April 2026 — normalize prediction/direction at save time
        pred = rule.get('prediction', '')

        # If prediction is BUY or SELL, convert to WIN + direction
        if pred in ('BUY', 'SELL'):
            rule['direction'] = pred
            rule['prediction'] = 'WIN'
        # If prediction is WIN, ensure direction is set
        elif pred == 'WIN':
            if not rule.get('direction'):
                # Try action field (old format)
                if rule.get('action') in ('BUY', 'SELL'):
                    rule['direction'] = rule['action']
                else:
                    # Default to BUY if we can't determine
                    # (most strategies are long-only on gold)
                    rule['direction'] = 'BUY'
                    print(f"[SAVED RULES] Rule saved without direction — defaulted to BUY. "
                          f"Set explicitly if this is a SELL rule.")

        entry = {
            "id": new_id,           # numeric (backward compat for delete, etc.)
            "rule_id": rule_id,     # descriptive (for display)
            "saved_at": datetime.now().isoformat(),
            "source": source,
            "notes": notes,
            "rule": rule,
        }

        all_rules.append(entry)

        # CHANGED: April 2026 — atomic write (audit MED #67)
        _atomic_write_json(all_rules, _SAVE_PATH)

    # WHY: Print a summary after every save so the user can verify
    #      entry_tf, direction, trade count, etc. in the console.
    # CHANGED: April 2026 — save confirmation log
    _saved_tf = rule.get('entry_timeframe', rule.get('entry_tf', '?'))
    _saved_dir = rule.get('direction', '?')
    _saved_trades = len(rule.get('trades', []))
    _saved_combo = rule.get('rule_combo', '?')
    _saved_wr = rule.get('win_rate', '?')
    _saved_pf = rule.get('net_profit_factor', '?')
    print(f"\n{'='*50}")
    print(f"[SAVED RULES] Rule #{new_id} saved successfully")
    print(f"  Rule ID:        {rule_id}")
    print(f"  Name:           {_saved_combo}")
    print(f"  Entry TF:       {_saved_tf}")
    print(f"  Direction:      {_saved_dir}")
    print(f"  Trades:         {_saved_trades}")
    print(f"  Win Rate:       {_saved_wr}")
    print(f"  Profit Factor:  {_saved_pf}")
    print(f"  Source:         {source}")
    print(f"{'='*50}\n")

    # WHY (Phase A.40a.1): Notify listeners OUTSIDE the _save_lock so a
    #      slow listener can't block other save/delete operations. The
    #      library state is already persisted by this point — listeners
    #      observe committed state.
    # CHANGED: April 2026 — Phase A.40a.1
    _notify_change('save', {'id': entry["id"]})

    return entry["id"]


def fix_legacy_rules():
    """One-time fix: normalize prediction/direction for all existing saved rules.

    WHY: Rules saved before April 2026 may have prediction=BUY/SELL or
         missing direction. Normalize them to WIN + direction format.
    CHANGED: April 2026 — one-time legacy rule fix

    Returns: (fixed_count, total_count)
    """
    with _save_lock:
        all_rules = load_all()
        fixed_count = 0

        for entry in all_rules:
            rule = entry.get('rule', {})
            pred = rule.get('prediction', '')
            original_pred = pred
            original_dir = rule.get('direction', '')

            # If prediction is BUY or SELL, convert to WIN + direction
            if pred in ('BUY', 'SELL'):
                rule['direction'] = pred
                rule['prediction'] = 'WIN'
                fixed_count += 1
            # If prediction is WIN, ensure direction is set
            elif pred == 'WIN':
                if not rule.get('direction'):
                    # Try action field (old format)
                    if rule.get('action') in ('BUY', 'SELL'):
                        rule['direction'] = rule['action']
                    else:
                        # Default to BUY
                        rule['direction'] = 'BUY'
                    fixed_count += 1

        if fixed_count > 0:
            _atomic_write_json(all_rules, _SAVE_PATH)
            print(f"[SAVED RULES] Fixed {fixed_count} legacy rules (normalized prediction/direction)")

    return (fixed_count, len(all_rules))


def backfill_descriptive_ids():
    """Generate descriptive rule_ids for rules that don't have them.

    WHY: Rules saved before the descriptive ID feature only have
         numeric IDs. This generates descriptive IDs for all of them.
    CHANGED: April 2026 — backfill descriptive IDs

    Returns: number of rules updated
    """
    with _save_lock:
        all_rules = load_all()
        updated = 0
        for entry in all_rules:
            if not entry.get('rule_id'):
                rule = entry.get('rule', {})
                # Pass saved_at so the ID date matches original save date
                rule['saved_at'] = entry.get('saved_at', '')
                entry['rule_id'] = _generate_rule_id(rule)
                updated += 1
        if updated > 0:
            _atomic_write_json(all_rules, _SAVE_PATH)
    return updated


def delete_rule(rule_id):
    """Delete a saved rule by numeric ID or descriptive rule_id.

    WHY: Support both old numeric IDs and new descriptive rule_ids
         for backward compatibility.
    CHANGED: April 2026 — support both ID types
    """
    # Phase 73 Fix 40: Wrap load-modify-save in lock
    with _save_lock:
        all_rules = load_all()
        # Support both numeric and string IDs
        all_rules = [r for r in all_rules
                     if r.get("id") != rule_id and r.get("rule_id") != rule_id]

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


def update_all_rules_firm():
    """Update all saved rules with the current firm from P1 config.

    WHY: Rules saved when config had wrong firm name need to be
         updated to the correct firm. Runs once on import.
    CHANGED: April 2026 — batch firm update
    """
    try:
        import importlib.util
        _p1_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))),
            'project1_reverse_engineering', 'config_loader.py')
        _spec = importlib.util.spec_from_file_location('_p1cl', _p1_path)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _cfg = _mod.load()

        firm_name = _cfg.get('prop_firm_name', '')
        if not firm_name:
            return 0

        firm_data = {
            'prop_firm_name': firm_name,
            'prop_firm_id': _cfg.get('prop_firm_id', ''),
            'prop_firm_stage': _cfg.get('prop_firm_stage', ''),
            'account_size': float(_cfg.get('prop_firm_account', 10000)),
            'pip_value_per_lot': float(_cfg.get('pip_value_per_lot', 1.0)),
            'spread_pips': float(_cfg.get('spread', 25.0)),
            'leverage': int(float(_cfg.get('prop_firm_leverage', 0))),
            # WHY: Risk and DD must be backfilled into old rules that
            #      were saved before the risk injection was added.
            # CHANGED: April 2026 — risk/DD in backfill
            'risk_pct': float(_cfg.get('risk_pct', 0)),
            'dd_daily_pct': float(_cfg.get('dd_daily_pct', 0)),
            'dd_total_pct': float(_cfg.get('dd_total_pct', 0)),
        }

        with _save_lock:
            all_rules = load_all()
            updated = 0
            for entry in all_rules:
                rule = entry.get('rule', {})
                changed = False
                for k, v in firm_data.items():
                    if k == 'prop_firm_name' or not rule.get(k):
                        if rule.get(k) != v:
                            rule[k] = v
                            changed = True
                if changed:
                    updated += 1
            if updated > 0:
                _atomic_write_json(all_rules, _SAVE_PATH)
        return updated
    except Exception as _e:
        print(f"[saved_rules] update_all_rules_firm error: {_e}")
        return 0


# WHY: Removed auto-run of update_all_rules_firm() on import.
#      It reads + rewrites saved_rules.json synchronously, blocking the
#      main thread every time any panel imports this module.
#      Callers that need firm data up-to-date can call it explicitly.
# CHANGED: April 2026 — remove blocking import-time I/O


def add_rule_variant_no_rolling():
    """Add a 3-condition variant of rule BUY_H1_4c_0422_568c.

    WHY: The M5_distance_to_rolling_level_786 indicator was unsupported
         in MQL5 and was always TRUE in the previous EA. The 3-condition
         version matches what actually ran on MT5 and produced profits.
    CHANGED: April 2026 — 3-condition variant
    """
    import copy
    with _save_lock:
        all_rules = load_all()

        # Check if already exists
        for e in all_rules:
            if e.get('rule_id', '') == 'BUY_H1_3c_0422_568c_no_rolling':
                print("[saved_rules] 3-condition variant already exists")
                return

        # Find source rule
        source = None
        for e in all_rules:
            r = e.get('rule', {})
            conds = r.get('conditions', [])
            feats = [c.get('feature', '') for c in conds]
            if ('M15_std_dev_20' in feats and 'D1_adx_21' in feats
                    and 'M5_distance_to_rolling_level_786' in feats
                    and float(r.get('risk_pct', 0)) > 0):
                source = e
                break

        if not source:
            print("[saved_rules] Source rule not found")
            return

        new_entry = copy.deepcopy(source)
        rule = new_entry['rule']

        # Remove rolling_level condition
        rule['conditions'] = [c for c in rule.get('conditions', [])
                               if 'rolling_level' not in c.get('feature', '')]

        # Update IDs
        new_id = max(e.get('id', 0) for e in all_rules) + 1
        new_entry['id'] = new_id
        new_entry['rule_id'] = 'BUY_H1_3c_0422_568c_no_rolling'
        rule['rule_combo'] = 'BUY_H1_3c_0422_568c_no_rolling'
        new_entry['source'] = 'Manual variant — removed unsupported indicator'

        from datetime import datetime, timezone
        new_entry['saved_at'] = datetime.now(timezone.utc).isoformat()

        all_rules.append(new_entry)
        _atomic_write_json(all_rules, _SAVE_PATH)
        print(f"[saved_rules] Added 3-condition variant as id={new_id}")


# Run on import
try:
    add_rule_variant_no_rolling()
except Exception as _e:
    print(f"[saved_rules] variant error: {_e}")
