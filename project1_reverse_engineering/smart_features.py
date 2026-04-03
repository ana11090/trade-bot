"""
Smart Features — computed from existing indicators and candle data.

These features capture:
- Inter-timeframe relationships (divergences, alignment)
- Indicator dynamics (direction, acceleration, regime changes)
- Time intelligence (session phases, calendar events)
- Price action patterns (candle sequences, key levels)
- Momentum quality

All features are added as new columns to the feature matrix.
Results cached to outputs/smart_feature_matrix.csv.
"""

import os
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(_HERE, 'outputs')
CACHE_PATH = os.path.join(OUTPUT_DIR, 'smart_feature_matrix.csv')


def compute_smart_features(feature_matrix_path=None, force_recompute=False,
                            progress_callback=None):
    """
    Compute smart features and return extended DataFrame.
    Caches to smart_feature_matrix.csv.

    Returns: DataFrame with original columns + ~50 new SMART_ columns.
    """
    if not force_recompute and os.path.exists(CACHE_PATH):
        cached_df = pd.read_csv(CACHE_PATH)
        smart_cols = [c for c in cached_df.columns if c.startswith('SMART_')]
        if len(smart_cols) > 30:
            return cached_df

    if feature_matrix_path is None:
        feature_matrix_path = os.path.join(OUTPUT_DIR, 'feature_matrix.csv')

    df = pd.read_csv(feature_matrix_path)
    total_steps = 8

    if progress_callback:
        progress_callback(1, total_steps, "Computing TF divergences...")
    df = _add_tf_divergences(df)

    if progress_callback:
        progress_callback(2, total_steps, "Computing indicator dynamics...")
    df = _add_indicator_dynamics(df)

    if progress_callback:
        progress_callback(3, total_steps, "Computing TF alignment...")
    df = _add_alignment_scores(df)

    if progress_callback:
        progress_callback(4, total_steps, "Computing session features...")
    df = _add_session_intelligence(df)

    if progress_callback:
        progress_callback(5, total_steps, "Computing calendar features...")
    df = _add_calendar_features(df)

    if progress_callback:
        progress_callback(6, total_steps, "Computing volatility regimes...")
    df = _add_volatility_regimes(df)

    if progress_callback:
        progress_callback(7, total_steps, "Computing price action...")
    df = _add_price_action(df)

    if progress_callback:
        progress_callback(8, total_steps, "Computing momentum quality...")
    df = _add_momentum_quality(df)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    df.to_csv(CACHE_PATH, index=False)
    return df


def _safe_col(df, col):
    """Return column values or zeros if column absent."""
    if col in df.columns:
        return df[col].fillna(0).values
    return np.zeros(len(df))


# ─────────────────────────────────────────────────────────────────────────────
# Feature groups
# ─────────────────────────────────────────────────────────────────────────────

def _add_tf_divergences(df):
    """Inter-timeframe divergences — detect when TFs disagree."""
    h4_rsi  = _safe_col(df, 'H4_rsi_14')
    h1_rsi  = _safe_col(df, 'H1_rsi_14')
    m15_rsi = _safe_col(df, 'M15_rsi_14')

    df['SMART_rsi_h4_minus_h1']  = h4_rsi  - h1_rsi
    df['SMART_rsi_h1_minus_m15'] = h1_rsi  - m15_rsi
    df['SMART_rsi_h4_minus_m15'] = h4_rsi  - m15_rsi

    h4_adx = _safe_col(df, 'H4_adx_14')
    h1_adx = _safe_col(df, 'H1_adx_14')
    df['SMART_adx_h4_minus_h1'] = h4_adx - h1_adx

    h1_ema200 = _safe_col(df, 'H1_ema_200_distance')
    h4_ema200 = _safe_col(df, 'H4_ema_200_distance')
    d1_ema200 = _safe_col(df, 'D1_ema_200_distance')
    df['SMART_above_ema200_count'] = (
        (h1_ema200 > 0).astype(int)
        + (h4_ema200 > 0).astype(int)
        + (d1_ema200 > 0).astype(int)
    )

    h1_macd = _safe_col(df, 'H1_macd_fast_diff')
    h4_macd = _safe_col(df, 'H4_macd_fast_diff')
    df['SMART_macd_agree'] = (
        ((h1_macd > 0) & (h4_macd > 0)).astype(int)
        - ((h1_macd < 0) & (h4_macd < 0)).astype(int)
    )
    return df


