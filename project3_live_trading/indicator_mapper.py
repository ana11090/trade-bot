"""
Maps Python indicator names (from feature_matrix columns) to platform-specific code.

Supports:
  - MT5: MQL5 handle-based indicator access
  - Tradovate: Python code using pandas-ta

Each entry maps a feature pattern to:
  - mt5_handle_init: MQL5 code for OnInit() to create indicator handle
  - mt5_handle_var: MQL5 variable declaration
  - mt5_buffer_read: MQL5 CopyBuffer to read value
  - mt5_code: inline MQL5 expression (for simple price math, no handle needed)
  - tradovate_code: Python expression using pandas-ta
  - custom_indicator_mt5: True if needs a .ex5 file installed separately
  - description: human-readable explanation
"""

import re

TIMEFRAME_MAP = {
    "M5":  {"mt5": "PERIOD_M5",  "tradovate": "5"},
    "M15": {"mt5": "PERIOD_M15", "tradovate": "15"},
    "H1":  {"mt5": "PERIOD_H1",  "tradovate": "60"},
    "H4":  {"mt5": "PERIOD_H4",  "tradovate": "240"},
    "D1":  {"mt5": "PERIOD_D1",  "tradovate": "1440"},
}

# ─────────────────────────────────────────────────────────────────────────
# .EX5 INDICATOR DEPENDENCIES (Phase 21 documentation)
# ─────────────────────────────────────────────────────────────────────────
# The following indicators require .ex5 custom indicator files installed
# in the user's MT5 MQL5/Indicators/ folder. Without these files, the EA
# init fails with "INIT_FAILED" and the user must locate the .ex5 file
# from a third-party source (codebase.mql5.com or commercial vendors).
#
#   Aroon         — Aroon.ex5            (CodeBase)
#   DPO           — DPO.ex5              (Detrended Price Oscillator)
#   KST           — KST.ex5              (Know Sure Thing — Pring)
#   VPT           — VPT.ex5              (Volume Price Trend)
#   UO            — UO.ex5               (Ultimate Oscillator)
#
# These templates use iCustom(NULL,{mt5_tf},"<NAME>",...) and will return
# INVALID_HANDLE if the .ex5 is missing. The EA's existing handle init
# check catches this and aborts startup with INIT_FAILED, so the failure
# is visible — but users hitting this error need to know which file to
# install. Phase 21 added this documentation block; the underlying
# templates were not changed.
#
# All other indicators use built-in MT5 functions (iMA, iRSI, iMACD, etc.)
# and require no external dependencies.
# CHANGED: April 2026 — .ex5 dependency docs (audit LOW #31)
# ─────────────────────────────────────────────────────────────────────────

# ── Indicator pattern templates ───────────────────────────────────────────────
# Keys are regex patterns matching the indicator part (after timeframe prefix).
# {tf}, {mt5_tf}, {tv_tf} are substituted with actual timeframe values.
# {p}, {p1}, {p2}, {period}, etc. are substituted with parsed numeric params.

