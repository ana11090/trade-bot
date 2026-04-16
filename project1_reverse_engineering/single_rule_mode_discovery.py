"""
PHASE A.39b — Single Rule Mode A: tightest AND-conjunction covering >=95% of trades

Discovers the tightest AND-conjunction of 2-5 indicator conditions that covers at
least 95% of the historical trade dataset. Intended for reverse-engineering a
deterministic EA where every trade came from a single underlying trigger —
the conjunction that ALL trades (minus ~5% for edge cases / timezone drift) share.

Algorithm outline:
  1. For each numeric feature, find the 5th and 95th percentile — each yields a
     single-sided condition covering 95% of trades by construction.
  2. Score each candidate condition by TIGHTNESS = what fraction of the broader
     candle background is contained by the same bound. A tight condition (bound
     hits only 30% of candles generally, but 95% of trade candles) is specific
     to the bot. A loose condition (95% of trade candles AND 95% of all candles)
     is trivial.
  3. Keep the 40 tightest conditions.
  4. Enumerate combinations of cardinality 2..5 from the pool. For each combo,
     compute joint coverage on the trade set. Accept if >= 95%.
  5. Score valid conjunctions by the PRODUCT of individual tightness scores
     (tighter-per-condition and more conditions both improve the product).
     Best conjunction has minimum product.
  6. Tie-break in favor of fewer conditions when scores are within 10%.

WHY (Phase A.39b): Step 3's decision tree fragments a deterministic bot's
     single trigger into multiple leaves because tree splits are local and
     purity-driven. Mode A takes a different angle: skip the classifier
     entirely and just look at what indicator states are universally
     present across all historical trades. This directly answers the
     user's reverse-engineering question: what rule did the bot actually
     use?
CHANGED: April 2026 — Phase A.39b
"""

import json
import math
import os
import random
from datetime import datetime

import numpy as np
import pandas as pd

from shared.logging_setup import get_logger
log = get_logger(__name__)


# ── Default algorithm parameters ───────────────────────────────────────────
# WHY: These were hardcoded originally. A.39b exposes them in the Run
#      Scenarios panel so the user can tune them without editing code.
#      The module-level constants are pure defaults — actual values used
#      at runtime come from `params` arg → config → these defaults (in
#      that priority order). See _resolve_params() below.
# CHANGED: April 2026 — Phase A.39b — expose to UI via config
_DEFAULT_TARGET_COVERAGE            = 0.95
_DEFAULT_PER_CONDITION_COVERAGE     = 0.95
_DEFAULT_MIN_NON_NAN_FRAC           = 0.95
_DEFAULT_POOL_SIZE                  = 40
_DEFAULT_MAX_CARDINALITY            = 5
_DEFAULT_MIN_CARDINALITY            = 2
_DEFAULT_MAX_ENUMERATIONS_PER_LEVEL = 5000
_DEFAULT_TIE_BREAK_WITHIN_PCT       = 0.10
_RNG_SEED                           = 42   # reproducibility — not user-tunable


def _resolve_params(params=None):
    """Build a concrete params dict by merging user overrides with defaults.

    Priority: explicit `params` arg > defaults. Callers that want to pull
    from p1_config.json should build the dict themselves (analyze.py does
    this) and pass it in. Missing keys fall back to the defaults.
    """
    p = params or {}
    def _f(key, default):
        try:
            v = p.get(key, default)
            return float(v) if v is not None else default
        except Exception:
            return default
    def _i(key, default):
        try:
            v = p.get(key, default)
            return int(float(v)) if v is not None else default
        except Exception:
            return default
    # WHY (Phase A.39b.5): Two new coercion helpers. _b parses truthy/falsy
    #      values robustly (tkinter writes 'true'/'false' strings; other
    #      callers may pass booleans or 1/0). _s clamps a string choice
    #      to a fixed allowlist.
    # CHANGED: April 2026 — Phase A.39b.5
    def _b(key, default):
        try:
            v = p.get(key, default)
            if isinstance(v, bool):
                return v
            if v is None:
                return default
            return str(v).strip().lower() in ('true', '1', 'yes', 'on')
        except Exception:
            return default
    def _s(key, default, allowed):
        try:
            v = p.get(key, default)
            if v is None:
                return default
            s = str(v).strip().lower()
            return s if s in allowed else default
        except Exception:
            return default

    out = {
        'target_coverage':            _f('target_coverage',            _DEFAULT_TARGET_COVERAGE),
        'per_condition_coverage':     _f('per_condition_coverage',     _DEFAULT_PER_CONDITION_COVERAGE),
        'min_non_nan_frac':           _f('min_non_nan_frac',           _DEFAULT_MIN_NON_NAN_FRAC),
        'pool_size':                  _i('pool_size',                  _DEFAULT_POOL_SIZE),
        'min_cardinality':            _i('min_cardinality',            _DEFAULT_MIN_CARDINALITY),
        'max_cardinality':            _i('max_cardinality',            _DEFAULT_MAX_CARDINALITY),
        'max_enumerations_per_level': _i('max_enumerations_per_level', _DEFAULT_MAX_ENUMERATIONS_PER_LEVEL),
        'tie_break_within_pct':       _f('tie_break_within_pct',       _DEFAULT_TIE_BREAK_WITHIN_PCT),
        # WHY (Phase A.39b.5): Two new user-tunable flags. dedup_correlated
        #      removes features >0.7 correlated with a higher-ranked pool
        #      member BEFORE conjunction enumeration. winner_selection
        #      switches the final-conjunction sort from tightness-first
        #      to coverage-first.
        # CHANGED: April 2026 — Phase A.39b.5
        'dedup_correlated':           _b('dedup_correlated',           False),
        'winner_selection':           _s('winner_selection',           'tightness',
                                         {'tightness', 'coverage'}),
    }
    # Defensive clamping so pathological inputs don't crash the search
    out['target_coverage']        = max(0.01, min(1.0,  out['target_coverage']))
    out['per_condition_coverage'] = max(0.01, min(1.0,  out['per_condition_coverage']))
    out['min_non_nan_frac']       = max(0.01, min(1.0,  out['min_non_nan_frac']))
    out['pool_size']              = max(1,    out['pool_size'])
    out['min_cardinality']        = max(1,    out['min_cardinality'])
    out['max_cardinality']        = max(out['min_cardinality'], out['max_cardinality'])
    out['max_enumerations_per_level'] = max(1, out['max_enumerations_per_level'])
    out['tie_break_within_pct']   = max(0.0,  out['tie_break_within_pct'])
    return out