def _add_indicator_dynamics(df):
    """Indicator direction and acceleration — what the indicator is DOING."""
    for col_name in ['H1_rsi_14', 'H4_adx_14', 'H1_atr_14', 'H1_macd_fast_diff',
                     'H4_rsi_14', 'H1_cci_14', 'H1_bb_20_2_width']:
        if col_name not in df.columns:
            continue
        vals = df[col_name].values
        direction = np.zeros(len(vals))
        direction[3:] = vals[3:] - vals[:-3]
        df[f'SMART_{col_name}_direction'] = direction

        accel = np.zeros(len(vals))
        accel[6:] = direction[6:] - direction[3:-3]
        df[f'SMART_{col_name}_accel'] = accel

    h1_atr    = _safe_col(df, 'H1_atr_14')
    h1_atr_50 = _safe_col(df, 'H1_atr_50')
    df['SMART_atr_expansion'] = np.where(
        h1_atr_50 > 0, h1_atr / np.maximum(h1_atr_50, 0.001), 0
    )
    return df


def _add_alignment_scores(df):
    """Multi-timeframe alignment — count how many TFs agree."""
    rsi_cols = ['M5_rsi_14', 'M15_rsi_14', 'H1_rsi_14', 'H4_rsi_14', 'D1_rsi_14']
    bull_count = np.zeros(len(df))
    for col in rsi_cols:
        if col in df.columns:
            bull_count += (df[col].fillna(50).values > 50).astype(float)
    df['SMART_rsi_bullish_tfs'] = bull_count

    adx_cols = ['M5_adx_14', 'M15_adx_14', 'H1_adx_14', 'H4_adx_14', 'D1_adx_14']
    trend_count = np.zeros(len(df))
    for col in adx_cols:
        if col in df.columns:
            trend_count += (df[col].fillna(0).values > 25).astype(float)
    df['SMART_trending_tfs'] = trend_count

    ema_cols = ['M15_ema_9_above_20', 'H1_ema_9_above_20', 'H4_ema_9_above_20']
    ema_align = np.zeros(len(df))
    for col in ema_cols:
        if col in df.columns:
            ema_align += df[col].fillna(0).values.astype(float)
    df['SMART_ema_bullish_tfs'] = ema_align
    return df


def _add_session_intelligence(df):
    """Session timing features."""
    if 'hour_of_day' not in df.columns:
        return df
    hour = df['hour_of_day'].values
    df['SMART_is_london_ny_overlap'] = ((hour >= 13) & (hour <= 16)).astype(int)
    df['SMART_is_early_london']      = ((hour >= 7)  & (hour <= 9)).astype(int)
    df['SMART_is_late_ny']           = ((hour >= 19) & (hour <= 21)).astype(int)
    df['SMART_is_asian_dead_zone']   = ((hour >= 3)  & (hour <= 5)).astype(int)
    df['SMART_is_pre_london']        = ((hour >= 6)  & (hour <= 7)).astype(int)
    df['SMART_is_pre_ny']            = ((hour >= 12) & (hour <= 13)).astype(int)
    df['SMART_hours_since_london']   = np.where(hour >= 7,  hour - 7,  0)
    df['SMART_hours_since_ny']       = np.where(hour >= 13, hour - 13, 0)

    session_count = np.zeros(len(df))
    session_count += ((hour >= 0)  & (hour < 8)).astype(int)
    session_count += ((hour >= 7)  & (hour < 16)).astype(int)
    session_count += ((hour >= 13) & (hour < 22)).astype(int)
    df['SMART_active_sessions'] = session_count
    return df


