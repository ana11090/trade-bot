"""
PHASE A.40a — Rule Library Bridge

A single shared helper that pipes auto-discovered rules from any of the
three discovery paths (Step 3 decision-tree extraction, Step 4 bot-entry
discovery, Mode A single-rule discovery) into the shared
saved_rules.json library used by the rest of the app.

Why this module exists:
  Each discovery path used to dump its rules to a different per-run
  JSON file (analysis_report.json, bot_entry_rules.json,
  single_rule_mode.json). The Saved Rules panel and the backtester
  consume saved_rules.json. Without a bridge, every discovered rule
  was stranded in its per-run file unless the user manually clicked
  "save" on it. A.40a closes that gap by piping all three paths into
  saved_rules.json automatically.

Behavior:
  - is_auto_save_enabled() reads p1_config.json (key
    'auto_save_discovered_rules', default 'true'). The Run Scenarios
    panel writes this key from a global checkbox.
  - auto_save_discovered_rules(rules, source, dedup=True, notes="")
    takes a list of rule dicts (in the standard 'conditions'/'prediction'
    schema), filters to the structurally valid ones, hashes each rule
    against the existing saved_rules.json contents, and appends only
    the new ones.
  - SHA1 fingerprint covers the rule's conditions (feature, operator,
    value rounded to 6 decimals — sorted for ordering invariance) and
    its prediction string. Source/notes/win_rate/etc. don't affect the
    hash so the same rule rediscovered with a different source label
    is still deduped.
  - Returns (saved_count, dedup_skipped_count, invalid_count).

CHANGED: April 2026 — Phase A.40a
"""

import hashlib
import json
import os
import sys

from shared.logging_setup import get_logger
log = get_logger(__name__)


# ── Config access ──────────────────────────────────────────────────────────
def _load_p1_config():
    """Best-effort load of project1 config_loader.load(). Returns {} on
    failure so callers can fall back to defaults."""
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        _p1_dir = os.path.normpath(os.path.join(_here, '..', 'project1_reverse_engineering'))
        if _p1_dir not in sys.path:
            sys.path.insert(0, _p1_dir)
        import config_loader as _cl
        return _cl.load()
    except Exception as _e:
        log.debug(f"[A.40a] could not load p1 config: {_e}")
        return {}


def is_auto_save_enabled():
    """True iff the global auto-save checkbox is on (default true)."""
    cfg = _load_p1_config()
    return str(cfg.get('auto_save_discovered_rules', 'true')).lower() == 'true'


# ── Rule fingerprinting ────────────────────────────────────────────────────
def _condition_fingerprint(cond):
    """Normalise one condition dict into a stable tuple.

    Accepts both legacy 'value' and canonical 'value' keys; falls back
    to 'threshold' (Mode A pre-A.40a Edit 3 schema). Numeric is rounded
    to 6 decimals so floating-point noise across runs doesn't break
    dedup.
    """
    feat = str(cond.get('feature', '')).strip()
    op   = str(cond.get('operator', '')).strip()
    val  = cond.get('value')
    if val is None:
        val = cond.get('threshold')
    try:
        val = round(float(val), 6)
    except Exception:
        val = str(val)
    return (feat, op, val)


def _rule_hash(rule):
    """SHA1 over the rule's structural identity: sorted conditions +
    prediction string. Returns hex digest."""
    conds = rule.get('conditions') or []
    fps = sorted(_condition_fingerprint(c) for c in conds)
    pred = str(rule.get('prediction', ''))
    blob = json.dumps({'c': fps, 'p': pred}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode('utf-8')).hexdigest()


def _existing_hashes():
    """Return the set of structural hashes already in saved_rules.json."""
    try:
        from shared import saved_rules as _sr
        existing = _sr.load_all() or []
    except Exception as _e:
        log.debug(f"[A.40a] could not load existing saved rules: {_e}")
        return set()
    out = set()
    for entry in existing:
        try:
            out.add(_rule_hash(entry.get('rule') or {}))
        except Exception:
            continue
    return out


def _is_valid_rule(rule):
    """Minimum structural check — at least one condition with feature+operator,
    plus a non-empty prediction."""
    if not isinstance(rule, dict):
        return False
    conds = rule.get('conditions')
    if not conds or not isinstance(conds, list):
        return False
    for c in conds:
        if not isinstance(c, dict):
            return False
        if not c.get('feature') or not c.get('operator'):
            return False
        if c.get('value') is None and c.get('threshold') is None:
            return False
    if not str(rule.get('prediction', '')).strip():
        return False
    return True


# ── Public API ─────────────────────────────────────────────────────────────
def auto_save_discovered_rules(rules, source, dedup=True, notes=""):
    """Persist `rules` into saved_rules.json via shared.saved_rules.save_rule.

    Args:
      rules:  iterable of rule dicts in the standard
              {'conditions': [...], 'prediction': str, ...} schema.
      source: short label written into the saved entry's 'source' field
              (e.g. "Step3:M5:BUY:conf72"). Same rule rediscovered with
              a different source still hashes equal so it's deduped.
      dedup:  when True (default), skip rules whose structural hash
              already exists in saved_rules.json.
      notes:  optional free-text note attached to every saved entry.

    Returns:
      (saved_count, dedup_skipped_count, invalid_count)

    Honors the global auto-save checkbox: when off, returns (0, 0, 0)
    without touching the disk.
    """
    if not is_auto_save_enabled():
        log.debug(f"[A.40a] auto-save disabled — skipping {source}")
        return (0, 0, 0)

    rules = list(rules or [])
    if not rules:
        return (0, 0, 0)

    try:
        from shared import saved_rules as _sr
    except Exception as _e:
        log.warning(f"[A.40a] cannot import shared.saved_rules: {_e}")
        return (0, 0, 0)

    seen_hashes = _existing_hashes() if dedup else set()
    saved = 0
    dedup_skipped = 0
    invalid = 0

    for r in rules:
        if not _is_valid_rule(r):
            invalid += 1
            continue
        h = _rule_hash(r)
        if dedup and h in seen_hashes:
            dedup_skipped += 1
            continue
        try:
            _sr.save_rule(r, source=source, notes=notes)
            seen_hashes.add(h)
            saved += 1
        except Exception as _e:
            log.warning(
                f"[A.40a] could not save rule from {source}: "
                f"{type(_e).__name__}: {_e}"
            )
            invalid += 1

    log.info(
        f"[A.40a] auto-save {source} rules: "
        f"saved={saved}, dedup-skipped={dedup_skipped}, invalid={invalid}"
    )
    return (saved, dedup_skipped, invalid)
