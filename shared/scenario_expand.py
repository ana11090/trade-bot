"""Expand discovery rules into (rule × direction × TF × exit) variants for the
EA Batch 'Run Scenario' mode — reusing the backtest's own exit list and the
matrix's direction-expansion/label logic, so the batch path and the Python
backtest agree on WHAT to test. This module does NOT run any backtest; it only
produces variant dicts that the panel feeds to generate_ea (MT5).

The two mirrored functions below copy logic that lives as nested closures inside
run_comparison_matrix (can't be imported). They are faithful copies of the
CURRENT code — keep in sync:
  - rule_directions  <- strategy_backtester.py:_a30_rule_directions (~5391)
  - _combo_label     <- strategy_backtester.py label formula (~5436-5448)
"""
import hashlib

SCENARIO_TFS = ["M5", "M15", "H1", "H4", "D1"]


def rule_directions(rule):
    """Faithful mirror of _a30_rule_directions (strategy_backtester.py:~5391).
    Reads 'action' first, then 'direction' (a SELL rule tagged only under
    'direction' must NOT be silently traded BUY). BUY/LONG -> BUY; SELL/SHORT ->
    SELL; BOTH/BIDIRECTIONAL/EITHER -> both; otherwise default BUY."""
    a = str(rule.get('action', '') or rule.get('direction', '') or '').upper().strip()
    if a in ('BUY', 'LONG'):
        return ['BUY']
    if a in ('SELL', 'SHORT'):
        return ['SELL']
    if a in ('BOTH', 'BIDIRECTIONAL', 'EITHER'):
        return ['BUY', 'SELL']
    return ['BUY']


def _combo_label(rule):
    """Faithful mirror of the matrix's label formula
    (strategy_backtester.py:~5436-5448). Uses the rule's saved id when present,
    else builds {dir}_{tf}_{nc}c_{hash} from the rule's OWN stored fields (the
    matrix derives the label from the rule, not the per-variant TF/direction)."""
    lbl = rule.get('_saved_rule_id', '') or rule.get('rule_id', '')
    if lbl:
        return lbl
    _dir = rule.get('direction', rule.get('action', 'BUY'))
    _tf = rule.get('entry_timeframe', rule.get('entry_tf', 'XX'))
    _nc = len(rule.get('conditions', []))
    _conds = str(sorted(str(c) for c in rule.get('conditions', [])))
    _exit = rule.get('exit_name', rule.get('exit_class', ''))
    _h = hashlib.md5((_conds + _exit).encode()).hexdigest()[:4]
    return f"{_dir}_{_tf}_{_nc}c_{_h}"


def default_exits_for_tf(tf, pip_size=0.01):
    """Reuse the backtest's exit set verbatim. Returns [(name, params), ...]."""
    from project2_backtesting.exit_strategies import get_default_exit_strategies
    out = []
    for es in get_default_exit_strategies(pip_size=pip_size, entry_tf=tf):
        out.append((getattr(es, "name", "Fixed SL/TP"),
                    dict(getattr(es, "params", {}) or {})))
    return out


def expand_scenario_rules(rules, tfs=None, exits_for_tf=None, pip_size=0.01):
    """rules -> list of variant dicts, one per (rule × direction × TF × exit).
    exits_for_tf: optional override (defaults to the backtest's default exits).
    Each variant carries entry_tf/entry_timeframe, direction, exit_name,
    exit_params, and a drift-free label that encodes direction+TF+exit so rows
    (and generated EA names) stay distinct even for bidirectional rules.
    Conditions/feature prefixes are left untouched (the backtester doesn't remap
    them either)."""
    tfs = tfs or SCENARIO_TFS
    exits_for_tf = exits_for_tf or default_exits_for_tf
    out = []
    for r in rules:
        base = _combo_label(r)           # matrix-faithful base from the rule itself
        for direction in rule_directions(r):
            for tf in tfs:
                for ex_name, ex_params in exits_for_tf(tf, pip_size):
                    v = dict(r)
                    v["entry_tf"] = tf
                    v["entry_timeframe"] = tf
                    v["direction"] = direction
                    v["exit_name"] = ex_name
                    v["exit_params"] = ex_params
                    _tag = (ex_name.replace(" ", "").replace("/", "")
                            .replace("+", "").replace("-", ""))
                    # direction in the label too: a BOTH rule yields BUY & SELL
                    # variants that would otherwise collide at the same TF+exit.
                    v["_fanned_label"] = f"{base}__{direction}__{tf}__{_tag}"
                    out.append(v)
    return out
