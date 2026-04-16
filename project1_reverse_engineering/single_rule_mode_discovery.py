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
    out = {
        'target_coverage':            _f('target_coverage',            _DEFAULT_TARGET_COVERAGE),
        'per_condition_coverage':     _f('per_condition_coverage',     _DEFAULT_PER_CONDITION_COVERAGE),
        'min_non_nan_frac':           _f('min_non_nan_frac',           _DEFAULT_MIN_NON_NAN_FRAC),
        'pool_size':                  _i('pool_size',                  _DEFAULT_POOL_SIZE),
        'min_cardinality':            _i('min_cardinality',            _DEFAULT_MIN_CARDINALITY),
        'max_cardinality':            _i('max_cardinality',            _DEFAULT_MAX_CARDINALITY),
        'max_enumerations_per_level': _i('max_enumerations_per_level', _DEFAULT_MAX_ENUMERATIONS_PER_LEVEL),
        'tie_break_within_pct':       _f('tie_break_within_pct',       _DEFAULT_TIE_BREAK_WITHIN_PCT),
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

    Tightness is computed against `background_df` when provided. The
    background is the broader candle dataset (all candles in the
    indicator cache) — it tells us what fraction of the feature's
    natural range is cut by the trade-derived bound. If no background
    is available, tightness falls back to the width of the bound
    relative to the feature's own trade-set std (less informative but
    non-zero).

    Returns a list of dicts, each:
        {
          'feature':        str,
          'operator':       '>' or '<',
          'threshold':      float,
          'trade_coverage': float in [0,1],  # fraction of trades passing
          'background_coverage': float in [0,1] or None,
                          # fraction of background candles passing;
                          # None if background unavailable
          'tightness':      float in [0,1],  # lower = tighter
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
            if background_df is not None and col in bg_cols:
                try:
                    bg_series = background_df[col]
                    if isinstance(bg_series, pd.DataFrame):
                        bg_series = bg_series.iloc[:, 0]
                    bg_arr = pd.to_numeric(bg_series, errors='coerce').to_numpy(
                        dtype=float, copy=False
                    )
                    bg_non_nan = ~np.isnan(bg_arr)
                    if bg_non_nan.sum() > 100:
                        with np.errstate(invalid='ignore'):
                            if op == '>':
                                bg_passes = bg_arr[bg_non_nan] > thr
                            else:
                                bg_passes = bg_arr[bg_non_nan] < thr
                        bg_cov = float(bg_passes.sum() / bg_non_nan.sum())
                except Exception as _be:
                    log.debug(f"[A.39b] background cov failed for {col}: {_be}")

            # WHY (Phase A.39b.1): Old code treated bg_cov=0.0 as
            #      "maximally tight" → such features sorted to the top
            #      of the pool. But bg_cov=0.0 actually means the
            #      background computation is BROKEN or MEANINGLESS —
            #      the feature's values across the background set do
            #      not span the trade-set threshold at all (e.g. VWAP
            #      compared against 25 years of mis-scoped candles).
            #      Treating that as "tight" guarantees garbage ranking.
            #
            #      New rule: bg_cov must be > a small epsilon to be
            #      trusted as a tightness signal. If bg_cov is below
            #      epsilon, or None, fall back to the proxy (trade-range
            #      threshold position). Record which path was taken so
            #      the pool ranking can apply a tiebreaker.
            # CHANGED: April 2026 — Phase A.39b.1 — Bug 2 fix
            _BG_COV_VALID_EPS = 0.001   # bg_cov must exceed 0.1% to be usable
            bg_cov_valid = (bg_cov is not None) and (bg_cov > _BG_COV_VALID_EPS)

            if bg_cov_valid:
                # Primary scoring path — use background coverage directly.
                # Smaller bg_cov = fewer background candles pass the bound
                # = tighter / more specific condition.
                tightness = float(bg_cov)
            else:
                # Fallback proxy — normalize threshold within trade range.
                # Worse than bg_cov (doesn't compare to broader population)
                # but at least deterministic and monotonic with threshold
                # position. Capped in [0, 1]. A constant feature resolves
                # to 1.0 (not useful).
                try:
                    vmin, vmax = float(vals.min()), float(vals.max())
                    span = vmax - vmin
                    if span <= 1e-12:
                        tightness = 1.0   # constant feature → not useful
                    else:
                        if op == '>':
                            tightness = (thr - vmin) / span
                        else:
                            tightness = (vmax - thr) / span
                        tightness = max(0.0, min(1.0, float(tightness)))
                except Exception:
                    tightness = 1.0
                # Extra penalty for fallback-path candidates so bg-valid
                # candidates always outrank them at the top of the pool
                # when both are available. The 0.5 offset pushes proxy
                # scores into the lower half of [0, 1]; a bg_cov=0.3
                # candidate (real) will sort above a proxy-tight=0.2
                # candidate because 0.3 < 0.7.
                tightness = 0.5 + 0.5 * tightness   # now lives in [0.5, 1.0]

            out.append({
                'feature':             col,
                'operator':            op,
                'threshold':           round(thr, 6),
                'trade_coverage':      round(trade_cov, 4),
                'background_coverage': round(bg_cov, 4) if bg_cov is not None else None,
                'background_coverage_valid': bool(bg_cov_valid),
                'tightness':           round(tightness, 4),
            })

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
        #      eras; comparing trade-time VWAP thresholds against
        #      candle-era VWAP values gives garbage tightness scores.
        #      Fix: restrict background_df to candles whose timestamp
        #      falls within the trade history's time window.
        # CHANGED: April 2026 — Phase A.39b.1 — Bug 1 fix
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
                                f"scoring may be unreliable. Consider expanding the "
                                f"background cache or shortening the trade history."
                            )
                else:
                    _l(
                        "  [A.39b.1] trade_df has no 'open_time' column — "
                        "cannot scope background. Using full background as-is."
                    )
            except Exception as _scoping_e:
                _l(
                    f"  [A.39b.1] background scoping failed: {_scoping_e} — "
                    f"using full background as-is"
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

        # WHY (Phase A.39b.1 — Bug 3 fix): Filter out TRIVIAL candidates
        #      before pool ranking. A candidate is trivial if:
        #        (a) bg_cov is invalid (fallback-proxy path) AND trade
        #            coverage is >=98% — i.e. the condition is trivially
        #            true on the trade set and we have no way to assess
        #            how restrictive it would be in the broader population
        #            (e.g. "upper_shadow > 0", "aroon_up < 100"), OR
        #        (b) bg_cov is valid but both trade_coverage and bg_cov
        #            are >=90% AND their absolute difference is <2pp —
        #            the condition covers almost everything, everywhere,
        #            and so carries essentially no signal.
        #      These candidates used to dominate the pool ranking because
        #      their apparent tightness was artificially low. Removing
        #      them here lets genuinely restrictive features come to the
        #      top.
        # CHANGED: April 2026 — Phase A.39b.1 — Bug 3 fix
        _TRIVIAL_TRADE_COV_FLOOR = 0.98      # "trivially true" threshold
        _TRIVIAL_JOINT_FLOOR     = 0.90      # both coverages >= this
        _TRIVIAL_DELTA_CEIL      = 0.02      # AND their delta < this
        non_trivial = []
        _trivial_dropped = 0
        for c in all_cands:
            is_trivial = False
            if not c.get('background_coverage_valid', False):
                if c['trade_coverage'] >= _TRIVIAL_TRADE_COV_FLOOR:
                    is_trivial = True
            else:
                bg = c.get('background_coverage') or 0.0
                tc = c.get('trade_coverage') or 0.0
                if bg >= _TRIVIAL_JOINT_FLOOR and tc >= _TRIVIAL_JOINT_FLOOR:
                    if abs(tc - bg) < _TRIVIAL_DELTA_CEIL:
                        is_trivial = True
            if is_trivial:
                _trivial_dropped += 1
            else:
                non_trivial.append(c)
        _l(f"  [A.39b.1] filtered {_trivial_dropped} trivial candidates "
           f"({len(non_trivial)} remain after triviality filter)")

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
        pool = non_trivial[:p['pool_size']]
        _l(f"  [A.39b] candidate pool: top {len(pool)} by tightness")
        for i, c in enumerate(pool[:5]):
            bg_str = (
                f"bg={c['background_coverage']:.2%}"
                if c['background_coverage'] is not None else "bg=n/a"
            )
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

        # ── Step 6-7: sort by tightness product; tie-break prefers shorter ──
        valid_conjunctions.sort(key=lambda v: v['tightness_product'])
        best_score = valid_conjunctions[0]['tightness_product']
        tie_limit = best_score * (1.0 + p['tie_break_within_pct'])

        near_best = [v for v in valid_conjunctions if v['tightness_product'] <= tie_limit]
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