INDICATOR_PATTERNS = [
    # RSI
    (r"^rsi_(\d+)$", {
        "mt5_handle_var":  "int handle_rsi_{tf}_{p};",
        "mt5_handle_init": "handle_rsi_{tf}_{p} = iRSI(NULL,{mt5_tf},{p},PRICE_CLOSE); if(handle_rsi_{tf}_{p}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double val_{var} = SafeCopyBuf(handle_rsi_{tf}_{p}, 0); if(val_{var} == EMPTY_VALUE) indicatorFailed = true;",
        "tradovate_code":  "ta.rsi(df_m{tv_tf}['close'], length={p}).iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "RSI({p}) on {tf}",
    }),
    # ADX
    (r"^adx_(\d+)$", {
        "mt5_handle_var":  "int handle_adx_{tf}_{p};",
        "mt5_handle_init": "handle_adx_{tf}_{p} = iADX(NULL,{mt5_tf},{p}); if(handle_adx_{tf}_{p}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double val_{var} = SafeCopyBuf(handle_adx_{tf}_{p}, 0); if(val_{var} == EMPTY_VALUE) indicatorFailed = true;",
        "tradovate_code":  "ta.adx(df_m{tv_tf}['high'], df_m{tv_tf}['low'], df_m{tv_tf}['close'], length={p})['ADX_{p}'].iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "ADX({p}) on {tf}",
    }),
    # CCI
    (r"^cci_(\d+)$", {
        "mt5_handle_var":  "int handle_cci_{tf}_{p};",
        "mt5_handle_init": "handle_cci_{tf}_{p} = iCCI(NULL,{mt5_tf},{p},PRICE_TYPICAL); if(handle_cci_{tf}_{p}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double val_{var} = SafeCopyBuf(handle_cci_{tf}_{p}, 0); if(val_{var} == EMPTY_VALUE) indicatorFailed = true;",
        "tradovate_code":  "ta.cci(df_m{tv_tf}['high'], df_m{tv_tf}['low'], df_m{tv_tf}['close'], length={p}).iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "CCI({p}) on {tf}",
    }),
    # ATR
    (r"^atr_(\d+)$", {
        "mt5_handle_var":  "int handle_atr_{tf}_{p};",
        "mt5_handle_init": "handle_atr_{tf}_{p} = iATR(NULL,{mt5_tf},{p}); if(handle_atr_{tf}_{p}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double val_{var} = SafeCopyBuf(handle_atr_{tf}_{p}, 0); if(val_{var} == EMPTY_VALUE) indicatorFailed = true;",
        "tradovate_code":  "ta.atr(df_m{tv_tf}['high'], df_m{tv_tf}['low'], df_m{tv_tf}['close'], length={p}).iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "ATR({p}) on {tf}",
    }),
    # MACD diff
    (r"^macd_fast_diff$", {
        "mt5_handle_var":  "int handle_macd_{tf};",
        "mt5_handle_init": "handle_macd_{tf} = iMACD(NULL,{mt5_tf},12,26,9,PRICE_CLOSE); if(handle_macd_{tf}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double val_{var} = SafeCopyBuf(handle_macd_{tf}, 2); if(val_{var} == EMPTY_VALUE) indicatorFailed = true;",
        "tradovate_code":  "ta.macd(df_m{tv_tf}['close'])['MACDh_12_26_9'].iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "MACD histogram on {tf}",
    }),
    # SMA distance
    # WHY: Python formula (shared/indicator_utils.py line 58) is
    #      (close - sma) / sma * 100 — a PERCENTAGE. Old MQL5 divided
    #      by _Point which gave POINTS (~100,000× different scale).
    #      Rules trained on % would never fire in live.
    #      Tradovate also updated to match: divide by sma and ×100.
    # CHANGED: April 2026 — fix sma_distance scale mismatch (audit bug family #7)
    (r"^sma_(\d+)_distance$", {
        "mt5_code":       "(((iClose(NULL,{mt5_tf},1) - iMA(NULL,{mt5_tf},{p},0,MODE_SMA,PRICE_CLOSE,1)) / MathMax(iMA(NULL,{mt5_tf},{p},0,MODE_SMA,PRICE_CLOSE,1), 0.000001)) * 100.0)",
        "tradovate_code": "((df_m{tv_tf}['close'].iloc[-1] - ta.sma(df_m{tv_tf}['close'], length={p}).iloc[-1]) / max(ta.sma(df_m{tv_tf}['close'], length={p}).iloc[-1], 1e-6) * 100)",
        "custom_indicator_mt5": False,
        "description": "Distance from SMA({p}) as % on {tf}",
    }),
    # Bollinger Band width
    # WHY: Python ta library's bollinger_wband() returns
    #      (upper - lower) / middle × 100 — a PERCENTAGE (~1-10 typical).
    #      Old MQL5 returned raw (upper - lower) which is price units
    #      (~20-200 on XAUUSD). ~1000× scale difference.
    #      MT5 iBands: buffer 0=middle, 1=upper, 2=lower.
    # CHANGED: April 2026 — fix bb_width scale mismatch (audit bug family #7)
    (r"^bb_(\d+)_(\d+(?:\.\d+)?)_width$", {
        "mt5_handle_var":  "int handle_bb_{tf}_{p1}_{p2s};",
        "mt5_handle_init": "handle_bb_{tf}_{p1}_{p2s} = iBands(NULL,{mt5_tf},{p1},0,{p2},PRICE_CLOSE); if(handle_bb_{tf}_{p1}_{p2s}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double _tmp0 = SafeCopyBuf(handle_bb_{tf}_{p1}_{p2s}, 0); double _tmp1 = SafeCopyBuf(handle_bb_{tf}_{p1}_{p2s}, 1); double _tmp2 = SafeCopyBuf(handle_bb_{tf}_{p1}_{p2s}, 2); if(_tmp0 == EMPTY_VALUE || _tmp1 == EMPTY_VALUE || _tmp2 == EMPTY_VALUE) { indicatorFailed = true; val_{var} = 0; } else { double val_{var} = (_tmp0 > 0) ? ((_tmp1 - _tmp2) / _tmp0 * 100.0) : 0.0; }",
        "tradovate_code":  "ta.bbands(df_m{tv_tf}['close'], length={p1}, std={p2})['BBB_{p1}_{p2}_0'].iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "Bollinger Band({p1},{p2}) width as % of middle on {tf}",
    }),
    # Aroon
    (r"^aroon_(?:down|up)$", {
        "mt5_handle_var":  "int handle_aroon_{tf};",
        "mt5_handle_init": "handle_aroon_{tf} = iCustom(NULL,{mt5_tf},\"Aroon\",14); if(handle_aroon_{tf}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double val_{var} = SafeCopyBuf(handle_aroon_{tf}, 0); if(val_{var} == EMPTY_VALUE) indicatorFailed = true;",
        "tradovate_code":  "ta.aroon(df_m{tv_tf}['high'], df_m{tv_tf}['low'], length=14)['AROOND_14'].iloc[-1]",
        "custom_indicator_mt5": True,
        "description": "Aroon on {tf} (custom indicator)",
    }),
    # Bears Power
    (r"^bear_power$", {
        "mt5_handle_var":  "int handle_bears_{tf};",
        "mt5_handle_init": "handle_bears_{tf} = iBearsPower(NULL,{mt5_tf},13); if(handle_bears_{tf}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double val_{var} = SafeCopyBuf(handle_bears_{tf}, 0); if(val_{var} == EMPTY_VALUE) indicatorFailed = true;",
        "tradovate_code":  "df_m{tv_tf}['low'].iloc[-1] - ta.ema(df_m{tv_tf}['close'], length=13).iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "Bears Power on {tf}",
    }),
    # Volume change
    (r"^volume_change$", {
        "mt5_code":       "(double)(iVolume(NULL,{mt5_tf},1)-iVolume(NULL,{mt5_tf},2))/MathMax((double)iVolume(NULL,{mt5_tf},2),1.0)",
        "tradovate_code": "(df_m{tv_tf}['volume'].iloc[-1] - df_m{tv_tf}['volume'].iloc[-2]) / max(df_m{tv_tf}['volume'].iloc[-2], 1)",
        "custom_indicator_mt5": False,
        "description": "Volume change on {tf}",
    }),
    # Ultimate Oscillator
    (r"^ultimate_oscillator$", {
        "mt5_handle_var":  "int handle_uo_{tf};",
        "mt5_handle_init": "handle_uo_{tf} = iCustom(NULL,{mt5_tf},\"UltimateOscillator\",7,14,28); if(handle_uo_{tf}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double val_{var} = SafeCopyBuf(handle_uo_{tf}, 0); if(val_{var} == EMPTY_VALUE) indicatorFailed = true;",
        "tradovate_code":  "ta.uo(df_m{tv_tf}['high'], df_m{tv_tf}['low'], df_m{tv_tf}['close']).iloc[-1]",
        "custom_indicator_mt5": True,
        "description": "Ultimate Oscillator on {tf} (custom indicator)",
    }),
    # DPO
    (r"^dpo$", {
        "mt5_handle_var":  "int handle_dpo_{tf};",
        "mt5_handle_init": "handle_dpo_{tf} = iCustom(NULL,{mt5_tf},\"DPO\",20); if(handle_dpo_{tf}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double val_{var} = SafeCopyBuf(handle_dpo_{tf}, 0); if(val_{var} == EMPTY_VALUE) indicatorFailed = true;",
        "tradovate_code":  "ta.dpo(df_m{tv_tf}['close'], length=20).iloc[-1]",
        "custom_indicator_mt5": True,
        "description": "DPO(20) on {tf} (custom indicator)",
    }),
    # KST
    (r"^kst$", {
        "mt5_handle_var":  "int handle_kst_{tf};",
        "mt5_handle_init": "handle_kst_{tf} = iCustom(NULL,{mt5_tf},\"KST\",10,15,20,30); if(handle_kst_{tf}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double val_{var} = SafeCopyBuf(handle_kst_{tf}, 0); if(val_{var} == EMPTY_VALUE) indicatorFailed = true;",
        "tradovate_code":  "ta.kst(df_m{tv_tf}['close'])['KST_10_15_20_30_10_10_10_15'].iloc[-1]",
        "custom_indicator_mt5": True,
        "description": "KST on {tf} (custom indicator)",
    }),
    # VPT
    (r"^vpt$", {
        "mt5_handle_var":  "int handle_vpt_{tf};",
        "mt5_handle_init": "handle_vpt_{tf} = iCustom(NULL,{mt5_tf},\"VPT\"); if(handle_vpt_{tf}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double val_{var} = SafeCopyBuf(handle_vpt_{tf}, 0); if(val_{var} == EMPTY_VALUE) indicatorFailed = true;",
        "tradovate_code":  "ta.vpt(df_m{tv_tf}['close'], df_m{tv_tf}['volume']).iloc[-1]",
        "custom_indicator_mt5": True,
        "description": "VPT on {tf} (custom indicator)",
    }),
    # Close position in bar range
    (r"^close_position_in_range$", {
        "mt5_code":       "(iClose(NULL,{mt5_tf},1)-iLow(NULL,{mt5_tf},1))/(iHigh(NULL,{mt5_tf},1)-iLow(NULL,{mt5_tf},1)+0.000001)",
        "tradovate_code": "(df_m{tv_tf}['close'].iloc[-1]-df_m{tv_tf}['low'].iloc[-1])/max(df_m{tv_tf}['high'].iloc[-1]-df_m{tv_tf}['low'].iloc[-1],0.000001)",
        "custom_indicator_mt5": False,
        "description": "Close position in bar range on {tf}",
    }),
    # Distance from high
    # WHY: Python (indicator_utils.py line 174) computes
    #      (high - close) / close × 100 — a PERCENTAGE (~0.0-2.0 typical).
    #      Old MQL5 divided by _Point → POINTS (~0-2000 on XAUUSD).
    #      ~100,000× scale difference.
    # CHANGED: April 2026 — fix distance_from_high scale (audit bug family #7)
    (r"^distance_from_high$", {
        "mt5_code":       "((iHigh(NULL,{mt5_tf},1)-iClose(NULL,{mt5_tf},1)) / MathMax(iClose(NULL,{mt5_tf},1), 0.000001) * 100.0)",
        "tradovate_code": "((df_m{tv_tf}['high'].iloc[-1] - df_m{tv_tf}['close'].iloc[-1]) / max(df_m{tv_tf}['close'].iloc[-1], 1e-6) * 100)",
        "custom_indicator_mt5": False,
        "description": "Distance from bar high as % on {tf}",
    }),
    # Fibonacci distance
    (r"^distance_to_fib_(\d+)$", {
        "mt5_handle_var":  "int handle_fib_{tf}_{p};",
        "mt5_handle_init": "// Fibonacci level {p} — computed from recent swing high/low\n   // handle_fib_{tf}_{p} not available as built-in; computed inline",
        "mt5_code":       "compute_fib_distance_{tf}_{p}()",
        "tradovate_code": "compute_fib_distance(df_m{tv_tf}, {p})",
        "custom_indicator_mt5": True,
        "description": "Distance to Fibonacci {p} level on {tf} (custom)",
    }),

    # ── Rate of Change ────────────────────────────────────────────────────
    # WHY: roc_1 = percentage price change over N bars.
    #      H4_roc_1 = (close - close[1]) / close[1] * 100
    #      Used heavily in discovered rules for momentum detection.
    #      iMomentum returns 100-based (100 = no change), subtract 100 for % change.
    # CHANGED: April 2026 — critical missing indicator
    (r"^roc_(\d+)$", {
        "mt5_handle_var":  "int handle_mom_{tf}_{p};",
        "mt5_handle_init": "handle_mom_{tf}_{p} = iMomentum(NULL,{mt5_tf},{p},PRICE_CLOSE); if(handle_mom_{tf}_{p}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": (
            "double _tmp = SafeCopyBuf(handle_mom_{tf}_{p}, 0); "
            "if(_tmp == EMPTY_VALUE) { indicatorFailed = true; val_{var} = 0; } "
            "else { double val_{var} = (_tmp - 100.0);  "
            "// iMomentum returns 100-based, subtract 100 to match Python roc % change"
        ),
        "tradovate_code":  "ta.roc(df_m{tv_tf}['close'], length={p}).iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "Rate of Change ({p}) on {tf}",
    }),

    # ── EMA (standard) ───────────────────────────────────────────────────
    # WHY: EMA is a core indicator. Used for crossovers and trend detection.
    # CHANGED: April 2026 — add EMA pattern
    (r"^ema_(\d+)$", {
        "mt5_handle_var":  "int handle_ema_{tf}_{p};",
        "mt5_handle_init": "handle_ema_{tf}_{p} = iMA(NULL,{mt5_tf},{p},0,MODE_EMA,PRICE_CLOSE); if(handle_ema_{tf}_{p}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double val_{var} = SafeCopyBuf(handle_ema_{tf}_{p}, 0); if(val_{var} == EMPTY_VALUE) indicatorFailed = true;",
        "tradovate_code":  "ta.ema(df_m{tv_tf}['close'], length={p}).iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "EMA({p}) on {tf}",
    }),

    # ── EMA distance (price distance from EMA as % of price) ─────────────
    # WHY: ema_9_distance = (close - EMA(9)) / close * 100
    #      Positive = price above EMA (bullish), negative = below (bearish).
    #      Used in rules to detect how far price has moved from the trend line.
    # CHANGED: April 2026 — critical missing indicator
    (r"^ema_(\d+)_distance$", {
        "mt5_handle_var":  "int handle_ema_{tf}_{p};",
        "mt5_handle_init": "handle_ema_{tf}_{p} = iMA(NULL,{mt5_tf},{p},0,MODE_EMA,PRICE_CLOSE); if(handle_ema_{tf}_{p}==INVALID_HANDLE) return(INIT_FAILED);",
        # WHY: Old code used iClose(tf, 0) = current forming bar. Python
        #      training reads from candle_idx - 1 (last CLOSED candle,
        #      see step1_align_price.py Phase 3 fix). MT5 must use
        #      iClose(tf, 1) to match. Same for iHigh/iLow shifts.
        # CHANGED: April 2026 — fix current-bar look-ahead (audit HIGH #29)
        "mt5_buffer_read": (
            "double _tmp_buf = SafeCopyBuf(handle_ema_{tf}_{p}, 0); "
            "if(_tmp_buf == EMPTY_VALUE) { indicatorFailed = true; val_{var} = 0; } "
            "else { double _ema_val_{tf}_{p} = _tmp_buf; "
            "double _close_{tf} = iClose(NULL,{mt5_tf},1); "
            "double val_{var} = (_close_{tf} > 0) ? (_close_{tf} - _ema_val_{tf}_{p}) / _close_{tf} * 100.0 : 0.0; }"
        ),
        "tradovate_code":  "(df_m{tv_tf}['close'].iloc[-1] - ta.ema(df_m{tv_tf}['close'], length={p}).iloc[-1]) / df_m{tv_tf}['close'].iloc[-1] * 100",
        "custom_indicator_mt5": False,
        "description": "Distance from EMA({p}) as % of price on {tf}",
    }),

    # ── EMA above (boolean: ema_fast > ema_slow) ─────────────────────────
    (r"^ema_(\d+)_above_(\d+)$", {
        "mt5_handle_var":  "int handle_ema_{tf}_{p1}; int handle_ema_{tf}_{p2};",
        "mt5_handle_init": (
            "handle_ema_{tf}_{p1} = iMA(NULL,{mt5_tf},{p1},0,MODE_EMA,PRICE_CLOSE); "
            "handle_ema_{tf}_{p2} = iMA(NULL,{mt5_tf},{p2},0,MODE_EMA,PRICE_CLOSE); "
            "if(handle_ema_{tf}_{p1}==INVALID_HANDLE || handle_ema_{tf}_{p2}==INVALID_HANDLE) return(INIT_FAILED);"
        ),
        "mt5_buffer_read": (
            "double _tmp1 = SafeCopyBuf(handle_ema_{tf}_{p1}, 0); "
            "double _tmp2 = SafeCopyBuf(handle_ema_{tf}_{p2}, 0); "
            "if(_tmp1 == EMPTY_VALUE || _tmp2 == EMPTY_VALUE) { indicatorFailed = true; val_{var} = 0; } "
            "else { double val_{var} = (_tmp1 > _tmp2) ? 1.0 : 0.0; }"
        ),
        "tradovate_code":  "1.0 if ta.ema(df_m{tv_tf}['close'],{p1}).iloc[-1] > ta.ema(df_m{tv_tf}['close'],{p2}).iloc[-1] else 0.0",
        "custom_indicator_mt5": False,
        "description": "EMA({p1}) above EMA({p2}) on {tf} (1=yes, 0=no)",
    }),

    # ── Distance to swing low/high ────────────────────────────────────────
    # WHY: distance_to_swing_low = (close - lowest_low_20) / close * 100
    #      Measures how far price is from the recent swing low.
    #      Used to detect oversold conditions or breakout potential.
    # CHANGED: April 2026 — critical missing indicator
    (r"^distance_to_swing_low$", {
        "mt5_handle_var":  "",
        "mt5_handle_init": "",
        # WHY: iLowest starts at shift 0 meaning "from the current
        #      forming bar back 20 bars". Must start at shift 1 to
        #      only look at closed bars. iClose must also use shift 1
        #      to match Python training. See Fix 1A for full reasoning.
        # CHANGED: April 2026 — fix current-bar look-ahead (audit HIGH #29)
        "mt5_buffer_read": (
            "int _sw_low_idx_{tf} = iLowest(NULL,{mt5_tf},MODE_LOW,20,1); "
            "double _sw_low_{tf} = iLow(NULL,{mt5_tf},_sw_low_idx_{tf}); "
            "double _cl_sw_{tf} = iClose(NULL,{mt5_tf},1); "
            "double val_{var} = (_cl_sw_{tf} > 0) ? (_cl_sw_{tf} - _sw_low_{tf}) / _cl_sw_{tf} * 100.0 : 0.0;"
        ),
        "tradovate_code":  "(df_m{tv_tf}['close'].iloc[-1] - df_m{tv_tf}['low'].rolling(20).min().iloc[-1]) / df_m{tv_tf}['close'].iloc[-1] * 100",
        "custom_indicator_mt5": False,
        "description": "Distance from 20-bar swing low as % on {tf}",
    }),

    (r"^distance_to_swing_high$", {
        "mt5_handle_var":  "",
        "mt5_handle_init": "",
        # CHANGED: April 2026 — fix current-bar look-ahead (audit HIGH #29)
        "mt5_buffer_read": (
            "int _sw_high_idx_{tf} = iHighest(NULL,{mt5_tf},MODE_HIGH,20,1); "
            "double _sw_high_{tf} = iHigh(NULL,{mt5_tf},_sw_high_idx_{tf}); "
            "double _cl_swh_{tf} = iClose(NULL,{mt5_tf},1); "
            "double val_{var} = (_cl_swh_{tf} > 0) ? (_sw_high_{tf} - _cl_swh_{tf}) / _cl_swh_{tf} * 100.0 : 0.0;"
        ),
        "tradovate_code":  "(df_m{tv_tf}['high'].rolling(20).max().iloc[-1] - df_m{tv_tf}['close'].iloc[-1]) / df_m{tv_tf}['close'].iloc[-1] * 100",
        "custom_indicator_mt5": False,
        "description": "Distance from 20-bar swing high as % on {tf}",
    }),

    # ── Position in swing range ───────────────────────────────────────────
    # WHY: 0.0 = at swing low, 1.0 = at swing high.
    (r"^position_in_swing_range$", {
        "mt5_handle_var":  "",
        "mt5_handle_init": "",
        # CHANGED: April 2026 — fix current-bar look-ahead (audit HIGH #29)
        "mt5_buffer_read": (
            "int _psr_lo_idx_{tf} = iLowest(NULL,{mt5_tf},MODE_LOW,20,1); "
            "int _psr_hi_idx_{tf} = iHighest(NULL,{mt5_tf},MODE_HIGH,20,1); "
            "double _psr_lo_{tf} = iLow(NULL,{mt5_tf},_psr_lo_idx_{tf}); "
            "double _psr_hi_{tf} = iHigh(NULL,{mt5_tf},_psr_hi_idx_{tf}); "
            "double _psr_range_{tf} = _psr_hi_{tf} - _psr_lo_{tf}; "
            "double val_{var} = (_psr_range_{tf} > 0) ? (iClose(NULL,{mt5_tf},1) - _psr_lo_{tf}) / _psr_range_{tf} : 0.5;"
        ),
        "tradovate_code":  "((df_m{tv_tf}['close'].iloc[-1] - df_m{tv_tf}['low'].rolling(20).min().iloc[-1]) / max(df_m{tv_tf}['high'].rolling(20).max().iloc[-1] - df_m{tv_tf}['low'].rolling(20).min().iloc[-1], 0.000001))",
        "custom_indicator_mt5": False,
        "description": "Position in 20-bar swing range (0=low, 1=high) on {tf}",
    }),

    # ── Stochastic %K ─────────────────────────────────────────────────────
    (r"^stoch_(\d+)_k$", {
        "mt5_handle_var":  "int handle_stoch_{tf}_{p};",
        "mt5_handle_init": "handle_stoch_{tf}_{p} = iStochastic(NULL,{mt5_tf},{p},3,3,MODE_SMA,STO_LOWHIGH); if(handle_stoch_{tf}_{p}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double val_{var} = SafeCopyBuf(handle_stoch_{tf}_{p}, 0); if(val_{var} == EMPTY_VALUE) indicatorFailed = true;",
        "tradovate_code":  "ta.stoch(df_m{tv_tf}['high'],df_m{tv_tf}['low'],df_m{tv_tf}['close'],k={p})['STOCHk_{p}_3_3'].iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "Stochastic %K({p}) on {tf}",
    }),

    # ── Williams %R ───────────────────────────────────────────────────────
    (r"^williams_r_(\d+)$", {
        "mt5_handle_var":  "int handle_wpr_{tf}_{p};",
        "mt5_handle_init": "handle_wpr_{tf}_{p} = iWPR(NULL,{mt5_tf},{p}); if(handle_wpr_{tf}_{p}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double val_{var} = SafeCopyBuf(handle_wpr_{tf}_{p}, 0); if(val_{var} == EMPTY_VALUE) indicatorFailed = true;",
        "tradovate_code":  "ta.willr(df_m{tv_tf}['high'],df_m{tv_tf}['low'],df_m{tv_tf}['close'],length={p}).iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "Williams %R({p}) on {tf}",
    }),

    # ── Standard Deviation ────────────────────────────────────────────────
    # WHY: Python pandas .rolling().std() uses ddof=1 (sample std,
    #      divisor N-1). MT5 iStdDev uses ddof=0 (population, divisor N).
    #      Ratio: sqrt(N / (N-1)). At N=14 that's 1.037 (3.7% bigger
    #      in Python). Multiply MQL5 value by this ratio to match.
    # CHANGED: April 2026 — fix std_dev ddof mismatch (audit bug family #7)
    (r"^std_dev_(\d+)$", {
        "mt5_handle_var":  "int handle_std_{tf}_{p};",
        "mt5_handle_init": "handle_std_{tf}_{p} = iStdDev(NULL,{mt5_tf},{p},0,MODE_SMA,PRICE_CLOSE); if(handle_std_{tf}_{p}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double _raw_{var} = SafeCopyBuf(handle_std_{tf}_{p}, 0); if(_raw_{var} == EMPTY_VALUE) { indicatorFailed = true; val_{var} = 0; } else { double val_{var} = _raw_{var} * MathSqrt((double){p} / MathMax((double){p} - 1.0, 1.0)); }",
        "tradovate_code":  "df_m{tv_tf}['close'].rolling({p}).std().iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "Standard Deviation({p}) on {tf} (ddof=1 to match Python)",
    }),

    # ── Keltner Channel width ─────────────────────────────────────────────
    # WHY: Python ta.keltner_channel_wband() returns
    #      (upper - lower) / middle × 100 — a PERCENTAGE of middle line.
    #      Old MQL5 returned raw (2 × ATR) in price units (~1000× different).
    #      Keltner middle = EMA(20), so we normalize by EMA.
    # CHANGED: April 2026 — fix keltner_width scale (audit bug family #7)
    (r"^keltner_width$", {
        "mt5_handle_var":  "int handle_ema_{tf}_20_kc; int handle_atr_{tf}_10_kc;",
        "mt5_handle_init": (
            "handle_ema_{tf}_20_kc = iMA(NULL,{mt5_tf},20,0,MODE_EMA,PRICE_CLOSE); "
            "handle_atr_{tf}_10_kc = iATR(NULL,{mt5_tf},10); "
            "if(handle_ema_{tf}_20_kc==INVALID_HANDLE || handle_atr_{tf}_10_kc==INVALID_HANDLE) return(INIT_FAILED);"
        ),
        "mt5_buffer_read": (
            "double _tmp_ema = SafeCopyBuf(handle_ema_{tf}_20_kc, 0); "
            "double _tmp_atr = SafeCopyBuf(handle_atr_{tf}_10_kc, 0); "
            "if(_tmp_ema == EMPTY_VALUE || _tmp_atr == EMPTY_VALUE || _tmp_ema <= 0) { indicatorFailed = true; val_{var} = 0; } "
            "else { double val_{var} = (_tmp_atr * 4.0) / _tmp_ema * 100.0; }  "
            "// Keltner width % = (upper - lower) / middle × 100 = (4 × ATR) / EMA × 100"
        ),
        "tradovate_code":  "((ta.atr(df_m{tv_tf}['high'],df_m{tv_tf}['low'],df_m{tv_tf}['close'],10).iloc[-1] * 4) / max(ta.ema(df_m{tv_tf}['close'],20).iloc[-1], 1e-6) * 100)",
        "custom_indicator_mt5": False,
        "description": "Keltner Channel width as % of middle on {tf}",
    }),
]

