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
    Check analysis_report.json for stale/missing fields.

    Returns dict:
      'is_stale': bool — True if any critical field is missing
      'issues': list of human-readable issue strings
      'fix_instructions': str — what the user should do
    """
    issues = []

    if not os.path.exists(_REPORT_PATH):
        return {
            'is_stale': True,
            'issues': ['analysis_report.json does not exist'],
            'fix_instructions': (
                'Run P4 Strategy Discovery first.\n'
                'Go to Project 4 → Scratch Discovery → click Quick Discovery.'
            ),
        }

    try:
        with open(_REPORT_PATH, 'r', encoding='utf-8') as f:
            report = json.load(f)
    except Exception:
        return {
            'is_stale': True,
            'issues': ['analysis_report.json is corrupt'],
            'fix_instructions': 'Re-run P4 Strategy Discovery to regenerate the file.',
        }

    # Check rules exist (rules are the most important thing)
    rules = report.get('rules', [])
    win_rules = [r for r in rules if r.get('prediction') == 'WIN']
    if not win_rules:
        issues.append('No WIN rules found — discovery may not have been run')

    # If we have rules with valid predictions, treat the rest as warnings (not stale)
    # WHY: rules can be valid even if top-level direction is missing — the rules
    #      themselves carry direction info via their `prediction` field.
    # CHANGED: April 2026 — check rule contents, not just top-level fields

    # Check entry_timeframe — but don't warn if multi-TF backtest gave per-row TF
    # WHY: When using multi-TF backtest, entry_tf is per strategy row, not global.
    #      Reporting a missing global entry_timeframe would be a false alarm.
    #
    #      Old condition was:
    #          (not entry_tf or entry_tf == 'None') and not win_rules and not has_per_row_entry_tf
    #      The `and not win_rules` clause meant the warning ONLY fired when
    #      there were ZERO rules — backwards. The warning should fire when
    #      there ARE rules AND the entry_tf is missing globally AND no per-row
    #      entry_tf is set. Fix: `and win_rules` (not inverted).
    # CHANGED: April 2026 — fix inverted warning condition (audit MED #68)
    entry_tf = report.get('entry_timeframe')
    has_per_row_entry_tf = any(
        r.get('entry_tf') or r.get('entry_timeframe')
        for r in win_rules
    )
    if (not entry_tf or entry_tf == 'None') and win_rules and not has_per_row_entry_tf:
        issues.append('Missing entry_timeframe — EA and backtester may use wrong timeframe')

    # Check rules have conditions (real problem regardless of other fields)
    for i, r in enumerate(win_rules):
        conds = r.get('conditions', [])
        if not conds:
            issues.append(f'Rule {i+1} has 0 conditions — will match every candle')

    # Check rules are recent (was activate run lately?)
    activated_at = report.get('activated_at')
    discovery_method = report.get('discovery_method')
    if win_rules and not activated_at and not discovery_method:
        issues.append('Rules have no discovery_method — may be from old run')

    fix = ''
    if issues:
        fix = (
            'To fix: Go to Project 4 → Scratch Discovery → run Quick Discovery\n'
            '→ click "Use These Rules" to re-activate.\n'
            'This re-saves analysis_report.json with all fields populated.'
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

    if not rule_data.get('exit_class') and not rule_data.get('exit_name'):
        issues.append('Missing exit strategy — EA will use default SL/TP')

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
