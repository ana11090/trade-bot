"""
Stale Rules Detection — check if analysis_report.json and saved rules
have all the fields needed for the pipeline to work correctly.

WHY: After code fixes are applied, old data files may be missing new fields
     like entry_timeframe, direction, exit_class, filters_applied. Without
     these, the backtester uses wrong defaults and the EA won't match
     the discovered strategy.

CHANGED: April 2026 — new feature
"""

import os
import json


_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
_REPORT_PATH = os.path.join(
    _PROJECT_ROOT, 'project1_reverse_engineering', 'outputs', 'analysis_report.json'
)


def check_analysis_report():
    """
    Check analysis_report.json for BREAKING issues only.

    Returns dict:
      'is_stale': bool — True only if the report cannot be used at all
      'issues': list of human-readable issue strings
      'fix_instructions': str — what the user should do

    WHY (Phase A.9): Prior versions of this function flagged reports as
         "stale" for missing optional metadata fields (entry_timeframe,
         activated_at, discovery_method) and for various inverted/edge
         cases that had been chased through Phases 77 Fix 42, 74 Fix 68,
         77 Fix 43, A.3, and A.8 without ever converging — every fix
         spawned a new false positive. The root cause was the policy,
         not the implementation: missing metadata is not the same as a
         broken report, and blocking the user with a popup over missing
         optional fields had negative value (downstream code has
         reasonable defaults for all of them).

         New policy: a report is stale ONLY if it cannot be used at all.
         That means one of:
           - file missing on disk
           - file exists but is not valid JSON
           - file has no rules at all
           - file has rules but none of them have any conditions
                 (a rule with zero conditions matches every candle)
         Everything else — missing entry_timeframe, missing activated_at,
         missing discovery_method, LOSS-only rules, per-row vs top-level
         TF mismatches — is silent and tolerated. Downstream callers
         handle the missing fields with defaults as they already do.
    CHANGED: April 2026 — Phase A.9 — minimum-correctness policy
    """
    issues = []

    # Check 1: file exists
    if not os.path.exists(_REPORT_PATH):
        return {
            'is_stale': True,
            'issues': ['analysis_report.json does not exist'],
            'fix_instructions': (
                'Run rule discovery first:\n'
                '  Project 1 → Run Scenarios (decision-tree path), OR\n'
                '  Project 4 → Scratch Discovery (XGBoost path).'
            ),
        }

    # Check 2: valid JSON
    try:
        with open(_REPORT_PATH, 'r', encoding='utf-8') as f:
            report = json.load(f)
    except Exception as _e:
        return {
            'is_stale': True,
            'issues': [f'analysis_report.json is corrupt: {_e}'],
            'fix_instructions': (
                'Re-run rule discovery to regenerate the file:\n'
                '  Project 1 → Run Scenarios, OR\n'
                '  Project 4 → Scratch Discovery.'
            ),
        }

    # Check 3: rules present
    rules = report.get('rules', []) or []
    if not rules:
        issues.append('No rules in analysis_report.json')

    # Check 4: at least one rule has at least one condition
    # WHY: a rule with zero conditions matches every candle and produces
    #      garbage results. This is the only rule-shape error worth blocking on.
    # CHANGED: April 2026 — Phase A.9
    if rules:
        total_conditions = 0
        for r in rules:
            # direct conditions
            conds = r.get('conditions', []) or []
            total_conditions += len(conds)
            # nested rules (some writers use {'rules': [{'conditions': [...]}]})
            for nested in r.get('rules', []) or []:
                total_conditions += len(nested.get('conditions', []) or [])
        if total_conditions == 0:
            issues.append(
                'All rules have zero conditions — each would match every '
                'candle. Re-run discovery.'
            )

    fix = ''
    if issues:
        fix = (
            'Re-run rule discovery to regenerate the file:\n'
            '  Project 1 → Run Scenarios, OR\n'
            '  Project 4 → Scratch Discovery.'
        )

    return {
        'is_stale': len(issues) > 0,
        'issues': issues,
        'fix_instructions': fix,
    }


def check_saved_rule(rule_data):
    """
    Check a single saved rule for stale/missing fields.

    rule_data: the 'rule' dict from saved_rules.json

    Returns dict:
      'is_stale': bool
      'issues': list of strings
      'fix_instructions': str
    """
    issues = []

    # WHY (Phase 77 Fix 43): Old check only flagged when BOTH were absent.
    #      A rule with exit_name='FixedSLTP' but no exit_class passes the
    #      check, but ea_generator resolves exit_class from exit_name via a
    #      lookup table — so this is actually fine. The check is overly strict
    #      in the other direction too: only exit_class is needed by the EA.
    #      Flag separately so callers know which field is missing.
    # CHANGED: April 2026 — Phase 77 Fix 43 — distinguish exit fields
    #          (audit Part F LOW #43)
    _has_exit = (rule_data.get('exit_class') or rule_data.get('exit_name')
                 or rule_data.get('exit_strategy'))
    if not _has_exit:
        issues.append(
            'Missing exit strategy (no exit_class, exit_name, or exit_strategy) '
            '— EA will use default SL/TP'
        )
    elif rule_data.get('exit_name') and not rule_data.get('exit_class'):
        # exit_name is resolvable by ea_generator — note but don't flag as stale
        pass  # OK — ea_generator resolves via exit_class_map

    if not rule_data.get('exit_strategy_params') and not rule_data.get('exit_params'):
        issues.append('Missing exit parameters')

    if not rule_data.get('entry_timeframe'):
        issues.append('Missing entry timeframe')

    conditions = rule_data.get('conditions', [])
    if not conditions:
        # Check nested rule structure
        rules = rule_data.get('rules', [])
        if rules:
            all_conds = sum(len(r.get('conditions', [])) for r in rules)
            if all_conds == 0:
                issues.append('No rule conditions')
        else:
            issues.append('No rules or conditions')

    fix = ''
    if issues:
        fix = (
            'To fix: Load this strategy in the Refiner and re-save it.\n'
            'The new save will capture all missing fields.'
        )

    return {
        'is_stale': len(issues) > 0,
        'issues': issues,
        'fix_instructions': fix,
    }


def format_warning(check_result, short=False):
    """Format a check result as a user-friendly warning string.

    short=True: one-line summary for dropdown labels
    short=False: full multi-line warning for dialogs
    """
    if not check_result['is_stale']:
        return ''

    if short:
        return f"⚠️ {len(check_result['issues'])} issue(s)"

    lines = ['⚠️ STALE DATA DETECTED:\n']
    for issue in check_result['issues']:
        lines.append(f'  • {issue}')
    lines.append(f'\n{check_result["fix_instructions"]}')
    return '\n'.join(lines)