# ── SMART feature formulas ────────────────────────────────────────────────────
# Each value is a formula dict interpreted by _generate_smart_mql().
SMART_FORMULAS = {
    # Inter-TF Divergences
    'SMART_rsi_h4_minus_h1':    {'type': 'diff',       'a': 'H4_rsi_14',          'b': 'H1_rsi_14'},
    'SMART_rsi_h1_minus_m15':   {'type': 'diff',       'a': 'H1_rsi_14',          'b': 'M15_rsi_14'},
    'SMART_rsi_h4_minus_m15':   {'type': 'diff',       'a': 'H4_rsi_14',          'b': 'M15_rsi_14'},
    'SMART_adx_h4_minus_h1':    {'type': 'diff',       'a': 'H4_adx_14',          'b': 'H1_adx_14'},
    'SMART_above_ema200_count':  {'type': 'count_gt',  'cols': ['H1_ema_200_distance','H4_ema_200_distance','D1_ema_200_distance'], 'threshold': 0},
    'SMART_macd_agree':          {'type': 'macd_agree','a': 'H1_macd_fast_diff',   'b': 'H4_macd_fast_diff'},
    # Indicator Dynamics
    'SMART_H1_rsi_14_direction':         {'type': 'direction', 'col': 'H1_rsi_14'},
    'SMART_H1_rsi_14_accel':             {'type': 'accel',     'col': 'H1_rsi_14'},
    'SMART_H4_adx_14_direction':         {'type': 'direction', 'col': 'H4_adx_14'},
    'SMART_H4_adx_14_accel':             {'type': 'accel',     'col': 'H4_adx_14'},
    'SMART_H1_atr_14_direction':         {'type': 'direction', 'col': 'H1_atr_14'},
    'SMART_H1_atr_14_accel':             {'type': 'accel',     'col': 'H1_atr_14'},
    'SMART_H1_macd_fast_diff_direction': {'type': 'direction', 'col': 'H1_macd_fast_diff'},
    'SMART_H1_macd_fast_diff_accel':     {'type': 'accel',     'col': 'H1_macd_fast_diff'},
    'SMART_H4_rsi_14_direction':         {'type': 'direction', 'col': 'H4_rsi_14'},
    'SMART_H4_rsi_14_accel':             {'type': 'accel',     'col': 'H4_rsi_14'},
    'SMART_H1_cci_14_direction':         {'type': 'direction', 'col': 'H1_cci_14'},
    'SMART_H1_cci_14_accel':             {'type': 'accel',     'col': 'H1_cci_14'},
    'SMART_H1_bb_20_2_width_direction':  {'type': 'direction', 'col': 'H1_bb_20_2_width'},
    'SMART_H1_bb_20_2_width_accel':      {'type': 'accel',     'col': 'H1_bb_20_2_width'},
    'SMART_atr_expansion':               {'type': 'ratio_safe','num': 'H1_atr_14', 'den': 'H1_atr_50'},
    # TF Alignment Scores
    'SMART_rsi_bullish_tfs': {'type': 'count_gt',  'cols': ['M5_rsi_14','M15_rsi_14','H1_rsi_14','H4_rsi_14','D1_rsi_14'], 'threshold': 50},
    'SMART_trending_tfs':    {'type': 'count_gt',  'cols': ['M5_adx_14','M15_adx_14','H1_adx_14','H4_adx_14','D1_adx_14'], 'threshold': 25},
    'SMART_ema_bullish_tfs': {'type': 'count_sum', 'cols': ['M15_ema_9_above_20','H1_ema_9_above_20','H4_ema_9_above_20']},
    # Session Intelligence
    'SMART_is_london_ny_overlap': {'type': 'time_range', 'lo': 13, 'hi': 16},
    'SMART_is_early_london':      {'type': 'time_range', 'lo': 7,  'hi': 9},
    'SMART_is_late_ny':           {'type': 'time_range', 'lo': 19, 'hi': 21},
    'SMART_is_asian_dead_zone':   {'type': 'time_range', 'lo': 3,  'hi': 5},
    'SMART_is_pre_london':        {'type': 'time_range', 'lo': 6,  'hi': 7},
    'SMART_is_pre_ny':            {'type': 'time_range', 'lo': 12, 'hi': 13},
    'SMART_hours_since_london':   {'type': 'time_since', 'open_hour': 7},
    'SMART_hours_since_ny':       {'type': 'time_since', 'open_hour': 13},
    'SMART_active_sessions':      {'type': 'count_sessions'},
    # Calendar / Fundamentals
    'SMART_is_monday':            {'type': 'cal_dow_eq',    'value': 0},
    'SMART_is_friday':            {'type': 'cal_dow_eq',    'value': 4},
    'SMART_is_midweek':           {'type': 'cal_dow_range', 'lo': 1, 'hi': 3},
    'SMART_is_month_start':       {'type': 'cal_dom_le',    'value': 3},
    'SMART_is_month_end':         {'type': 'cal_dom_ge',    'value': 27},
    'SMART_is_nfp_friday':        {'type': 'cal_nfp'},
    'SMART_is_quarter_end_month': {'type': 'cal_quarter_end'},
    'SMART_week_of_month':        {'type': 'cal_week_of_month'},
    # Volatility Regimes
    'SMART_bb_squeeze':    {'type': 'bb_vs_keltner'},
    'SMART_atr_vs_long':   {'type': 'ratio_safe', 'num': 'H1_atr_14',       'den': 'H1_atr_100'},
    'SMART_vol_expanding': {'type': 'compare',    'a': 'H4_atr_14', 'op': '>', 'b': 'H4_atr_50'},
    'SMART_std_ratio':     {'type': 'ratio_safe', 'num': 'H1_std_dev_20',    'den': 'H1_std_dev_50'},
    # Price Action Patterns
    'SMART_dist_to_round_50':   {'type': 'price_mod',     'col': 'H1_pivot_point', 'modulo': 50,  'half': 25},
    'SMART_dist_to_round_100':  {'type': 'price_mod',     'col': 'H1_pivot_point', 'modulo': 100, 'half': 50},
    'SMART_daily_range_used':   {'type': 'ratio_safe',    'num': 'H1_candle_range','den': 'D1_atr_14'},
    'SMART_strong_candle':      {'type': 'compare_const', 'a': 'H1_body_to_range_ratio',     'op': '>',  'value': 0.7},
    'SMART_indecision_candle':  {'type': 'compare_const', 'a': 'H1_body_to_range_ratio',     'op': '<',  'value': 0.3},
    'SMART_near_swing_high':    {'type': 'compare_const', 'a': 'H1_position_in_swing_range', 'op': '>',  'value': 0.8},
    'SMART_near_swing_low':     {'type': 'compare_const', 'a': 'H1_position_in_swing_range', 'op': '<',  'value': 0.2},
    'SMART_swing_pos_h4_vs_h1': {'type': 'diff',          'a': 'H4_position_in_swing_range', 'b': 'H1_position_in_swing_range'},
    # Momentum Quality
    'SMART_rsi_zone':            {'type': 'rsi_zone',       'col': 'H1_rsi_14'},
    'SMART_rsi_crossed_50_up':   {'type': 'crossed_above',  'col': 'H1_rsi_14', 'threshold': 50},
    'SMART_rsi_crossed_50_down': {'type': 'crossed_below',  'col': 'H1_rsi_14', 'threshold': 50},
    'SMART_macd_normalized':     {'type': 'ratio_safe',     'num': 'H1_macd_fast_diff', 'den': 'H1_atr_14'},
    'SMART_stoch_overbought':    {'type': 'compare_const',  'a': 'H1_stoch_14_k',    'op': '>', 'value': 80},
    'SMART_stoch_oversold':      {'type': 'compare_const',  'a': 'H1_stoch_14_k',    'op': '<', 'value': 20},
    'SMART_willr_extreme_high':  {'type': 'compare_const',  'a': 'H1_williams_r_14', 'op': '>', 'value': -20},
    'SMART_willr_extreme_low':   {'type': 'compare_const',  'a': 'H1_williams_r_14', 'op': '<', 'value': -80},
    # WHY (Phase 60 Fix 4): SMART_tsi_bullish and SMART_tsi_strong reference
    #      H1_tsi which is NOT implementable as a built-in MT5 indicator
    #      (TSI requires double-EMA of momentum — no iCustom-free solution).
    #      The indicator_mapper generates a loud Print() error and returns 0.
    #      Tag these entries so callers can detect TSI usage before generating EAs.
    # CHANGED: April 2026 — Phase 60 Fix 4 — TSI explicitly flagged unsupported
    #          (audit Part D HIGH #11)
    'SMART_tsi_bullish':         {'type': 'compare_const',  'a': 'H1_tsi', 'op': '>', 'value': 0,
                                  'live_unsupported': True, 'live_note': 'TSI not in MT5 built-ins'},
    'SMART_tsi_strong':          {'type': 'compare_const_abs', 'a': 'H1_tsi', 'op': '>', 'value': 20,
                                  'live_unsupported': True, 'live_note': 'TSI not in MT5 built-ins'},
}