# ── Phase A.39b.3 diagnostic state ─────────────────────────────────────────
# WHY (Phase A.39b.3): Module-level set of exception signatures already
#      reported during a scan so we don't spam the log with the same
#      failure repeated 620 times. Cleared at the start of each
#      discover_mode_a call (see reset inside that function).
# CHANGED: April 2026 — Phase A.39b.3
_scan_reported_exceptions = set()


# ── LEAK columns (same set analyze.py uses) ────────────────────────────────
# WHY: Feature selection must NOT consider trade outcome columns or meta
#      columns — that would produce a rule like "pips > 0" which trivially
#      covers 100% of wins but means nothing. Copied from analyze.py's
#      LEAK_COLS to stay in sync.
# CHANGED: April 2026 — Phase A.39b
_LEAK_COLS = frozenset({
    'trade_id', 'open_time', 'close_time', 'action', 'pips',
    'profit', 'lots', 'sl', 'tp', 'open_price', 'close_price',
    'is_winner', 'trade_direction', 'trade_duration_minutes',
    'outcome',
    '_LEAK_pips', '_LEAK_profit',
    'symbol', 'duration', 'change_pct', 'hour_of_day',
    'day_of_week', 'day_of_month',
})


def _is_feature_column(col_name, dtype):
    """Decide whether a column is a usable numeric feature."""
    if col_name in _LEAK_COLS:
        return False
    if col_name.startswith('_LEAK_'):
        return False
    if 'candle_idx' in col_name or 'candle_time' in col_name:
        return False
    if dtype not in ('float64', 'int64', 'float32', 'int32'):
        return False
    return True


