"""
My Rules — a MANUAL-ONLY rule collection.

Unlike saved_rules.json (which rule-discovery auto-populates), nothing writes
here except an explicit user action. Separate file = discovery cannot touch it.
API mirrors shared/saved_rules.py so the saved-rules panel rendering can be reused.
"""
import os
import json
import tempfile
import threading
import hashlib
from datetime import datetime

_MY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'my_rules.json')
_save_lock = threading.Lock()
_change_listeners = []


def register_change_listener(cb):
    if cb not in _change_listeners:
        _change_listeners.append(cb)


def unregister_change_listener(cb):
    if cb in _change_listeners:
        _change_listeners.remove(cb)


def _notify_change(event, payload):
    for cb in list(_change_listeners):
        try:
            cb(event, payload)
        except Exception:
            pass


def _atomic_write_json(data, path):
    d = os.path.dirname(path) or '.'
    fd, tmp = tempfile.mkstemp(dir=d, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2, default=str)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_all():
    """Load all manually-saved rules. Returns list of entry dicts."""
    if not os.path.exists(_MY_PATH):
        return []
    try:
        with open(_MY_PATH, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_rule(rule, source="manual", notes=""):
    """Explicit user save into the manual collection."""
    # Normalize keys (direction, exit_name, etc.) — same as saved_rules
    try:
        from shared.saved_rules import _normalize_rule
        rule = _normalize_rule(dict(rule))  # copy so caller's dict is untouched
    except Exception:
        rule = dict(rule)

    with _save_lock:
        all_rules = load_all()
        existing_ids = [r.get("id", 0) for r in all_rules
                        if isinstance(r.get("id"), int)]
        new_id = max(existing_ids, default=0) + 1

        _dir = rule.get('direction', rule.get('action', 'BUY'))
        _tf = rule.get('entry_timeframe', rule.get('entry_tf', '?'))
        _h = hashlib.sha1(
            json.dumps(rule, sort_keys=True, default=str).encode()
        ).hexdigest()[:8]
        rule_id = f"{_dir}_{_tf}_{datetime.now().strftime('%m%d')}_{_h}"

        entry = {
            "id": new_id,
            "rule_id": rule_id,
            "saved_at": datetime.now().isoformat(),
            "source": source,
            "notes": notes,
            "rule": rule,
        }
        all_rules.append(entry)
        _atomic_write_json(all_rules, _MY_PATH)

    _notify_change("save", {"id": new_id, "rule_id": rule_id})
    print(f"[MY RULES] Saved #{new_id} ({rule_id}) — {rule.get('rule_combo', '?')}")
    return new_id


def delete_rule(rule_id):
    """Delete a rule by numeric ID or descriptive rule_id."""
    with _save_lock:
        all_rules = load_all()
        kept = [r for r in all_rules
                if str(r.get("id")) != str(rule_id)
                and str(r.get("rule_id")) != str(rule_id)]
        _atomic_write_json(kept, _MY_PATH)
    _notify_change("delete", {"rule_id": rule_id})


def delete_all():
    """Clear the entire manual collection."""
    with _save_lock:
        _atomic_write_json([], _MY_PATH)
    _notify_change("delete_all", {})