# ── REGIME feature formulas ───────────────────────────────────────────────────
REGIME_FORMULAS = {
    'REGIME_atr_pct_of_price':    {'type': 'ratio_safe_price',     'num': 'H1_atr_14',              'scale': 100},
    'REGIME_h4_atr_pct':          {'type': 'ratio_safe_price',     'num': 'H4_atr_14',              'scale': 100},
    'REGIME_d1_atr_pct':          {'type': 'ratio_safe_price',     'num': 'D1_atr_14',              'scale': 100},
    'REGIME_bb_width_pct':        {'type': 'ratio_safe_price',     'num': 'H1_bb_20_2_width',       'scale': 100},
    'REGIME_keltner_width_pct':   {'type': 'ratio_safe_price',     'num': 'H1_keltner_width',       'scale': 100},
    'REGIME_daily_range_pct':     {'type': 'ratio_safe_price',     'num': 'D1_atr_14',              'scale': 100},
    'REGIME_std_dev_pct':         {'type': 'ratio_safe_price',     'num': 'H1_std_dev_20',          'scale': 100},
    'REGIME_h4_std_pct':          {'type': 'ratio_safe_price',     'num': 'H4_std_dev_20',          'scale': 100},
    'REGIME_swing_height_pct_h1': {'type': 'ratio_safe_price',     'num': 'H1_atr_50',              'scale': 100},
    'REGIME_swing_height_pct_h4': {'type': 'ratio_safe_price',     'num': 'H4_atr_50',              'scale': 100},
    'REGIME_pivot_dist_pct':      {'type': 'ratio_safe_price_abs', 'num': 'H1_pivot_point_distance','scale': 100},
    'REGIME_price_bucket':        {'type': 'price_bucket'},
    # WHY (Phase 77 Fix IM-2): Training side uses rolling mean price (Phase 43).
    #      Live EA side still hardcoded 2000 (XAUUSD-specific). For EURUSD
    #      price is always < 2 so live EA always returns 0; for BTC always 1.
    #      Add note — the live formula must be calibrated per-instrument.
    #      Mark as live_unsupported for non-XAUUSD until per-symbol thresholds
    #      are implemented.
    # CHANGED: April 2026 — Phase 77 Fix IM-2 — flag as instrument-specific
    'REGIME_is_high_price_era':   {'type': 'price_gt', 'value': 2000,
                                   'live_note': 'threshold 2000 is XAUUSD-specific; '
                                                'calibrate for other instruments'},
    # WHY (Phase 77 Fix IM-1): Phase 76 Fix 28 removed roc_1 from indicator
    #      computation (it's pure noise). Update REGIME_FORMULAS to match —
    #      only count roc_20 and roc_50 for the alignment score.
    # CHANGED: April 2026 — Phase 77 Fix IM-1 — remove roc_1 from REGIME_roc_alignment
    'REGIME_roc_alignment':       {'type': 'count_gt',             'cols': ['H1_roc_20','H1_roc_50'], 'threshold': 0},
}


# ── Sub-feature expression helpers ────────────────────────────────────────────

def _mql5_sub_expr(feat_name, uid=''):
    """
    Returns (setup_lines, val_expr) for reading a base indicator value inline.
    Uses iRSI()/iADX()/etc. — MQL5 caches handles on repeated calls with same params.
    uid: unique suffix to avoid variable name conflicts when calling multiple times.
    """
    parsed = parse_feature_name(feat_name)
    tf = parsed['timeframe']
    ind = parsed['indicator']
    params = parsed['params']

    tf_info = TIMEFRAME_MAP.get(tf, TIMEFRAME_MAP['H1'])
    mt5_tf  = tf_info['mt5']
    p   = params[0] if params else '14'
    p1  = params[0] if len(params) > 0 else '20'
    p2  = params[1] if len(params) > 1 else '2'
    buf = f'_sb_{re.sub(r"[^a-zA-Z0-9]", "_", feat_name).lower()}{uid}'

    # RSI
    if re.match(r'^rsi_\d+$', ind):
        return ([f'double {buf}[1]; CopyBuffer(iRSI(NULL,{mt5_tf},{p},PRICE_CLOSE),0,0,1,{buf});'],
                f'{buf}[0]')
    # ADX
    if re.match(r'^adx_\d+$', ind):
        return ([f'double {buf}[1]; CopyBuffer(iADX(NULL,{mt5_tf},{p}),0,0,1,{buf});'],
                f'{buf}[0]')
    # ATR (atr_14, atr_50, atr_100 etc.)
    if re.match(r'^atr_\d+$', ind):
        return ([f'double {buf}[1]; CopyBuffer(iATR(NULL,{mt5_tf},{p}),0,0,1,{buf});'],
                f'{buf}[0]')
    # MACD histogram
    if ind == 'macd_fast_diff':
        return ([f'double {buf}[1]; CopyBuffer(iMACD(NULL,{mt5_tf},12,26,9,PRICE_CLOSE),2,0,1,{buf});'],
                f'{buf}[0]')
    # BB width
    if re.match(r'^bb_\d+_[\d.]+_width$', ind):
        return ([f'double {buf}u[1],{buf}l[1]; '
                 f'CopyBuffer(iBands(NULL,{mt5_tf},{p1},0,{p2},PRICE_CLOSE),1,0,1,{buf}u); '
                 f'CopyBuffer(iBands(NULL,{mt5_tf},{p1},0,{p2},PRICE_CLOSE),2,0,1,{buf}l);'],
                f'({buf}u[0]-{buf}l[0])')
    # Keltner width ≈ 2 × ATR(20) (standard Keltner Channel formula)
    if ind == 'keltner_width':
        return ([f'double {buf}[1]; CopyBuffer(iATR(NULL,{mt5_tf},20),0,0,1,{buf});'],
                f'(2.0*{buf}[0])')
    # EMA distance from close
    if re.match(r'^ema_(\d+)_distance$', ind):
        # WHY: Old sub-expr used MODE_SMA (wrong — should be EMA) and /_Point
        #      which gives a POINTS value (~100k× different from % on XAUUSD).
        #      Matches the mt5_buffer_read template that divides by close ×100.
        # CHANGED: April 2026 — fix ema_distance MODE and scale (audit HIGH)
        return ([],
                f'((iClose(NULL,{mt5_tf},1)-iMA(NULL,{mt5_tf},{p},0,MODE_EMA,PRICE_CLOSE,1))/MathMax(iClose(NULL,{mt5_tf},1),0.000001)*100.0)')
    # EMA9 above EMA20 (binary)
    if re.match(r'^ema_(\d+)_above_(\d+)$', ind):
        return ([],
                f'(iMA(NULL,{mt5_tf},{p1},0,MODE_EMA,PRICE_CLOSE,1)>iMA(NULL,{mt5_tf},{p2},0,MODE_EMA,PRICE_CLOSE,1)?1.0:0.0)')
    # Standard deviation
    if re.match(r'^std_dev_\d+$', ind):
        return ([f'double {buf}[1]; CopyBuffer(iStdDev(NULL,{mt5_tf},{p},0,MODE_SMA,PRICE_CLOSE),0,0,1,{buf});'],
                f'{buf}[0]')
    # Stochastic %K
    if re.match(r'^stoch_(\d+)_k$', ind):
        return ([f'double {buf}[1]; CopyBuffer(iStochastic(NULL,{mt5_tf},{p},3,3,MODE_SMA,STO_LOWHIGH),0,0,1,{buf});'],
                f'{buf}[0]')
    # Williams %R
    if re.match(r'^williams_r_\d+$', ind):
        return ([f'double {buf}[1]; CopyBuffer(iWPR(NULL,{mt5_tf},{p}),0,0,1,{buf});'],
                f'{buf}[0]')
    # CCI
    if re.match(r'^cci_\d+$', ind):
        return ([f'double {buf}[1]; CopyBuffer(iCCI(NULL,{mt5_tf},{p},PRICE_TYPICAL),0,0,1,{buf});'],
                f'{buf}[0]')
    # Candle range (H - L)
    if ind == 'candle_range':
        return ([], f'(iHigh(NULL,{mt5_tf},1)-iLow(NULL,{mt5_tf},1))')
    # Body-to-range ratio
    if ind == 'body_to_range_ratio':
        return ([], f'(MathAbs(iClose(NULL,{mt5_tf},1)-iOpen(NULL,{mt5_tf},1))/MathMax(iHigh(NULL,{mt5_tf},1)-iLow(NULL,{mt5_tf},1),0.000001))')
    # WHY: Python pivot uses previous bar of CURRENT timeframe
    #      ((prev_high + prev_low + prev_close) / 3 via .shift(1)).
    #      Old MQL5 hardcoded PERIOD_D1 which meant an H1 feature
    #      would read YESTERDAY's D1 bar — completely different value.
    # CHANGED: April 2026 — fix pivot_point timeframe (audit bug family #7)
    if ind == 'pivot_point':
        return ([], f'((iHigh(NULL,{mt5_tf},1)+iLow(NULL,{mt5_tf},1)+iClose(NULL,{mt5_tf},1))/3.0)')
    # Pivot-point distance (current close − pivot)
    # WHY: Old expression mixed shift 0 (current forming bar) for the
    #      close with shift 1 (last closed) for the pivot components.
    #      Now uses shift 1 everywhere — current close is the last
    #      closed bar's close, pivot is computed from the same bar.
    #      Matches Python's candle_idx-1 training convention.
    # CHANGED: April 2026 — consistent shift-1 everywhere (audit HIGH #29)
    if ind == 'pivot_point_distance':
        return ([], f'(iClose(NULL,{mt5_tf},1)-(iHigh(NULL,{mt5_tf},1)+iLow(NULL,{mt5_tf},1)+iClose(NULL,{mt5_tf},1))/3.0)')
    # ROC (rate of change)
    if re.match(r'^roc_\d+$', ind):
        # WHY: ROC is defined as percent change × 100. Old expr divided but
        #      did not multiply by 100, producing a fraction (0.001–0.02) instead
        #      of a percentage (0.1–2.0). Models trained on % values would never
        #      fire on the live fractional values.
        # CHANGED: April 2026 — fix roc sub-expr missing ×100 (audit HIGH)
        n = int(p) + 1
        return ([], f'((iClose(NULL,{mt5_tf},1)-iClose(NULL,{mt5_tf},{n}))/MathMax(iClose(NULL,{mt5_tf},{n}),0.001)*100.0)')
    # Position in swing range (20-bar rolling min/max)
    if ind == 'position_in_swing_range':
        lo_buf = f'{buf}lo'; hi_buf = f'{buf}hi'
        lines = [
            f'double {lo_buf}=iClose(NULL,{mt5_tf},1); double {hi_buf}=iClose(NULL,{mt5_tf},1);',
            f'for(int _si=1;_si<20;_si++){{double _sc=iClose(NULL,{mt5_tf},_si);if(_sc<{lo_buf}){lo_buf}=_sc;if(_sc>{hi_buf}){hi_buf}=_sc;}}',
        ]
        return (lines, f'((iClose(NULL,{mt5_tf},1)-{lo_buf})/MathMax({hi_buf}-{lo_buf},0.000001))')
    # WHY: TSI (True Strength Index) requires double EMA of momentum which MT5 lacks as built-in.
    #      Inline computation is complex (25+ lines with nested loops). Risk of subtle bugs.
    #      Instead of silently returning 0, FAIL LOUD so user knows TSI features are broken.
    # CHANGED: April 2026 — make TSI failure loud instead of silent (audit bug family #7, FIX 8 Option B)
    if ind == 'tsi':
        return ([
            f'Print("ERROR: TSI indicator not implemented for feature {feat_name}");',
            f'Print("FIX: Either remove TSI features from rules OR implement custom TSI indicator");',
            f'indicatorFailed = true;'
        ], '0.0')
    # Unknown
    return ([f'// TODO: {feat_name} — unknown sub-feature'], '0.0')