def _scan_candidate_conditions(trade_df, background_df=None, params=None):
    """Build the per-feature candidate conditions from the trade dataset.

    For each usable feature, produce up to 2 single-sided candidates:
      feature > p5  (covers 95% of trades by construction)
      feature < p95 (covers 95% of trades by construction)

    Tightness is SELF-CONTAINED (Phase A.39b.4) — computed only from the
    trade dataset's own distribution. For each threshold the score is:

        extremity = |threshold - median| / IQR
        tightness = 1 / (1 + extremity)   in (0, 1]

    A threshold far from the median (in IQR units) scores near 0 (tight
    / specific). A threshold near the median scores near 1 (loose).
    This replaces the old background-based scoring, which was broken
    because the trade-level feature matrix and the cross-TF background
    compute some indicators at incompatible scales (A.39b.3 diagnostic).

    `background_df` is still accepted (and optionally exercised for
    diagnostic bg_cov metrics), but it does NOT affect tightness or
    ranking.

    Returns a list of dicts, each:
        {
          'feature':        str,
          'operator':       '>' or '<',
          'threshold':      float,
          'trade_coverage': float in [0,1],  # fraction of trades passing
          'background_coverage': float in [0,1] or None,  # diagnostic only
          'background_coverage_valid': bool,               # diagnostic only
          'tightness':      float in (0,1],  # lower = tighter
        }
    """
    p = _resolve_params(params)
    out = []
    n_trades = len(trade_df)
    if n_trades == 0:
        return out

    usable_cols = [
        c for c in trade_df.columns
        if _is_feature_column(c, str(trade_df[c].dtype))
    ]
    if not usable_cols:
        log.warning("[A.39b] no usable feature columns — cannot scan")
        return out

    bg_cols = set(background_df.columns) if background_df is not None else set()

    # WHY (Phase A.39b.3): When the cross-TF background merge succeeds
    #      but zero candidates end up with valid bg_cov, we need to
    #      know WHERE the mismatch is. Log the column overlap by
    #      prefix group so we can see at a glance whether specific
    #      timeframes are missing, or whether it's a naming drift
    #      between feature_matrix.csv and build_multi_tf_indicators().
    # CHANGED: April 2026 — Phase A.39b.3
    trade_cols_set = set(usable_cols)
    both = trade_cols_set & bg_cols
    trade_only = trade_cols_set - bg_cols
    bg_only = bg_cols - trade_cols_set
    log.info(
        f"  [A.39b.3] column overlap trade vs background: "
        f"both={len(both)} trade_only={len(trade_only)} bg_only={len(bg_only)}"
    )
    for _prefix in ('M5_', 'M15_', 'H1_', 'H4_', 'D1_'):
        _t = sum(1 for c in trade_cols_set if c.startswith(_prefix))
        _b_in_both = sum(1 for c in both if c.startswith(_prefix))
        _t_only = sum(1 for c in trade_only if c.startswith(_prefix))
        log.info(
            f"  [A.39b.3]   {_prefix}* — trade has {_t}, overlap with bg={_b_in_both}, "
            f"trade-only (not in bg)={_t_only}"
        )
    if trade_only:
        _sample = sorted(list(trade_only))[:5]
        log.info(f"  [A.39b.3]   sample trade-only features: {_sample}")

    # Value-range spot check on canonical features present in both frames.
    # If trade_df and background_df compute indicators differently (different
    # windowing, different scaling, wrong units, wrong timezone), the value
    # ranges will disagree and every threshold comparison will be nonsense.
    for _probe in ('H1_vwap', 'H1_atr_band_upper', 'H1_std_dev_20',
                   'H1_adx_14', 'M5_std_dev_50'):
        _in_t = _probe in trade_df.columns
        _in_b = (background_df is not None) and (_probe in bg_cols)
        if not _in_t and not _in_b:
            continue
        _msg = f"  [A.39b.3]   probe {_probe}:"
        if _in_t:
            try:
                _tv = pd.to_numeric(trade_df[_probe], errors='coerce').to_numpy(
                    dtype=float, copy=False
                )
                _tv_nn = _tv[~np.isnan(_tv)]
                if len(_tv_nn) > 0:
                    _msg += (
                        f" trade[min={_tv_nn.min():.4g},"
                        f" max={_tv_nn.max():.4g},"
                        f" mean={_tv_nn.mean():.4g},"
                        f" n={len(_tv_nn)}]"
                    )
                else:
                    _msg += " trade[all_NaN]"
            except Exception as _pe:
                _msg += f" trade[probe_failed:{_pe}]"
        else:
            _msg += " trade[NOT_IN_TRADE]"
        if _in_b:
            try:
                _bv = pd.to_numeric(background_df[_probe], errors='coerce').to_numpy(
                    dtype=float, copy=False
                )
                _bv_nn = _bv[~np.isnan(_bv)]
                if len(_bv_nn) > 0:
                    _msg += (
                        f" bg[min={_bv_nn.min():.4g},"
                        f" max={_bv_nn.max():.4g},"
                        f" mean={_bv_nn.mean():.4g},"
                        f" n={len(_bv_nn)}]"
                    )
                else:
                    _msg += " bg[all_NaN]"
            except Exception as _pe:
                _msg += f" bg[probe_failed:{_pe}]"
        else:
            _msg += " bg[NOT_IN_BG]"
        log.info(_msg)

    # Per-feature outcome counters for the post-scan distribution summary.
    _bg_outcome_counts = {
        'column_not_in_bg': 0,
        'coercion_failed':  0,
        'too_few_non_nan':  0,
        'bg_cov_zero':      0,
        'bg_cov_one':       0,
        'bg_cov_valid':     0,
        'bg_cov_low':       0,
    }

    for col in usable_cols:
        col_vals = trade_df[col]
        non_nan = col_vals.notna()
        if non_nan.sum() / n_trades < p['min_non_nan_frac']:
            continue
        vals = col_vals[non_nan].astype(float).values
        if len(vals) < 50:
            continue  # too few samples for a stable percentile
        try:
            p5  = float(np.percentile(vals, 5))
            p95 = float(np.percentile(vals, 95))
        except Exception as _pe:
            log.debug(f"[A.39b] percentile failed for {col}: {_pe}")
            continue

        # Degenerate: if p5 and p95 are equal the feature is constant —
        # useless as a discriminator.
        if p95 - p5 < 1e-12:
            continue

        for op, thr in (('>', p5), ('<', p95)):
            # Trade coverage — by construction should be ~0.95, but
            # compute exactly because NaN handling can nudge it.
            with np.errstate(invalid='ignore'):
                if op == '>':
                    passes = vals > thr
                else:
                    passes = vals < thr
            trade_cov = float(passes.sum() / len(vals))
            if trade_cov < p['per_condition_coverage'] - 0.02:  # allow small slop
                continue

            # Background coverage — fraction of all candle rows passing
            bg_cov = None
            if background_df is None or col not in bg_cols:
                # WHY (Phase A.39b.3): Record the dominant reason why
                #      bg_cov couldn't be computed.
                # CHANGED: April 2026 — Phase A.39b.3
                _bg_outcome_counts['column_not_in_bg'] += 1
            else:
                try:
                    bg_series = background_df[col]
                    if isinstance(bg_series, pd.DataFrame):
                        bg_series = bg_series.iloc[:, 0]
                    bg_arr = pd.to_numeric(bg_series, errors='coerce').to_numpy(
                        dtype=float, copy=False
                    )
                    bg_non_nan = ~np.isnan(bg_arr)
                    _n_bg_valid = int(bg_non_nan.sum())
                    if _n_bg_valid <= 100:
                        # WHY (Phase A.39b.3): differentiate "column missing"
                        #      from "column present but mostly NaN".
                        # CHANGED: April 2026 — Phase A.39b.3
                        _bg_outcome_counts['too_few_non_nan'] += 1
                    else:
                        with np.errstate(invalid='ignore'):
                            if op == '>':
                                bg_passes = bg_arr[bg_non_nan] > thr
                            else:
                                bg_passes = bg_arr[bg_non_nan] < thr
                        bg_cov = float(bg_passes.sum() / _n_bg_valid)
                        if bg_cov == 0.0:
                            _bg_outcome_counts['bg_cov_zero'] += 1
                        elif bg_cov == 1.0:
                            _bg_outcome_counts['bg_cov_one'] += 1
                        elif bg_cov > 0.001:
                            _bg_outcome_counts['bg_cov_valid'] += 1
                        else:
                            _bg_outcome_counts['bg_cov_low'] += 1
                except Exception as _be:
                    # WHY (Phase A.39b.3): Upgrade from silent debug to
                    #      visible warning. Cap at 3 distinct exception
                    #      signatures per scan to avoid spam.
                    # CHANGED: April 2026 — Phase A.39b.3
                    _bg_outcome_counts['coercion_failed'] += 1
                    _exc_key = f"{type(_be).__name__}:{str(_be)[:60]}"
                    if _exc_key not in _scan_reported_exceptions:
                        _scan_reported_exceptions.add(_exc_key)
                        if len(_scan_reported_exceptions) <= 3:
                            log.warning(
                                f"  [A.39b.3] bg_cov computation failed for "
                                f"{col!r}: {type(_be).__name__}: {_be}"
                            )

            # WHY (Phase A.39b.4): Background-based tightness was abandoned
            #      after A.39b.3 diagnostics proved the trade and
            #      cross-TF bg datasets compute indicators at different
            #      scales (e.g. H1_vwap trade=$1507..$5534 vs bg=$1235..
            #      $1612), so bg_cov comparisons are meaningless. Replace
            #      with a SELF-CONTAINED tightness based only on the
            #      trade dataset's own distribution: how far is the
            #      threshold from the feature's median, in IQR units?
            #        extremity = |thr - median| / IQR
            #        tightness = 1 / (1 + extremity)  ∈ (0, 1]
            #      A threshold far from the median (large extremity)
            #      scores close to 0 → tight. A threshold near the
            #      median (small extremity) scores close to 1 → loose.
            #      No cross-dataset alignment, no cache, no scale
            #      mismatch possible.
            # CHANGED: April 2026 — Phase A.39b.4
            try:
                _median = float(np.median(vals))
                _q1 = float(np.percentile(vals, 25))
                _q3 = float(np.percentile(vals, 75))
                _iqr = _q3 - _q1
                if _iqr <= 1e-12:
                    # Degenerate: distribution is a point mass around
                    # the median. Any threshold is either on top of the
                    # median (loose) or arbitrarily far (tight in raw
                    # units but meaningless). Score as loose.
                    tightness = 1.0
                else:
                    extremity = abs(thr - _median) / _iqr
                    tightness = 1.0 / (1.0 + extremity)
                    tightness = max(0.0, min(1.0, float(tightness)))
            except Exception:
                tightness = 1.0

            # Keep the bg_cov_valid flag for diagnostic/back-compat
            # purposes (it's still surfaced in the candidate dict and
            # used by the post-scan distribution summary) but it no
            # longer feeds into tightness scoring.
            _BG_COV_VALID_EPS = 0.001
            bg_cov_valid = (bg_cov is not None) and (bg_cov > _BG_COV_VALID_EPS)

            # WHY (Phase A.40a): emit BOTH 'threshold' (legacy Mode A
            #      key) and 'value' (canonical key used by Step 3, Step 4,
            #      and the backtester). Older consumers keep reading
            #      'threshold'; new consumers including the rule library
            #      bridge see 'value'. Same number under two keys.
            # CHANGED: April 2026 — Phase A.40a
            _thr_rounded = round(thr, 6)
            out.append({
                'feature':             col,
                'operator':            op,
                'threshold':           _thr_rounded,
                'value':               _thr_rounded,
                'trade_coverage':      round(trade_cov, 4),
                'background_coverage': round(bg_cov, 4) if bg_cov is not None else None,
                'background_coverage_valid': bool(bg_cov_valid),
                'tightness':           round(tightness, 4),
            })

    # WHY (Phase A.39b.4): bg_cov no longer drives tightness — this
    #      summary is purely diagnostic now, and only meaningful if a
    #      background frame was actually supplied. Suppress it when
    #      there's no background so the log isn't cluttered with
    #      all-zero rows.
    # CHANGED: April 2026 — Phase A.39b.4
    if background_df is not None:
        _total = sum(_bg_outcome_counts.values())
        log.info(
            f"  [A.39b.4] bg_cov outcome distribution (diagnostic only, "
            f"no longer affects tightness; total={_total}):"
        )
        for _k in ('bg_cov_valid', 'bg_cov_zero', 'bg_cov_one',
                   'bg_cov_low', 'column_not_in_bg',
                   'too_few_non_nan', 'coercion_failed'):
            _v = _bg_outcome_counts.get(_k, 0)
            _pct = (100.0 * _v / _total) if _total > 0 else 0.0
            log.info(f"  [A.39b.4]   {_k:<18} {_v:>5}  ({_pct:5.1f}%)")

    return out


