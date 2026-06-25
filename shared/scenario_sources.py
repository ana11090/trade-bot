"""Shared scenario-source discovery + rule loading.

Single source of truth for the rule sources shown in the Run Backtest panel,
reused by the EA Batch panel's 'Run Scenario' mode so both load identical rules.

This module is a faithful, UI-free mirror of the Run Backtest panel's nested
closures `_get_available_sources` and `_load_rules_from_source`
(project2_backtesting/panels/run_backtest_panel.py). The labels, source order,
counts, dedup hash and WIN/LOSS filter are kept byte-identical so the EA Batch
panel's scenario grid loads exactly what Run Backtest would load for the same
source. If those closures change, update this module too (Run Backtest is NOT
wired to delegate here — see the FEATURE prompt STEP 2, intentionally skipped).
"""
import os
import json


def _normalize_conditions(rule):
    """Lazy import of helpers.normalize_conditions (matches run_backtest_panel,
    which imports it at call time). Falls back to identity if unavailable."""
    try:
        from helpers import normalize_conditions
        return normalize_conditions(rule)
    except Exception:
        return rule


def _project_root():
    # shared/ sits directly under the project root, so one level up.
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def get_available_sources():
    """Return [(label, path), ...] of available rule files. Mirrors the Run
    Backtest panel's _get_available_sources scanner exactly (same order, labels,
    counts). Adds 'All Sources Combined' at the top when >1 source exists."""
    sources = []
    project_root = _project_root()
    p1_report  = os.path.join(project_root, 'project1_reverse_engineering', 'outputs', 'analysis_report.json')
    p4_scratch = os.path.join(project_root, 'project4_strategy_creation', 'outputs', 'discovery_scratch.json')
    # XGBoost panel writes xgboost_result.json; older runs used discovery_xgboost.json.
    _xgb_new   = os.path.join(project_root, 'project1_reverse_engineering', 'outputs', 'xgboost_result.json')
    _xgb_old   = os.path.join(project_root, 'project1_reverse_engineering', 'outputs', 'discovery_xgboost.json')
    p1_xgboost = _xgb_new if os.path.exists(_xgb_new) else _xgb_old
    p1_bot_entry  = os.path.join(project_root, 'project1_reverse_engineering', 'outputs', 'bot_entry_rules.json')
    my_rules_path = os.path.join(project_root, 'my_rules.json')
    saved_path    = os.path.join(project_root, 'saved_rules.json')

    if os.path.exists(p1_report):
        try:
            with open(p1_report, encoding='utf-8') as f:
                d = json.load(f)
            win = [r for r in d.get('rules', []) if r.get('prediction') == 'WIN']
            method = d.get('discovery_method', 'Decision Tree')
            sources.append((f"Active Rules — {method} ({len(win)} WIN)", p1_report))
        except Exception:
            pass

    if os.path.exists(p4_scratch):
        try:
            with open(p4_scratch, encoding='utf-8') as f:
                d = json.load(f)
            sources.append((f"Scratch Discovery ({len(d.get('rules', []))} rules)", p4_scratch))
        except Exception:
            pass

    if os.path.exists(p1_xgboost):
        try:
            with open(p1_xgboost, encoding='utf-8') as f:
                d = json.load(f)
            sources.append((f"XGBoost Discovery ({len(d.get('rules', []))} rules)", p1_xgboost))
        except Exception:
            pass

    if os.path.exists(p1_bot_entry):
        try:
            with open(p1_bot_entry, encoding='utf-8') as f:
                d = json.load(f)
            sources.append((f"Bot Entry Rules ({len(d.get('rules', []))} rules, all TFs)", p1_bot_entry))
        except Exception:
            pass

    # My Rules + Saved are list-format files (entries, not {'rules': [...]}),
    # so they are counted via len(d), matching the Run Backtest scanner.
    if os.path.exists(my_rules_path):
        try:
            with open(my_rules_path, encoding='utf-8') as f:
                d = json.load(f)
            sources.append((f"★ My Rules ({len(d)} rules)", my_rules_path))
        except Exception:
            pass

    if os.path.exists(saved_path):
        try:
            with open(saved_path, encoding='utf-8') as f:
                d = json.load(f)
            if d:
                sources.append((f"Saved/Bookmarked Rules ({len(d)} rules)", saved_path))
        except Exception:
            pass

    if len(sources) > 1:
        sources.insert(0, (f"📦 All Sources Combined ({len(sources)} sources)",
                           "__ALL_SOURCES__"))
    return sources


def _source_tag(lbl):
    """Mirror of the Run Backtest panel's _a46_source_tag (My Rules checked
    before generic Saved; Bot Entry has its own tag)."""
    if 'Active Rules' in lbl or 'Decision Tree' in lbl: return 'Step3'
    if 'Bot Entry' in lbl:                              return 'BotEntry'
    if 'My Rules' in lbl:                               return 'MyRule'
    if 'Saved' in lbl or 'Bookmarked' in lbl:           return 'Saved'
    if 'XGBoost' in lbl:                                return 'XGB'
    if 'Scratch' in lbl:                                return 'Scratch'
    return 'Other'


def load_rules_for_source(label, source_paths):
    """Return the normalized WIN/LOSS rule dicts for the chosen source label.
    Faithful UI-free mirror of the Run Backtest panel's _load_rules_from_source
    (both the single-source and All-Sources-Combined paths). These are the SAME
    rule dicts the backtester would receive — and the shape _gen_ea_for /
    batch_generate expect."""
    import hashlib
    path = source_paths.get(label)

    def _rule_hash(r):
        conds_str = str(sorted(str(c) for c in r.get('conditions', [])))
        return hashlib.md5(
            f"{conds_str}|{r.get('prediction','')}|{r.get('action','')}".encode()
        ).hexdigest()

    # ── All Sources Combined: merge every real source, dedup by conditions ──
    if path == "__ALL_SOURCES__":
        out = []
        seen = set()
        for src_label, src_path in source_paths.items():
            if not src_path or src_path == "__ALL_SOURCES__" or not os.path.exists(src_path):
                continue
            try:
                with open(src_path, encoding='utf-8') as f:
                    d = json.load(f)
                rules = ([e.get('rule', e) for e in d] if isinstance(d, list)
                         else d.get('rules', []))
                rules = [_normalize_conditions(r) for r in rules]
                tag = _source_tag(src_label)
                for r in rules:
                    if r.get('prediction', 'WIN') not in ('WIN', 'LOSS'):
                        continue
                    h = _rule_hash(r)
                    if h in seen:
                        continue
                    seen.add(h)
                    r['_a46_source_tag'] = tag
                    out.append(r)
            except Exception:
                continue
        return out

    # ── Single source ──
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return []

    if isinstance(data, list):
        # List format (saved_rules.json / my_rules.json): preserve entry ids so
        # downstream labelling/status tracking matches the Run Backtest panel.
        rules = []
        for entry in data:
            rule = entry.get('rule', entry) if isinstance(entry, dict) else entry
            if isinstance(rule, dict):
                rule['_saved_entry_id'] = entry.get('id') if isinstance(entry, dict) else None
                rule['_saved_rule_id'] = entry.get('rule_id', '') if isinstance(entry, dict) else ''
            rules.append(rule)
    else:
        rules = data.get('rules', [])

    rules = [_normalize_conditions(r) for r in rules]
    out = [r for r in rules if r.get('prediction', 'WIN') in ('WIN', 'LOSS')]
    for r in out:
        if isinstance(r, dict):
            r.setdefault('_a46_source_tag', _source_tag(label))
    return out