def _py_sub_expr(feat_name):
    """Returns a Python/Tradovate expression for reading a base indicator value."""
    parsed = parse_feature_name(feat_name)
    tf    = parsed['timeframe']
    ind   = parsed['indicator']
    params = parsed['params']

    tf_info = TIMEFRAME_MAP.get(tf, TIMEFRAME_MAP['H1'])
    tv_tf = tf_info['tradovate']
    p   = params[0] if params else '14'
    p1  = params[0] if len(params) > 0 else '20'
    p2  = params[1] if len(params) > 1 else '2'
    df  = f"df_m{tv_tf}"

    if re.match(r'^rsi_\d+$', ind):
        return f"ta.rsi({df}['close'], length={p}).iloc[-1]"
    if re.match(r'^adx_\d+$', ind):
        return f"ta.adx({df}['high'],{df}['low'],{df}['close'],length={p})['ADX_{p}'].iloc[-1]"
    if re.match(r'^atr_\d+$', ind):
        return f"ta.atr({df}['high'],{df}['low'],{df}['close'],length={p}).iloc[-1]"
    if ind == 'macd_fast_diff':
        return f"ta.macd({df}['close'])['MACDh_12_26_9'].iloc[-1]"
    if re.match(r'^bb_\d+_[\d.]+_width$', ind):
        return (f"(ta.bbands({df}['close'],length={p1},std={p2})[f'BBU_{p1}_{p2}_0'].iloc[-1]"
                f" - ta.bbands({df}['close'],length={p1},std={p2})[f'BBL_{p1}_{p2}_0'].iloc[-1])")
    if ind == 'keltner_width':
        return (f"(ta.kc({df}['high'],{df}['low'],{df}['close'])['KCUe_20_2'].iloc[-1]"
                f" - ta.kc({df}['high'],{df}['low'],{df}['close'])['KCLe_20_2'].iloc[-1])")
    if re.match(r'^ema_\d+_distance$', ind):
        return f"({df}['close'].iloc[-1] - ta.ema({df}['close'],length={p}).iloc[-1])"
    if re.match(r'^ema_\d+_above_\d+$', ind):
        return (f"(1.0 if ta.ema({df}['close'],length={p1}).iloc[-1]"
                f" > ta.ema({df}['close'],length={p2}).iloc[-1] else 0.0)")
    if re.match(r'^std_dev_\d+$', ind):
        return f"{df}['close'].rolling({p}).std().iloc[-1]"
    if re.match(r'^stoch_\d+_k$', ind):
        return f"ta.stoch({df}['high'],{df}['low'],{df}['close'],k={p})['STOCHk_{p}_3_3'].iloc[-1]"
    if re.match(r'^williams_r_\d+$', ind):
        return f"ta.willr({df}['high'],{df}['low'],{df}['close'],length={p}).iloc[-1]"
    if re.match(r'^cci_\d+$', ind):
        return f"ta.cci({df}['high'],{df}['low'],{df}['close'],length={p}).iloc[-1]"
    if ind == 'candle_range':
        return f"({df}['high'].iloc[-1] - {df}['low'].iloc[-1])"
    if ind == 'body_to_range_ratio':
        return f"(abs({df}['close'].iloc[-1]-{df}['open'].iloc[-1])/max({df}['high'].iloc[-1]-{df}['low'].iloc[-1],1e-6))"
    # WHY: Python indicator_utils computes pivot from previous bar of
    #      CURRENT timeframe, not D1. Old code used df_d1440 which
    #      produced different values for H1/M15/H4 features.
    # CHANGED: April 2026 — fix pivot_point timeframe (audit bug family #7)
    if ind == 'pivot_point':
        return f"({df}['high'].iloc[-2]+{df}['low'].iloc[-2]+{df}['close'].iloc[-2])/3.0"
    if ind == 'pivot_point_distance':
        return (f"({df}['close'].iloc[-1]"
                f" - ({df}['high'].iloc[-2]+{df}['low'].iloc[-2]+{df}['close'].iloc[-2])/3.0)")
    if re.match(r'^roc_\d+$', ind):
        n = int(p) + 1
        return f"(({df}['close'].iloc[-1]-{df}['close'].iloc[-{n}])/max({df}['close'].iloc[-{n}],0.001))"
    if ind == 'position_in_swing_range':
        return (f"(({df}['close'].iloc[-1]-{df}['close'].rolling(20).min().iloc[-1])"
                f"/max({df}['close'].rolling(20).max().iloc[-1]-{df}['close'].rolling(20).min().iloc[-1],1e-6))")
    if ind == 'tsi':
        return f"ta.tsi({df}['close']).iloc[-1]"
    return '0.0  # TODO: unknown sub-feature'


# ── SMART / REGIME code generator ─────────────────────────────────────────────

