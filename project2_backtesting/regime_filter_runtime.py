"""
PHASE A.38a — Regime Filter Runtime

Reads the discovered regime filter from p1_config.json and applies it as a
boolean mask at backtest signal-evaluation time. Pure consumer of A.37's
output.

Per-direction handling: features whose value's sign changes meaning between
BUY and SELL trades (EMA distance, MACD diff, etc.) get their operator
inverted when applied to a SELL rule. Magnitude-only features (std_dev,
ATR, ADX, candle_range) are applied identically regardless of rule action.

WHY (Phase A.38a): The discovered regime filter from A.37 sat in
     analysis_report.json doing nothing. A.38a wires it into the
     backtester's per-candle signal evaluation so signals at
     wrong-regime candles are blocked at evaluation time. Rules
     themselves are unchanged — this means turning the filter off
     restores identical behavior to pre-A.36 baseline.

A.37 schema note: A.37's compact payload uses key 'subset' for the list
     of chosen conditions and stores the comparison operator under the
     key 'direction' on each condition (a misnomer — it's an operator
     like '>' / '<', not a rule direction). This module reads both
     'subset' and 'subset_chosen', and both 'operator' and 'direction',
     so it works regardless of which writer wrote the payload.

CHANGED: April 2026 — Phase A.38a
"""

import json
import os
import sys
import numpy as np
import pandas as pd

from shared.logging_setup import get_logger
log = get_logger(__name__)


# ── Direction-aware feature detection ──────────────────────────────────────
# WHY: A feature is "direction-aware" if its value's sign flips meaning
#      between BUY and SELL trades. Concretely: price_above_EMA200 is good
#      for BUY (price > EMA), bad for SELL (which wants price < EMA).
#      Features where only magnitude matters (volatility, trend strength,
#      candle size) are direction-agnostic — same threshold, same operator,
#      regardless of rule action.
#
#      Detection is by SUBSTRING in the feature name. Conservative:
#      if a feature isn't recognized as direction-aware, it's treated as
#      direction-agnostic (the safer default — same filter for BUY and
#      SELL means consistent gating).
# CHANGED: April 2026 — Phase A.38a
_DIRECTION_AWARE_SUBSTRINGS = (
    'ema_200_distance',
    'ema_100_distance',
    'ema_50_distance',
    'ema_20_distance',
    'ema_9_distance',
    'sma_200_distance',
    'sma_50_distance',
    'sma_20_distance',
    'macd_std_diff',
    'macd_fast_diff',
    'macd_std_signal',
    'macd_fast_signal',
    'macd_std',
    'macd_fast',
    'price_change',
    'change_pct',
    'roc_',
    'momentum_',
    'tsi_',
    'kst_',
    'awesome_oscillator',
    'cci_',
    'dpo_',
    'elder_bull',
    'elder_bear',
    'aroon_oscillator',
    'pivot',
    'ema_9_above',
    'ema_20_above',
    'ema_50_above',
)

_DIRECTION_AGNOSTIC_KEYWORDS = (
    'std_dev',
    'atr',
    'adx_',
    'bb_',
    'volatility',
    'candle_range',
    'candle_body',
    'wick',
    'volume',
    'rsi',
    'stoch',
    'williams_r',
    'mass_index',
    'aroon_up',
    'aroon_down',
    'mfi',
    'ultimate_oscillator',
    'cmf',
    'obv',
    'vpt',
    'donchian',
    'keltner',
    'ichimoku',
    'parabolic_sar',
)


def _classify_direction_awareness(feature_name):
    """Returns 'aware' or 'agnostic' for a feature column name.

    Conservative: only marks 'aware' if the substring match is explicit;
    otherwise treats as 'agnostic' (safer default — same filter for both
    directions).
    """
    if not isinstance(feature_name, str):
        return 'agnostic'
    feat_lower = feature_name.lower()

    for sub in _DIRECTION_AWARE_SUBSTRINGS:
        if sub.lower() in feat_lower:
            return 'aware'

    for sub in _DIRECTION_AGNOSTIC_KEYWORDS:
        if sub.lower() in feat_lower:
            return 'agnostic'

    return 'agnostic'


def _invert_operator(op):
    """Flip a comparison operator. Used for direction-aware features when
    applying to SELL rules."""
    return {
        '>':  '<',
        '<':  '>',
        '>=': '<=',
        '<=': '>=',
        '==': '==',
        '!=': '!=',
    }.get(op, op)


