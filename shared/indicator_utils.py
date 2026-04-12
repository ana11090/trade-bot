"""
INDICATOR UTILITIES
Functions to compute all 119 technical indicators used in reverse engineering.
Each function takes a DataFrame of OHLCV candles and returns a Series indexed by timestamp.
"""

import pandas as pd
import numpy as np
import ta


def compute_all_indicators(candles_df, prefix=""):
    """
    Compute ALL 119 indicators on a candle DataFrame.

    Args:
        candles_df: DataFrame with columns: timestamp, open, high, low, close, volume
        prefix: Optional prefix to add to all column names (e.g., "H1_" for combined scenarios)

    Returns:
        DataFrame with all indicator values, indexed by timestamp
    """
    print(f"  Computing all indicators{' with prefix: ' + prefix if prefix else ''}...")

    # Create output DataFrame with same index as candles_df (integer index for proper alignment)
    indicators = pd.DataFrame(index=candles_df.index)

    # Extract OHLCV arrays
    open_prices = candles_df['open'].values
    high_prices = candles_df['high'].values
    low_prices = candles_df['low'].values
    close_prices = candles_df['close'].values
    volume = candles_df['volume'].values

    # GROUP A — RSI (5 features)
    for period in [7, 14, 21, 28, 50]:
        indicators[f'{prefix}rsi_{period}'] = ta.momentum.RSIIndicator(close=candles_df['close'], window=period).rsi()

    # GROUP B — EMA Distance (5 features)
    for period in [9, 20, 50, 100, 200]:
        ema = ta.trend.EMAIndicator(close=candles_df['close'], window=period).ema_indicator()
        indicators[f'{prefix}ema_{period}_distance'] = ((candles_df['close'] - ema) / ema * 100)

    # GROUP C — EMA Cross Signals (4 features)
    ema_9 = ta.trend.EMAIndicator(close=candles_df['close'], window=9).ema_indicator()
    ema_20 = ta.trend.EMAIndicator(close=candles_df['close'], window=20).ema_indicator()
    ema_50 = ta.trend.EMAIndicator(close=candles_df['close'], window=50).ema_indicator()
    ema_200 = ta.trend.EMAIndicator(close=candles_df['close'], window=200).ema_indicator()

    indicators[f'{prefix}ema_9_above_20'] = (ema_9 > ema_20).astype(int)
    indicators[f'{prefix}ema_20_above_50'] = (ema_20 > ema_50).astype(int)
    indicators[f'{prefix}ema_50_above_200'] = (ema_50 > ema_200).astype(int)
    indicators[f'{prefix}ema_9_above_200'] = (ema_9 > ema_200).astype(int)

    # GROUP D — SMA Distance (3 features)
    for period in [20, 50, 200]:
        sma = ta.trend.SMAIndicator(close=candles_df['close'], window=period).sma_indicator()
        indicators[f'{prefix}sma_{period}_distance'] = ((candles_df['close'] - sma) / sma * 100)

    # GROUP E — MACD (6 features)
    # Standard MACD (12, 26, 9)
    macd_std = ta.trend.MACD(close=candles_df['close'], window_slow=26, window_fast=12, window_sign=9)
    indicators[f'{prefix}macd_std'] = macd_std.macd()
    indicators[f'{prefix}macd_std_signal'] = macd_std.macd_signal()
    indicators[f'{prefix}macd_std_diff'] = macd_std.macd_diff()

    # Fast MACD (5, 13, 5)
    macd_fast = ta.trend.MACD(close=candles_df['close'], window_slow=13, window_fast=5, window_sign=5)
    indicators[f'{prefix}macd_fast'] = macd_fast.macd()
    indicators[f'{prefix}macd_fast_signal'] = macd_fast.macd_signal()
    indicators[f'{prefix}macd_fast_diff'] = macd_fast.macd_diff()

    # GROUP F — ATR (6 features)
    for period in [7, 14, 21, 28, 50, 100]:
        indicators[f'{prefix}atr_{period}'] = ta.volatility.AverageTrueRange(
            high=candles_df['high'],
            low=candles_df['low'],
            close=candles_df['close'],
            window=period
        ).average_true_range()

    # GROUP G — Bollinger Bands (5 features)
    for period, std in [(20, 2), (20, 3)]:
        bb = ta.volatility.BollingerBands(close=candles_df['close'], window=period, window_dev=std)
        indicators[f'{prefix}bb_{period}_{std}_upper'] = bb.bollinger_hband()
        indicators[f'{prefix}bb_{period}_{std}_lower'] = bb.bollinger_lband()
        indicators[f'{prefix}bb_{period}_{std}_width'] = bb.bollinger_wband()

    # Additional BB width for period 50
    bb_50 = ta.volatility.BollingerBands(close=candles_df['close'], window=50, window_dev=2)
    indicators[f'{prefix}bb_50_2_width'] = bb_50.bollinger_wband()

    # GROUP H — ADX (3 features)
    for period in [14, 21, 28]:
        adx = ta.trend.ADXIndicator(high=candles_df['high'], low=candles_df['low'], close=candles_df['close'], window=period)
        indicators[f'{prefix}adx_{period}'] = adx.adx()

    # GROUP I — Stochastic Oscillator (4 features)
    for period in [14, 21]:
        stoch = ta.momentum.StochasticOscillator(
            high=candles_df['high'],
            low=candles_df['low'],
            close=candles_df['close'],
            window=period,
            smooth_window=3
        )
        indicators[f'{prefix}stoch_{period}_k'] = stoch.stoch()
        indicators[f'{prefix}stoch_{period}_d'] = stoch.stoch_signal()

    # GROUP J — CCI (3 features)
    for period in [14, 20, 50]:
        indicators[f'{prefix}cci_{period}'] = ta.trend.CCIIndicator(
            high=candles_df['high'],
            low=candles_df['low'],
            close=candles_df['close'],
            window=period
        ).cci()

    # GROUP K — Williams %R (2 features)
    for period in [14, 28]:
        indicators[f'{prefix}williams_r_{period}'] = ta.momentum.WilliamsRIndicator(
            high=candles_df['high'],
            low=candles_df['low'],
            close=candles_df['close'],
            lbp=period
        ).williams_r()

    # GROUP L — Volume Features (6 features)
    # Volume ratio to moving average
    volume_sma_20 = candles_df['volume'].rolling(window=20).mean()
    indicators[f'{prefix}volume_ratio_20'] = candles_df['volume'] / volume_sma_20

    # Volume change
    indicators[f'{prefix}volume_change'] = candles_df['volume'].pct_change()

    # On-Balance Volume (OBV)
    indicators[f'{prefix}obv'] = ta.volume.OnBalanceVolumeIndicator(
        close=candles_df['close'],
        volume=candles_df['volume']
    ).on_balance_volume()

    # Volume Price Trend (VPT)
    indicators[f'{prefix}vpt'] = ta.volume.VolumePriceTrendIndicator(
        close=candles_df['close'],
        volume=candles_df['volume']
    ).volume_price_trend()

    # Chaikin Money Flow
    indicators[f'{prefix}cmf'] = ta.volume.ChaikinMoneyFlowIndicator(
        high=candles_df['high'],
        low=candles_df['low'],
        close=candles_df['close'],
        volume=candles_df['volume'],
        window=20
    ).chaikin_money_flow()

    # Money Flow Index
    indicators[f'{prefix}mfi'] = ta.volume.MFIIndicator(
        high=candles_df['high'],
        low=candles_df['low'],
        close=candles_df['close'],
        volume=candles_df['volume'],
        window=14
    ).money_flow_index()

    # GROUP M — Price Action & Candle Structure (8 features)
    indicators[f'{prefix}candle_body'] = abs(candles_df['close'] - candles_df['open'])
    indicators[f'{prefix}candle_range'] = candles_df['high'] - candles_df['low']
    indicators[f'{prefix}upper_shadow'] = candles_df['high'] - candles_df[['close', 'open']].max(axis=1)
    indicators[f'{prefix}lower_shadow'] = candles_df[['close', 'open']].min(axis=1) - candles_df['low']
    indicators[f'{prefix}body_to_range_ratio'] = indicators[f'{prefix}candle_body'] / indicators[f'{prefix}candle_range'].replace(0, np.nan)
    indicators[f'{prefix}is_bullish'] = (candles_df['close'] > candles_df['open']).astype(int)
    indicators[f'{prefix}close_position_in_range'] = (candles_df['close'] - candles_df['low']) / indicators[f'{prefix}candle_range'].replace(0, np.nan)
    indicators[f'{prefix}distance_from_high'] = (candles_df['high'] - candles_df['close']) / candles_df['close'] * 100

    # GROUP N — Support & Resistance Proximity (5 features)
    # Recent swing high/low
    swing_period = 50
    indicators[f'{prefix}swing_high_{swing_period}'] = candles_df['high'].rolling(window=swing_period).max()
    indicators[f'{prefix}swing_low_{swing_period}'] = candles_df['low'].rolling(window=swing_period).min()
    indicators[f'{prefix}distance_to_swing_high'] = (indicators[f'{prefix}swing_high_{swing_period}'] - candles_df['close']) / candles_df['close'] * 100
    indicators[f'{prefix}distance_to_swing_low'] = (candles_df['close'] - indicators[f'{prefix}swing_low_{swing_period}']) / candles_df['close'] * 100

    # Price position within recent range
    swing_range = indicators[f'{prefix}swing_high_{swing_period}'] - indicators[f'{prefix}swing_low_{swing_period}']
    indicators[f'{prefix}position_in_swing_range'] = (candles_df['close'] - indicators[f'{prefix}swing_low_{swing_period}']) / swing_range.replace(0, np.nan)

    # GROUP O — Momentum & Rate of Change (5 features)
    for period in [1, 5, 10, 20, 50]:
        indicators[f'{prefix}roc_{period}'] = ((candles_df['close'] - candles_df['close'].shift(period)) / candles_df['close'].shift(period) * 100)

    # GROUP P — Session & Time Features (8 features)
    # Extract time components from timestamp
    timestamps = pd.to_datetime(candles_df['timestamp'])
    indicators[f'{prefix}hour_of_day'] = timestamps.dt.hour
    indicators[f'{prefix}day_of_week'] = timestamps.dt.dayofweek  # Monday=0, Sunday=6
    indicators[f'{prefix}day_of_month'] = timestamps.dt.day

    # WHY: Old code used pd.between(lo, hi) which is inclusive on both
    #      ends, and the three session ranges overlapped:
    #        Asian:  between(0, 7)   → hours 0-7  (comment lied "00:00-08:00")
    #        London: between(7, 15)  → hours 7-15 (overlap with Asian at 7)
    #        NY:     between(12, 20) → hours 12-20 (overlap with London at 12-15)
    #      A candle at hour 7 had is_asian_session=1 AND is_london_session=1,
    #      breaking rules like "asian only" or "london only". Same at hours
    #      12-15. Fix: non-overlapping sessions with priority
    #      (NY > London > Asian), matching strategy_refiner._get_session.
    # CHANGED: April 2026 — non-overlapping sessions (audit HIGH #54)
    _hr = timestamps.dt.hour
    indicators[f'{prefix}is_asian_session']  = ((_hr >= 0) & (_hr <= 6)).astype(int)   # hours 00:00-06:59 UTC
    indicators[f'{prefix}is_london_session'] = ((_hr >= 7) & (_hr <= 11)).astype(int)  # hours 07:00-11:59 UTC
    indicators[f'{prefix}is_ny_session']     = ((_hr >= 12) & (_hr <= 20)).astype(int) # hours 12:00-20:59 UTC
    indicators[f'{prefix}is_late_session']   = ((_hr >= 21) & (_hr <= 23)).astype(int) # hours 21:00-23:59 UTC (Sydney open)
    indicators[f'{prefix}is_weekend'] = (timestamps.dt.dayofweek >= 5).astype(int)

    # GROUP Q — Rolling range levels (NOT Fibonacci)
    # WHY: Old code named these distance_to_fib_236/382/500/618/786 but
    #      the underlying swing_high/swing_low are 50-bar rolling min/max
    #      which shift every bar. A real Fibonacci retracement uses
    #      identified swing pivots (significant highs/lows) that don't
    #      shift until a new pivot forms. Rolling levels are a
    #      fundamentally different signal — they're a moving average of
    #      recent range, not a reference-level retracement. Renamed to
    #      rolling_level_* so the feature name matches what the code
    #      actually computes. Rules referencing the old names will fail
    #      with a missing-feature error — intentional, because the old
    #      values were meaningless "Fibonacci" signals.
    # CHANGED: April 2026 — honest rename (audit MED #53)
    rng_swing_high = indicators[f'{prefix}swing_high_{swing_period}']
    rng_swing_low  = indicators[f'{prefix}swing_low_{swing_period}']
    rng_range      = rng_swing_high - rng_swing_low

    rng_236 = rng_swing_low + 0.236 * rng_range
    rng_382 = rng_swing_low + 0.382 * rng_range
    rng_500 = rng_swing_low + 0.500 * rng_range
    rng_618 = rng_swing_low + 0.618 * rng_range
    rng_786 = rng_swing_low + 0.786 * rng_range

    indicators[f'{prefix}distance_to_rolling_level_236'] = (candles_df['close'] - rng_236) / candles_df['close'] * 100
    indicators[f'{prefix}distance_to_rolling_level_382'] = (candles_df['close'] - rng_382) / candles_df['close'] * 100
    indicators[f'{prefix}distance_to_rolling_level_500'] = (candles_df['close'] - rng_500) / candles_df['close'] * 100
    indicators[f'{prefix}distance_to_rolling_level_618'] = (candles_df['close'] - rng_618) / candles_df['close'] * 100
    indicators[f'{prefix}distance_to_rolling_level_786'] = (candles_df['close'] - rng_786) / candles_df['close'] * 100

    # ══════════════════════════════════════════════════════════════════════════════
    # ADDITIONAL INDICATORS - High Priority
    # ══════════════════════════════════════════════════════════════════════════════

    # GROUP R — Ichimoku Cloud (5 features)
    ichimoku = ta.trend.IchimokuIndicator(
        high=candles_df['high'],
        low=candles_df['low'],
        window1=9,
        window2=26,
        window3=52
    )
    indicators[f'{prefix}ichimoku_conversion'] = ichimoku.ichimoku_conversion_line()
    indicators[f'{prefix}ichimoku_base'] = ichimoku.ichimoku_base_line()
    indicators[f'{prefix}ichimoku_a'] = ichimoku.ichimoku_a()
    indicators[f'{prefix}ichimoku_b'] = ichimoku.ichimoku_b()
    # Distance metrics for Ichimoku
    indicators[f'{prefix}price_above_cloud'] = (
        candles_df['close'].values > indicators[f'{prefix}ichimoku_a'].values
    ).astype(int)

    # GROUP S — Parabolic SAR (2 features)
    psar = ta.trend.PSARIndicator(
        high=candles_df['high'],
        low=candles_df['low'],
        close=candles_df['close']
    )
    indicators[f'{prefix}psar'] = psar.psar()
    indicators[f'{prefix}psar_signal'] = (candles_df['close'].values > indicators[f'{prefix}psar'].values).astype(int)

    # GROUP T — VWAP (1 feature)
    # VWAP = Cumulative(Price × Volume) / Cumulative(Volume)
    # WHY: Old code used a global cumsum from the start of the dataset.
    #      VWAP resets each trading day — a Monday VWAP should not include
    #      Friday's volume. Intraday strategies (M5/M15/H1) that compare
    #      price to "VWAP" were actually comparing against a multi-week
    #      average, making the feature meaningless for single-day setups.
    # CHANGED: April 2026 — daily reset for VWAP (audit MED)
    typical_price  = (candles_df['high'] + candles_df['low'] + candles_df['close']) / 3
    _date_group    = pd.to_datetime(candles_df['timestamp']).dt.normalize()
    _vwap_num      = (typical_price * candles_df['volume']).groupby(_date_group).cumsum()
    _vwap_den      = candles_df['volume'].groupby(_date_group).cumsum()
    indicators[f'{prefix}vwap'] = _vwap_num / _vwap_den
    indicators[f'{prefix}vwap_distance'] = ((candles_df['close'] - indicators[f'{prefix}vwap']) /
                                           indicators[f'{prefix}vwap'] * 100)

    # GROUP U — ATR Bands (2 features)
    # WHY: This was never a real Supertrend — Supertrend is a direction-switching
    #      indicator that uses ATR and price-relative logic to flip between an upper
    #      and lower band. This code simply computes hl_avg ± 3×ATR, which are
    #      symmetric ATR bands (similar to Keltner). Calling them "supertrend"
    #      was misleading: strategies trained on them under the name "supertrend"
    #      produce completely different MQL5 code than a real Supertrend EA.
    #      Renamed to atr_band_upper/lower so the name matches the actual formula.
    # CHANGED: April 2026 — rename supertrend_upper/lower → atr_band_upper/lower (audit MED)
    atr_10 = ta.volatility.AverageTrueRange(
        high=candles_df['high'],
        low=candles_df['low'],
        close=candles_df['close'],
        window=10
    ).average_true_range()

    hl_avg = (candles_df['high'] + candles_df['low']) / 2
    multiplier = 3
    basic_ub = hl_avg + (multiplier * atr_10)
    basic_lb = hl_avg - (multiplier * atr_10)

    indicators[f'{prefix}atr_band_upper'] = basic_ub
    indicators[f'{prefix}atr_band_lower'] = basic_lb

    # GROUP V — Pivot Points (7 features)
    # Classic Pivot Points calculation
    prev_high = candles_df['high'].shift(1)
    prev_low = candles_df['low'].shift(1)
    prev_close = candles_df['close'].shift(1)

    pivot = (prev_high + prev_low + prev_close) / 3
    indicators[f'{prefix}pivot_point'] = pivot
    indicators[f'{prefix}resistance_1'] = 2 * pivot - prev_low
    indicators[f'{prefix}support_1'] = 2 * pivot - prev_high
    indicators[f'{prefix}resistance_2'] = pivot + (prev_high - prev_low)
    indicators[f'{prefix}support_2'] = pivot - (prev_high - prev_low)
    indicators[f'{prefix}resistance_3'] = prev_high + 2 * (pivot - prev_low)
    indicators[f'{prefix}support_3'] = prev_low - 2 * (prev_high - pivot)

    # GROUP W — DMI Components (2 features)
    # +DI and -DI (we already have ADX)
    adx_indicator = ta.trend.ADXIndicator(
        high=candles_df['high'],
        low=candles_df['low'],
        close=candles_df['close'],
        window=14
    )
    indicators[f'{prefix}plus_di'] = adx_indicator.adx_pos()
    indicators[f'{prefix}minus_di'] = adx_indicator.adx_neg()

    # ══════════════════════════════════════════════════════════════════════════════
    # ADDITIONAL INDICATORS - Medium Priority
    # ══════════════════════════════════════════════════════════════════════════════

    # GROUP X — Keltner Channels (3 features)
    keltner = ta.volatility.KeltnerChannel(
        high=candles_df['high'],
        low=candles_df['low'],
        close=candles_df['close'],
        window=20
    )
    indicators[f'{prefix}keltner_upper'] = keltner.keltner_channel_hband()
    indicators[f'{prefix}keltner_lower'] = keltner.keltner_channel_lband()
    indicators[f'{prefix}keltner_width'] = keltner.keltner_channel_wband()

    # GROUP Y — Donchian Channels (3 features)
    donchian = ta.volatility.DonchianChannel(
        high=candles_df['high'],
        low=candles_df['low'],
        close=candles_df['close'],
        window=20
    )
    indicators[f'{prefix}donchian_upper'] = donchian.donchian_channel_hband()
    indicators[f'{prefix}donchian_lower'] = donchian.donchian_channel_lband()
    indicators[f'{prefix}donchian_middle'] = donchian.donchian_channel_mband()

    # GROUP Z — Aroon Indicator (3 features)
    aroon = ta.trend.AroonIndicator(
        high=candles_df['high'],
        low=candles_df['low'],
        window=25
    )
    indicators[f'{prefix}aroon_up'] = aroon.aroon_up()
    indicators[f'{prefix}aroon_down'] = aroon.aroon_down()
    indicators[f'{prefix}aroon_indicator'] = aroon.aroon_indicator()

    # GROUP AA — Elder Ray (2 features)
    ema_13 = ta.trend.EMAIndicator(close=candles_df['close'], window=13).ema_indicator()
    indicators[f'{prefix}bull_power'] = candles_df['high'] - ema_13
    indicators[f'{prefix}bear_power'] = candles_df['low'] - ema_13

    # GROUP AB — TSI (True Strength Index) (2 features)
    tsi = ta.momentum.TSIIndicator(close=candles_df['close'], window_slow=25, window_fast=13)
    indicators[f'{prefix}tsi'] = tsi.tsi()

    # GROUP AC — KST (Know Sure Thing) (2 features)
    kst = ta.trend.KSTIndicator(close=candles_df['close'])
    indicators[f'{prefix}kst'] = kst.kst()
    indicators[f'{prefix}kst_signal'] = kst.kst_sig()

    # GROUP AD — Ultimate Oscillator (1 feature)
    uo = ta.momentum.UltimateOscillator(
        high=candles_df['high'],
        low=candles_df['low'],
        close=candles_df['close']
    )
    indicators[f'{prefix}ultimate_oscillator'] = uo.ultimate_oscillator()

    # GROUP AE — Awesome Oscillator (1 feature)
    ao = ta.momentum.AwesomeOscillatorIndicator(
        high=candles_df['high'],
        low=candles_df['low']
    )
    indicators[f'{prefix}awesome_oscillator'] = ao.awesome_oscillator()

    # GROUP AF — Mass Index (1 feature)
    mass = ta.trend.MassIndex(
        high=candles_df['high'],
        low=candles_df['low']
    )
    indicators[f'{prefix}mass_index'] = mass.mass_index()

    # GROUP AG — DPO (Detrended Price Oscillator) (1 feature)
    dpo = ta.trend.DPOIndicator(close=candles_df['close'], window=20)
    indicators[f'{prefix}dpo'] = dpo.dpo()

    # GROUP AH — Standard Deviation (2 features)
    indicators[f'{prefix}std_dev_20'] = candles_df['close'].rolling(window=20).std()
    indicators[f'{prefix}std_dev_50'] = candles_df['close'].rolling(window=50).std()

    # WHY: bfill() fills warmup-period NaN with FUTURE values — the trader
    #      never saw those values at the time of the early trades, so
    #      training on them is a look-ahead leak. Keep ffill (which is
    #      causal — carries PAST values forward) but drop bfill. Warmup
    #      rows will remain NaN and be handled by the sentinel-based NaN
    #      fill in the downstream pipeline.
    # CHANGED: April 2026 — remove bfill leak (audit bug: indicator_utils)
    indicators = indicators.ffill()

    print(f"  Computed {len(indicators.columns)} indicators")

    # Set timestamp as index so reset_index() yields a named 'timestamp' column
    # (matches compute_indicators behavior)
    if 'timestamp' in candles_df.columns:
        indicators.index = candles_df['timestamp'].values
        indicators.index.name = 'timestamp'

    return indicators