def _build_condition_mask(df, cond):
    """Given a condition dict, return a boolean numpy array of length len(df).
    NaN values evaluate to False."""
    col = cond['feature']
    if col not in df.columns:
        return None
    _raw = df[col]
    if isinstance(_raw, pd.DataFrame):
        _raw = _raw.iloc[:, 0]
    try:
        arr = pd.to_numeric(_raw, errors='coerce').to_numpy(dtype=float, copy=False)
    except Exception:
        return None
    thr = float(cond['threshold'])
    op = cond['operator']
    with np.errstate(invalid='ignore'):
        if op == '>':
            m = arr > thr
        elif op == '<':
            m = arr < thr
        elif op == '>=':
            m = arr >= thr
        elif op == '<=':
            m = arr <= thr
        else:
            return None
    m = np.where(np.isnan(arr), False, m)
    return m


def _combinations_capped(pool, r, cap):
    """Generate combinations of `pool` taken `r` at a time, capped at `cap`.
    If total combinations exceed cap, randomly sample `cap` of them using
    a seeded RNG. Yields tuples of indices.
    """
    import itertools
    n = len(pool)
    if r > n:
        return
    total = math.comb(n, r)
    if total <= cap:
        for combo in itertools.combinations(range(n), r):
            yield combo
        return
    # Sampling fallback — seeded RNG for reproducibility
    rng = random.Random(_RNG_SEED + r)
    seen = set()
    while len(seen) < cap:
        combo = tuple(sorted(rng.sample(range(n), r)))
        if combo in seen:
            continue
        seen.add(combo)
        yield combo


