"""
ROBOT ANALYZER — Complete reverse engineering analysis.

Reads the feature matrix and produces:
  1. Robot Profile (auto-detected characteristics)
  2. Feature Importance (what indicators matter most)
  3. Trading Rules (IF/THEN rules with confidence)
  4. Trade Clusters (auto-detected trade types)
  5. Market Regime Analysis (performance by market conditions)
  6. Time Period Evolution (how the robot changed over time)
  7. Anomaly Detection (outlier trades)
  8. Improvement Suggestions (filters that would improve performance)

Usage: python analyze.py
Output: outputs/analysis_report.json + outputs/analysis_report.txt
"""

import sys
import os
import time
import json
import pandas as pd
import numpy as np
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')

# CHANGED: April 2026 — UI-safe logging (Phase 19d)
from shared.logging_setup import get_logger
log = get_logger(__name__)


# ── Section 1: Robot Profile ──────────────────────────────────────────────────

def build_robot_profile(df):
    """
    Auto-detect everything about the robot from trade data.
    No assumptions — works with any robot.
    """
    profile = {}

    # Direction
    if 'action' in df.columns:
        counts = df['action'].str.capitalize().value_counts()
        total = counts.sum()
        split = {k: round(v / total, 3) for k, v in counts.items()}
        buy_pct = split.get('Buy', 0)
        sell_pct = split.get('Sell', 0)
        # WHY (Phase 59 Fix 2): Old threshold was 90% — a strategy at
        #      89% buy / 11% sell was labelled 'both' and the EA
        #      generator emitted bidirectional code. 75% is a much
        #      more sensible boundary: clearly directional above it,
        #      genuinely mixed below it. The 11% minority is treated
        #      as noise, not a deliberate second direction.
        # CHANGED: April 2026 — Phase 59 Fix 2 — direction threshold 90%→75%
        #          (audit Part D HIGH #40)
        _DIR_THRESHOLD = 0.75
        if buy_pct >= _DIR_THRESHOLD:
            direction = 'buy_only'
        elif sell_pct >= _DIR_THRESHOLD:
            direction = 'sell_only'
        else:
            direction = 'both'
        profile['direction'] = direction
        profile['direction_split'] = split
    else:
        profile['direction'] = 'unknown'
        profile['direction_split'] = {}

    profile['trade_count'] = len(df)

    # Date range
    if 'open_time' in df.columns:
        open_dt = pd.to_datetime(df['open_time'])
        profile['date_range'] = (str(open_dt.min().date()), str(open_dt.max().date()))
    else:
        profile['date_range'] = (None, None)

    # Duration
    # WHY: Old code read trade_duration_minutes directly. But step2
    #      explicitly does NOT write that column (it's a leak source —
    #      winning trades naturally run longer than losers). Result:
    #      every robot profile silently reported 'unknown' duration.
    #      Fix: compute duration from open_time and close_time at
    #      point of use, which are both present in step2's output.
    # CHANGED: April 2026 — derive duration on the fly (audit HIGH)
    if 'open_time' in df.columns and 'close_time' in df.columns:
        try:
            ot = pd.to_datetime(df['open_time'])
            ct = pd.to_datetime(df['close_time'])
            duration_minutes = (ct - ot).dt.total_seconds() / 60.0
            median_dur = float(duration_minutes.median())
            profile['avg_duration_minutes'] = round(median_dur, 1)
            if median_dur < 15:
                profile['duration_category'] = 'scalper'
            elif median_dur < 240:
                profile['duration_category'] = 'day_trader'
            else:
                profile['duration_category'] = 'swing'
        except Exception:
            profile['avg_duration_minutes'] = 0
            profile['duration_category'] = 'unknown'
    elif 'trade_duration_minutes' in df.columns:
        # Legacy path — some older feature matrices may have this column
        median_dur = df['trade_duration_minutes'].median()
        profile['avg_duration_minutes'] = round(float(median_dur), 1)
        if median_dur < 15:
            profile['duration_category'] = 'scalper'
        elif median_dur < 240:
            profile['duration_category'] = 'day_trader'
        else:
            profile['duration_category'] = 'swing'
    else:
        profile['avg_duration_minutes'] = 0
        profile['duration_category'] = 'unknown'

    # Win rate and pip stats
    # WHY: Old code read is_winner directly — but step2 explicitly does
    #      NOT write that column. Result: win_rate was always 0.0 for
    #      every robot profile. Fix: derive from pips at point of use.
    # CHANGED: April 2026 — derive is_winner from pips (audit HIGH)
    if 'is_winner' in df.columns:
        profile['win_rate'] = round(float(df['is_winner'].mean()), 4)
    elif 'pips' in df.columns:
        profile['win_rate'] = round(float((df['pips'] > 0).mean()), 4)
    else:
        profile['win_rate'] = 0.0

    if 'pips' in df.columns:
        winners = df[df['pips'] > 0]['pips']
        losers  = df[df['pips'] < 0]['pips']
        avg_win  = float(winners.mean()) if len(winners) > 0 else 0.0
        avg_loss = float(losers.mean())  if len(losers)  > 0 else 0.0
        profile['avg_win_pips']  = round(avg_win, 1)
        profile['avg_loss_pips'] = round(avg_loss, 1)
        profile['reward_risk_ratio'] = (
            round(avg_win / abs(avg_loss), 2) if avg_loss != 0 else 0.0
        )

        # WHY (Phase 59 Fix 3): Old code checked only the top mode.
        #      A two-level SL system (40% at 100 pips, 40% at 150 pips)
        #      has mode=100 and near_mode=0.4 → "not fixed". Both levels
        #      are clearly fixed — the bot uses two SL values. Check the
        #      top-2 modes: if they collectively cover >70% of losses
        #      and each individually covers >20%, call it "two-level fixed".
        # CHANGED: April 2026 — Phase 59 Fix 3 — two-level SL detection
        #          (audit Part D HIGH #41)
        if len(losers) > 0:
            _modes = losers.mode()
            mode_loss = _modes.iloc[0] if len(_modes) > 0 else None
            if mode_loss is not None:
                near_mode = ((losers - mode_loss).abs() / abs(mode_loss) < 0.05).mean()
                if near_mode > 0.60:
                    # Single-level fixed SL
                    profile['sl_pattern'] = {
                        'fixed': True,
                        'levels': 1,
                        'fixed_value_pips': round(abs(float(mode_loss)), 1),
                        'confidence': round(float(near_mode), 2),
                    }
                elif len(_modes) >= 2:
                    # Check two-level fixed SL
                    mode2 = _modes.iloc[1]
                    near_mode2 = ((losers - mode2).abs() / abs(mode2) < 0.05).mean()
                    combined   = near_mode + near_mode2
                    if combined > 0.70 and near_mode > 0.20 and near_mode2 > 0.20:
                        profile['sl_pattern'] = {
                            'fixed': True,
                            'levels': 2,
                            'fixed_value_pips':  round(abs(float(mode_loss)), 1),
                            'fixed_value2_pips': round(abs(float(mode2)), 1),
                            'confidence': round(float(combined), 2),
                        }
                    else:
                        profile['sl_pattern'] = {
                            'fixed': False,
                            'range_pips': (round(abs(float(losers.max())), 1),
                                           round(abs(float(losers.min())), 1)),
                            'median_pips': round(abs(float(losers.median())), 1),
                        }
                else:
                    profile['sl_pattern'] = {
                        'fixed': False,
                        'range_pips': (round(abs(float(losers.max())), 1),
                                       round(abs(float(losers.min())), 1)),
                        'median_pips': round(abs(float(losers.median())), 1),
                    }
            else:
                profile['sl_pattern'] = {'fixed': False}
        else:
            profile['sl_pattern'] = {'fixed': False}

        # WHY (Phase 59 Fix 4): If the user's CSV stores pips as deci-pips
        #      (1 pip = 10 units), a 100-pip SL appears as 1000. The
        #      detected fixed_value_pips would then be 1000, and any
        #      generated EA would use a 10× too large SL silently.
        #      Warn when the detected SL or median exceeds 500 pips
        #      (implausible for most instruments; XAUUSD SL rarely > 300).
        # CHANGED: April 2026 — Phase 59 Fix 4 — deci-pip warning
        #          (audit Part D HIGH #42)
        _sl = profile.get('sl_pattern', {})
        _sl_val = _sl.get('fixed_value_pips') or _sl.get('median_pips') or 0
        if _sl_val > 500:
            log.warning(
                f"[ANALYZE] SL detected as {_sl_val:.0f} pips — unusually large. "
                f"If your CSV stores pips as deci-pips (1 pip = 10 units), divide "
                f"by 10 before running, or set pip_size correctly in config."
            )
            profile['sl_pattern']['deci_pip_warning'] = True

        # TP detection
        if len(winners) > 0:
            mode_win = winners.mode().iloc[0] if len(winners.mode()) > 0 else None
            if mode_win is not None:
                near_mode_tp = ((winners - mode_win).abs() / abs(mode_win) < 0.05).mean()
                cv = float(winners.std() / winners.mean()) if winners.mean() != 0 else 999
                if near_mode_tp > 0.60:
                    profile['tp_pattern'] = {
                        'fixed': True,
                        'fixed_value_pips': round(float(mode_win), 1),
                        'confidence': round(float(near_mode_tp), 2),
                    }
                elif cv > 0.5:
                    profile['tp_pattern'] = {
                        'fixed': False,
                        'type': 'trailing_or_indicator',
                        'cv': round(cv, 2),
                    }
                else:
                    profile['tp_pattern'] = {
                        'fixed': False,
                        'range_pips': (round(float(winners.min()), 1),
                                       round(float(winners.max()), 1)),
                    }
            else:
                profile['tp_pattern'] = {'fixed': False}
        else:
            profile['tp_pattern'] = {'fixed': False}
    else:
        profile['avg_win_pips']      = 0.0
        profile['avg_loss_pips']     = 0.0
        profile['reward_risk_ratio'] = 0.0
        profile['sl_pattern']        = {'fixed': False}
        profile['tp_pattern']        = {'fixed': False}

    # Session / hour detection
    if 'hour_of_day' in df.columns:
        hours = df['hour_of_day'].value_counts()
        total_t = len(df)
        # WHY (Phase 51 Fix 2): Old 2% threshold didn't scale with
        #      trade count. For 100 trades, 2% = 2 trades (sensible);
        #      for 10 trades, 2% = 0.2 trades (any hour with a trade
        #      qualifies). Floor at 2 actual trades minimum so small
        #      strategies aren't credited with "active" in every
        #      hour they happened to fire once.
        # WHY (Phase 61 Fix 4): Old threshold was a fixed 2% of total trades.
        #      For 20 trades, 2% = 0.4, so any hour with ≥1 trade qualified.
        #      For 10000 trades, 2% = 200 trades — extremely restrictive.
        #      Use a combination: at least 1% of trades OR at least 3 trades,
        #      whichever is larger, so the threshold scales sensibly.
        # CHANGED: April 2026 — Phase 61 Fix 4 — scaling active_hours threshold
        #          (audit Part D MEDIUM #45)
        _min_pct   = max(0.01, 3 / max(total_t, 1))   # 1% or 3 trades, whichever larger
        active_hours = sorted(hours[hours / total_t >= _min_pct].index.tolist())
        peak_hours   = hours.head(3).index.tolist()
        profile['active_hours'] = active_hours
        profile['peak_hours']   = peak_hours

        sessions = []
        # WHY (Phase 51 Fix 3): Session ranges below assume hour_of_day
        #      is in UTC. step1 may produce broker-time hours (EET etc.)
        #      depending on the trade source CSV. If broker-time, the
        #      session labels here are off by the broker-UTC offset.
        #      Real fix needs upstream timezone tagging in step1 →
        #      step2 → analyze.py. Until then, the labels are an
        #      approximation. Marker added so future readers don't
        #      "simplify" the assumption away.
        # CHANGED: April 2026 — Phase 51 Fix 3 — timezone assumption doc
        #          (audit Part D MED #46)
        if any(h in active_hours for h in range(0, 8)):
            sessions.append('Asian')
        if any(h in active_hours for h in range(7, 16)):
            sessions.append('London')
        if any(h in active_hours for h in range(12, 21)):
            sessions.append('New York')
        profile['sessions'] = sessions
    else:
        profile['active_hours'] = []
        profile['peak_hours']   = []
        profile['sessions']     = []

    # Day-of-week activity
    if 'day_of_week' in df.columns:
        day_names = {0: 'Monday', 1: 'Tuesday', 2: 'Wednesday',
                     3: 'Thursday', 4: 'Friday', 5: 'Saturday', 6: 'Sunday'}
        active_days = [day_names[d] for d in
                       df['day_of_week'].value_counts().head(5).index.tolist()
                       if d in day_names]
        profile['active_days'] = active_days
    else:
        profile['active_days'] = []

    # Frequency and trend
    if 'open_time' in df.columns:
        open_dt = pd.to_datetime(df['open_time'])
        ym = open_dt.dt.to_period('M')
        monthly = ym.value_counts().sort_index()
        profile['trades_per_month_avg'] = round(float(monthly.mean()), 1)

        n = len(monthly)
        if n >= 4:
            first_half  = monthly.iloc[:n // 2].mean()
            second_half = monthly.iloc[n // 2:].mean()
            if second_half > first_half * 1.5:
                profile['frequency_trend'] = 'increasing'
            elif second_half < first_half * 0.67:
                profile['frequency_trend'] = 'decreasing'
            else:
                profile['frequency_trend'] = 'stable'
        else:
            profile['frequency_trend'] = 'stable'
    else:
        profile['trades_per_month_avg'] = 0
        profile['frequency_trend']      = 'unknown'

    # Yearly stats
    if 'open_time' in df.columns and 'pips' in df.columns:
        open_dt = pd.to_datetime(df['open_time'])
        df_tmp  = df.copy()
        df_tmp['_year']  = open_dt.dt.year
        df_tmp['_month'] = open_dt.dt.month
        yearly = []
        for year, grp in df_tmp.groupby('_year'):
            n_months = grp['_month'].nunique()
            yearly.append({
                'year':            int(year),
                'count':           len(grp),
                # WHY: Same derivation as the top-level profile —
                #      is_winner doesn't exist in step2 output, so
                #      derive from pips.
                # CHANGED: April 2026 — derive from pips (audit HIGH)
                'win_rate':        round(float(grp['is_winner'].mean()), 3) if 'is_winner' in grp else (
                    round(float((grp['pips'] > 0).mean()), 3) if 'pips' in grp else 0
                ),
                'avg_pips':        round(float(grp['pips'].mean()), 1),
                'trades_per_month': round(len(grp) / max(n_months, 1), 1),
            })
        profile['yearly_stats'] = yearly
    else:
        profile['yearly_stats'] = []

    return profile


# ── Section 2: Feature Importance ────────────────────────────────────────────

def compute_feature_importance(df):
    """
    Train a Random Forest to predict trade outcome and extract importances.

    WHY: This is the legacy standalone analysis. The 7-step pipeline doesn't
         call it, but it can be run directly. Leak-guard: is_winner and
         trade_duration_minutes must NEVER appear as features (X), only
         is_winner is used as the target (y).
    CHANGED: April 2026 — explicit leak guard
    """
    from sklearn.ensemble import RandomForestClassifier

    # Hard exclude list — these are targets or target-correlated.
    # is_winner IS the target → must not appear in X.
    # trade_duration_minutes leaks because winners run longer than losers.
    # WHY (Phase 52 Fix 3b): Phase 52 Fix 3 renames pips/profit to
    #      _LEAK_pips/_LEAK_profit in step2 output. Add both names
    #      to LEAK_COLS for backward compat with existing feature
    #      matrices. The list comprehension also adds a prefix-based
    #      filter so any future _LEAK_* column is auto-excluded.
    # CHANGED: April 2026 — Phase 52 Fix 3b — accept _LEAK_ prefix
    #          (audit Part D MED #38)
    LEAK_COLS = {
        'trade_id', 'open_time', 'close_time', 'action', 'pips',
        'profit', 'lots', 'sl', 'tp', 'open_price', 'close_price',
        'is_winner', 'trade_direction', 'trade_duration_minutes',
        'outcome',
        '_LEAK_pips', '_LEAK_profit',
        'symbol', 'duration', 'change_pct', 'hour_of_day',
        'day_of_week', 'day_of_month',
    }
    feature_cols = [
        c for c in df.columns
        if c not in LEAK_COLS
        and not c.startswith('_LEAK_')   # Phase 52 Fix 3c — auto-exclude any future leak col
        and 'candle_idx'  not in c
        and 'candle_time' not in c
        and df[c].dtype in ['float64', 'int64', 'float32', 'int32']
    ]

    if not feature_cols:
        log.info("[ANALYZE] ERROR: no usable features after excluding leak columns")
        return None

    # WHY (Phase A.1 hotfix): step2 deliberately does NOT write is_winner
    #      to the feature matrix (see step2_compute_indicators.py lines
    #      85-86). build_robot_profile derives it from pips > 0 at point
    #      of use, but compute_feature_importance was missed in that fix
    #      and bailed with return None → run_analysis line ~1110 crashed
    #      with TypeError: 'NoneType' object is not subscriptable.
    #      pips is in LEAK_COLS so deriving the label from it is safe —
    #      it is already excluded from feature_cols and cannot leak into X.
    # CHANGED: April 2026 — Phase A.1 — derive is_winner from pips
    if 'is_winner' not in df.columns:
        if 'pips' in df.columns:
            log.info("[ANALYZE] is_winner missing — deriving from pips > 0")
            df = df.copy()
            df['is_winner'] = (df['pips'] > 0).astype(int)
        else:
            log.info(
                "[ANALYZE] ERROR: neither is_winner nor pips present — "
                "cannot train. Columns available: %s",
                list(df.columns)[:30],
            )
            return None

    log.info(f"[ANALYZE] Training on {len(feature_cols)} features "
          f"(excluded {len(LEAK_COLS)} leak/meta cols)")

    y = df['is_winner'].values
    X = df[feature_cols].copy()
    X = X.fillna(X.median())
    X = X.replace([np.inf, -np.inf], np.nan).fillna(0)

    # WHY (Phase 59 Fix 1): Old code hardcoded 0.75 train split and
    #      RF params (300 trees, depth 8, leaf 15) — ignoring every
    #      value the user set in the configuration panel. The config
    #      default for train_test_split is 0.80 (not 0.75), so the
    #      split was also wrong by default. Read all four values from
    #      config_loader; fall back to safe defaults if config fails.
    #      Note: the split remains CHRONOLOGICAL (not shuffled) — this
    #      is intentional for time-series data to avoid look-ahead.
    # CHANGED: April 2026 — Phase 59 Fix 1 — config-driven split + RF params
    #          (audit Part D HIGH #39)
    try:
        import config_loader as _cl39
        _cfg39        = _cl39.load()
        _split_frac   = float(_cfg39.get('train_test_split',  '0.80'))
        _n_estimators = int(  _cfg39.get('rf_trees',          '500'))
        _max_depth    = int(  _cfg39.get('max_tree_depth',    '6'))
        _min_leaf     = int(  _cfg39.get('min_samples_leaf',  '10'))
    except Exception:
        _split_frac, _n_estimators, _max_depth, _min_leaf = 0.80, 500, 6, 10

    split_idx = int(len(X) * _split_frac)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y[:split_idx],       y[split_idx:]
    log.info(f"[ANALYZE] Train/test split: {split_idx}/{len(X)-split_idx} "
             f"({_split_frac*100:.0f}%/{(1-_split_frac)*100:.0f}% chronological)")

    rf = RandomForestClassifier(
        n_estimators=_n_estimators,
        max_depth=_max_depth,
        min_samples_leaf=_min_leaf,
        random_state=42,
        n_jobs=-1,
    )
    rf.fit(X_train, y_train)

    train_acc = rf.score(X_train, y_train)
    test_acc  = rf.score(X_test,  y_test)

    importances = sorted(
        zip(feature_cols, rf.feature_importances_),
        key=lambda x: x[1], reverse=True,
    )

    return {
        'importances':    importances,
        'top_20':         importances[:20],
        'train_accuracy': round(train_acc, 4),
        'test_accuracy':  round(test_acc,  4),
        'model':          rf,
        'feature_cols':   feature_cols,
        'X':              X,
        'y':              y,
        # WHY (Phase 41 Fix 1): Expose the train slice so extract_rules
        #      can fit on it instead of the full X/y. Old code fit the
        #      decision tree on the full dataset, training rules on
        #      data that also served as their own test set. Inflated
        #      confidence and win-rate metrics. Same bug family as
        #      scratch_discovery Deep/Exhaustive (Round 2A #11/#12).
        # CHANGED: April 2026 — Phase 41 Fix 1 — expose train slice
        #          (audit Part D CRITICAL #1)
        'X_train':        X_train,
        'y_train':        y_train,
        'X_test':         X_test,
        'y_test':         y_test,
    }


# ── Section 3: Rule Extraction ────────────────────────────────────────────────

def extract_rules(df, model_result):
    """
    Extract human-readable IF/THEN rules from a shallow decision tree.
    """
    from sklearn.tree import DecisionTreeClassifier

    # WHY (Phase 41 Fix 1b): Old code used model_result['X'] and ['y']
    #      — the FULL dataset including the test slice. Every rule was
    #      trained on its own test data. Fit on the train slice only.
    #      Fall back to full X/y for backward compat if train slice
    #      not present (older callers).
    # CHANGED: April 2026 — Phase 41 Fix 1b — train-only fit
    #          (audit Part D CRITICAL #1)
    X            = model_result.get('X_train', model_result['X'])
    y            = model_result.get('y_train', model_result['y'])
    feature_cols = model_result['feature_cols']

    # WHY (Phase 57 Fix 3): read rule_min_confidence from config so the
    #      user's panel setting actually takes effect.
    try:
        import config_loader as _cl
        _min_confidence = float(_cl.load().get('rule_min_confidence', '0.55'))
    except Exception:
        _min_confidence = 0.55

    tree = DecisionTreeClassifier(
        max_depth=5,
        min_samples_leaf=20,
        min_samples_split=40,
        random_state=42,
    )
    tree.fit(X, y)

    tree_   = tree.tree_
    rules   = []

    def recurse(node_id, conditions):
        if tree_.feature[node_id] == -2:          # leaf
            samples    = tree_.n_node_samples[node_id]
            values     = tree_.value[node_id][0]
            total      = values.sum()
            win_count  = values[1] if len(values) > 1 else 0
            loss_count = values[0]

            prediction = 'WIN' if win_count >= loss_count else 'LOSS'
            confidence = max(win_count, loss_count) / total if total > 0 else 0
            win_rate   = win_count / total if total > 0 else 0

            # WHY (Phase 57 Fix 3): Old code hardcoded 0.55, which is LOWER
            #      than the configured default (rule_min_confidence=0.65).
            #      Users who raised their threshold in the panel still got
            #      low-confidence rules. Read from config_loader; fall back
            #      to 0.55 only if config cannot be loaded (safe default).
            #      WARNING: raises the floor for most users (0.55→0.65),
            #      which will produce fewer rules — quality over quantity.
            # CHANGED: April 2026 — Phase 57 Fix 3 — config-driven confidence floor
            #          (audit Part D MEDIUM #48)
            if samples >= 15 and confidence >= _min_confidence:
                mask = pd.Series(True, index=X.index)
                for cond in conditions:
                    col_vals = X[cond['feature']]
                    if cond['operator'] == '<=':
                        mask &= col_vals <= cond['value']
                    else:
                        mask &= col_vals > cond['value']

                # WHY (Phase A.2 hotfix): Phase 41 Fix 1b changed X to
                #      X_train (884 rows = train slice of 1106). mask is
                #      built from X.index so it is also 884 rows long.
                #      The old code passed mask.values (a bare 884-length
                #      bool array) into df.loc where df is the FULL 1106-
                #      row feature matrix, raising
                #          IndexError: Boolean index has wrong length:
                #                      884 instead of 1106
                #      Fix: use label-based indexing — mask[mask].index
                #      gives the actual row labels where mask is True, and
                #      df.loc[labels, 'pips'] selects them correctly from
                #      df regardless of how X_train relates to df (works
                #      even if the split were non-chronological in future).
                # CHANGED: April 2026 — Phase A.2 — label-based indexing
                if 'pips' in df.columns:
                    _matching_labels = mask[mask].index
                    matching_pips = df.loc[_matching_labels, 'pips']
                else:
                    matching_pips = pd.Series([0])

                rules.append({
                    'conditions':   conditions.copy(),
                    'prediction':   prediction,
                    'confidence':   round(float(confidence), 3),
                    'coverage':     int(samples),
                    'coverage_pct': round(samples / len(X) * 100, 1),
                    'win_rate':     round(float(win_rate), 3),
                    'avg_pips':     round(float(matching_pips.mean()), 1) if len(matching_pips) > 0 else 0,
                })
            return

        feature   = feature_cols[tree_.feature[node_id]]
        threshold = round(float(tree_.threshold[node_id]), 4)

        recurse(tree_.children_left[node_id],
                conditions + [{'feature': feature, 'operator': '<=', 'value': threshold}])
        recurse(tree_.children_right[node_id],
                conditions + [{'feature': feature, 'operator': '>',  'value': threshold}])

    recurse(0, [])
    rules.sort(key=lambda r: r['confidence'] * r['coverage'], reverse=True)
    return rules


# ── Section 4: Trade Clustering ───────────────────────────────────────────────

def cluster_trades(df, model_result, n_clusters=4):
    """
    Auto-detect natural groups of trades using K-Means.
    """
    from sklearn.cluster       import KMeans
    from sklearn.preprocessing import StandardScaler

    X            = model_result['X'].copy()
    feature_cols = model_result['feature_cols']

    scaler   = StandardScaler()
    X_scaled = pd.DataFrame(scaler.fit_transform(X), columns=feature_cols, index=X.index)
    X_scaled = X_scaled.fillna(0)

    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(X_scaled)

    df_c         = df.copy()
    df_c['cluster'] = labels
    overall_mean = X.mean()

    clusters = []
    for cid in range(n_clusters):
        mask       = labels == cid
        cluster_df = df_c[mask]
        cluster_X  = X[mask]

        count    = int(mask.sum())
        # WHY (Phase 45 Fix 5): Old code assumed trade_duration_minutes
        #      exists. But step2 doesn't write it (it's a leakage source).
        #      Result: cluster_trades crashed with KeyError. Fix: check
        #      if the column exists, and if not, mark avg_dur = 0.0 and
        #      set a 'duration_category': 'unknown' marker so downstream
        #      code knows this cluster's duration is unavailable.
        # CHANGED: April 2026 — Phase 45 Fix 5 — duration-unknown marker
        #          (audit Part D HIGH #37)
        avg_dur  = float(cluster_df['trade_duration_minutes'].mean()) if 'trade_duration_minutes' in cluster_df else 0.0
        avg_pips = float(cluster_df['pips'].mean())     if 'pips'      in cluster_df else 0.0
        # WHY (Phase 45 Fix 6): Old code assumed is_winner column exists.
        #      But step2 doesn't write it. Result: cluster_trades crashed
        #      or reported 0.0 win_rate for all clusters. Fix: derive
        #      from pips > 0, which step2 does provide.
        # CHANGED: April 2026 — Phase 45 Fix 6 — derive is_winner from pips
        #          (audit Part D HIGH #38)
        if 'is_winner' in cluster_df.columns:
            win_rate = float(cluster_df['is_winner'].mean())
        elif 'pips' in cluster_df.columns:
            win_rate = float((cluster_df['pips'] > 0).mean())
        else:
            win_rate = 0.0

        # Top differentiating features
        cluster_mean = cluster_X.mean()
        diff = ((cluster_mean - overall_mean) / overall_mean.replace(0, np.nan)).abs()
        diff = diff.dropna().sort_values(ascending=False)
        top_features = diff.head(5).index.tolist()

        # Auto-name
        # Phase 45 Fix 5b: If avg_dur is 0.0 (unknown), don't use duration
        #                  for naming — rely on pips-based patterns instead.
        if avg_pips > 300:
            name = 'Big winners'
        elif avg_pips < -50:
            name = 'Noise trades'
        elif avg_dur == 0.0:
            # Duration unknown — name by pip magnitude
            if avg_pips > 50:
                name = 'Profit cluster'
            elif avg_pips < 0:
                name = 'Loss cluster'
            else:
                name = 'Mixed cluster'
        elif avg_dur < 5:
            name = 'Quick scalps'
        elif avg_dur < 30:
            name = 'Short-term trades'
        elif avg_dur < 240:
            name = 'Medium holds'
        else:
            name = 'Long holds'

        clusters.append({
            'cluster_id':       cid,
            'name':             name,
            'count':            count,
            'pct':              round(count / len(df) * 100, 1),
            'avg_duration_min': round(avg_dur,  1),
            'avg_pips':         round(avg_pips, 1),
            'win_rate':         round(win_rate, 3),
            'top_features':     top_features,
        })

    clusters.sort(key=lambda c: c['avg_pips'], reverse=True)
    return clusters


# ── Section 5: Market Regime Analysis ────────────────────────────────────────

def analyze_market_regimes(df, model_result):
    """
    Analyze robot performance across different market conditions.
    """
    regimes = {}

    def _perf(mask):
        sub = df[mask]
        if len(sub) < 10:
            return None
        # Phase 45 Fix 6b: derive is_winner from pips (same as cluster_trades)
        if 'is_winner' in sub.columns:
            _wr = round(float(sub['is_winner'].mean()), 3)
        elif 'pips' in sub.columns:
            _wr = round(float((sub['pips'] > 0).mean()), 3)
        else:
            _wr = 0.0
        return {
            'count':      len(sub),
            'win_rate':   _wr,
            'avg_pips':   round(float(sub['pips'].mean()), 1),
            'total_pips': round(float(sub['pips'].sum()), 0),
        }

    # Trend regime (ADX)
    adx_col = next((c for c in ['H4_adx_14', 'H1_adx_14', 'D1_adx_14']
                    if c in df.columns and df[c].notna().sum() > 100), None)
    if adx_col:
        # WHY (Phase 57 Fix 4b): Old code hardcoded ADX > 25 — correct for
        #      XAUUSD but wrong for instruments that rarely/always trend at
        #      that level. Read adx_trend_threshold from config; default 25.
        # CHANGED: April 2026 — Phase 57 Fix 4b — configurable ADX threshold
        #          (audit Part D MEDIUM #47)
        try:
            import config_loader as _cl4
            _adx_thr = float(_cl4.load().get('adx_trend_threshold', '25'))
        except Exception:
            _adx_thr = 25.0
        trending = df[adx_col] > _adx_thr
        regimes['trend'] = {
            'trending':       _perf(trending),
            'ranging':        _perf(~trending),
            'indicator_used': adx_col,
            'adx_threshold':  _adx_thr,
        }

    # Volatility regime (ATR)
    atr_col = next((c for c in ['H1_atr_14', 'H4_atr_14']
                    if c in df.columns and df[c].notna().sum() > 100), None)
    if atr_col:
        atr_median = df[atr_col].median()
        high_vol   = df[atr_col] > atr_median
        regimes['volatility'] = {
            'high':           _perf(high_vol),
            'low':            _perf(~high_vol),
            'indicator_used': atr_col,
            'median_value':   round(float(atr_median), 2),
        }

    # Direction (price vs EMA 200)
    ema_col = next((c for c in ['H4_ema_200_distance', 'H1_ema_200_distance']
                    if c in df.columns and df[c].notna().sum() > 100), None)
    if ema_col:
        above_ema = df[ema_col] > 0
        regimes['direction'] = {
            'above_ema200':   _perf(above_ema),
            'below_ema200':   _perf(~above_ema),
            'indicator_used': ema_col,
        }

    # Session
    if 'hour_of_day' in df.columns:
        asian  = df['hour_of_day'].between(0,  7)
        london = df['hour_of_day'].between(7,  15)
        ny     = df['hour_of_day'].between(12, 20)
        regimes['session'] = {
            'asian':     _perf(asian),
            'london':    _perf(london),
            'new_york':  _perf(ny),
        }

    # Day of week
    if 'day_of_week' in df.columns:
        day_names = {0: 'Monday', 1: 'Tuesday', 2: 'Wednesday',
                     3: 'Thursday', 4: 'Friday'}
        regimes['day_of_week'] = {
            name: _perf(df['day_of_week'] == d)
            for d, name in day_names.items()
        }

    return regimes


# ── Section 6: Time Period Evolution ─────────────────────────────────────────

def analyze_evolution(df):
    """
    Detect how the robot's behavior changed over time (yearly breakdown).
    """
    df_tmp          = df.copy()
    df_tmp['_dt']   = pd.to_datetime(df_tmp['open_time'])
    df_tmp['_year'] = df_tmp['_dt'].dt.year
    df_tmp['_mon']  = df_tmp['_dt'].dt.month

    periods = []
    for year, grp in df_tmp.groupby('_year'):
        n_months  = grp['_mon'].nunique()
        per_month = len(grp) / max(n_months, 1)
        # Phase 45 Fix 5c: derive duration from open_time/close_time if available
        if 'trade_duration_minutes' in grp.columns:
            avg_dur = float(grp['trade_duration_minutes'].mean())
        elif 'open_time' in grp.columns and 'close_time' in grp.columns:
            try:
                _ot = pd.to_datetime(grp['open_time'])
                _ct = pd.to_datetime(grp['close_time'])
                _dur = (_ct - _ot).dt.total_seconds() / 60.0
                avg_dur = float(_dur.mean())
            except Exception:
                avg_dur = 0.0
        else:
            avg_dur = 0.0
        peak_hour = None
        if 'hour_of_day' in grp.columns and len(grp) > 0:
            mode_val = grp['hour_of_day'].mode()
            if len(mode_val) > 0:
                peak_hour = int(mode_val.iloc[0])

        # Phase 45 Fix 6c: derive is_winner from pips
        if 'is_winner' in grp.columns:
            _wr = round(float(grp['is_winner'].mean()), 3)
        elif 'pips' in grp.columns:
            _wr = round(float((grp['pips'] > 0).mean()), 3)
        else:
            _wr = 0.0

        periods.append({
            'period':            str(year),
            'trades':            len(grp),
            'months':            n_months,
            'trades_per_month':  round(per_month, 1),
            'win_rate':          _wr,
            'avg_pips':          round(float(grp['pips'].mean()), 1) if 'pips' in grp else 0,
            'avg_duration_min':  round(avg_dur, 1),
            'peak_hour':         peak_hour,
        })

    return periods


# ── Section 7: Anomaly Detection ─────────────────────────────────────────────

def detect_anomalies(df, model_result):
    """
    Find trades that don't fit the robot's normal pattern using Isolation Forest.
    """
    from sklearn.ensemble import IsolationForest

    X = model_result['X'].copy().fillna(0).replace([np.inf, -np.inf], 0)

    iso = IsolationForest(contamination=0.05, random_state=42, n_jobs=-1)
    labels = iso.fit_predict(X)
    scores = iso.score_samples(X)

    anomaly_mask = labels == -1
    anomalies    = []

    # Phase 45 Fix 5d+6d: derive is_winner from pips, duration from timestamps
    for i, idx in enumerate(df.index[anomaly_mask]):
        trade = df.loc[idx]
        if 'is_winner' in df.columns:
            _is_winner = bool(trade.get('is_winner', False))
        elif 'pips' in df.columns:
            _is_winner = bool(trade.get('pips', 0) > 0)
        else:
            _is_winner = False

        if 'trade_duration_minutes' in df.columns:
            _duration = round(float(trade.get('trade_duration_minutes', 0)), 1)
        elif 'open_time' in df.columns and 'close_time' in df.columns:
            try:
                _ot = pd.to_datetime(trade['open_time'])
                _ct = pd.to_datetime(trade['close_time'])
                _duration = round(float((_ct - _ot).total_seconds() / 60.0), 1)
            except Exception:
                _duration = 0.0
        else:
            _duration = 0.0

        anomalies.append({
            'trade_id':  int(trade.get('trade_id', idx)),
            'open_time': str(trade.get('open_time', '')),
            'pips':      float(trade.get('pips', 0)),
            'is_winner': _is_winner,
            'score':     round(float(scores[df.index.get_loc(idx)]), 3),
            'hour':      int(trade.get('hour_of_day', 0)),
            'duration':  _duration,
        })

    anomalies.sort(key=lambda a: a['score'])

    # Phase 45 Fix 6e: derive anomaly win rates from pips
    if 'is_winner' in df.columns:
        _anom_wr = round(float(df[anomaly_mask]['is_winner'].mean()), 3) if anomaly_mask.sum() > 0 else 0
        _norm_wr = round(float(df[~anomaly_mask]['is_winner'].mean()), 3)
    elif 'pips' in df.columns:
        _anom_wr = round(float((df[anomaly_mask]['pips'] > 0).mean()), 3) if anomaly_mask.sum() > 0 else 0
        _norm_wr = round(float((df[~anomaly_mask]['pips'] > 0).mean()), 3)
    else:
        _anom_wr = 0.0
        _norm_wr = 0.0

    return {
        'count':             int(anomaly_mask.sum()),
        'pct':               round(anomaly_mask.sum() / len(df) * 100, 1),
        'anomaly_win_rate':  _anom_wr,
        'normal_win_rate':   _norm_wr,
        'top_anomalies':     anomalies[:20],
    }


# ── Section 8: Improvement Suggestions ───────────────────────────────────────

def suggest_improvements(df, model_result, regimes, clusters, profile):
    """
    Based on all analysis, suggest concrete improvements.
    """
    suggestions  = []
    # Phase 45 Fix 6f: derive overall_wr from pips if is_winner not present
    if 'is_winner' in df.columns:
        overall_wr = float(df['is_winner'].mean())
    elif 'pips' in df.columns:
        overall_wr = float((df['pips'] > 0).mean())
    else:
        overall_wr = 0.0

    # Trend filter
    if ('trend' in regimes
            and regimes['trend'].get('trending')
            and regimes['trend'].get('ranging')):
        trending_wr = regimes['trend']['trending']['win_rate']
        ranging_wr  = regimes['trend']['ranging']['win_rate']
        if trending_wr > ranging_wr + 0.10:
            suggestions.append({
                'type':             'filter',
                'description':      'Only trade in trending markets (ADX > 25)',
                'impact':           f"Would improve win rate from {overall_wr*100:.0f}% to ~{trending_wr*100:.0f}%",
                'trades_filtered':  regimes['trend']['ranging']['count'],
                'pct_filtered':     round(regimes['trend']['ranging']['count'] / len(df) * 100, 0),
            })

    # Worst day filter
    if 'day_of_week' in regimes:
        valid_days = {k: v for k, v in regimes['day_of_week'].items() if v is not None}
        if valid_days:
            worst_day = min(valid_days.items(), key=lambda x: x[1]['win_rate'])
            if worst_day[1]['win_rate'] < overall_wr - 0.08:
                suggestions.append({
                    'type':             'filter',
                    'description':      f"Avoid trading on {worst_day[0]}",
                    'impact':           f"{worst_day[0]} win rate is only {worst_day[1]['win_rate']*100:.0f}% vs {overall_wr*100:.0f}% overall",
                    'trades_filtered':  worst_day[1]['count'],
                    'pct_filtered':     round(worst_day[1]['count'] / len(df) * 100, 0),
                })

    # Volatility filter
    if ('volatility' in regimes
            and regimes['volatility'].get('high')
            and regimes['volatility'].get('low')):
        high_wr = regimes['volatility']['high']['win_rate']
        low_wr  = regimes['volatility']['low']['win_rate']
        if abs(high_wr - low_wr) > 0.08:
            better = 'high' if high_wr > low_wr else 'low'
            worse  = 'low'  if better == 'high' else 'high'
            suggestions.append({
                'type':             'filter',
                'description':      f"Only trade in {better} volatility (ATR {'above' if better == 'high' else 'below'} median)",
                'impact':           f"Would improve win rate from {overall_wr*100:.0f}% to ~{regimes['volatility'][better]['win_rate']*100:.0f}%",
                'trades_filtered':  regimes['volatility'][worse]['count'],
                'pct_filtered':     round(regimes['volatility'][worse]['count'] / len(df) * 100, 0),
            })

    # Worst cluster
    if clusters:
        worst = min(clusters, key=lambda c: c['avg_pips'])
        if worst['avg_pips'] < 0:
            suggestions.append({
                'type':             'remove_cluster',
                'description':      f"Filter out '{worst['name']}' trades ({worst['count']} trades, {worst['pct']}%)",
                'impact':           f"These trades average {worst['avg_pips']} pips — removing them improves overall performance",
                'trades_filtered':  worst['count'],
                'pct_filtered':     worst['pct'],
            })

    # Session filter
    if 'session' in regimes:
        valid_sessions = {k: v for k, v in regimes['session'].items() if v is not None}
        if valid_sessions:
            worst_session = min(valid_sessions.items(), key=lambda x: x[1]['win_rate'])
            best_session  = max(valid_sessions.items(), key=lambda x: x[1]['win_rate'])
            if worst_session[1]['win_rate'] < overall_wr - 0.10:
                suggestions.append({
                    'type':             'filter',
                    'description':      f"Avoid the {worst_session[0]} session; focus on {best_session[0]}",
                    'impact':           f"{worst_session[0].capitalize()} session win rate {worst_session[1]['win_rate']*100:.0f}% vs best {best_session[0].capitalize()} {best_session[1]['win_rate']*100:.0f}%",
                    'trades_filtered':  worst_session[1]['count'],
                    'pct_filtered':     round(worst_session[1]['count'] / len(df) * 100, 0),
                })

    return suggestions


# ── Text report ───────────────────────────────────────────────────────────────

def _write_text_report(report, filepath):
    lines = []
    lines.append('=' * 70)
    lines.append('ROBOT ANALYSIS REPORT')
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"Trades analyzed: {report['trade_count']} | Features: {report['feature_count']}")
    lines.append('=' * 70)

    p = report['profile']
    lines.append('\n-- ROBOT IDENTITY (auto-detected) ---------------------------------')
    lines.append(f"  Direction:      {p['direction']}")
    lines.append(f"  Type:           {p['duration_category']}")
    lines.append(f"  Date range:     {p['date_range'][0]} to {p['date_range'][1]}")
    lines.append(f"  Win rate:       {p['win_rate']*100:.0f}%")
    lines.append(f"  Reward:Risk:    {p['reward_risk_ratio']:.1f}:1")
    lines.append(f"  Avg win:        +{p['avg_win_pips']:.0f} pips")
    lines.append(f"  Avg loss:       {p['avg_loss_pips']:.0f} pips")
    if p['sl_pattern'].get('fixed'):
        lines.append(f"  Stop loss:      Fixed {p['sl_pattern']['fixed_value_pips']:.0f} pips "
                     f"(confidence: {p['sl_pattern']['confidence']*100:.0f}%)")
    else:
        lines.append(f"  Stop loss:      Dynamic")
    if p['tp_pattern'].get('fixed'):
        lines.append(f"  Take profit:    Fixed {p['tp_pattern']['fixed_value_pips']:.0f} pips "
                     f"(confidence: {p['tp_pattern']['confidence']*100:.0f}%)")
    else:
        lines.append(f"  Take profit:    Dynamic / trailing")
    lines.append(f"  Sessions:       {', '.join(p['sessions']) if p['sessions'] else 'N/A'}")
    lines.append(f"  Peak hours:     {p['peak_hours']} UTC")
    lines.append(f"  Frequency:      {p['frequency_trend']} ({p.get('trades_per_month_avg', 0):.0f}/month avg)")

    fi = report['feature_importance']
    lines.append('\n-- TOP FEATURES (what the robot reacts to) ------------------------')
    lines.append(f"  Model accuracy: train={fi['train_accuracy']*100:.1f}%, test={fi['test_accuracy']*100:.1f}%")
    for i, (feat, imp) in enumerate(fi['top_20'][:10]):
        lines.append(f"  {i+1:2d}. {feat:45s} {imp*100:.1f}%")

    lines.append('\n-- TRADING RULES (IF/THEN) ----------------------------------------')
    for i, rule in enumerate(report['rules'][:10]):
        conds = ' AND '.join(
            f"{c['feature']} {c['operator']} {c['value']}"
            for c in rule['conditions']
        )
        lines.append(f"\n  RULE {i+1} ({rule['prediction']}, conf: {rule['confidence']*100:.0f}%, {rule['coverage']} trades)")
        lines.append(f"    IF {conds}")
        lines.append(f"    Win rate: {rule['win_rate']*100:.0f}% | Avg pips: {rule['avg_pips']:+.0f}")

    lines.append('\n-- TRADE CLUSTERS (auto-detected groups) --------------------------')
    for c in report['clusters']:
        lines.append(f"  '{c['name']}': {c['count']} trades ({c['pct']}%), "
                     f"WR {c['win_rate']*100:.0f}%, avg {c['avg_pips']:+.0f} pips, "
                     f"avg dur {c['avg_duration_min']:.0f}min")

    lines.append('\n-- MARKET REGIME PERFORMANCE --------------------------------------')
    for regime_name, regime_data in report['regimes'].items():
        if not isinstance(regime_data, dict):
            continue
        lines.append(f"  {regime_name}:")
        for sub_name, sub_data in regime_data.items():
            if isinstance(sub_data, dict) and 'win_rate' in sub_data:
                lines.append(f"    {sub_name:22s}: WR {sub_data['win_rate']*100:.0f}%, "
                             f"avg {sub_data['avg_pips']:+.0f} pips ({sub_data['count']} trades)")

    lines.append('\n-- TIME PERIOD EVOLUTION ------------------------------------------')
    for period in report['evolution']:
        lines.append(f"  {period['period']}: {period['trades']:>4d} trades, "
                     f"WR {period['win_rate']*100:.0f}%, "
                     f"avg {period['avg_pips']:+.0f} pips, "
                     f"{period['trades_per_month']}/month")

    a = report['anomalies']
    lines.append('\n-- ANOMALIES -------------------------------------------------------')
    lines.append(f"  {a['count']} outlier trades ({a['pct']}%)")
    lines.append(f"  Anomaly win rate: {a['anomaly_win_rate']*100:.0f}% "
                 f"vs normal: {a['normal_win_rate']*100:.0f}%")

    lines.append('\n-- IMPROVEMENT SUGGESTIONS ----------------------------------------')
    if report['suggestions']:
        for s in report['suggestions']:
            lines.append(f"  -> {s['description']}")
            lines.append(f"     {s['impact']}")
    else:
        lines.append("  No significant improvements detected.")

    lines.append('\n' + '=' * 70)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


# ── Main ──────────────────────────────────────────────────────────────────────

def run_analysis(feature_matrix_path=None):
    """Run complete analysis and save results."""
    if feature_matrix_path is None:
        feature_matrix_path = os.path.join(OUTPUT_DIR, 'feature_matrix.csv')

    log.info('=' * 70)
    log.info('ROBOT ANALYSIS — Full Reverse Engineering')
    log.info('=' * 70)

    start = time.time()

    # Load
    log.info('\nLoading feature matrix...')
    df = pd.read_csv(feature_matrix_path)
    log.info(f'  {len(df)} trades x {len(df.columns)} features')

    # 1. Profile
    log.info('\n[1/8] Building robot profile...')
    profile = build_robot_profile(df)
    log.info(f"  Type: {profile['direction']} {profile['duration_category']}")
    log.info(f"  Win rate: {profile['win_rate']*100:.0f}%, R:R {profile['reward_risk_ratio']:.1f}:1")
    sl = profile['sl_pattern']
    log.info(f"  SL: {'Fixed ' + str(sl.get('fixed_value_pips', '?')) + ' pips' if sl.get('fixed') else 'Dynamic'}")

    # 2. Feature Importance
    log.info('\n[2/8] Computing feature importance...')
    model_result = compute_feature_importance(df)
    # WHY (Phase A.1 hotfix): compute_feature_importance can legitimately
    #      return None (no usable features, missing label column). Old
    #      code subscripted it directly and crashed the entire scenario
    #      with an opaque TypeError. Fail fast with a clear, actionable
    #      error message instead so the user knows what went wrong.
    # CHANGED: April 2026 — Phase A.1 — None-check before subscripting
    if model_result is None:
        raise RuntimeError(
            "compute_feature_importance returned None — feature matrix "
            "lacks either usable numeric features or a label column "
            "(is_winner or pips). Check the feature matrix CSV and the "
            "preceding [ANALYZE] ERROR log line for the specific cause."
        )
    log.info(f"  Model accuracy: train={model_result['train_accuracy']*100:.1f}%, test={model_result['test_accuracy']*100:.1f}%")
    log.info('  Top 5 features:')
    for feat, imp in model_result['top_20'][:5]:
        log.info(f'    {feat}: {imp*100:.1f}%')

    # 3. Rules
    log.info('\n[3/8] Extracting trading rules...')
    rules = extract_rules(df, model_result)
    log.info(f'  Extracted {len(rules)} rules')
    for i, rule in enumerate(rules[:3]):
        conds = ' AND '.join(f"{c['feature']} {c['operator']} {c['value']}" for c in rule['conditions'])
        log.info(f"  Rule {i+1}: IF {conds}")
        log.info(f"           THEN {rule['prediction']} (conf: {rule['confidence']*100:.0f}%, "
              f"coverage: {rule['coverage']} trades, WR: {rule['win_rate']*100:.0f}%)")

    # 4. Clusters
    log.info('\n[4/8] Clustering trades...')
    clusters = cluster_trades(df, model_result)
    for c in clusters:
        log.info(f"  '{c['name']}': {c['count']} trades ({c['pct']}%), "
              f"WR {c['win_rate']*100:.0f}%, avg {c['avg_pips']:+.0f} pips")

    # 5. Regimes
    log.info('\n[5/8] Analyzing market regimes...')
    regimes = analyze_market_regimes(df, model_result)
    for regime_name, regime_data in regimes.items():
        if isinstance(regime_data, dict):
            log.info(f'  {regime_name}:')
            for sub_name, sub_data in regime_data.items():
                if isinstance(sub_data, dict) and 'win_rate' in sub_data:
                    log.info(f"    {sub_name}: WR {sub_data['win_rate']*100:.0f}%, "
                          f"avg {sub_data['avg_pips']:+.0f} pips ({sub_data['count']} trades)")

    # 6. Evolution
    log.info('\n[6/8] Analyzing time periods...')
    evolution = analyze_evolution(df)
    for p in evolution:
        log.info(f"  {p['period']}: {p['trades']} trades, WR {p['win_rate']*100:.0f}%, "
              f"avg {p['avg_pips']:+.0f} pips, {p['trades_per_month']}/month")

    # 7. Anomalies
    log.info('\n[7/8] Detecting anomalies...')
    anomalies = detect_anomalies(df, model_result)
    log.info(f"  {anomalies['count']} anomalous trades ({anomalies['pct']}%)")
    log.info(f"  Anomaly WR: {anomalies['anomaly_win_rate']*100:.0f}% "
          f"vs normal: {anomalies['normal_win_rate']*100:.0f}%")

    # 8. Suggestions
    log.info('\n[8/8] Generating improvement suggestions...')
    suggestions = suggest_improvements(df, model_result, regimes, clusters, profile)
    for s in suggestions:
        log.info(f"  -> {s['description']}: {s['impact']}")

    elapsed = time.time() - start

    # WHY (Phase A.3 hotfix): analysis_report.json was missing three
    #      top-level fields — entry_timeframe, activated_at, and
    #      discovery_method — that shared/stale_check.py requires when
    #      win rules are present. Result: every freshly-discovered rule
    #      set triggered a "Stale Rules Warning" popup in the EA
    #      generator panel the moment the user tried to export an EA.
    #      step6_extract_rules.py (legacy) and scratch_discovery.py (P4)
    #      already emit these fields; this writer was never updated.
    #
    #      entry_timeframe is derived from the feature_matrix_path
    #      parent dir name (run_scenarios.py writes to scenario_<TF>/).
    #      For multi-TF scenario names like "H1_M15" the entry TF is
    #      the first segment, matching step6_extract_rules.py line 367.
    # CHANGED: April 2026 — Phase A.3 — populate stale-check fields
    if feature_matrix_path is not None:
        _scenario_dir = os.path.basename(
            os.path.dirname(os.path.abspath(feature_matrix_path))
        )
        # Expected form: "scenario_M5", "scenario_H1_M15", "scenario_H4"
        if _scenario_dir.startswith('scenario_'):
            _scenario_name = _scenario_dir[len('scenario_'):]
        else:
            _scenario_name = None
        if _scenario_name:
            _entry_timeframe = (
                _scenario_name.split('_')[0]
                if '_' in _scenario_name
                else _scenario_name
            )
        else:
            _entry_timeframe = None
    else:
        _scenario_name = None
        _entry_timeframe = None

    if _entry_timeframe is None:
        log.warning(
            "[ANALYZE] Could not derive entry_timeframe from feature_matrix_path "
            "(expected .../scenario_<TF>/feature_matrix.csv). The stale check "
            "will flag this report as missing entry_timeframe."
        )

    # Assemble report
    report = {
        'generated_at':    datetime.now().isoformat(),
        # WHY (Phase A.3 hotfix): shared/stale_check.py requires these
        #      three top-level fields when win rules are present, or it
        #      flags the report as stale and blocks EA generation with a
        #      warning popup. See the comment block above for full
        #      rationale and why other writers (step6, scratch_discovery)
        #      already emit these.
        # CHANGED: April 2026 — Phase A.3
        'entry_timeframe': _entry_timeframe,
        'activated_at':    time.strftime('%Y-%m-%d %H:%M:%S'),
        'discovery_method': 'p1_run_scenarios',
        'scenario':        _scenario_name,
        'analysis_time_s': round(elapsed, 1),
        'trade_count':     len(df),
        'feature_count':   len(df.columns),
        'profile':         profile,
        'feature_importance': {
            'top_20':         [(f, round(float(i), 6)) for f, i in model_result['top_20']],
            'train_accuracy': model_result['train_accuracy'],
            'test_accuracy':  model_result['test_accuracy'],
        },
        'rules':       rules[:20],
        'clusters':    clusters,
        'regimes':     regimes,
        'evolution':   evolution,
        'anomalies': {
            'count':            anomalies['count'],
            'pct':              anomalies['pct'],
            'anomaly_win_rate': anomalies['anomaly_win_rate'],
            'normal_win_rate':  anomalies['normal_win_rate'],
            'top_10':           anomalies['top_anomalies'][:10],
        },
        'suggestions': suggestions,
    }

    # WHY: Phase 25 Fix 4 — Allow caller to redirect outputs to a
    #      per-workspace folder. Falls back to OUTPUT_DIR for callers
    #      that don't pass feature_matrix_path. New convention: the
    #      report goes next to the feature_matrix that was loaded.
    # CHANGED: April 2026 — Phase 25 Fix 4 — input-relative output (audit Part B #20)
    if feature_matrix_path is not None:
        _output_dir = os.path.dirname(os.path.abspath(feature_matrix_path))
    else:
        _output_dir = OUTPUT_DIR
    os.makedirs(_output_dir, exist_ok=True)

    json_path = os.path.join(_output_dir, 'analysis_report.json')
    with open(json_path, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    log.info(f'\nSaved: {json_path}')

    txt_path = os.path.join(_output_dir, 'analysis_report.txt')
    _write_text_report(report, txt_path)
    log.info(f'Saved: {txt_path}')

    log.info(f"\n{'=' * 70}")
    log.info(f'ANALYSIS COMPLETE in {elapsed:.0f}s')
    log.info(f"{'=' * 70}")

    return report


if __name__ == '__main__':
    run_analysis()