def _add_calendar_features(df):
    """Calendar-based features."""
    if 'open_time' not in df.columns:
        return df
    dates = pd.to_datetime(df['open_time'], errors='coerce')
    dow  = dates.dt.dayofweek
    dom  = dates.dt.day
    month = dates.dt.month

    df['SMART_is_monday']  = (dow == 0).astype(int)
    df['SMART_is_friday']  = (dow == 4).astype(int)
    df['SMART_is_midweek'] = ((dow >= 1) & (dow <= 3)).astype(int)

    df['SMART_is_month_start'] = (dom <= 3).astype(int)
    df['SMART_is_month_end']   = (dom >= 27).astype(int)
    df['SMART_is_nfp_friday']  = ((dow == 4) & (dom <= 7)).astype(int)
    df['SMART_is_quarter_end_month'] = month.isin([3, 6, 9, 12]).astype(int)
    df['SMART_week_of_month']  = ((dom - 1) // 7 + 1).clip(1, 5)
    return df


def _add_volatility_regimes(df):
    """Volatility regime detection."""
    h1_bb_w  = _safe_col(df, 'H1_bb_20_2_width')
    h1_kel_w = _safe_col(df, 'H1_keltner_width')
    df['SMART_bb_squeeze'] = np.where(
        h1_kel_w > 0, (h1_bb_w < h1_kel_w).astype(int), 0
    )

    h1_atr     = _safe_col(df, 'H1_atr_14')
    h1_atr_100 = _safe_col(df, 'H1_atr_100')
    df['SMART_atr_vs_long'] = np.where(
        h1_atr_100 > 0, h1_atr / np.maximum(h1_atr_100, 0.001), 1
    )

    h4_atr    = _safe_col(df, 'H4_atr_14')
    h4_atr_50 = _safe_col(df, 'H4_atr_50')
    df['SMART_vol_expanding'] = (h4_atr > h4_atr_50).astype(int)

    h1_std    = _safe_col(df, 'H1_std_dev_20')
    h1_std_50 = _safe_col(df, 'H1_std_dev_50')
    df['SMART_std_ratio'] = np.where(
        h1_std_50 > 0, h1_std / np.maximum(h1_std_50, 0.001), 1
    )
    return df


def _add_price_action(df):
    """Price action intelligence."""
    h1_pivot = _safe_col(df, 'H1_pivot_point')
    if np.any(h1_pivot > 0):
        df['SMART_dist_to_round_50']  = np.abs(h1_pivot % 50  - 25) / 25
        df['SMART_dist_to_round_100'] = np.abs(h1_pivot % 100 - 50) / 50

    d1_atr   = _safe_col(df, 'D1_atr_14')
    h1_range = _safe_col(df, 'H1_candle_range')
    df['SMART_daily_range_used'] = np.where(
        d1_atr > 0, h1_range / np.maximum(d1_atr, 0.001), 0
    )

    h1_body  = _safe_col(df, 'H1_body_to_range_ratio')
    df['SMART_strong_candle']     = (h1_body > 0.7).astype(int)
    df['SMART_indecision_candle'] = (h1_body < 0.3).astype(int)

    h1_swing = _safe_col(df, 'H1_position_in_swing_range')
    h4_swing = _safe_col(df, 'H4_position_in_swing_range')
    df['SMART_near_swing_high']     = (h1_swing > 0.8).astype(int)
    df['SMART_near_swing_low']      = (h1_swing < 0.2).astype(int)
    df['SMART_swing_pos_h4_vs_h1']  = h4_swing - h1_swing
    return df


def _add_momentum_quality(df):
    """Momentum quality features."""
    h1_rsi = _safe_col(df, 'H1_rsi_14')
    df['SMART_rsi_zone'] = np.select(
        [h1_rsi > 70, h1_rsi > 60, h1_rsi > 50,
         h1_rsi > 40, h1_rsi > 30, h1_rsi <= 30],
        [3, 2, 1, -1, -2, -3],
        default=0
    )

    rsi_vals = df['H1_rsi_14'].values if 'H1_rsi_14' in df.columns else np.zeros(len(df))
    crossed_up   = np.zeros(len(df))
    crossed_down = np.zeros(len(df))
    crossed_up[1:]   = ((rsi_vals[1:] > 50) & (rsi_vals[:-1] <= 50)).astype(float)
    crossed_down[1:] = ((rsi_vals[1:] < 50) & (rsi_vals[:-1] >= 50)).astype(float)
    df['SMART_rsi_crossed_50_up']   = crossed_up
    df['SMART_rsi_crossed_50_down'] = crossed_down

    h1_macd_diff = _safe_col(df, 'H1_macd_fast_diff')
    h1_atr       = _safe_col(df, 'H1_atr_14')
    df['SMART_macd_normalized'] = np.where(
        h1_atr > 0, h1_macd_diff / np.maximum(h1_atr, 0.001), 0
    )

    h1_stoch = _safe_col(df, 'H1_stoch_14_k')
    df['SMART_stoch_overbought'] = (h1_stoch > 80).astype(int)
    df['SMART_stoch_oversold']   = (h1_stoch < 20).astype(int)

    h1_willr = _safe_col(df, 'H1_williams_r_14')
    df['SMART_willr_extreme_high'] = (h1_willr > -20).astype(int)
    df['SMART_willr_extreme_low']  = (h1_willr < -80).astype(int)

    h1_tsi = _safe_col(df, 'H1_tsi')
    df['SMART_tsi_bullish'] = (h1_tsi > 0).astype(int)
    df['SMART_tsi_strong']  = (np.abs(h1_tsi) > 20).astype(int)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────────────

SMART_FEATURE_CATEGORIES = {
    "Inter-TF Divergences": [
        ("SMART_rsi_h4_minus_h1",    "RSI divergence H4 vs H1"),
        ("SMART_rsi_h1_minus_m15",   "RSI divergence H1 vs M15"),
        ("SMART_rsi_h4_minus_m15",   "RSI divergence H4 vs M15"),
        ("SMART_adx_h4_minus_h1",    "ADX trend strength divergence"),
        ("SMART_above_ema200_count", "TFs above EMA200 (0-3)"),
        ("SMART_macd_agree",         "MACD direction agreement H1+H4"),
    ],
    "Indicator Dynamics": [
        ("SMART_H1_rsi_14_direction",       "H1 RSI rising/falling"),
        ("SMART_H1_rsi_14_accel",           "H1 RSI acceleration"),
        ("SMART_H4_adx_14_direction",       "H4 ADX rising/falling"),
        ("SMART_H1_atr_14_direction",       "H1 ATR rising/falling"),
        ("SMART_H1_macd_fast_diff_direction","H1 MACD histogram direction"),
        ("SMART_atr_expansion",             "ATR vs 50-period ATR ratio"),
    ],
    "TF Alignment Scores": [
        ("SMART_rsi_bullish_tfs", "TFs where RSI > 50 (0-5)"),
        ("SMART_trending_tfs",    "TFs where ADX > 25 (0-5)"),
        ("SMART_ema_bullish_tfs", "TFs where EMA9 > EMA20 (0-3)"),
    ],
    "Session Intelligence": [
        ("SMART_is_london_ny_overlap", "London-NY overlap (highest volume)"),
        ("SMART_is_early_london",      "First 2 hours of London session"),
        ("SMART_is_late_ny",           "Late NY — position squaring"),
        ("SMART_is_asian_dead_zone",   "Asian dead zone (03-05 UTC)"),
        ("SMART_is_pre_london",        "Pre-London positioning (06-07)"),
        ("SMART_is_pre_ny",            "Pre-NY positioning (12-13)"),
        ("SMART_hours_since_london",   "Hours elapsed since London open"),
        ("SMART_hours_since_ny",       "Hours elapsed since NY open"),
        ("SMART_active_sessions",      "Number of active sessions (0-3)"),
    ],
    "Calendar / Fundamentals": [
        ("SMART_is_monday",              "Monday — reversal risk"),
        ("SMART_is_friday",              "Friday — position closing"),
        ("SMART_is_midweek",             "Tue-Thu — best trending days"),
        ("SMART_is_month_start",         "First 3 days of month"),
        ("SMART_is_month_end",           "Last 3 days of month"),
        ("SMART_is_nfp_friday",          "NFP Friday (1st Friday of month)"),
        ("SMART_is_quarter_end_month",   "Quarter-end month (Mar/Jun/Sep/Dec)"),
        ("SMART_week_of_month",          "Week of month (1-5)"),
    ],
    "Volatility Regimes": [
        ("SMART_bb_squeeze",    "Bollinger inside Keltner (breakout signal)"),
        ("SMART_atr_vs_long",   "Current ATR vs 100-period ATR ratio"),
        ("SMART_vol_expanding", "H4 ATR > 50-period ATR (expanding vol)"),
        ("SMART_std_ratio",     "20-period vs 50-period std dev ratio"),
    ],
    "Price Action Patterns": [
        ("SMART_dist_to_round_50",   "Distance to nearest 50-pt round level"),
        ("SMART_dist_to_round_100",  "Distance to nearest 100-pt round level"),
        ("SMART_daily_range_used",   "Daily ATR consumed (0-1+)"),
        ("SMART_strong_candle",      "Body > 70% of range (conviction)"),
        ("SMART_indecision_candle",  "Body < 30% of range (doji-like)"),
        ("SMART_near_swing_high",    "Price near top of swing range"),
        ("SMART_near_swing_low",     "Price near bottom of swing range"),
        ("SMART_swing_pos_h4_vs_h1", "H4 vs H1 position in swing range"),
    ],
    "Momentum Quality": [
        ("SMART_rsi_zone",            "RSI zone (-3 oversold to +3 overbought)"),
        ("SMART_rsi_crossed_50_up",   "RSI just crossed 50 upward"),
        ("SMART_rsi_crossed_50_down", "RSI just crossed 50 downward"),
        ("SMART_macd_normalized",     "MACD histogram / ATR (normalised)"),
        ("SMART_stoch_overbought",    "Stochastic > 80"),
        ("SMART_stoch_oversold",      "Stochastic < 20"),
        ("SMART_willr_extreme_high",  "Williams %R > -20 (overbought)"),
        ("SMART_willr_extreme_low",   "Williams %R < -80 (oversold)"),
        ("SMART_tsi_bullish",         "TSI > 0"),
        ("SMART_tsi_strong",          "TSI magnitude > 20"),
    ],
}


def get_smart_feature_names():
    """Return flat list of all SMART_ feature names."""
    return [name for group in SMART_FEATURE_CATEGORIES.values() for name, _ in group]