def _cond_operator(cond):
    """A.37 stored the comparison under key 'direction' (misnomer).
    Future payloads may use 'operator'. Read either."""
    return cond.get('operator') or cond.get('direction') or '>'


def _load_active_filter():
    """Read p1_config.json and return (enabled, mode, subset, strictness).

    Returns (False, None, None, None) on any error or if the filter is off.
    Never raises — always returns a tuple.
    """
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        _p1_dir = os.path.normpath(os.path.join(_here, '..', 'project1_reverse_engineering'))
        if _p1_dir not in sys.path:
            sys.path.insert(0, _p1_dir)
        import config_loader as _cl
        cfg = _cl.load()
    except Exception as _e:
        log.debug(f"[A.38a] could not load config: {_e}")
        return (False, None, None, None)

    enabled = str(cfg.get('regime_filter_enabled', 'false')).lower() == 'true'
    if not enabled:
        return (False, None, None, None)

    mode = str(cfg.get('regime_filter_mode', 'automatic')).lower()
    strictness = str(cfg.get('regime_filter_strictness', 'conservative')).lower()

    # WHY (Phase A.38a): Manual mode is not yet wired (A.38b will add it).
    #      For now, manual-mode runs treat the filter as off so the user
    #      sees consistent behavior.
    # CHANGED: April 2026 — Phase A.38a
    if mode != 'automatic':
        log.info(
            f"[A.38a] regime filter mode is {mode!r} — A.38a only handles "
            f"'automatic' mode. Treating filter as off for this backtest."
        )
        return (False, None, None, strictness)

    discovered_str = cfg.get('regime_filter_discovered', '') or ''
    if not discovered_str:
        log.info(
            "[A.38a] regime filter is enabled but no discovery has run yet "
            "— treating filter as off. Run scenarios to populate discovery."
        )
        return (False, None, None, strictness)

    try:
        discovered = json.loads(discovered_str)
    except Exception as _e:
        log.warning(f"[A.38a] could not parse regime_filter_discovered: {_e}")
        return (False, None, None, strictness)

    if discovered.get('status') != 'ok':
        log.info(
            f"[A.38a] regime filter discovery status is "
            f"{discovered.get('status')!r} — treating filter as off."
        )
        return (False, None, None, strictness)

    # A.37 writes 'subset'; accept 'subset_chosen' for forward compat.
    subset = discovered.get('subset') or discovered.get('subset_chosen') or []
    if not subset:
        log.info("[A.38a] discovery succeeded but subset is empty — filter off.")
        return (False, None, None, strictness)

    return (True, mode, subset, strictness)