def _generate_smart_mql(feature_name, formula, platform):
    """Generate platform code for a SMART_ or REGIME_ feature."""
    var_name = re.sub(r'[^a-zA-Z0-9]', '_', feature_name).lower()
    ftype = formula['type']
    sep   = '\n   '  # indentation matching ea_generator emission

    def _rc(lines):
        """Join setup lines + return as read_code string."""
        return sep.join(lines)

    # ── MT5 ──────────────────────────────────────────────────────────────────
    if platform == 'mt5':
        lines = []

        if ftype == 'diff':
            ls_a, ea = _mql5_sub_expr(formula['a'], '_a')
            ls_b, eb = _mql5_sub_expr(formula['b'], '_b')
            lines = ls_a + ls_b + [f'double val_{var_name} = {ea} - {eb};']

        elif ftype == 'ratio_safe':
            ls_n, en = _mql5_sub_expr(formula['num'], '_n')
            ls_d, ed = _mql5_sub_expr(formula['den'], '_d')
            lines = ls_n + ls_d + [f'double val_{var_name} = (MathAbs({ed})>0)?({en}/MathMax(MathAbs({ed}),0.001)):0.0;']

        elif ftype == 'ratio_safe_price':
            # WHY: Old code used D1 pivot as price proxy for normalization.
            #      After FIX 4A/4B, pivot_point uses current timeframe, so
            #      ratio features should normalize by current close, not D1 pivot.
            # CHANGED: April 2026 — fix ratio normalization base (audit bug family #7)
            ls_n, en = _mql5_sub_expr(formula['num'], '_n')
            s = formula.get('scale', 100)
            lines = ls_n + [
                'double _ps = MathMax(iClose(NULL,PERIOD_H1,0),1.0);',
                f'double val_{var_name} = ({en}/_ps)*{s}.0;',
            ]

        elif ftype == 'ratio_safe_price_abs':
            # WHY: Same as ratio_safe_price above.
            # CHANGED: April 2026 — fix ratio normalization base (audit bug family #7)
            ls_n, en = _mql5_sub_expr(formula['num'], '_n')
            s = formula.get('scale', 100)
            lines = ls_n + [
                'double _ps = MathMax(iClose(NULL,PERIOD_H1,0),1.0);',
                f'double val_{var_name} = (MathAbs({en})/_ps)*{s}.0;',
            ]

        elif ftype == 'count_gt':
            all_ls = []
            exprs  = []
            for i, col in enumerate(formula['cols']):
                ls, ex = _mql5_sub_expr(col, f'_{i}')
                all_ls.extend(ls)
                exprs.append(f'(({ex})>{formula["threshold"]}?1:0)')
            lines = all_ls + [f'double val_{var_name} = (double)({"+".join(exprs)});']

        elif ftype == 'count_sum':
            all_ls = []
            exprs  = []
            for i, col in enumerate(formula['cols']):
                ls, ex = _mql5_sub_expr(col, f'_{i}')
                all_ls.extend(ls)
                exprs.append(ex)
            lines = all_ls + [f'double val_{var_name} = (double)({"+".join(exprs)});']

        elif ftype == 'macd_agree':
            ls_a, ea = _mql5_sub_expr(formula['a'], '_a')
            ls_b, eb = _mql5_sub_expr(formula['b'], '_b')
            lines = ls_a + ls_b + [
                f'double val_{var_name} = (double)(({ea}>0&&{eb}>0)?1:(({ea}<0&&{eb}<0)?-1:0));'
            ]

        elif ftype == 'direction':
            # current - value 3 bars ago; need 4-element buffer
            col = formula['col']
            p_info = parse_feature_name(col)
            tf_info = TIMEFRAME_MAP.get(p_info['timeframe'], TIMEFRAME_MAP['H1'])
            mt5_tf  = tf_info['mt5']
            pr      = (p_info['params'] or ['14'])[0]
            ind     = p_info['indicator']
            buf     = f'_db_{var_name}'
            if re.match(r'^rsi_\d+$', ind):
                init = f'iRSI(NULL,{mt5_tf},{pr},PRICE_CLOSE)'
            elif re.match(r'^adx_\d+$', ind):
                init = f'iADX(NULL,{mt5_tf},{pr})'
            elif re.match(r'^atr_\d+$', ind):
                init = f'iATR(NULL,{mt5_tf},{pr})'
            elif ind == 'macd_fast_diff':
                init = f'iMACD(NULL,{mt5_tf},12,26,9,PRICE_CLOSE)'
            elif re.match(r'^cci_\d+$', ind):
                init = f'iCCI(NULL,{mt5_tf},{pr},PRICE_TYPICAL)'
            elif re.match(r'^bb_\d+_[\d.]+_width$', ind):
                p1 = p_info['params'][0] if p_info['params'] else '20'
                p2 = p_info['params'][1] if len(p_info['params']) > 1 else '2'
                # BB direction: buffer is upper-lower (compute from 2 bands)
                bufU = f'{buf}u'; bufL = f'{buf}l'
                lines = [
                    f'double {bufU}[4],{bufL}[4]; CopyBuffer(iBands(NULL,{mt5_tf},{p1},0,{p2},PRICE_CLOSE),1,0,4,{bufU}); CopyBuffer(iBands(NULL,{mt5_tf},{p1},0,{p2},PRICE_CLOSE),2,0,4,{bufL});',
                    f'double val_{var_name} = ({bufU}[0]-{bufL}[0]) - ({bufU}[3]-{bufL}[3]);',
                ]
                return {'var_name': var_name, 'handle_var': '', 'handle_init': '',
                        'read_code': _rc(lines), 'custom_indicator': False,
                        'description': f'Direction of {col}'}
            else:
                init = f'// TODO direction of {col}'
                lines = [f'double val_{var_name} = 0.0; // TODO direction of {col}']
                return {'var_name': var_name, 'handle_var': '', 'handle_init': '',
                        'read_code': _rc(lines), 'custom_indicator': False,
                        'description': f'Direction of {col}'}
            buf_n = 2 if ind == 'macd_fast_diff' else 0
            lines = [
                f'double {buf}[4]; CopyBuffer({init},{buf_n},0,4,{buf});',
                f'double val_{var_name} = {buf}[0] - {buf}[3];',
            ]

        elif ftype == 'accel':
            # direction change: (cur-3ago) - (3ago-6ago) = cur - 2*3ago + 6ago; need 7 bars
            col = formula['col']
            p_info = parse_feature_name(col)
            tf_info = TIMEFRAME_MAP.get(p_info['timeframe'], TIMEFRAME_MAP['H1'])
            mt5_tf  = tf_info['mt5']
            pr      = (p_info['params'] or ['14'])[0]
            ind     = p_info['indicator']
            buf     = f'_ac_{var_name}'
            if re.match(r'^rsi_\d+$', ind):
                init = f'iRSI(NULL,{mt5_tf},{pr},PRICE_CLOSE)'
            elif re.match(r'^adx_\d+$', ind):
                init = f'iADX(NULL,{mt5_tf},{pr})'
            elif re.match(r'^atr_\d+$', ind):
                init = f'iATR(NULL,{mt5_tf},{pr})'
            elif ind == 'macd_fast_diff':
                init = f'iMACD(NULL,{mt5_tf},12,26,9,PRICE_CLOSE)'
            elif re.match(r'^cci_\d+$', ind):
                init = f'iCCI(NULL,{mt5_tf},{pr},PRICE_TYPICAL)'
            elif re.match(r'^bb_\d+_[\d.]+_width$', ind):
                # WHY (Phase 64 Fix 1): bb_width accel was silently 0.0.
                #      Width = upper - lower band. Accel = width_change rate.
                #      Use 7 bars: (w[0]-w[3]) - (w[3]-w[6]).
                # CHANGED: April 2026 — Phase 64 Fix 1 — bb_width accel
                p1a = p_info['params'][0] if p_info['params'] else '20'
                p2a = p_info['params'][1] if len(p_info['params']) > 1 else '2'
                bufU = f'{buf}u'; bufL = f'{buf}l'
                lines = [
                    f'double {bufU}[7],{bufL}[7];'
                    f' CopyBuffer(iBands(NULL,{mt5_tf},{p1a},0,{p2a},PRICE_CLOSE),1,0,7,{bufU});'
                    f' CopyBuffer(iBands(NULL,{mt5_tf},{p1a},0,{p2a},PRICE_CLOSE),2,0,7,{bufL});',
                    f'double _w0={bufU}[0]-{bufL}[0], _w3={bufU}[3]-{bufL}[3], _w6={bufU}[6]-{bufL}[6];',
                    f'double val_{var_name} = (_w0-_w3) - (_w3-_w6);',
                ]
                return {'var_name': var_name, 'handle_var': '', 'handle_init': '',
                        'read_code': _rc(lines), 'custom_indicator': False,
                        'description': f'Acceleration of {col}'}
            else:
                # WHY (Phase 64 Fix 1): Remaining unknowns still return 0.0 but
                #      now emit a Print() so the user sees it in the MT5 journal.
                lines = [
                    f'Print("WARNING: accel not implemented for {col} — val=0");',
                    f'double val_{var_name} = 0.0;',
                ]
                return {'var_name': var_name, 'handle_var': '', 'handle_init': '',
                        'read_code': _rc(lines), 'custom_indicator': False,
                        'description': f'Acceleration of {col}'}
            buf_n = 2 if ind == 'macd_fast_diff' else 0
            lines = [
                f'double {buf}[7]; CopyBuffer({init},{buf_n},0,7,{buf});',
                f'double val_{var_name} = ({buf}[0]-{buf}[3]) - ({buf}[3]-{buf}[6]);',
            ]

        elif ftype in ('time_range', 'time_since', 'count_sessions'):
            # WHY: Old code used TimeCurrent() which returns BROKER SERVER
            #      time (typically GMT+2 or GMT+3 depending on DST). Python
            #      features use candle timestamps which are in UTC. Same
            #      rule evaluated on the same candle gave different hour
            #      values — a rule like "hour_of_day between 13 and 16"
            #      fires at UTC 13-16 in Python training but broker 13-16
            #      (= UTC 11-14 EDT) in live. Fix: use TimeGMT() which
            #      returns UTC directly. Works in both live and backtest.
            #      The EA generator already uses TimeGMT() for the news
            #      blackout check — indicator mapper just never caught up.
            # CHANGED: April 2026 — TimeGMT() for UTC consistency (audit CRITICAL #24)
            mdt = f'_mdt_{var_name}'
            if ftype == 'time_range':
                lo, hi = formula['lo'], formula['hi']
                lines = [
                    f'MqlDateTime {mdt}; TimeToStruct(TimeGMT(),{mdt}); int _hr_{var_name}={mdt}.hour;',
                    f'double val_{var_name} = (_hr_{var_name}>={lo}&&_hr_{var_name}<={hi})?1.0:0.0;',
                ]
            elif ftype == 'time_since':
                oh = formula['open_hour']
                # WHY: Old expression clamped to 0 when current_hour < open_hour,
                #      losing overnight continuity. At 3am UTC, hours_since_london
                #      (open_hour=7) returned 0 instead of 20. Rules requiring
                #      "hours_since X >= 18" could never trigger. Fix: modulo 24
                #      wraparound so the value increases monotonically across
                #      the 24-hour cycle.
                # CHANGED: April 2026 — overnight wrap (audit MED #28)
                lines = [
                    f'MqlDateTime {mdt}; TimeToStruct(TimeGMT(),{mdt}); int _hr_{var_name}={mdt}.hour;',
                    f'double val_{var_name} = (double)((_hr_{var_name} - {oh} + 24) % 24);',
                ]
            else:  # count_sessions
                lines = [
                    f'MqlDateTime {mdt}; TimeToStruct(TimeGMT(),{mdt}); int _hr_{var_name}={mdt}.hour;',
                    f'double val_{var_name} = (double)((_hr_{var_name}>=0&&_hr_{var_name}<8?1:0)+(_hr_{var_name}>=7&&_hr_{var_name}<16?1:0)+(_hr_{var_name}>=13&&_hr_{var_name}<22?1:0));',
                ]

        # WHY: All calendar features use TimeGMT() for UTC consistency
        #      with Python candle timestamps. See audit CRITICAL #24 +
        #      Phase 18 Fix 1. Without this, live cal_nfp / cal_dom_*
        #      features evaluate on broker server hour which differs
        #      from Python training hour by the broker's UTC offset.
        # CHANGED: April 2026 — TimeGMT() throughout calendar block
        elif ftype in ('cal_dow_eq', 'cal_dow_range', 'cal_dom_le', 'cal_dom_ge',
                       'cal_nfp', 'cal_quarter_end', 'cal_week_of_month'):
            mdt = f'_mdt_{var_name}'
            if ftype == 'cal_dow_eq':
                # WHY: MQL5 day_of_week is Sun=0,Mon=1,...,Sat=6.
                #      Python .weekday() is Mon=0,...,Sun=6. SMART_INDICATOR
                #      dict stores Python-convention values (0=Mon, 4=Fri).
                #      Old MQL5 code compared raw day_of_week against Python
                #      values — e.g., is_monday checked day_of_week==0 which
                #      is Sunday in MQL5, not Monday. Fix: convert MQL5 to
                #      Python via (day_of_week+6)%7 before comparing.
                # CHANGED: April 2026 — fix MQL5 weekday convention (audit HIGH)
                lines = [
                    f'MqlDateTime {mdt}; TimeToStruct(TimeGMT(),{mdt});',
                    f'double val_{var_name} = (({mdt}.day_of_week+6)%7=={formula["value"]})?1.0:0.0;',
                ]
            elif ftype == 'cal_dow_range':
                # WHY: Same MQL5 Sun=0 vs Python Mon=0 mismatch as cal_dow_eq.
                # CHANGED: April 2026 — fix MQL5 weekday convention (audit HIGH)
                lo, hi = formula['lo'], formula['hi']
                lines = [
                    f'MqlDateTime {mdt}; TimeToStruct(TimeGMT(),{mdt});',
                    f'double val_{var_name} = ({lo}<=({mdt}.day_of_week+6)%7&&({mdt}.day_of_week+6)%7<={hi})?1.0:0.0;',
                ]
            elif ftype == 'cal_dom_le':
                lines = [
                    f'MqlDateTime {mdt}; TimeToStruct(TimeGMT(),{mdt});',
                    f'double val_{var_name} = ({mdt}.day<={formula["value"]})?1.0:0.0;',
                ]
            elif ftype == 'cal_dom_ge':
                lines = [
                    f'MqlDateTime {mdt}; TimeToStruct(TimeGMT(),{mdt});',
                    f'double val_{var_name} = ({mdt}.day>={formula["value"]})?1.0:0.0;',
                ]
            elif ftype == 'cal_nfp':
                # WHY: MQL5 day_of_week: Sunday=0 Mon=1 ... Fri=5 Sat=6.
                #      Old code used ==4 (Thursday in MQL5). NFP is always
                #      Friday (first Friday of the month, day<=7).
                # CHANGED: April 2026 — fix MQL5 weekday for NFP (audit HIGH)
                lines = [
                    f'MqlDateTime {mdt}; TimeToStruct(TimeGMT(),{mdt});',
                    f'double val_{var_name} = ({mdt}.day_of_week==5&&{mdt}.day<=7)?1.0:0.0;',
                ]
            elif ftype == 'cal_quarter_end':
                lines = [
                    f'MqlDateTime {mdt}; TimeToStruct(TimeGMT(),{mdt});',
                    f'double val_{var_name} = ({mdt}.mon==3||{mdt}.mon==6||{mdt}.mon==9||{mdt}.mon==12)?1.0:0.0;',
                ]
            else:  # cal_week_of_month
                lines = [
                    f'MqlDateTime {mdt}; TimeToStruct(TimeGMT(),{mdt});',
                    f'double val_{var_name} = (double)(({mdt}.day-1)/7+1);',
                ]

        elif ftype == 'compare':
            ls_a, ea = _mql5_sub_expr(formula['a'], '_a')
            ls_b, eb = _mql5_sub_expr(formula['b'], '_b')
            op = formula['op']
            lines = ls_a + ls_b + [f'double val_{var_name} = ({ea}{op}{eb})?1.0:0.0;']

        elif ftype == 'compare_const':
            ls_a, ea = _mql5_sub_expr(formula['a'], '_a')
            lines = ls_a + [f'double val_{var_name} = ({ea}{formula["op"]}{formula["value"]})?1.0:0.0;']

        elif ftype == 'compare_const_abs':
            ls_a, ea = _mql5_sub_expr(formula['a'], '_a')
            lines = ls_a + [f'double val_{var_name} = (MathAbs({ea}){formula["op"]}{formula["value"]})?1.0:0.0;']

        elif ftype == 'bb_vs_keltner':
            ls_bb, ebb = _mql5_sub_expr('H1_bb_20_2_width', '_bb')
            ls_kel, ekkel = _mql5_sub_expr('H1_keltner_width', '_kel')
            lines = ls_bb + ls_kel + [f'double val_{var_name} = ({ebb}<{ekkel})?1.0:0.0;']

        elif ftype == 'price_mod':
            # WHY (Phase 60 Fix 2a): Old formula was MathAbs(MathMod(x,mod)-half)/half
            #      which returned 1.0 when AT a round level (should be 0.0).
            #      Phase 57 corrected the Python training side to
            #      1.0 - abs(x%mod-half)/half. Mirror the correction here
            #      so live EA and training agree on direction.
            # CHANGED: April 2026 — Phase 60 Fix 2a — corrected dist_to_round live formula
            ls_c, ec = _mql5_sub_expr(formula['col'], '_c')
            mod, half = formula['modulo'], formula['half']
            lines = ls_c + [f'double val_{var_name} = 1.0-MathAbs(MathMod({ec},{mod}.0)-{half}.0)/{half}.0;']

        elif ftype == 'rsi_zone':
            ls, er = _mql5_sub_expr(formula['col'], '_rz')
            lines = ls + [
                f'double _rz_{var_name} = {er};',
                f'double val_{var_name} = (_rz_{var_name}>70)?3.0:(_rz_{var_name}>60)?2.0:(_rz_{var_name}>50)?1.0:(_rz_{var_name}>40)?-1.0:(_rz_{var_name}>30)?-2.0:-3.0;',
            ]

        elif ftype == 'crossed_above':
            # WHY: Old code hardcoded iRSI regardless of what indicator
            #      formula['col'] referred to. 'SMART_adx_crossed_25' would
            #      read RSI(14) instead of ADX(14) — silent wrong indicator.
            #      Now dispatch based on the indicator in col.
            # CHANGED: April 2026 — fix crossed_above indicator dispatch (audit bug family #7)
            col = formula['col']
            thr = formula['threshold']
            p_info = parse_feature_name(col)
            tf_info = TIMEFRAME_MAP.get(p_info['timeframe'], TIMEFRAME_MAP['H1'])
            mt5_tf  = tf_info['mt5']
            pr      = (p_info['params'] or ['14'])[0]
            buf     = f'_cx_{var_name}'
            ind_name = p_info['indicator']

            # Dispatch by indicator type — reads 2 consecutive values
            if re.match(r'^rsi_\d+$', ind_name):
                handle_expr = f'iRSI(NULL,{mt5_tf},{pr},PRICE_CLOSE)'
                buf_idx = 0
            elif re.match(r'^adx_\d+$', ind_name):
                handle_expr = f'iADX(NULL,{mt5_tf},{pr})'
                buf_idx = 0
            elif re.match(r'^cci_\d+$', ind_name):
                handle_expr = f'iCCI(NULL,{mt5_tf},{pr},PRICE_TYPICAL)'
                buf_idx = 0
            elif re.match(r'^stoch_\d+_k$', ind_name):
                handle_expr = f'iStochastic(NULL,{mt5_tf},{pr},3,3,MODE_SMA,STO_LOWHIGH)'
                buf_idx = 0  # %K main line
            elif re.match(r'^williams_r_\d+$', ind_name):
                handle_expr = f'iWPR(NULL,{mt5_tf},{pr})'
                buf_idx = 0
            elif re.match(r'^atr_\d+$', ind_name):
                handle_expr = f'iATR(NULL,{mt5_tf},{pr})'
                buf_idx = 0
            else:
                # Fallback: warn and disable this rule (better than silently wrong)
                print(f"[WARN] crossed_above: unsupported indicator {ind_name!r} in {col!r} — rule will always be 0")
                lines = [f'double val_{var_name} = 0.0; // crossed_above unsupported for {ind_name}']
                return {
                    'var_name':        var_name,
                    'handle_var':      '',
                    'handle_init':     '',
                    'read_code':       _rc(lines),
                    'custom_indicator': False,
                    'description':     f'{feature_name} (unsupported indicator {ind_name})',
                }

            lines = [
                f'double {buf}[2]; CopyBuffer({handle_expr},{buf_idx},0,2,{buf});',
                f'double val_{var_name} = ({buf}[0]>{thr}&&{buf}[1]<={thr})?1.0:0.0;',
            ]

        elif ftype == 'crossed_below':
            # WHY: Same fix as crossed_above — dispatch by indicator type
            #      instead of hardcoding iRSI.
            # CHANGED: April 2026 — fix crossed_below indicator dispatch (audit bug family #7)
            col = formula['col']
            thr = formula['threshold']
            p_info = parse_feature_name(col)
            tf_info = TIMEFRAME_MAP.get(p_info['timeframe'], TIMEFRAME_MAP['H1'])
            mt5_tf  = tf_info['mt5']
            pr      = (p_info['params'] or ['14'])[0]
            buf     = f'_cx_{var_name}'
            ind_name = p_info['indicator']

            if re.match(r'^rsi_\d+$', ind_name):
                handle_expr = f'iRSI(NULL,{mt5_tf},{pr},PRICE_CLOSE)'
                buf_idx = 0
            elif re.match(r'^adx_\d+$', ind_name):
                handle_expr = f'iADX(NULL,{mt5_tf},{pr})'
                buf_idx = 0
            elif re.match(r'^cci_\d+$', ind_name):
                handle_expr = f'iCCI(NULL,{mt5_tf},{pr},PRICE_TYPICAL)'
                buf_idx = 0
            elif re.match(r'^stoch_\d+_k$', ind_name):
                handle_expr = f'iStochastic(NULL,{mt5_tf},{pr},3,3,MODE_SMA,STO_LOWHIGH)'
                buf_idx = 0
            elif re.match(r'^williams_r_\d+$', ind_name):
                handle_expr = f'iWPR(NULL,{mt5_tf},{pr})'
                buf_idx = 0
            elif re.match(r'^atr_\d+$', ind_name):
                handle_expr = f'iATR(NULL,{mt5_tf},{pr})'
                buf_idx = 0
            else:
                print(f"[WARN] crossed_below: unsupported indicator {ind_name!r} in {col!r} — rule will always be 0")
                lines = [f'double val_{var_name} = 0.0; // crossed_below unsupported for {ind_name}']
                return {
                    'var_name':        var_name,
                    'handle_var':      '',
                    'handle_init':     '',
                    'read_code':       _rc(lines),
                    'custom_indicator': False,
                    'description':     f'{feature_name} (unsupported indicator {ind_name})',
                }

            lines = [
                f'double {buf}[2]; CopyBuffer({handle_expr},{buf_idx},0,2,{buf});',
                f'double val_{var_name} = ({buf}[0]<{thr}&&{buf}[1]>={thr})?1.0:0.0;',
            ]

        elif ftype == 'price_bucket':
            # WHY: _px here represents current price for bucketing. Old
            #      code computed previous D1 pivot which is a different
            #      concept. Use current close to match Python semantics.
            # CHANGED: April 2026 — fix price bucketing base (audit bug family #7)
            lines = [
                'double _px = iClose(NULL,PERIOD_CURRENT,0);',
                f'double val_{var_name} = (_px<1000)?0.0:(_px<2000)?1.0:(_px<3000)?2.0:3.0;',
            ]

        elif ftype == 'price_gt':
            # WHY: Same as price_bucket above.
            # CHANGED: April 2026 — fix price comparison base (audit bug family #7)
            # WHY (Phase 77 Fix IM-2): threshold is instrument-specific.
            _threshold = formula['value']
            _note      = formula.get('live_note', '')
            _warn_line = (f'Print("NOTE: {_note}");' if _note else '')
            lines = [
                'double _px = iClose(NULL,PERIOD_CURRENT,0);',
                f'double val_{var_name} = (_px>{_threshold})?1.0:0.0;',
            ]
            if _warn_line:
                lines.insert(0, f'static bool _warned_{var_name} = false; '
                                f'if(!_warned_{var_name}){{ {_warn_line} '
                                f'_warned_{var_name}=true; }}')

        else:
            # WHY (Phase 64 Fix 4a): Unknown formula type silently emitted 0.0.
            #      The EA ran without error but features never fired. Print a
            #      warning in the MT5 journal so users see what's missing.
            # CHANGED: April 2026 — Phase 64 Fix 4a — loud unknown ftype
            lines = [
                f'Print("WARNING: formula type \\"{ftype}\\" not implemented for {feature_name} — val=0");',
                f'double val_{var_name} = 0.0;',
            ]

        return {
            'var_name':        var_name,
            'handle_var':      '',
            'handle_init':     '',
            'read_code':       _rc(lines),
            'custom_indicator': False,
            'description':     f'{feature_name} (computed)',
        }

    # ── Tradovate / Python ────────────────────────────────────────────────────
    else:
        def _py(col, suf=''):
            return _py_sub_expr(col)

        if ftype == 'diff':
            expr = f"({_py(formula['a'])} - {_py(formula['b'])})"
        elif ftype == 'ratio_safe':
            en = _py(formula['num']); ed = _py(formula['den'])
            expr = f"(({en}) / max(abs({ed}), 0.001) if abs({ed}) > 0 else 0.0)"
        elif ftype in ('ratio_safe_price', 'ratio_safe_price_abs'):
            # WHY: Match MQL5 side - use current H1 close for normalization.
            # CHANGED: April 2026 — fix ratio normalization base (audit bug family #7)
            en = _py(formula['num']); s = formula.get('scale', 100)
            price_proxy = "df_m60['close'].iloc[-1]"
            if ftype == 'ratio_safe_price_abs':
                expr = f"(abs({en}) / max({price_proxy}, 1.0)) * {s}"
            else:
                expr = f"(({en}) / max({price_proxy}, 1.0)) * {s}"
        elif ftype == 'count_gt':
            parts = [f"(1 if ({_py(c)}) > {formula['threshold']} else 0)" for c in formula['cols']]
            expr = f"float({' + '.join(parts)})"
        elif ftype == 'count_sum':
            expr = f"float({' + '.join([_py(c) for c in formula['cols']])})"
        elif ftype == 'macd_agree':
            ea = _py(formula['a']); eb = _py(formula['b'])
            expr = f"(1 if ({ea}>0 and {eb}>0) else (-1 if ({ea}<0 and {eb}<0) else 0))"
        elif ftype == 'direction':
            col = formula['col']
            p_info = parse_feature_name(col)
            tf_info = TIMEFRAME_MAP.get(p_info['timeframe'], TIMEFRAME_MAP['H1'])
            tv_tf = tf_info['tradovate']
            pr = (p_info['params'] or ['14'])[0]
            ind = p_info['indicator']
            df = f"df_m{tv_tf}"
            if re.match(r'^rsi_\d+$', ind):
                expr = f"(ta.rsi({df}['close'],length={pr}).iloc[-1] - ta.rsi({df}['close'],length={pr}).iloc[-4])"
            elif re.match(r'^adx_\d+$', ind):
                expr = f"(ta.adx({df}['high'],{df}['low'],{df}['close'],length={pr})['ADX_{pr}'].iloc[-1] - ta.adx({df}['high'],{df}['low'],{df}['close'],length={pr})['ADX_{pr}'].iloc[-4])"
            elif re.match(r'^atr_\d+$', ind):
                expr = f"(ta.atr({df}['high'],{df}['low'],{df}['close'],length={pr}).iloc[-1] - ta.atr({df}['high'],{df}['low'],{df}['close'],length={pr}).iloc[-4])"
            elif re.match(r'^cci_\d+$', ind):
                # WHY (Phase 64 Fix 2): CCI direction was missing for Tradovate.
                # CHANGED: April 2026 — Phase 64 Fix 2
                expr = f"(ta.cci({df}['high'],{df}['low'],{df}['close'],length={pr}).iloc[-1] - ta.cci({df}['high'],{df}['low'],{df}['close'],length={pr}).iloc[-4])"
            elif ind == 'macd_fast_diff':
                expr = f"(ta.macd({df}['close'],fast=12,slow=26,signal=9)['MACDh_12_26_9'].iloc[-1] - ta.macd({df}['close'],fast=12,slow=26,signal=9)['MACDh_12_26_9'].iloc[-4])"
            elif re.match(r'^bb_\d+_[\d.]+_width$', ind):
                p1b = p_info['params'][0] if p_info['params'] else '20'
                p2b = p_info['params'][1] if len(p_info['params']) > 1 else '2'
                expr = (f"((ta.bbands({df}['close'],length={p1b},std={p2b})['BBU_{p1b}_{p2b}'].iloc[-1]"
                        f" - ta.bbands({df}['close'],length={p1b},std={p2b})['BBL_{p1b}_{p2b}'].iloc[-1])"
                        f" - (ta.bbands({df}['close'],length={p1b},std={p2b})['BBU_{p1b}_{p2b}'].iloc[-4]"
                        f" - ta.bbands({df}['close'],length={p1b},std={p2b})['BBL_{p1b}_{p2b}'].iloc[-4]))")
            else:
                # Phase 64 Fix 2: log instead of silent 0.0
                expr = f"(print('WARNING: direction not implemented for {col}') or 0.0)"
        elif ftype == 'accel':
            col = formula['col']
            p_info = parse_feature_name(col)
            tf_info = TIMEFRAME_MAP.get(p_info['timeframe'], TIMEFRAME_MAP['H1'])
            tv_tf = tf_info['tradovate']
            pr = (p_info['params'] or ['14'])[0]
            ind = p_info['indicator']
            df = f"df_m{tv_tf}"
            if re.match(r'^rsi_\d+$', ind):
                expr = (f"((ta.rsi({df}['close'],length={pr}).iloc[-1]-ta.rsi({df}['close'],length={pr}).iloc[-4])"
                        f" - (ta.rsi({df}['close'],length={pr}).iloc[-4]-ta.rsi({df}['close'],length={pr}).iloc[-7]))")
            elif re.match(r'^adx_\d+$', ind):
                # WHY (Phase 64 Fix 3): ADX accel was missing for Tradovate.
                # CHANGED: April 2026 — Phase 64 Fix 3
                _adx = f"ta.adx({df}['high'],{df}['low'],{df}['close'],length={pr})['ADX_{pr}']"
                expr = (f"(({_adx}.iloc[-1]-{_adx}.iloc[-4])"
                        f" - ({_adx}.iloc[-4]-{_adx}.iloc[-7]))")
            elif re.match(r'^atr_\d+$', ind):
                _atr = f"ta.atr({df}['high'],{df}['low'],{df}['close'],length={pr})"
                expr = (f"(({_atr}.iloc[-1]-{_atr}.iloc[-4])"
                        f" - ({_atr}.iloc[-4]-{_atr}.iloc[-7]))")
            elif re.match(r'^cci_\d+$', ind):
                _cci = f"ta.cci({df}['high'],{df}['low'],{df}['close'],length={pr})"
                expr = (f"(({_cci}.iloc[-1]-{_cci}.iloc[-4])"
                        f" - ({_cci}.iloc[-4]-{_cci}.iloc[-7]))")
            else:
                # Phase 64 Fix 3: log instead of silent 0.0
                expr = f"(print('WARNING: accel not implemented for {col}') or 0.0)"
        elif ftype == 'time_range':
            # WHY: Old code had `import datetime as _dt` but the expr below
            #      uses __import__('datetime') instead — _dt was dead.
            # CHANGED: April 2026 — remove dead local import (Phase 19b)
            lo, hi = formula['lo'], formula['hi']
            expr = f"(1.0 if {lo} <= __import__('datetime').datetime.utcnow().hour <= {hi} else 0.0)"
        elif ftype == 'time_since':
            oh = formula['open_hour']
            # CHANGED: April 2026 — overnight wrap to match MT5 (audit MED #28)
            expr = f"((__import__('datetime').datetime.utcnow().hour - {oh} + 24) % 24)"
        elif ftype == 'count_sessions':
            expr = ("(lambda h: int(0<=h<8) + int(7<=h<16) + int(13<=h<22))"
                    "(__import__('datetime').datetime.utcnow().hour)")
        elif ftype == 'cal_dow_eq':
            expr = f"(1.0 if __import__('datetime').datetime.utcnow().weekday() == {formula['value']} else 0.0)"
        elif ftype == 'cal_dow_range':
            lo, hi = formula['lo'], formula['hi']
            expr = f"(1.0 if {lo} <= __import__('datetime').datetime.utcnow().weekday() <= {hi} else 0.0)"
        elif ftype == 'cal_dom_le':
            expr = f"(1.0 if __import__('datetime').datetime.utcnow().day <= {formula['value']} else 0.0)"
        elif ftype == 'cal_dom_ge':
            expr = f"(1.0 if __import__('datetime').datetime.utcnow().day >= {formula['value']} else 0.0)"
        elif ftype == 'cal_nfp':
            expr = ("(lambda d: 1.0 if d.weekday()==4 and d.day<=7 else 0.0)"
                    "(__import__('datetime').datetime.utcnow())")
        elif ftype == 'cal_quarter_end':
            expr = "(1.0 if __import__('datetime').datetime.utcnow().month in (3,6,9,12) else 0.0)"
        elif ftype == 'cal_week_of_month':
            expr = "float((__import__('datetime').datetime.utcnow().day - 1) // 7 + 1)"
        elif ftype == 'compare':
            ea = _py(formula['a']); eb = _py(formula['b']); op = formula['op']
            expr = f"(1.0 if ({ea}) {op} ({eb}) else 0.0)"
        elif ftype == 'compare_const':
            ea = _py(formula['a']); op = formula['op']; v = formula['value']
            expr = f"(1.0 if ({ea}) {op} {v} else 0.0)"
        elif ftype == 'compare_const_abs':
            ea = _py(formula['a']); op = formula['op']; v = formula['value']
            expr = f"(1.0 if abs({ea}) {op} {v} else 0.0)"
        elif ftype == 'bb_vs_keltner':
            bb  = _py('H1_bb_20_2_width')
            kel = _py('H1_keltner_width')
            expr = f"(1.0 if ({bb}) < ({kel}) else 0.0)"
        elif ftype == 'price_mod':
            # WHY (Phase 60 Fix 2b): Mirror Phase 57 correction for Tradovate.
            # CHANGED: April 2026 — Phase 60 Fix 2b — corrected Tradovate dist_to_round
            ec = _py(formula['col']); mod = formula['modulo']; half = formula['half']
            expr = f"1.0 - abs(({ec}) % {mod} - {half}) / {half}"
        elif ftype == 'rsi_zone':
            er = _py(formula['col'])
            expr = (f"(lambda r: 3.0 if r>70 else 2.0 if r>60 else 1.0 if r>50 else"
                    f" -1.0 if r>40 else -2.0 if r>30 else -3.0)({er})")
        elif ftype == 'crossed_above':
            col = formula['col']
            p_info = parse_feature_name(col)
            tf_info = TIMEFRAME_MAP.get(p_info['timeframe'], TIMEFRAME_MAP['H1'])
            tv_tf = tf_info['tradovate']; pr = (p_info['params'] or ['14'])[0]; thr = formula['threshold']
            df = f"df_m{tv_tf}"
            expr = (f"(1.0 if ta.rsi({df}['close'],length={pr}).iloc[-1]>{thr}"
                    f" and ta.rsi({df}['close'],length={pr}).iloc[-2]<={thr} else 0.0)")
        elif ftype == 'crossed_below':
            col = formula['col']
            p_info = parse_feature_name(col)
            tf_info = TIMEFRAME_MAP.get(p_info['timeframe'], TIMEFRAME_MAP['H1'])
            tv_tf = tf_info['tradovate']; pr = (p_info['params'] or ['14'])[0]; thr = formula['threshold']
            df = f"df_m{tv_tf}"
            expr = (f"(1.0 if ta.rsi({df}['close'],length={pr}).iloc[-1]<{thr}"
                    f" and ta.rsi({df}['close'],length={pr}).iloc[-2]>={thr} else 0.0)")
        elif ftype == 'price_bucket':
            # WHY: Match MQL5 side - use current close for price bucketing.
            # CHANGED: April 2026 — fix price bucketing base (audit bug family #7)
            price = "df_m60['close'].iloc[-1]"
            expr = f"(lambda p: 0.0 if p<1000 else 1.0 if p<2000 else 2.0 if p<3000 else 3.0)({price})"
        elif ftype == 'price_gt':
            # WHY: Match MQL5 side - use current close for price comparison.
            # CHANGED: April 2026 — fix price comparison base (audit bug family #7)
            price = "df_m60['close'].iloc[-1]"
            expr = f"(1.0 if ({price}) > {formula['value']} else 0.0)"
        else:
            # WHY (Phase 64 Fix 4b): Mirror Fix 4a for Tradovate side.
            # CHANGED: April 2026 — Phase 64 Fix 4b — loud unknown ftype
            expr = f"(print('WARNING: formula type \"{ftype}\" not implemented for {feature_name}') or 0.0)"

        return {
            'var_name':        var_name,
            'python_code':     f'val_{var_name} = {expr}',
            'custom_indicator': False,
            'description':     f'{feature_name} (computed)',
        }


