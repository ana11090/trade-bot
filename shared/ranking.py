"""Risk-adjusted ranking score for rule/strategy comparison.

WHY: The old ranking by raw 'net_total_pips' and 'confidence × coverage'
     selects volume, not profitability per unit of drawdown risk. The
     user's goal is a prop-firm EA that must not hit the DD limit. This
     score weights expectancy, sample size, and DD together so strategies
     that win small but survive outrank strategies that net more pips at
     the cost of a single catastrophic run.

CHANGED: April 2026 — risk-adjusted ranking
"""
import math


def _wilson_lower_bound(successes, total, z=1.96):
    """Wilson score lower bound of win rate at 95% confidence.

    WHY: Raw win_rate overrates small leaves. Wilson LB shrinks small
         samples toward the neutral 50% while leaving big samples alone.
    CHANGED: April 2026 — small-sample shrinkage
    """
    if total <= 0:
        return 0.0
    p = successes / total
    denom = 1.0 + (z * z) / total
    centre = p + (z * z) / (2.0 * total)
    margin = z * math.sqrt((p * (1 - p) + (z * z) / (4.0 * total)) / total)
    return max(0.0, (centre - margin) / denom)


def risk_adjusted_score(row):
    """Score a backtest matrix row. Higher = better.

    Required row fields (present at top level OR under row['stats']):
        total_trades, win_rate, expectancy, max_dd_pips, net_total_pips,
        net_profit_factor, avg_loser
    Reads both top-level and nested 'stats' — the matrix format varies.

    The score has four ingredients:
      E         = expectancy per trade (net pips, can be negative)
      n_weight  = min(1.0, total_trades / 100)  — caps at 100 trades
      dd_penalty = 1 / (1 + max_dd_pips / abs(avg_loser * 5))
                   small DD relative to typical loser = ~1.0
                   DD five times avg_loser = ~0.5
                   DD ten times avg_loser = ~0.33
      pf_gate   = 0 if net_profit_factor < 1.0, else 1

    Score = E × n_weight × dd_penalty × pf_gate

    Returns float; 0.0 on bad rows (no trades, PF<1, missing fields).
    """
    s = row.get('stats') if isinstance(row.get('stats'), dict) else row

    total_trades = int(s.get('total_trades', row.get('total_trades', 0)) or 0)
    if total_trades < 10:
        return 0.0

    pf = float(s.get('net_profit_factor',
                     row.get('net_profit_factor', 0.0)) or 0.0)
    if pf < 1.0:
        return 0.0

    expectancy = float(s.get('expectancy',
                             row.get('expectancy', 0.0)) or 0.0)
    if expectancy <= 0:
        return 0.0

    max_dd_pips = abs(float(s.get('max_dd_pips',
                                  row.get('max_dd_pips', 0.0)) or 0.0))
    avg_loser = abs(float(s.get('avg_loser',
                                row.get('avg_loser', 0.0)) or 0.0))

    n_weight = min(1.0, total_trades / 100.0)

    # 5× avg loser is a normal DD; 10× is a concerning DD; 20× is dangerous
    if avg_loser > 0:
        dd_penalty = 1.0 / (1.0 + max_dd_pips / (avg_loser * 5.0))
    else:
        # No losers in sample = suspicious unless PF>>1 and n_weight high;
        # still honor the result but neutralize the penalty
        dd_penalty = 1.0

    return expectancy * n_weight * dd_penalty


def rule_discovery_score(rule):
    """Score a P1-discovered rule dict. Higher = better.

    Input is a rule dict from analyze.py (has 'confidence', 'coverage',
    'win_rate', 'avg_pips'). Different shape from backtest matrix rows —
    no exit strategy, no DD, no expectancy yet.

    Score = wilson_lower_bound × coverage_weight × avg_pips_signum
      wilson_lower_bound = lower 95% CI of win_rate at given sample size
      coverage_weight    = min(1.0, coverage / 50) — caps at 50 samples
      avg_pips_signum    = 1.0 if avg_pips > 0, 0 otherwise

    Intent: prefer rules with defensible win rates on meaningful samples
    whose in-sample trades at least make money on average.
    """
    coverage = int(rule.get('coverage', 0) or 0)
    if coverage <= 0:
        return 0.0

    win_rate = float(rule.get('win_rate', 0.0) or 0.0)
    successes = int(round(win_rate * coverage))
    wlb = _wilson_lower_bound(successes, coverage)

    avg_pips = float(rule.get('avg_pips', 0.0) or 0.0)
    signum = 1.0 if avg_pips > 0 else 0.0

    coverage_weight = min(1.0, coverage / 50.0)
    return wlb * coverage_weight * signum