def build_regime_pass_mask(ind, rule_action='BUY', override_conditions=None):
    """Build a boolean numpy array of length len(ind), True where the
    regime filter passes for the given rule action.

    Args:
        ind:                indicators DataFrame (the same `ind` the
                            backtester uses for rule mask building).
        rule_action:        'BUY', 'SELL', or 'BOTH'. Direction-aware
                            features get their operator inverted for SELL.
        override_conditions: list of condition dicts (same schema as
                            'subset' in the discovery payload) to use
                            directly, bypassing the global config. When
                            provided, the config is not read — these
                            are the conditions baked into the rule at
                            save time (Phase A.43).

    Returns:
        (mask, info). When the filter is off, mask is all-True and
        info['enabled'] is False.
    """
    n = len(ind)
    # WHY (Phase A.43): When override_conditions is supplied, use them
    #      directly — the rule carries its own discovery-time conditions.
    #      This lets the backtester reproduce the exact regime that was
    #      active when the rule was found, regardless of the current
    #      global config state.
    # CHANGED: April 2026 — Phase A.43
    if override_conditions is not None:
        if not override_conditions:
            # Empty list = rule was saved with filter OFF → no filtering
            return (
                np.ones(n, dtype=bool),
                {
                    'enabled':          False,
                    'subset':           [],
                    'strictness':       'per-rule',
                    'pass_count':       n,
                    'total':            n,
                    'pass_pct':         100.0,
                    'rule_action_used': rule_action,
                },
            )
        enabled   = True
        subset    = override_conditions
        strictness = 'per-rule'
    else:
        enabled, mode, subset, strictness = _load_active_filter()

    if not enabled or not subset:
        return (
            np.ones(n, dtype=bool),
            {
                'enabled':          False,
                'subset':           [],
                'strictness':       strictness,
                'pass_count':       n,
                'total':            n,
                'pass_pct':         100.0,
                'rule_action_used': rule_action,
            },
        )

    # Dedup duplicate columns defensively (same pattern as backtester)
    _dup_cols = ind.columns[ind.columns.duplicated()].tolist()
    if _dup_cols:
        ind = ind.loc[:, ~ind.columns.duplicated()]

    mask = np.ones(n, dtype=bool)
    applied_conditions = []
    is_sell = (str(rule_action).upper() == 'SELL')

    for cond in subset:
        feat = cond.get('feature')
        op   = _cond_operator(cond)
        thr  = cond.get('threshold')

        if not feat or thr is None or feat not in ind.columns:
            log.debug(
                f"[A.38a] skipping condition — feature={feat!r} "
                f"(in_columns={feat in ind.columns if feat else False})"
            )
            continue

        awareness = _classify_direction_awareness(feat)
        applied_op = op
        if is_sell and awareness == 'aware':
            applied_op = _invert_operator(op)

        _raw = ind[feat]
        if isinstance(_raw, pd.DataFrame):
            _raw = _raw.iloc[:, 0]
        try:
            col_arr = pd.to_numeric(_raw, errors='coerce').to_numpy(dtype=float, copy=False)
        except Exception as _e:
            log.warning(
                f"[A.38a] could not coerce {feat!r} to numeric: {_e} "
                f"— condition skipped"
            )
            continue

        try:
            thr_f = float(thr)
        except Exception:
            log.warning(f"[A.38a] non-numeric threshold {thr!r} on {feat!r} — skipped")
            continue

        with np.errstate(invalid='ignore'):
            if applied_op == '>':
                cond_arr = col_arr > thr_f
            elif applied_op == '<':
                cond_arr = col_arr < thr_f
            elif applied_op == '>=':
                cond_arr = col_arr >= thr_f
            elif applied_op == '<=':
                cond_arr = col_arr <= thr_f
            elif applied_op == '==':
                cond_arr = col_arr == thr_f
            elif applied_op == '!=':
                cond_arr = col_arr != thr_f
            else:
                log.warning(f"[A.38a] unknown operator {applied_op!r} on {feat!r} — skipped")
                continue

        cond_arr = np.where(np.isnan(col_arr), False, cond_arr)
        mask &= cond_arr
        applied_conditions.append({
            'feature':   feat,
            'orig_op':   op,
            'used_op':   applied_op,
            'threshold': thr_f,
            'awareness': awareness,
            'pass_count_alone': int(cond_arr.sum()),
        })

    pass_count = int(mask.sum())
    pass_pct = (pass_count / n * 100.0) if n > 0 else 0.0

    return (
        mask,
        {
            'enabled':          True,
            'subset':           applied_conditions,
            'strictness':       strictness,
            'pass_count':       pass_count,
            'total':            n,
            'pass_pct':         round(pass_pct, 2),
            'rule_action_used': rule_action,
        },
    )


# ── Per-process logging cache ──────────────────────────────────────────────
# WHY (Phase A.38a): Comparison matrix calls fast_backtest hundreds of times.
#      Logging the regime filter summary on every call would spam the console.
#      Cache the summary by (subset signature, action) so we log once per
#      distinct configuration per process lifetime.
# CHANGED: April 2026 — Phase A.38a
_logged_keys = set()


def log_filter_summary_once(info, source_label='backtest'):
    """Emit one summary log line per distinct filter configuration."""
    if not info.get('enabled'):
        return
    sig = (
        tuple((c['feature'], c['used_op'], c['threshold']) for c in info.get('subset', [])),
        info.get('rule_action_used', '?'),
    )
    if sig in _logged_keys:
        return
    _logged_keys.add(sig)
    cond_strs = [
        f"{c['feature']} {c['used_op']} {c['threshold']}"
        for c in info.get('subset', [])
    ]
    log.info(
        f"[A.38a/{source_label}] regime filter active "
        f"(strictness={info.get('strictness')}, action={info.get('rule_action_used')}): "
        f"{' AND '.join(cond_strs)} | "
        f"{info.get('pass_count')}/{info.get('total')} candles pass "
        f"({info.get('pass_pct')}%)"
    )


def reset_logging_cache():
    """Clear the per-process log cache. Called at the start of each
    comparison matrix run so the user sees a fresh summary line."""
    _logged_keys.clear()