# ── INT_ feature code generator ───────────────────────────────────────────────

def _generate_int_mql(feature_name, platform):
    """
    Generate code for INT_{op}_{featA}__{featB} interaction features.
    op: ratio | diff | prod
    featA/featB: full feature names (double-underscore separator).
    Returns None if the name does not match the pattern.
    """
    m = re.match(r'^INT_(ratio|diff|prod)_(.+?)__(.+)$', feature_name)
    if not m:
        return None
    op, feat_a, feat_b = m.group(1), m.group(2), m.group(3)
    var_name = re.sub(r'[^a-zA-Z0-9]', '_', feature_name).lower()
    sep = '\n   '

    if platform == 'mt5':
        ls_a, ea = _mql5_sub_expr(feat_a, '_ia')
        ls_b, eb = _mql5_sub_expr(feat_b, '_ib')
        if op == 'ratio':
            combine = f'double val_{var_name} = ({eb}!=0.0)?({ea}/MathMax(MathAbs({eb}),0.001)):0.0;'
        elif op == 'diff':
            combine = f'double val_{var_name} = {ea} - {eb};'
        else:  # prod
            combine = f'double val_{var_name} = {ea} * {eb};'
        lines = ls_a + ls_b + [combine]
        return {
            'var_name':        var_name,
            'handle_var':      '',
            'handle_init':     '',
            'read_code':       sep.join(lines),
            'custom_indicator': False,
            'description':     f'INT {op}: {feat_a} vs {feat_b}',
        }
    else:
        ea = _py_sub_expr(feat_a)
        eb = _py_sub_expr(feat_b)
        if op == 'ratio':
            expr = f"(({ea}) / max(abs({eb}), 0.001) if ({eb}) != 0 else 0.0)"
        elif op == 'diff':
            expr = f"({ea}) - ({eb})"
        else:  # prod
            expr = f"({ea}) * ({eb})"
        return {
            'var_name':        var_name,
            'python_code':     f'val_{var_name} = {expr}',
            'custom_indicator': False,
            'description':     f'INT {op}: {feat_a} vs {feat_b}',
        }


