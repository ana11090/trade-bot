"""
Regime Filter Discovery — Phase A.37.

Given the aligned-trade DataFrame, the RandomForest model result, and the
regime breakdown emitted by analyze.analyze_market_regimes(), auto-discover
which market-condition features separate winning trades from losing trades,
and the per-feature threshold + direction at which the gate is worth keeping.

WHY: Phase A.36 shipped the UI scaffolding only (master checkbox + mode
     radios) — the Automatic card is a placeholder. A.37 provides the
     discovery logic the UI promised. Called from analyze.run_analysis()
     only when the user has opted in via regime_filter_enabled=true AND
     regime_filter_mode=automatic. Result is persisted to
     p1_config.json['regime_filter_discovered'] so the panel can show it,
     and is also embedded in analysis_report.json for audit.

     No filter is APPLIED by A.37 — that is A.38's job. A.37 only
     discovers and records.

Architecture (locked in A.36 comment block):
  - Hybrid candidate pool: features surfaced by analyze_market_regimes()
    PLUS the top RF features from model_result['top_20'].
  - Correlation dedup: keep the higher-importance of any two features
    with |corr| > _DEDUP_CORR_THRESHOLD so the subset search does not
    waste capacity on near-duplicates.
  - Per-feature grid search on a 20-point grid between the 20th and 80th
    percentile. Both comparison directions (>, <) tested. The threshold
    that maximises expectancy lift while surviving the overfitting floors
    wins.
  - Subset selection: brute-force 2^N-1 subsets (capped at _MAX_SUBSET_N
    survivors) so we do not search an exponentially large space.
  - Overfitting controls: ALL gates (30% survival floor, >=100 trades,
    WR lift >=3pp, expectancy lift >=10%, train/test stability <=10pp on
    a chronological 80/20 split).

CHANGED: April 2026 — Phase A.37 — discovery implementation
"""

import numpy as np
import pandas as pd
import itertools

try:
    from shared.logging_setup import get_logger
    _log = get_logger(__name__)
except Exception:
    import logging
    _log = logging.getLogger(__name__)


# ─── Tunables — intentionally NOT config-driven in A.37 ──────────────────────
# WHY: Users configure via the checkbox + mode radio only. Internal
#      discovery knobs are exposed for developer tuning, not end users;
#      adding more config keys now would widen the Run Scenarios panel
#      without giving the user something actionable to change.
# CHANGED: April 2026 — Phase A.37
_MIN_WR_DELTA_FOR_CANDIDATE = 0.05   # candidate must show >=5pp WR gap in regime stats
_RF_TOP_N                    = 15    # number of RF features to admit into the candidate pool
_DEDUP_CORR_THRESHOLD        = 0.70  # drop the lower-importance feature if |corr| exceeds this
_GRID_SIZE                   = 20    # threshold points between p20 and p80

# WHY (Phase A.37.2): A.37 had four hardcoded overfitting-control floors
#      that favored trade-count preservation over WR maximization. Some
#      users want stricter filtering (higher WR, fewer surviving trades),
#      some want looser (more candidates pass, more filters in the
#      discovered subset). Three presets cover the common shapes. The
#      stability floor (max train/test WR delta) is the same across all
#      three — loosening it would defeat the methodological purpose of
#      the train/test split.
# CHANGED: April 2026 — Phase A.37.2 — strictness presets
_STRICTNESS_PRESETS = {
    # survival_fraction, survival_floor, wr_lift, expectancy_lift (ratio)
    'conservative': {
        'survival_fraction': 0.30,
        'survival_floor':    100,
        'wr_lift':           0.03,   # +3 percentage points
        'expectancy_lift':   1.10,   # 10% better expectancy (ratio)
    },
    'balanced': {
        'survival_fraction': 0.20,
        'survival_floor':    80,
        'wr_lift':           0.02,   # +2 pp
        'expectancy_lift':   1.05,
    },
    'strict': {
        'survival_fraction': 0.10,
        'survival_floor':    50,
        'wr_lift':           0.05,   # +5 pp — strict mode demands clean separation
        'expectancy_lift':   1.20,
    },
}
_DEFAULT_STRICTNESS = 'conservative'