# ── Selective indicator computation ──────────────────────────────────────────

INDICATOR_GROUP_MAP = {
    "rsi": "rsi",
    "ema": "ema",
    "sma": "sma",
    "macd": "macd",
    "macd_fast_diff": "macd",
    "macd_std_diff": "macd",
    "macd_fast": "macd",
    "macd_std": "macd",
    "atr": "atr",
    "bb": "bb",
    "adx": "adx",
    "stoch": "stoch",
    "cci": "cci",
    "williams_r": "williams_r",
    "volume_ratio": "volume",
    "volume_change": "volume",
    "obv": "volume",
    "vpt": "volume",
    "cmf": "volume",
    "mfi": "volume",
    "candle_body": "price_action",
    "candle_range": "price_action",
    "upper_shadow": "price_action",
    "lower_shadow": "price_action",
    "body_to_range_ratio": "price_action",
    "is_bullish": "price_action",
    "close_position_in_range": "price_action",
    "distance_from_high": "price_action",
    "swing_high": "swing",
    "swing_low": "swing",
    "distance_to_swing_high": "swing",
    "distance_to_swing_low": "swing",
    "position_in_swing_range": "swing",
    "roc": "roc",
    "hour_of_day": "session",
    "day_of_week": "session",
    "day_of_month": "session",
    "is_asian_session": "session",
    "is_london_session": "session",
    "is_ny_session": "session",
    "is_late_session": "session",   # CHANGED: April 2026 — new non-overlapping 21-23 slot
    "is_weekend": "session",
    "distance_to_rolling_level": "rolling_level",  # CHANGED: April 2026 — renamed from fib (audit MED #53)
    "ichimoku": "ichimoku",
    "price_above_cloud": "ichimoku",
    "psar": "psar",
    "vwap": "vwap",
    "vwap_distance": "vwap",
    "supertrend": "supertrend",       # legacy key — group still loads atr_band_upper/lower
    "atr_band_upper": "supertrend",   # new canonical name
    "atr_band_lower": "supertrend",   # new canonical name
    "pivot_point": "pivot",
    "resistance": "pivot",
    "support": "pivot",
    "plus_di": "dmi",
    "minus_di": "dmi",
    "keltner": "keltner",
    "donchian": "donchian",
    "aroon_down": "aroon",
    "aroon_up": "aroon",
    "aroon_indicator": "aroon",
    "aroon": "aroon",
    "bull_power": "elder_ray",
    "bear_power": "elder_ray",
    "tsi": "tsi",
    "kst": "kst",
    "kst_signal": "kst",
    "ultimate_oscillator": "uo",
    "awesome_oscillator": "ao",
    "mass_index": "mass_index",
    "dpo": "dpo",
    "std_dev": "std_dev",
}