def parse_feature_name(feature_name):
    """
    Parse a Python feature name into components.
    'H1_distance_to_fib_500' -> {'timeframe': 'H1', 'indicator': 'distance_to_fib_500'}
    'M5_adx_14' -> {'timeframe': 'M5', 'indicator': 'adx_14', 'params': ['14']}

    Returns dict with 'timeframe', 'indicator', 'params' (list of numeric strings).
    """
    known_tfs = ['M5', 'M15', 'H1', 'H4', 'D1']
    for tf in known_tfs:
        if feature_name.startswith(tf + '_'):
            indicator = feature_name[len(tf) + 1:]
            # Extract numeric params
            params = re.findall(r'\d+(?:\.\d+)?', indicator)
            return {'timeframe': tf, 'indicator': indicator, 'params': params}
    # No known prefix — treat entire name as indicator on H1
    return {'timeframe': 'H1', 'indicator': feature_name, 'params': []}


def _match_pattern(indicator_name):
    """Find matching pattern entry for an indicator name."""
    for pattern, template in INDICATOR_PATTERNS:
        m = re.match(pattern, indicator_name)
        if m:
            return template, m.groups()
    return None, ()


def get_mql_code(feature_name, platform='mt5'):
    """
    Convert a Python feature name to platform code.

    Returns dict with:
      'var_name': safe variable name for this indicator value
      'handle_var': MQL5 handle variable declaration (MT5 only)
      'handle_init': MQL5 OnInit() code (MT5 only)
      'read_code': code to read the indicator value into var_name
      'custom_indicator': bool — needs custom .ex5 (MT5) or extra Python func
      'description': human-readable explanation
    """
    parsed = parse_feature_name(feature_name)
    tf       = parsed['timeframe']
    ind      = parsed['indicator']
    params   = parsed['params']

    tf_info  = TIMEFRAME_MAP.get(tf, TIMEFRAME_MAP['H1'])
    mt5_tf   = tf_info['mt5']
    tv_tf    = tf_info['tradovate']

    # Safe variable name
    var_name = re.sub(r'[^a-zA-Z0-9]', '_', feature_name).lower()

    template, groups = _match_pattern(ind)

    # ── SMART / REGIME / INT routing ─────────────────────────────────────────
    if feature_name in SMART_FORMULAS:
        return _generate_smart_mql(feature_name, SMART_FORMULAS[feature_name], platform)
    if feature_name in REGIME_FORMULAS:
        return _generate_smart_mql(feature_name, REGIME_FORMULAS[feature_name], platform)
    if feature_name.startswith('INT_'):
        result = _generate_int_mql(feature_name, platform)
        if result is not None:
            return result

    if template is None:
        # Unknown indicator — generate FAIL-LOUD placeholder
        # WHY: Old version emitted `double val = 0.0` and a TODO comment.
        #      The EA compiled and ran but the feature was always 0,
        #      causing rules to silently misfire. Phase 4 fixed this for
        #      TSI specifically. Phase 20 generalizes the pattern: any
        #      unknown indicator now sets indicatorFailed = true and
        #      emits a loud Print() so the user notices in the Experts
        #      log immediately.
        # CHANGED: April 2026 — fail loud on unknown indicators (audit MED #27)
        return {
            'var_name':        var_name,
            'handle_var':      f"// UNKNOWN: {feature_name}",
            'handle_init':     f'Print("ERROR: indicator_mapper has no MT5 template for {feature_name}. The EA will not enter trades."); indicatorFailed = true;',
            'read_code':       f'indicatorFailed = true; double val_{var_name} = 0.0; // ERROR: unknown indicator {feature_name} — see init log',
            'custom_indicator': True,
            'description':     f"Unknown indicator: {feature_name}",
        }

    # Substitution context
    p  = params[0] if params else '14'
    p1 = params[0] if len(params) > 0 else '20'
    p2 = params[1] if len(params) > 1 else '2'
    p2s = p2.replace('.', '_')

    def sub(s):
        return (s
            .replace('{tf}',    tf)
            .replace('{mt5_tf}', mt5_tf)
            .replace('{tv_tf}', tv_tf)
            .replace('{var}',   var_name)
            .replace('{p}',     p)
            .replace('{p1}',    p1)
            .replace('{p2}',    p2)
            .replace('{p2s}',   p2s)
        )

    custom = template.get('custom_indicator_mt5', False)

    if platform == 'mt5':
        if 'mt5_code' in template:
            # Inline code, no handle needed
            return {
                'var_name':        var_name,
                'handle_var':      '',
                'handle_init':     '',
                'read_code':       f"double val_{var_name} = {sub(template['mt5_code'])};",
                'custom_indicator': custom,
                'description':     sub(template.get('description', ind)),
            }
        else:
            handle_var  = sub(template.get('mt5_handle_var', ''))
            handle_init = sub(template.get('mt5_handle_init', ''))
            read_code   = sub(template.get('mt5_buffer_read', f'double val_{var_name} = 0.0;'))
            return {
                'var_name':        var_name,
                'handle_var':      handle_var,
                'handle_init':     handle_init,
                'read_code':       read_code,
                'custom_indicator': custom,
                'description':     sub(template.get('description', ind)),
            }
    else:
        # Tradovate Python
        tv_code = sub(template.get('tradovate_code', '0.0'))
        return {
            'var_name':        var_name,
            'python_code':     f"val_{var_name} = {tv_code}",
            'custom_indicator': False,  # pandas-ta handles everything
            'description':     sub(template.get('description', ind)),
        }


def get_all_handles_for_rules(rules, platform='mt5'):
    """
    Given a list of rule dicts, return all unique indicator code entries needed.
    Used to generate OnInit() and global variables section of the EA.
    """
    seen = set()
    result = []
    win_rules = [r for r in rules if r.get('prediction') == 'WIN']
    for rule in win_rules:
        for cond in rule.get('conditions', []):
            feat = cond.get('feature', '')
            if feat in seen:
                continue
            seen.add(feat)
            result.append(get_mql_code(feat, platform))
    return result


def get_custom_indicator_list(rules):
    """
    Return list of custom indicator names that need to be installed separately (MT5).
    """
    handles = get_all_handles_for_rules(rules, platform='mt5')
    custom = []
    for h in handles:
        if h.get('custom_indicator'):
            desc = h.get('description', h.get('var_name', '?'))
            if desc not in custom:
                custom.append(desc)
    return custom