# Backwards-compat constants that mirror the default (conservative) floors.
# The discovery function now reads from _STRICTNESS_PRESETS at call time;
# these remain for any external code that imports them directly.
_MIN_SURVIVAL_PCT            = _STRICTNESS_PRESETS[_DEFAULT_STRICTNESS]['survival_fraction']
_MIN_SURVIVAL_COUNT          = _STRICTNESS_PRESETS[_DEFAULT_STRICTNESS]['survival_floor']
_MIN_WR_LIFT                 = _STRICTNESS_PRESETS[_DEFAULT_STRICTNESS]['wr_lift']
# Relative-gain form (e.g. 0.10 == 1.10x ratio). Kept for backwards compat.
_MIN_EXPECTANCY_LIFT_REL     = _STRICTNESS_PRESETS[_DEFAULT_STRICTNESS]['expectancy_lift'] - 1.0

_MAX_TRAIN_TEST_WR_GAP       = 0.10  # <=10pp WR delta (NOT loosened by strictness)
_TRAIN_FRAC                  = 0.80  # chronological split fraction

_MAX_SUBSET_N                = 8     # brute-force cap — 2^8 = 256 subsets max


def _emit(progress_log, msg):
    """Send a progress line to both the shared log and the optional UI hook."""
    _log.info(msg)
    if progress_log is not None:
        try:
            progress_log(msg)
        except Exception:
            pass


# ─── Candidate feature extraction ────────────────────────────────────────────

def _features_from_regimes(regimes):
    """Extract the column names that analyze_market_regimes() used."""
    names = []
    for key in ('trend', 'volatility', 'direction'):
        block = regimes.get(key) if isinstance(regimes, dict) else None
        if isinstance(block, dict):
            ind = block.get('indicator_used')
            if ind:
                names.append(ind)
    return names


def _features_from_rf(model_result, df):
    """Top-N numeric RF features present in df."""
    top_20 = (model_result or {}).get('top_20') or []
    out = []
    for item in top_20[:_RF_TOP_N]:
        # top_20 entries are (name, importance) tuples
        if isinstance(item, (list, tuple)) and len(item) >= 1:
            name = item[0]
        else:
            name = item
        if isinstance(name, str) and name in df.columns:
            if pd.api.types.is_numeric_dtype(df[name]):
                out.append(name)
    return out


def _dedupe_by_correlation(candidates, df, importances):
    """Drop near-duplicates. Keep the one with higher RF importance."""
    if len(candidates) <= 1:
        return list(candidates)
    keep = []
    for feat in sorted(candidates, key=lambda f: -importances.get(f, 0.0)):
        skip = False
        for already in keep:
            try:
                a = df[feat].astype(float)
                b = df[already].astype(float)
                mask = a.notna() & b.notna()
                if mask.sum() < 30:
                    continue
                c = float(np.corrcoef(a[mask], b[mask])[0, 1])
                if abs(c) > _DEDUP_CORR_THRESHOLD:
                    skip = True
                    break
            except Exception:
                continue
        if not skip:
            keep.append(feat)
    return keep


# ─── Baseline + gated metrics ────────────────────────────────────────────────

def _winner_series(df):
    if 'is_winner' in df.columns:
        return df['is_winner'].astype(bool)
    if 'pips' in df.columns:
        return (df['pips'] > 0)
    return pd.Series([False] * len(df), index=df.index)


def _expectancy(df_slice):
    if 'pips' in df_slice.columns and len(df_slice) > 0:
        return float(df_slice['pips'].mean())
    return 0.0


def _baseline(df):
    return {
        'count':       int(len(df)),
        'win_rate':    float(_winner_series(df).mean()) if len(df) else 0.0,
        'expectancy': _expectancy(df),
    }


def _gated_metrics(df, mask):
    sub = df[mask]
    return {
        'count':       int(len(sub)),
        'win_rate':    float(_winner_series(sub).mean()) if len(sub) else 0.0,
        'expectancy':  _expectancy(sub),
        'survival':    float(len(sub) / len(df)) if len(df) else 0.0,
    }


def _train_test_wr(df, mask):
    """Chronological first-80% vs last-20% WR on the gated rows."""
    if 'open_time' not in df.columns:
        return None, None
    try:
        order = pd.to_datetime(df['open_time']).argsort().values
    except Exception:
        return None, None
    sub_idx = np.where(mask.values if hasattr(mask, 'values') else mask)[0]
    if len(sub_idx) < 20:
        return None, None
    # Sort sub-index by time
    df_reset = df.reset_index(drop=True)
    try:
        times = pd.to_datetime(df_reset['open_time']).values
    except Exception:
        return None, None
    sub_sorted = sorted(sub_idx, key=lambda i: times[i])
    cut = int(len(sub_sorted) * _TRAIN_FRAC)
    if cut < 5 or (len(sub_sorted) - cut) < 5:
        return None, None
    wins = _winner_series(df_reset).values
    tr_wr = float(np.mean([wins[i] for i in sub_sorted[:cut]]))
    te_wr = float(np.mean([wins[i] for i in sub_sorted[cut:]]))
    return tr_wr, te_wr


