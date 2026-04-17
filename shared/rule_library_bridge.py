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


# WHY (Phase A.40a.2): Inline reason-aware validity check. Used to be
#      a bare True/False via _is_valid_rule(); callers couldn't tell
#      WHY a rule was rejected. Now returns (ok, reason, sample) so
#      auto_save_discovered_rules can surface the first failure.
# CHANGED: April 2026 — Phase A.40a.2
def _why_invalid(rule):
    """Return (ok, reason, sample). ok=True means the rule is savable
    and reason/sample are None. ok=False means rule is rejected and
    reason/sample explain why."""
    if not isinstance(rule, dict):
        return (False, f"not a dict (got {type(rule).__name__})", repr(rule)[:60])
    conds = rule.get('conditions') or []
    if not conds or not isinstance(conds, list):
        return (False, "rule has no 'conditions' key OR it's empty/non-list",
                list(rule.keys())[:8])
    for i, c in enumerate(conds):
        if not isinstance(c, dict):
            return (False, f"condition[{i}] is not a dict (got {type(c).__name__})",
                    repr(c)[:60])
        if not c.get('feature'):
            return (False, f"condition[{i}] missing 'feature'",
                    list(c.keys())[:8])
        if not c.get('operator'):
            return (False, f"condition[{i}] missing 'operator'",
                    list(c.keys())[:8])
        if c.get('value') is None and c.get('threshold') is None:
            return (False, f"condition[{i}] missing both 'value' and 'threshold'",
                    list(c.keys())[:8])
    # WHY (Phase A.40a.3): Accept 'action' as a fallback for 'prediction'.
    #      Step 4 rules have 'prediction' today (from _extract_rules_from_tree),
    #      but also carry 'action'. Future discovery paths or user-composed
    #      rules might only have 'action'. Accepting both costs nothing and
    #      prevents a silent rejection that's hard to debug.
    # CHANGED: April 2026 — Phase A.40a.3
    _pred = str(rule.get('prediction', '') or rule.get('action', '')).strip()
    if not _pred:
        return (False, "rule missing both 'prediction' and 'action'",
                list(rule.keys())[:8])
    return (True, None, None)


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
      (saved_count, dedup_skipped_count, invalid_count, diag)

    diag is None when nothing was rejected, otherwise a dict
        {'reason': str, 'sample': any} describing the FIRST invalid
        rule we hit in this batch. Hooks log this so users can see why
        rules didn't make it into the library.

    Honors the global auto-save checkbox: when off, returns
    (0, 0, 0, None) without touching the disk.
    """
    # WHY (Phase A.40a.3): Removed the redundant is_auto_save_enabled()
    #      check here. The THREE caller hooks each check it themselves
    #      and log a loud "DISABLED" message when off. Having the bridge
    #      also check it created an invisible (debug-level) early return
    #      that produced (0,0,0,None) — hooks saw saved=0 with no
    #      explanation if the double-check disagreed (edge case). One
    #      gatekeeper (the hooks) is enough.
    # CHANGED: April 2026 — Phase A.40a.3

    rules = list(rules or [])
    if not rules:
        return (0, 0, 0, None)

    try:
        from shared import saved_rules as _sr
    except Exception as _e:
        log.warning(f"[A.40a] cannot import shared.saved_rules: {_e}")
        return (0, 0, 0, {'reason': f'shared.saved_rules import failed: {_e}',
                          'sample': None})

    seen_hashes = _existing_hashes() if dedup else set()
    saved = 0
    dedup_skipped = 0
    invalid = 0
    # WHY (Phase A.40a.2): Track the FIRST invalid-rule reason so the
    #      caller can include it in its log line. Without this, hooks
    #      that report invalid=N > 0 don't tell the user WHY any rule
    #      was rejected — "invalid" is a black box.
    # CHANGED: April 2026 — Phase A.40a.2
    first_invalid_reason = None
    first_invalid_sample = None

    def _note_invalid(reason, sample=None):
        nonlocal first_invalid_reason, first_invalid_sample
        if first_invalid_reason is None:
            first_invalid_reason = reason
            first_invalid_sample = sample

    # WHY (Phase A.43): Bake the active regime filter conditions into each rule
    #      at save time so the backtester can reproduce the exact filter that
    #      was in effect during discovery — even if the user later changes the
    #      global config. Conditions are stored under key 'regime_filter' in the
    #      rule dict. Rules saved when the filter is off carry no key.
    # CHANGED: April 2026 — Phase A.43
    _a43_conditions = None
    try:
        _a43_cfg = _load_p1_config()
        if str(_a43_cfg.get('regime_filter_enabled', 'false')).lower() == 'true':
            _a43_disc_str = _a43_cfg.get('regime_filter_discovered', '') or ''
            if _a43_disc_str:
                _a43_disc = json.loads(_a43_disc_str)
                if _a43_disc.get('status') == 'ok':
                    _a43_sub = (_a43_disc.get('subset')
                                or _a43_disc.get('subset_chosen') or [])
                    if _a43_sub:
                        _a43_conditions = _a43_sub
    except Exception as _a43_e:
        log.debug(f"[A.43] could not read regime filter for rule save: {_a43_e}")

    for r in rules:
        ok, _why, _sample = _why_invalid(r)
        if not ok:
            invalid += 1
            _note_invalid(_why, _sample)
            continue
        h = _rule_hash(r)
        if dedup and h in seen_hashes:
            dedup_skipped += 1
            continue
        try:
            _r_to_save = ({**r, 'regime_filter': _a43_conditions}
                          if _a43_conditions else r)
            _sr.save_rule(_r_to_save, source=source, notes=notes)
            seen_hashes.add(h)
            saved += 1
        except Exception as _e:
            log.warning(
                f"[A.40a] could not save rule from {source}: "
                f"{type(_e).__name__}: {_e}"
            )
            invalid += 1
            _note_invalid(f"save_rule exception: {type(_e).__name__}: {_e}",
                          sample=None)

    log.info(
        f"[A.40a] auto-save {source} rules: "
        f"saved={saved}, dedup-skipped={dedup_skipped}, invalid={invalid}"
    )
    diag = None
    if first_invalid_reason is not None:
        diag = {'reason': first_invalid_reason, 'sample': first_invalid_sample}
    return (saved, dedup_skipped, invalid, diag)