def map_rule_indicators_to_compute_groups(indicator_names):
    """Convert rule indicator names (e.g. 'aroon_down', 'adx_14') to compute group names."""
    groups = set()
    for name in indicator_names:
        if name in INDICATOR_GROUP_MAP:
            groups.add(INDICATOR_GROUP_MAP[name])
            continue
        matched = False
        for key, group in INDICATOR_GROUP_MAP.items():
            if name.startswith(key):
                groups.add(group)
                matched = True
                break
        if not matched:
            groups.add(name.split('_')[0])
    if 'fib' in groups:
        groups.add('swing')
    return sorted(groups)


def compute_indicators(df, only=None, prefix=""):
    """
    Compute technical indicators on a candle DataFrame.

    Args:
        df: DataFrame with columns: timestamp, open, high, low, close, volume
        only: optional list/set of group names to compute.
              e.g. ["adx", "aroon", "cci"] — skips all other groups.
              If None, computes all indicators (equivalent to compute_all_indicators).
        prefix: optional column name prefix (e.g. "H1_")

    Returns:
        DataFrame indexed by timestamp with computed indicator columns.
    """
    if only is not None:
        only = set(only)
        if 'fib' in only:
            only.add('swing')   # Fibonacci needs swing_high/swing_low

    indicators = pd.DataFrame(index=df.index)

    # GROUP A — RSI
    if only is None or 'rsi' in only:
        for period in [7, 14, 21, 28, 50]:
            indicators[f'{prefix}rsi_{period}'] = ta.momentum.RSIIndicator(
                close=df['close'], window=period).rsi()

    # GROUP B+C — EMA Distance & Cross Signals
    if only is None or 'ema' in only:
        for period in [9, 20, 50, 100, 200]:
            ema = ta.trend.EMAIndicator(close=df['close'], window=period).ema_indicator()
            indicators[f'{prefix}ema_{period}_distance'] = (df['close'] - ema) / ema * 100
        ema_9   = ta.trend.EMAIndicator(close=df['close'], window=9).ema_indicator()
        ema_20  = ta.trend.EMAIndicator(close=df['close'], window=20).ema_indicator()
        ema_50  = ta.trend.EMAIndicator(close=df['close'], window=50).ema_indicator()
        ema_200 = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
        indicators[f'{prefix}ema_9_above_20']   = (ema_9  > ema_20).astype(int)
        indicators[f'{prefix}ema_20_above_50']  = (ema_20 > ema_50).astype(int)
        indicators[f'{prefix}ema_50_above_200'] = (ema_50 > ema_200).astype(int)
        indicators[f'{prefix}ema_9_above_200']  = (ema_9  > ema_200).astype(int)

    # GROUP D — SMA Distance
    if only is None or 'sma' in only:
        for period in [20, 50, 200]:
            sma = ta.trend.SMAIndicator(close=df['close'], window=period).sma_indicator()
            indicators[f'{prefix}sma_{period}_distance'] = (df['close'] - sma) / sma * 100

    # GROUP E — MACD
    if only is None or 'macd' in only:
        macd_std = ta.trend.MACD(close=df['close'], window_slow=26, window_fast=12, window_sign=9)
        indicators[f'{prefix}macd_std']        = macd_std.macd()
        indicators[f'{prefix}macd_std_signal'] = macd_std.macd_signal()
        indicators[f'{prefix}macd_std_diff']   = macd_std.macd_diff()
        macd_fast = ta.trend.MACD(close=df['close'], window_slow=13, window_fast=5, window_sign=5)
        indicators[f'{prefix}macd_fast']        = macd_fast.macd()
        indicators[f'{prefix}macd_fast_signal'] = macd_fast.macd_signal()
        indicators[f'{prefix}macd_fast_diff']   = macd_fast.macd_diff()

    # GROUP F — ATR
    if only is None or 'atr' in only:
        for period in [7, 14, 21, 28, 50, 100]:
            indicators[f'{prefix}atr_{period}'] = ta.volatility.AverageTrueRange(
                high=df['high'], low=df['low'], close=df['close'], window=period
            ).average_true_range()

    # GROUP G — Bollinger Bands
    if only is None or 'bb' in only:
        for period, std in [(20, 2), (20, 3)]:
            bb = ta.volatility.BollingerBands(close=df['close'], window=period, window_dev=std)
            indicators[f'{prefix}bb_{period}_{std}_upper'] = bb.bollinger_hband()
            indicators[f'{prefix}bb_{period}_{std}_lower'] = bb.bollinger_lband()
            indicators[f'{prefix}bb_{period}_{std}_width'] = bb.bollinger_wband()
        bb_50 = ta.volatility.BollingerBands(close=df['close'], window=50, window_dev=2)
        indicators[f'{prefix}bb_50_2_width'] = bb_50.bollinger_wband()

    # GROUP H — ADX
    if only is None or 'adx' in only:
        for period in [14, 21, 28]:
            adx = ta.trend.ADXIndicator(
                high=df['high'], low=df['low'], close=df['close'], window=period)
            indicators[f'{prefix}adx_{period}'] = adx.adx()

    # GROUP I — Stochastic
    if only is None or 'stoch' in only:
        for period in [14, 21]:
            stoch = ta.momentum.StochasticOscillator(
                high=df['high'], low=df['low'], close=df['close'],
                window=period, smooth_window=3)
            indicators[f'{prefix}stoch_{period}_k'] = stoch.stoch()
            indicators[f'{prefix}stoch_{period}_d'] = stoch.stoch_signal()

    # GROUP J — CCI
    if only is None or 'cci' in only:
        for period in [14, 20, 50]:
            indicators[f'{prefix}cci_{period}'] = ta.trend.CCIIndicator(
                high=df['high'], low=df['low'], close=df['close'], window=period).cci()

    # GROUP K — Williams %R
    if only is None or 'williams_r' in only:
        for period in [14, 28]:
            indicators[f'{prefix}williams_r_{period}'] = ta.momentum.WilliamsRIndicator(
                high=df['high'], low=df['low'], close=df['close'], lbp=period).williams_r()

    # GROUP L — Volume
    if only is None or 'volume' in only:
        vol_sma20 = df['volume'].rolling(window=20).mean()
        indicators[f'{prefix}volume_ratio_20'] = df['volume'] / vol_sma20
        indicators[f'{prefix}volume_change']   = df['volume'].pct_change()
        indicators[f'{prefix}obv'] = ta.volume.OnBalanceVolumeIndicator(
            close=df['close'], volume=df['volume']).on_balance_volume()
        indicators[f'{prefix}vpt'] = ta.volume.VolumePriceTrendIndicator(
            close=df['close'], volume=df['volume']).volume_price_trend()
        indicators[f'{prefix}cmf'] = ta.volume.ChaikinMoneyFlowIndicator(
            high=df['high'], low=df['low'], close=df['close'],
            volume=df['volume'], window=20).chaikin_money_flow()
        indicators[f'{prefix}mfi'] = ta.volume.MFIIndicator(
            high=df['high'], low=df['low'], close=df['close'],
            volume=df['volume'], window=14).money_flow_index()

    # GROUP M — Price Action
    if only is None or 'price_action' in only:
        candle_body  = abs(df['close'] - df['open'])
        candle_range = df['high'] - df['low']
        indicators[f'{prefix}candle_body']             = candle_body
        indicators[f'{prefix}candle_range']            = candle_range
        indicators[f'{prefix}upper_shadow']            = df['high'] - df[['close', 'open']].max(axis=1)
        indicators[f'{prefix}lower_shadow']            = df[['close', 'open']].min(axis=1) - df['low']
        indicators[f'{prefix}body_to_range_ratio']     = candle_body / candle_range.replace(0, np.nan)
        indicators[f'{prefix}is_bullish']              = (df['close'] > df['open']).astype(int)
        indicators[f'{prefix}close_position_in_range'] = (df['close'] - df['low']) / candle_range.replace(0, np.nan)
        indicators[f'{prefix}distance_from_high']      = (df['high'] - df['close']) / df['close'] * 100

    # GROUP N — Swing High/Low
    if only is None or 'swing' in only:
        sp = 50
        sw_high = df['high'].rolling(window=sp).max()
        sw_low  = df['low'].rolling(window=sp).min()
        indicators[f'{prefix}swing_high_{sp}']       = sw_high
        indicators[f'{prefix}swing_low_{sp}']        = sw_low
        indicators[f'{prefix}distance_to_swing_high'] = (sw_high - df['close']) / df['close'] * 100
        indicators[f'{prefix}distance_to_swing_low']  = (df['close'] - sw_low) / df['close'] * 100
        swing_range = sw_high - sw_low
        indicators[f'{prefix}position_in_swing_range'] = (
            df['close'] - sw_low) / swing_range.replace(0, np.nan)

    # GROUP O — Rate of Change
    if only is None or 'roc' in only:
        for period in [1, 5, 10, 20, 50]:
            indicators[f'{prefix}roc_{period}'] = (
                (df['close'] - df['close'].shift(period)) / df['close'].shift(period) * 100)

    # GROUP P — Session/Time
    if only is None or 'session' in only:
        ts = pd.to_datetime(df['timestamp'])
        indicators[f'{prefix}hour_of_day']       = ts.dt.hour
        indicators[f'{prefix}day_of_week']       = ts.dt.dayofweek
        indicators[f'{prefix}day_of_month']      = ts.dt.day
        # WHY: Same non-overlapping session fix as the main compute path.
        # CHANGED: April 2026 — non-overlapping sessions (audit HIGH #54)
        _hr2 = ts.dt.hour
        indicators[f'{prefix}is_asian_session']  = ((_hr2 >= 0) & (_hr2 <= 6)).astype(int)
        indicators[f'{prefix}is_london_session'] = ((_hr2 >= 7) & (_hr2 <= 11)).astype(int)
        indicators[f'{prefix}is_ny_session']     = ((_hr2 >= 12) & (_hr2 <= 20)).astype(int)
        indicators[f'{prefix}is_late_session']   = ((_hr2 >= 21) & (_hr2 <= 23)).astype(int)
        indicators[f'{prefix}is_weekend']        = (ts.dt.dayofweek >= 5).astype(int)

    # GROUP Q — Rolling range levels (NOT Fibonacci)
    # WHY: Same honest rename as the main compute path. See audit MED #53.
    # CHANGED: April 2026 — renamed fib to rolling_level
    if only is None or 'fib' in only or 'rolling_level' in only:
        sp   = 50
        sw_h = indicators.get(f'{prefix}swing_high_{sp}', df['high'].rolling(sp).max())
        sw_l = indicators.get(f'{prefix}swing_low_{sp}',  df['low'].rolling(sp).min())
        rng_range = sw_h - sw_l
        for level, ratio in [('236', 0.236), ('382', 0.382), ('500', 0.500),
                              ('618', 0.618), ('786', 0.786)]:
            indicators[f'{prefix}distance_to_rolling_level_{level}'] = (
                (df['close'] - (sw_l + ratio * rng_range)) / df['close'] * 100)

    # GROUP R — Ichimoku
    if only is None or 'ichimoku' in only:
        ich = ta.trend.IchimokuIndicator(
            high=df['high'], low=df['low'], window1=9, window2=26, window3=52)
        indicators[f'{prefix}ichimoku_conversion'] = ich.ichimoku_conversion_line()
        indicators[f'{prefix}ichimoku_base']       = ich.ichimoku_base_line()
        ich_a = ich.ichimoku_a()
        indicators[f'{prefix}ichimoku_a']          = ich_a
        indicators[f'{prefix}ichimoku_b']          = ich.ichimoku_b()
        indicators[f'{prefix}price_above_cloud']   = (df['close'].values > ich_a.values).astype(int)

    # GROUP S — Parabolic SAR
    if only is None or 'psar' in only:
        psar_ind = ta.trend.PSARIndicator(high=df['high'], low=df['low'], close=df['close'])
        psar_vals = psar_ind.psar()
        indicators[f'{prefix}psar']        = psar_vals
        indicators[f'{prefix}psar_signal'] = (df['close'].values > psar_vals.values).astype(int)

    # GROUP T — VWAP
    # WHY: Same daily-reset fix as compute_all_indicators above.
    # CHANGED: April 2026 — daily reset for VWAP (audit MED)
    if only is None or 'vwap' in only:
        tp          = (df['high'] + df['low'] + df['close']) / 3
        _dg         = pd.to_datetime(df['timestamp']).dt.normalize()
        vwap        = (tp * df['volume']).groupby(_dg).cumsum() / df['volume'].groupby(_dg).cumsum()
        indicators[f'{prefix}vwap']          = vwap
        indicators[f'{prefix}vwap_distance'] = (df['close'] - vwap) / vwap * 100

    # GROUP U — ATR Bands (renamed from supertrend — see compute_all_indicators for WHY)
    # CHANGED: April 2026 — rename supertrend_upper/lower → atr_band_upper/lower (audit MED)
    if only is None or 'supertrend' in only or 'atr_band' in only:
        atr_10 = ta.volatility.AverageTrueRange(
            high=df['high'], low=df['low'], close=df['close'], window=10).average_true_range()
        hl_avg = (df['high'] + df['low']) / 2
        indicators[f'{prefix}atr_band_upper'] = hl_avg + (3 * atr_10)
        indicators[f'{prefix}atr_band_lower'] = hl_avg - (3 * atr_10)

    # GROUP V — Pivot Points
    if only is None or 'pivot' in only:
        ph = df['high'].shift(1)
        pl = df['low'].shift(1)
        pc = df['close'].shift(1)
        pivot = (ph + pl + pc) / 3
        indicators[f'{prefix}pivot_point']  = pivot
        indicators[f'{prefix}resistance_1'] = 2 * pivot - pl
        indicators[f'{prefix}support_1']    = 2 * pivot - ph
        indicators[f'{prefix}resistance_2'] = pivot + (ph - pl)
        indicators[f'{prefix}support_2']    = pivot - (ph - pl)
        indicators[f'{prefix}resistance_3'] = ph + 2 * (pivot - pl)
        indicators[f'{prefix}support_3']    = pl - 2 * (ph - pivot)

    # GROUP W — DMI Components
    if only is None or 'dmi' in only:
        adx_ind = ta.trend.ADXIndicator(
            high=df['high'], low=df['low'], close=df['close'], window=14)
        indicators[f'{prefix}plus_di']  = adx_ind.adx_pos()
        indicators[f'{prefix}minus_di'] = adx_ind.adx_neg()

    # GROUP X — Keltner Channels
    if only is None or 'keltner' in only:
        kc = ta.volatility.KeltnerChannel(
            high=df['high'], low=df['low'], close=df['close'], window=20)
        indicators[f'{prefix}keltner_upper'] = kc.keltner_channel_hband()
        indicators[f'{prefix}keltner_lower'] = kc.keltner_channel_lband()
        indicators[f'{prefix}keltner_width'] = kc.keltner_channel_wband()

    # GROUP Y — Donchian Channels
    if only is None or 'donchian' in only:
        dc = ta.volatility.DonchianChannel(
            high=df['high'], low=df['low'], close=df['close'], window=20)
        indicators[f'{prefix}donchian_upper']  = dc.donchian_channel_hband()
        indicators[f'{prefix}donchian_lower']  = dc.donchian_channel_lband()
        indicators[f'{prefix}donchian_middle'] = dc.donchian_channel_mband()

    # GROUP Z — Aroon
    if only is None or 'aroon' in only:
        aroon = ta.trend.AroonIndicator(high=df['high'], low=df['low'], window=25)
        indicators[f'{prefix}aroon_up']        = aroon.aroon_up()
        indicators[f'{prefix}aroon_down']      = aroon.aroon_down()
        indicators[f'{prefix}aroon_indicator'] = aroon.aroon_indicator()

    # GROUP AA — Elder Ray (Bull/Bear Power)
    if only is None or 'elder_ray' in only:
        ema_13 = ta.trend.EMAIndicator(close=df['close'], window=13).ema_indicator()
        indicators[f'{prefix}bull_power'] = df['high'] - ema_13
        indicators[f'{prefix}bear_power'] = df['low']  - ema_13

    # GROUP AB — TSI
    if only is None or 'tsi' in only:
        tsi = ta.momentum.TSIIndicator(close=df['close'], window_slow=25, window_fast=13)
        indicators[f'{prefix}tsi'] = tsi.tsi()

    # GROUP AC — KST
    if only is None or 'kst' in only:
        kst_ind = ta.trend.KSTIndicator(close=df['close'])
        indicators[f'{prefix}kst']        = kst_ind.kst()
        indicators[f'{prefix}kst_signal'] = kst_ind.kst_sig()

    # GROUP AD — Ultimate Oscillator
    if only is None or 'uo' in only:
        uo = ta.momentum.UltimateOscillator(
            high=df['high'], low=df['low'], close=df['close'])
        indicators[f'{prefix}ultimate_oscillator'] = uo.ultimate_oscillator()

    # GROUP AE — Awesome Oscillator
    if only is None or 'ao' in only:
        ao = ta.momentum.AwesomeOscillatorIndicator(high=df['high'], low=df['low'])
        indicators[f'{prefix}awesome_oscillator'] = ao.awesome_oscillator()

    # GROUP AF — Mass Index
    if only is None or 'mass_index' in only:
        mi = ta.trend.MassIndex(high=df['high'], low=df['low'])
        indicators[f'{prefix}mass_index'] = mi.mass_index()

    # GROUP AG — DPO
    if only is None or 'dpo' in only:
        dpo_ind = ta.trend.DPOIndicator(close=df['close'], window=20)
        indicators[f'{prefix}dpo'] = dpo_ind.dpo()

    # GROUP AH — Standard Deviation
    if only is None or 'std_dev' in only:
        indicators[f'{prefix}std_dev_20'] = df['close'].rolling(window=20).std()
        indicators[f'{prefix}std_dev_50'] = df['close'].rolling(window=50).std()

    # WHY: bfill() fills warmup-period NaN with FUTURE values — the trader
    #      never saw those values at the time of the early trades, so
    #      training on them is a look-ahead leak. Keep ffill (which is
    #      causal — carries PAST values forward) but drop bfill. Warmup
    #      rows will remain NaN and be handled by the sentinel-based NaN
    #      fill in the downstream pipeline.
    # CHANGED: April 2026 — remove bfill leak (audit bug: indicator_utils)
    indicators = indicators.ffill()

    # Use timestamp as index so reset_index() yields a named 'timestamp' column
    indicators.index = df['timestamp']
    indicators.index.name = 'timestamp'

    # WHY (Phase 74 Fix 26): The selective compute_indicators stopped after
    #      the ~119 base indicators. It did NOT call smart_features.py to
    #      add the 50 SMART_ and 14 REGIME_ columns. Callers expecting the
    #      same coverage as compute_all_indicators + smart_features got a
    #      silently smaller feature set, causing KeyErrors downstream when
    #      SMART_ rules were applied during backtesting.
    # CHANGED: April 2026 — Phase 74 Fix 26 — call smart_features in selective path
    #          (audit Part F HIGH #26)
    try:
        from project1_reverse_engineering import smart_features as _sf
        from shared import feature_toggles as _ft
        if _ft.get_smart() or _ft.get_regime():
            indicators = _sf.compute_smart_features(indicators)
    except Exception as _e:
        # Smart features are optional — log but don't fail
        import logging as _log
        _log.getLogger(__name__).warning(
            f"[indicator_utils] smart_features unavailable in selective path: {_e}"
        )
    return indicators


def get_indicator_values_at_timestamp(indicators_df, timestamp):
    """
    Extract indicator values at a specific timestamp.

    Args:
        indicators_df: DataFrame with indicators (indexed by timestamp)
        timestamp: Timestamp to extract values for

    Returns:
        Series of indicator values at that timestamp
    """
    try:
        return indicators_df.loc[timestamp]
    except KeyError:
        # WHY: method='nearest' can return a FUTURE row when the query
        #      timestamp falls between two candles and the next candle is
        #      closer. That leaks future indicator values into the feature
        #      used for trade entry decisions (look-ahead bias).
        #      method='pad' (forward-fill) always returns the last row whose
        #      index is <= the query timestamp — no future data possible.
        #      If the query is before all rows (idx==-1) return the first row.
        # CHANGED: April 2026 — fix look-ahead bias in indicator lookup (audit CRITICAL)
        idx = indicators_df.index.get_indexer([timestamp], method='pad')[0]
        if idx == -1:
            return indicators_df.iloc[0]
        return indicators_df.iloc[idx]