# ─── Per-feature threshold grid search ───────────────────────────────────────

def _best_threshold(df, feat, baseline, strictness_floors):
    """Search a grid of thresholds for the best (direction, value).

    Returns dict or None if no threshold survives the overfitting floors.

    WHY (Phase A.37.2): Hard floors now come from the strictness preset
         passed in via `strictness_floors`. Previously hardcoded to
         module constants (_MIN_SURVIVAL_PCT etc). Those constants are
         still exported for backwards compat but no longer read here.
    CHANGED: April 2026 — Phase A.37.2
    """
    col = df[feat].astype(float)
    if col.notna().sum() < 50:
        return None
    p20, p80 = np.nanpercentile(col, [20, 80])
    if not np.isfinite(p20) or not np.isfinite(p80) or p20 >= p80:
        return None
    grid = np.linspace(p20, p80, _GRID_SIZE)

    best = None
    for direction in ('>', '<'):
        for thr in grid:
            mask = (col > thr) if direction == '>' else (col < thr)
            mask = mask & col.notna()
            m = _gated_metrics(df, mask)

            # Hard floors — survival first (cheapest check)
            if m['survival'] < strictness_floors['survival_fraction']:
                continue
            if m['count'] < strictness_floors['survival_floor']:
                continue

            wr_lift = m['win_rate'] - baseline['win_rate']
            if wr_lift < strictness_floors['wr_lift']:
                continue

            base_exp = baseline['expectancy']
            if base_exp == 0:
                exp_lift_rel = 0.0
            else:
                exp_lift_rel = (m['expectancy'] - base_exp) / abs(base_exp)
            # Convert preset's ratio form (e.g. 1.10) to the relative-gain
            # form the existing code uses (0.10). Keeps the negative-baseline
            # edge case working: when base_exp <= 0, exp_lift_rel is 0 and
            # any preset's ratio >1.0 rejects the filter — which is the
            # conservative right answer.
            _exp_floor_rel = strictness_floors['expectancy_lift'] - 1.0
            if exp_lift_rel < _exp_floor_rel:
                continue

            tr_wr, te_wr = _train_test_wr(df, mask)
            if tr_wr is not None and te_wr is not None:
                if abs(tr_wr - te_wr) > _MAX_TRAIN_TEST_WR_GAP:
                    continue

            score = exp_lift_rel * float(np.log10(m['count'] + 1))
            if best is None or score > best['score']:
                best = {
                    'feature':     feat,
                    'direction':   direction,
                    'threshold':   round(float(thr), 6),
                    'survival':    round(m['survival'], 4),
                    'count':       m['count'],
                    'win_rate':    round(m['win_rate'], 4),
                    'expectancy':  round(m['expectancy'], 3),
                    'wr_lift':     round(wr_lift, 4),
                    'exp_lift_rel':round(exp_lift_rel, 4),
                    'train_wr':    None if tr_wr is None else round(tr_wr, 4),
                    'test_wr':     None if te_wr is None else round(te_wr, 4),
                    'score':       round(score, 4),
                }
    return best


# ─── Subset search ───────────────────────────────────────────────────────────

def _mask_for_filter(df, f):
    col = df[f['feature']].astype(float)
    if f['direction'] == '>':
        return (col > f['threshold']) & col.notna()
    return (col < f['threshold']) & col.notna()