def discover_mode_a(trade_df, background_df=None, progress_log=None,
                    params=None, model_result=None):
    """Main entry. Returns dict suitable for JSON serialization.

    Args:
        trade_df:       trade-level feature matrix (one row per historical trade).
        background_df:  optional candle-level indicator dataframe for tightness
                        scoring. If None, tightness uses a weaker proxy.
        progress_log:   optional callable (str -> None) for streaming log lines
                        in addition to the module logger.
        params:         optional dict of overrides for the 8 tunable knobs:
                          target_coverage, per_condition_coverage,
                          min_non_nan_frac, pool_size, min_cardinality,
                          max_cardinality, max_enumerations_per_level,
                          tie_break_within_pct.
                        Missing keys fall back to module defaults.

    Returns:
        {
          'status':  'ok' | 'no_candidates' | 'no_conjunction' | 'failed',
          'variant': 'a',
          'target_coverage': 0.95,
          'trade_count':     int,
          'candidate_pool':  list of condition dicts,
          'chosen':          list of condition dicts (the AND-conjunction)
                             — empty if status != 'ok',
          'chosen_stats':    {'joint_coverage': float, 'tightness_product': float,
                              'cardinality': int},
          'top_10_conjunctions': list of alternatives with same schema as
                                 chosen_stats + 'features',
          'reason':          str, present when status != 'ok',
        }
    """
    def _l(msg):
        log.info(msg)
        if progress_log is not None:
            try:
                progress_log(msg)
            except Exception:
                pass

    try:
        # WHY (Phase A.39b.3): Reset the per-module exception signature
        #      cache so diagnostic warnings fire fresh for each
        #      scenario run, not just the very first run after
        #      process start.
        # CHANGED: April 2026 — Phase A.39b.3
        try:
            _scan_reported_exceptions.clear()
        except Exception:
            pass

        p = _resolve_params(params)
        _l(f"  [A.39b] params: target={p['target_coverage']:.2%} "
           f"per_cond={p['per_condition_coverage']:.2%} "
           f"min_nonNaN={p['min_non_nan_frac']:.2%} "
           f"pool={p['pool_size']} card={p['min_cardinality']}..{p['max_cardinality']} "
           f"cap={p['max_enumerations_per_level']} tie={p['tie_break_within_pct']:.2%}")

        n_trades = len(trade_df)
        if n_trades == 0:
            return {
                'status':  'failed',
                'variant': 'a',
                'reason':  'trade_df is empty',
            }

        _l(f"  [A.39b] trade dataset: {n_trades} trades")

        # WHY (Phase A.39b.1 — Bug 1 fix): The H1 indicator cache spans
        #      the full candle history (~2000 onwards). Trades typically
        #      span a much shorter recent window. Cumulative indicators
        #      like VWAP have radically different value ranges across
        #      eras. Fix: restrict background_df to candles whose
        #      timestamp falls within the trade history's time window.
        # WHY (Phase A.39b.2): analyze.py now scopes the background
        #      BEFORE calling discover_mode_a (to save a costly cross-TF
        #      merge over irrelevant pre-trade candles). This block is
        #      now a safety net — it detects whether the frame is
        #      already scoped and either skips silently or re-scopes
        #      if the caller passed an un-scoped frame (test harnesses,
        #      custom invocations).
        # CHANGED: April 2026 — Phase A.39b.1 / A.39b.2
        if background_df is not None and 'timestamp' in background_df.columns:
            try:
                if 'open_time' in trade_df.columns:
                    _t_series = pd.to_datetime(
                        trade_df['open_time'], errors='coerce'
                    ).dropna()
                    if len(_t_series) > 0:
                        _t_min = _t_series.min()
                        _t_max = _t_series.max()
                        _bg_ts = pd.to_datetime(
                            background_df['timestamp'], errors='coerce'
                        )
                        _bg_min = _bg_ts.min()
                        _bg_max = _bg_ts.max()

                        # If the background is already inside the trade
                        # window (analyze.py did the scoping for us),
                        # don't re-scope. Tolerance: 1 day either side
                        # to absorb float/date rounding.
                        _already_scoped = (
                            pd.notna(_bg_min) and pd.notna(_bg_max)
                            and (_bg_min >= _t_min - pd.Timedelta(days=1))
                            and (_bg_max <= _t_max + pd.Timedelta(days=1))
                        )

                        if _already_scoped:
                            _l(
                                f"  [A.39b.1/2] background already scoped to trade "
                                f"window by caller ({len(background_df)} rows). "
                                f"Skipping redundant scoping."
                            )
                        else:
                            _in_window = (_bg_ts >= _t_min) & (_bg_ts <= _t_max)
                            _n_before = len(background_df)
                            background_df = background_df[_in_window].reset_index(drop=True)
                            _n_after = len(background_df)
                            _l(
                                f"  [A.39b.1] scoped background to trade time window "
                                f"[{_t_min.date()} .. {_t_max.date()}]: "
                                f"{_n_before} -> {_n_after} candles "
                                f"({_n_after / max(_n_before, 1) * 100:.1f}% kept)"
                            )
                            if _n_after < 1000:
                                _l(
                                    f"  [A.39b.1] WARNING: only {_n_after} background "
                                    f"candles overlap the trade window — tightness "
                                    f"scoring may be unreliable."
                                )
                else:
                    _l(
                        "  [A.39b.1] trade_df has no 'open_time' column — "
                        "cannot scope background. Using as-is."
                    )
            except Exception as _scoping_e:
                _l(
                    f"  [A.39b.1] background scoping check failed: "
                    f"{_scoping_e} — using background as-is"
                )

        # WHY (Phase A.39b.1 — Bug 4 fix): Build an RF importance lookup
        #      so the pool-ranking sort can tie-break by "is this
        #      feature informative according to the Random Forest"
        #      when two candidates share rounded tightness.
        # CHANGED: April 2026 — Phase A.39b.1 — Bug 4 fix
        rf_importance_map = {}
        try:
            if model_result is not None:
                for _feat, _imp in (model_result.get('importances') or []):
                    try:
                        rf_importance_map[str(_feat)] = float(_imp)
                    except Exception:
                        pass
                _l(
                    f"  [A.39b.1] loaded RF importance for "
                    f"{len(rf_importance_map)} features (tiebreaker enabled)"
                )
        except Exception as _rf_e:
            _l(f"  [A.39b.1] could not load RF importance — no tiebreaker: {_rf_e}")

        # ── Step 1-3: candidate scan + tightness + pool ──────────────────
        all_cands = _scan_candidate_conditions(trade_df, background_df, params=p)
        _l(f"  [A.39b] scanned {len(all_cands)} single-sided candidate conditions")

        if not all_cands:
            return {
                'status':  'no_candidates',
                'variant': 'a',
                'target_coverage': p['target_coverage'],
                'trade_count':     n_trades,
                'candidate_pool':  [],
                'chosen':          [],
                'chosen_stats':    None,
                'top_10_conjunctions': [],
                'params':          p,
                'reason':          'No usable feature columns produced candidate conditions.',
            }

        # WHY (Phase A.39b.4): Triviality filter is now driven by
        #      self-contained tightness instead of background coverage.
        #      A threshold sitting right on or very close to the
        #      feature's own median (extremity ≈ 0, tightness ≈ 1) is
        #      trivially true on the trade set and carries no signal
        #      — drop it. The secondary guard catches the edge case of
        #      a candidate that is just barely under the ceiling but
        #      also covers nearly every trade (≥98%) with almost no
        #      tightness to speak of.
        # CHANGED: April 2026 — Phase A.39b.4
        _TRIVIAL_TIGHTNESS_CEIL  = 0.95   # tightness >= ceil → trivial
        _TRIVIAL_TRADE_COV_FLOOR = 0.98   # secondary guard
        _TRIVIAL_SECONDARY_TIGHT = 0.80   # secondary guard tightness
        non_trivial = []
        _trivial_dropped = 0
        for c in all_cands:
            is_trivial = False
            tight = c.get('tightness')
            if tight is None:
                is_trivial = True
            elif tight >= _TRIVIAL_TIGHTNESS_CEIL:
                is_trivial = True
            elif (tight >= _TRIVIAL_SECONDARY_TIGHT
                  and c.get('trade_coverage', 0.0) >= _TRIVIAL_TRADE_COV_FLOOR):
                is_trivial = True
            if is_trivial:
                _trivial_dropped += 1
            else:
                non_trivial.append(c)
        _l(f"  [A.39b.4] filtered {_trivial_dropped} trivial candidates "
           f"(tightness>={_TRIVIAL_TIGHTNESS_CEIL} or "
           f"tightness>={_TRIVIAL_SECONDARY_TIGHT}+trade_cov>={_TRIVIAL_TRADE_COV_FLOOR}); "
           f"{len(non_trivial)} remain")

        if not non_trivial:
            return {
                'status':  'no_candidates',
                'variant': 'a',
                'target_coverage': p['target_coverage'],
                'trade_count':     n_trades,
                'candidate_pool':  [],
                'chosen':          [],
                'chosen_stats':    None,
                'top_10_conjunctions': [],
                'params':          p,
                'reason':          f'All {len(all_cands)} candidates were trivial '
                                   f'(covered >={int(_TRIVIAL_TRADE_COV_FLOOR*100)}% of '
                                   f'trades with no measurable background contrast). '
                                   f'The indicator cache may not overlap the trade '
                                   f'time range — check that trades fall within the '
                                   f'background candle timestamps.',
            }

        # WHY (Phase A.39b.1 — Bug 4 fix): Add an RF-importance tiebreaker
        #      to the sort. When two candidates share the same rounded
        #      tightness, the one the Random Forest already identified
        #      as informative wins. rf_importance_map is built from
        #      model_result at the top of discover_mode_a.
        # CHANGED: April 2026 — Phase A.39b.1 — Bug 4 fix
        non_trivial.sort(key=lambda c: (
            round(c['tightness'], 3),                                  # primary
            -c['trade_coverage'],                                      # secondary
            -float(rf_importance_map.get(c['feature'], 0.0)),          # tiebreaker
            c['feature'],                                              # final determinism
        ))

        # WHY (Phase A.39b.5): Optional correlation-based dedup. When
        #      enabled, iterate the tightness-sorted candidates and
        #      reject any whose FEATURE has |Pearson corr| > 0.7 with
        #      a feature already kept. Correlation is computed on the
        #      trade dataframe (shared non-NaN rows) — the same data
        #      the thresholds were derived from, so it's self-consistent.
        #
        #      Two candidates with the SAME feature (one '>' at P5 and
        #      one '<' at P95) are NOT deduped against each other —
        #      they represent opposite sides of the distribution and
        #      carry different information. Dedup is feature-to-feature.
        #
        #      A single pass over non_trivial collecting at most
        #      pool_size unique-feature-class members avoids computing
        #      pairwise correlations for the full 1069-candidate pool
        #      (would be O(N^2) = ~1.1M corr calls). Instead we compute
        #      O(kept * candidate_scan) ≈ O(pool_size * 2 * pool_size) =
        #      ~3200 corr calls for pool_size=40. Fast.
        # CHANGED: April 2026 — Phase A.39b.5
        if p['dedup_correlated']:
            _DEDUP_CORR_THRESHOLD = 0.7
            _kept_features = []         # list of feature names already in dedup_pool
            _kept_series_cache = {}     # feature_name -> numpy array of non-NaN trade values (for reuse)
            dedup_pool = []
            _dedup_rejected = 0
            for cand in non_trivial:
                if len(dedup_pool) >= p['pool_size']:
                    break
                _feat = cand['feature']
                # Same-feature candidates (e.g. '>' at P5 and '<' at P95)
                # share dedup status. If this feature is already kept,
                # admit the new candidate without a corr check — it's the
                # opposite threshold direction, informationally distinct.
                if _feat in _kept_features:
                    dedup_pool.append(cand)
                    continue
                # Check corr vs every DIFFERENT feature already kept
                try:
                    _cand_vals = trade_df[_feat]
                    _cand_arr = pd.to_numeric(_cand_vals, errors='coerce').to_numpy(
                        dtype=float, copy=False
                    )
                except Exception:
                    # If we can't pull the values, we can't dedup — admit it.
                    dedup_pool.append(cand)
                    _kept_features.append(_feat)
                    continue
                is_redundant = False
                for _kept_feat in _kept_features:
                    if _kept_feat == _feat:
                        continue
                    _kept_arr = _kept_series_cache.get(_kept_feat)
                    if _kept_arr is None:
                        try:
                            _kept_arr = pd.to_numeric(
                                trade_df[_kept_feat], errors='coerce'
                            ).to_numpy(dtype=float, copy=False)
                            _kept_series_cache[_kept_feat] = _kept_arr
                        except Exception:
                            continue
                    # Both arrays same length (= n_trades). Mask to shared non-NaN.
                    _mask = ~(np.isnan(_cand_arr) | np.isnan(_kept_arr))
                    if _mask.sum() < 50:
                        continue  # too few shared rows to trust the corr
                    try:
                        _a = _cand_arr[_mask]
                        _b = _kept_arr[_mask]
                        _sa, _sb = _a.std(), _b.std()
                        if _sa <= 1e-12 or _sb <= 1e-12:
                            continue  # one side is constant → corr undefined
                        _corr = float(np.corrcoef(_a, _b)[0, 1])
                        if np.isnan(_corr):
                            continue
                        if abs(_corr) > _DEDUP_CORR_THRESHOLD:
                            is_redundant = True
                            break
                    except Exception:
                        continue
                if is_redundant:
                    _dedup_rejected += 1
                else:
                    dedup_pool.append(cand)
                    _kept_features.append(_feat)
                    _kept_series_cache[_feat] = _cand_arr
            _l(f"  [A.39b.5] dedup correlated features (>{_DEDUP_CORR_THRESHOLD}): "
               f"rejected {_dedup_rejected}, pool size {len(dedup_pool)}/{p['pool_size']}")
            pool = dedup_pool[:p['pool_size']]
        else:
            pool = non_trivial[:p['pool_size']]
        # WHY (Phase A.39b.4): tightness is self-contained now, so the
        #      meaningful diagnostic is the spread of pool tightness
        #      scores — if they cluster near 1.0 the pool has no tight
        #      candidates; if they span a wide range the pool is
        #      healthy.
        # CHANGED: April 2026 — Phase A.39b.4
        _pool_tights = [c.get('tightness', 1.0) for c in pool]
        if _pool_tights:
            _t_min = min(_pool_tights)
            _t_max = max(_pool_tights)
            _t_med = float(np.median(_pool_tights))
            _l(
                f"  [A.39b] candidate pool: top {len(pool)} by tightness "
                f"(range: min={_t_min:.3f}, median={_t_med:.3f}, max={_t_max:.3f})"
            )
        else:
            _l(f"  [A.39b] candidate pool: top {len(pool)} by tightness (empty)")
        for i, c in enumerate(pool[:5]):
            if c.get('background_coverage') is not None:
                bg_str = f"bg={c['background_coverage']:.2%}"
                if not c.get('background_coverage_valid'):
                    bg_str += " (invalid)"
            else:
                bg_str = "bg=n/a"
            _l(f"    [{i+1}] {c['feature']} {c['operator']} {c['threshold']}  "
               f"trade={c['trade_coverage']:.2%} {bg_str} tight={c['tightness']:.3f}")

        # ── Step 4: precompute per-condition boolean masks for the pool ──
        pool_masks = []
        for c in pool:
            m = _build_condition_mask(trade_df, c)
            if m is None:
                pool_masks.append(None)
            else:
                pool_masks.append(m)

        # Reject pool members whose mask is None (cannot evaluate)
        pool_valid_idx = [i for i, m in enumerate(pool_masks) if m is not None]
        pool = [pool[i] for i in pool_valid_idx]
        pool_masks = [pool_masks[i] for i in pool_valid_idx]
        _l(f"  [A.39b] {len(pool)} candidates have valid masks after rebuild")

        # ── Step 5: enumerate conjunctions and find ones meeting target ──
        valid_conjunctions = []
        for r in range(p['min_cardinality'], p['max_cardinality'] + 1):
            _l(f"  [A.39b] enumerating conjunctions of size {r}...")
            count_seen = 0
            count_passing = 0
            for combo_idx in _combinations_capped(pool, r, p['max_enumerations_per_level']):
                count_seen += 1
                joint = pool_masks[combo_idx[0]].copy()
                for k in combo_idx[1:]:
                    joint &= pool_masks[k]
                joint_cov = float(joint.sum() / n_trades)
                if joint_cov < p['target_coverage']:
                    continue
                # Tightness product across selected conditions
                tightness_product = 1.0
                for k in combo_idx:
                    tightness_product *= max(pool[k]['tightness'], 1e-6)
                valid_conjunctions.append({
                    'indices':        list(combo_idx),
                    'features':       [pool[k]['feature'] for k in combo_idx],
                    'joint_coverage': round(joint_cov, 4),
                    'tightness_product': round(tightness_product, 6),
                    'cardinality':    r,
                })
                count_passing += 1
            _l(f"  [A.39b] size {r}: seen {count_seen} combos, "
               f"{count_passing} met >={p['target_coverage']:.0%} coverage")

        if not valid_conjunctions:
            return {
                'status':  'no_conjunction',
                'variant': 'a',
                'target_coverage': p['target_coverage'],
                'trade_count':     n_trades,
                'candidate_pool':  pool,
                'chosen':          [],
                'chosen_stats':    None,
                'top_10_conjunctions': [],
                'params':          p,
                'reason':          f'No conjunction of {p["min_cardinality"]}..{p["max_cardinality"]} conditions '
                                   f'reached {p["target_coverage"]:.0%} coverage. The bot may '
                                   f'use conditions that span beyond the {p["pool_size"]}-candidate '
                                   f'pool — try reducing per-condition coverage or expanding '
                                   f'the pool size.',
            }

        # ── Step 6-7: winner selection — tightness-first or coverage-first ──
        # WHY (Phase A.39b.5): User-tunable. Default 'tightness' preserves
        #      A.39b.4 behavior. 'coverage' picks the highest-coverage
        #      conjunction above target, tie-breaking by tightness.
        #
        #      In both paths: after identifying a best-scoring candidate,
        #      admit all conjunctions within tie_break_within_pct of that
        #      best score, then prefer SHORTER conjunctions (fewer
        #      conditions = simpler rule) as the final tiebreaker.
        # CHANGED: April 2026 — Phase A.39b.5
        if p['winner_selection'] == 'coverage':
            # Higher joint_coverage = better → sort descending by coverage,
            # then ascending by tightness_product (tighter is better).
            valid_conjunctions.sort(key=lambda v: (-v['joint_coverage'],
                                                   v['tightness_product']))
            best_coverage = valid_conjunctions[0]['joint_coverage']
            # Tie zone: accept conjunctions within tie_break_within_pct of
            # the best coverage. Symmetric with the tightness path: both
            # use tie_break_within_pct to widen the winner zone.
            cov_floor = best_coverage * (1.0 - p['tie_break_within_pct'])
            near_best = [v for v in valid_conjunctions
                         if v['joint_coverage'] >= cov_floor]
            # Final tiebreak: shorter conjunction, then tighter product.
            near_best.sort(key=lambda v: (v['cardinality'], v['tightness_product']))
            chosen = near_best[0]
            _l(f"  [A.39b.5] winner_selection=coverage: best cov="
               f"{best_coverage:.2%}, admitted {len(near_best)} in tie zone, "
               f"final: {chosen['cardinality']} conds cov="
               f"{chosen['joint_coverage']:.2%} tightness_product="
               f"{chosen['tightness_product']}")
        else:
            # Tightness-first (original A.39b/A.39b.1/A.39b.4 behavior)
            valid_conjunctions.sort(key=lambda v: v['tightness_product'])
            best_score = valid_conjunctions[0]['tightness_product']
            tie_limit = best_score * (1.0 + p['tie_break_within_pct'])
            near_best = [v for v in valid_conjunctions
                         if v['tightness_product'] <= tie_limit]
            near_best.sort(key=lambda v: (v['cardinality'], v['tightness_product']))
            chosen = near_best[0]

        chosen_conditions = [pool[k] for k in chosen['indices']]
        _l(f"  [A.39b] chosen conjunction: {chosen['cardinality']} conditions, "
           f"joint coverage={chosen['joint_coverage']:.2%}, "
           f"tightness product={chosen['tightness_product']}")
        for c in chosen_conditions:
            bg_str = (
                f"bg={c['background_coverage']:.2%}"
                if c['background_coverage'] is not None else "bg=n/a"
            )
            _l(f"    {c['feature']} {c['operator']} {c['threshold']}  "
               f"trade={c['trade_coverage']:.2%} {bg_str}")

        top_10 = []
        for v in valid_conjunctions[:10]:
            top_10.append({
                'features':          v['features'],
                'conditions':        [pool[k] for k in v['indices']],
                'joint_coverage':    v['joint_coverage'],
                'tightness_product': v['tightness_product'],
                'cardinality':       v['cardinality'],
            })

        # WHY (Phase A.40a): pipe the discovered Mode A conjunction
        #      into the shared saved_rules.json library so the Saved
        #      Rules panel and the backtester see it without the user
        #      manually clicking 💾. Source tag captures the key
        #      shape parameters so a rediscovery with different
        #      settings produces a distinct-source entry. Honors the
        #      global auto-save checkbox via is_auto_save_enabled().
        # WHY (Phase A.40a.2): Mode A previously logged ONLY on
        #      exception, so a successful save was completely silent
        #      from the user's perspective — they had to dig into
        #      saved_rules.json to confirm anything happened. Add an
        #      explicit success log line with library size delta and
        #      surface the first invalid-rule reason via the bridge's
        #      diag field. Also honor the global auto-save checkbox at
        #      the hook level for symmetry with Step 3 / Step 4.
        # CHANGED: April 2026 — Phase A.40a / A.40a.2
        try:
            from shared.rule_library_bridge import (
                auto_save_discovered_rules as _a40a_save,
                is_auto_save_enabled as _a40a_enabled,
            )
            _a40a_rule = {
                'conditions': [
                    {
                        'feature':  c['feature'],
                        'operator': c['operator'],
                        'value':    c.get('value', c.get('threshold')),
                    }
                    for c in chosen_conditions
                ],
                'prediction':       'BUY',  # Mode A is direction-agnostic; default BUY
                # WHY (Phase A.40a hotfix): Mode A doesn't compute a WR
                #      (it's a coverage/tightness optimiser). Writing
                #      None here crashed the Saved Rules panel which
                #      compares wr <= 1.0. Use 0.0 — display path
                #      already treats it as "no WR available".
                # CHANGED: April 2026 — Phase A.40a hotfix
                'win_rate':         0.0,
                'avg_pips':         0.0,
                'coverage':         int(round(float(chosen['joint_coverage']) * n_trades)),
                'confidence':       float(chosen['joint_coverage']),
                'tightness_product': float(chosen['tightness_product']),
            }
            _winner = p.get('winner_selection', 'tightness')
            _src = (
                f"ModeA:cov{int(round(float(chosen['joint_coverage'])*100))}%"
                f":tight{float(chosen['tightness_product']):.3f}"
                f":winner={_winner}"
            )
            if not _a40a_enabled():
                try:
                    _l(f"  [A.40a.2] Mode A auto-save DISABLED via global "
                       f"checkbox — discovered rule NOT piped into library")
                except Exception:
                    pass
            else:
                try:
                    from shared.saved_rules import load_all as _a40a_load_all
                    _a40a_size_before = len(_a40a_load_all() or [])
                except Exception:
                    _a40a_size_before = -1
                _s, _d, _i, _diag = _a40a_save([_a40a_rule], source=_src, dedup=True)
                try:
                    _a40a_size_after = len(_a40a_load_all() or [])
                except Exception:
                    _a40a_size_after = -1
                try:
                    _l(f"  [A.40a] Mode A auto-save: "
                       f"saved={_s}, dedup-skipped={_d}, invalid={_i} "
                       f"(library: {_a40a_size_before} → {_a40a_size_after})")
                except Exception:
                    pass
                if _i > 0 and _diag is not None:
                    try:
                        _l(f"  [A.40a.2] Mode A first invalid rule reason: "
                           f"{_diag.get('reason')}; sample={_diag.get('sample')}")
                    except Exception:
                        pass
        except Exception as _a40a_e:
            try:
                _l(f"  [A.40a] mode-A auto-save skipped: "
                   f"{type(_a40a_e).__name__}: {_a40a_e}")
            except Exception:
                pass

        return {
            'status':           'ok',
            'variant':          'a',
            'target_coverage':  p['target_coverage'],
            'trade_count':      n_trades,
            'candidate_pool':   pool,
            'chosen':           chosen_conditions,
            'chosen_stats': {
                'joint_coverage':    chosen['joint_coverage'],
                'tightness_product': chosen['tightness_product'],
                'cardinality':       chosen['cardinality'],
            },
            'top_10_conjunctions': top_10,
            'params':           p,
        }

    except Exception as _e:
        import traceback as _tb
        return {
            'status':  'failed',
            'variant': 'a',
            'reason':  f'{type(_e).__name__}: {_e}',
            'trace':   _tb.format_exc(),
        }