def _score_subset(df, subset, baseline, strictness_floors):
    """Score a subset of filter conditions.

    WHY (Phase A.37.2): strictness_floors now drives hard floors, same as
         the per-feature path.
    CHANGED: April 2026 — Phase A.37.2
    """
    if not subset:
        return None
    mask = _mask_for_filter(df, subset[0])
    for f in subset[1:]:
        mask = mask & _mask_for_filter(df, f)
    m = _gated_metrics(df, mask)

    if m['survival'] < strictness_floors['survival_fraction']:
        return None
    if m['count'] < strictness_floors['survival_floor']:
        return None

    wr_lift = m['win_rate'] - baseline['win_rate']
    if wr_lift < strictness_floors['wr_lift']:
        return None

    base_exp = baseline['expectancy']
    exp_lift_rel = 0.0 if base_exp == 0 else (m['expectancy'] - base_exp) / abs(base_exp)
    _exp_floor_rel = strictness_floors['expectancy_lift'] - 1.0
    if exp_lift_rel < _exp_floor_rel:
        return None

    tr_wr, te_wr = _train_test_wr(df, mask)
    if tr_wr is not None and te_wr is not None:
        if abs(tr_wr - te_wr) > _MAX_TRAIN_TEST_WR_GAP:
            return None

    score = exp_lift_rel * float(np.log10(m['count'] + 1))
    return {
        'count':        m['count'],
        'survival':     round(m['survival'], 4),
        'win_rate':     round(m['win_rate'], 4),
        'expectancy':   round(m['expectancy'], 3),
        'wr_lift':      round(wr_lift, 4),
        'exp_lift_rel': round(exp_lift_rel, 4),
        'train_wr':     None if tr_wr is None else round(tr_wr, 4),
        'test_wr':      None if te_wr is None else round(te_wr, 4),
        'score':        round(score, 4),
    }


# ─── Main entry ──────────────────────────────────────────────────────────────

def discover_regime_filter(df, model_result, regimes, progress_log=None,
                           strictness=None):
    """Return a compact dict describing the best regime filter for this dataset.

    Args:
        df, model_result, regimes: inputs from analyze.run_analysis().
        progress_log:              optional callable for UI progress updates.
        strictness:                'conservative' | 'balanced' | 'strict'.
                                   Defaults to _DEFAULT_STRICTNESS when None
                                   or unrecognized. Added in A.37.2 — controls
                                   the four overfitting floors.

    Shape:
      {
        'status':             'ok' | 'no_survivors' | 'no_candidates' | 'error',
        'message':            human-readable summary,
        'strictness':         resolved preset name,
        'strictness_floors':  {survival_fraction, survival_floor, wr_lift, expectancy_lift},
        'baseline':           {count, win_rate, expectancy},
        'candidates_scanned': [feat, ...],
        'per_feature':        [ {feature, direction, threshold, ...}, ... ],
        'best_subset':        [ {feature, direction, threshold}, ... ],
        'best_subset_metrics': { count, survival, win_rate, ... },
      }
    """
    # WHY (Phase A.37.2): Resolve strictness at call time so every
    #      per-feature optimization and subset score uses the same
    #      floors. Unknown strictness -> fall back to the default so a
    #      typo never silently disables the filter.
    # CHANGED: April 2026 — Phase A.37.2
    _resolved_strictness = (strictness or _DEFAULT_STRICTNESS).lower()
    if _resolved_strictness not in _STRICTNESS_PRESETS:
        _log.warning(
            f"[A.37.2] unknown strictness {strictness!r} — falling back "
            f"to {_DEFAULT_STRICTNESS!r}"
        )
        _resolved_strictness = _DEFAULT_STRICTNESS
    strictness_floors = _STRICTNESS_PRESETS[_resolved_strictness]

    try:
        baseline = _baseline(df)
        _emit(progress_log,
              f"  [A.37] strictness preset: {_resolved_strictness} "
              f"(survival>={strictness_floors['survival_fraction']*100:.0f}%, "
              f"WR_lift>={strictness_floors['wr_lift']*100:.0f}pp, "
              f"expectancy_lift>={strictness_floors['expectancy_lift']:.2f}x)")
        _emit(progress_log,
              f"  Regime filter baseline: {baseline['count']} trades, "
              f"WR {baseline['win_rate']*100:.1f}%, "
              f"expectancy {baseline['expectancy']:+.2f} pips")

        # 1. Candidate pool
        regime_feats = _features_from_regimes(regimes)
        rf_feats     = _features_from_rf(model_result, df)
        pool = []
        seen = set()
        for f in list(regime_feats) + list(rf_feats):
            if f not in seen and f in df.columns:
                if pd.api.types.is_numeric_dtype(df[f]):
                    pool.append(f)
                    seen.add(f)
        if not pool:
            _emit(progress_log, "  No numeric candidate features found — skipping.")
            return {
                'status':            'no_candidates',
                'message':           'No numeric candidate features available.',
                'strictness':        _resolved_strictness,
                'strictness_floors': dict(strictness_floors),
                'baseline':          baseline,
            }

        # 2. Dedup by correlation — use RF importance as tiebreaker
        importances = {}
        for item in (model_result or {}).get('top_20') or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                importances[item[0]] = float(item[1])
        deduped = _dedupe_by_correlation(pool, df, importances)
        _emit(progress_log,
              f"  Candidates: {len(pool)} raw -> {len(deduped)} after correlation dedup")

        # 3. Per-feature threshold grid search
        # Phase A.37.2: pass strictness_floors so per-feature filters
        # honor the user's chosen preset.
        per_feature = []
        for feat in deduped:
            best = _best_threshold(df, feat, baseline, strictness_floors)
            if best is not None:
                per_feature.append(best)

        if not per_feature:
            _emit(progress_log,
                  "  No single feature survived the overfitting floors — "
                  "filter will not be recommended.")
            return {
                'status':  'no_survivors',
                'message': (f'No feature threshold passed all overfitting '
                            f'controls at strictness={_resolved_strictness!r} '
                            f"(survival>={strictness_floors['survival_fraction']*100:.0f}% "
                            f"AND >={strictness_floors['survival_floor']} trades, "
                            f"WR lift>={strictness_floors['wr_lift']*100:.0f}pp, "
                            f"expectancy lift>={strictness_floors['expectancy_lift']:.2f}x, "
                            f"train/test WR gap <=10pp). Try a looser preset "
                            f"(Conservative is loosest)."),
                'strictness':         _resolved_strictness,
                'strictness_floors':  dict(strictness_floors),
                'baseline':           baseline,
                'candidates_scanned': deduped,
                'per_feature':        [],
            }

        # Sort survivors by score descending, cap to _MAX_SUBSET_N
        per_feature.sort(key=lambda d: -d['score'])
        survivors = per_feature[:_MAX_SUBSET_N]
        _emit(progress_log,
              f"  {len(per_feature)} features survived; "
              f"searching subsets over top {len(survivors)}.")

        # 4. Brute-force subset search
        best_subset = None
        best_subset_metrics = None
        for r in range(1, len(survivors) + 1):
            for combo in itertools.combinations(survivors, r):
                # Phase A.37.2: pass strictness_floors so subset survival
                # check uses the same floor as per-feature.
                m = _score_subset(df, combo, baseline, strictness_floors)
                if m is None:
                    continue
                if best_subset_metrics is None or m['score'] > best_subset_metrics['score']:
                    best_subset = list(combo)
                    best_subset_metrics = m

        if best_subset is None:
            # Single-feature fallback: take the top per-feature survivor
            top = survivors[0]
            best_subset = [top]
            best_subset_metrics = {
                'count':        top['count'],
                'survival':     top['survival'],
                'win_rate':     top['win_rate'],
                'expectancy':   top['expectancy'],
                'wr_lift':      top['wr_lift'],
                'exp_lift_rel': top['exp_lift_rel'],
                'train_wr':     top['train_wr'],
                'test_wr':      top['test_wr'],
                'score':        top['score'],
            }

        _emit(progress_log,
              f"  Best subset: {len(best_subset)} filter(s), "
              f"survival {best_subset_metrics['survival']*100:.1f}% "
              f"({best_subset_metrics['count']} trades), "
              f"WR {best_subset_metrics['win_rate']*100:.1f}% "
              f"(+{best_subset_metrics['wr_lift']*100:.1f}pp), "
              f"expectancy {best_subset_metrics['expectancy']:+.2f} pips")

        subset_compact = [
            {'feature':   f['feature'],
             'direction': f['direction'],
             'threshold': f['threshold']}
            for f in best_subset
        ]

        return {
            'status':             'ok',
            'message':            (f"{len(best_subset)} filter(s) discovered "
                                   f"(strictness={_resolved_strictness}); "
                                   f"survival {best_subset_metrics['survival']*100:.1f}%."),
            'strictness':         _resolved_strictness,
            'strictness_floors':  dict(strictness_floors),
            'baseline':           baseline,
            'candidates_scanned': deduped,
            'per_feature':        per_feature,
            'best_subset':        subset_compact,
            'best_subset_metrics': best_subset_metrics,
        }

    except Exception as e:
        _log.exception("[A.37] discover_regime_filter crashed")
        return {
            'status':  'error',
            'message': f'Discovery crashed: {e}',
        }
