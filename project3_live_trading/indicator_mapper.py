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

# ── Indicator pattern templates ───────────────────────────────────────────────
# Keys are regex patterns matching the indicator part (after timeframe prefix).
# {tf}, {mt5_tf}, {tv_tf} are substituted with actual timeframe values.
# {p}, {p1}, {p2}, {period}, etc. are substituted with parsed numeric params.

INDICATOR_PATTERNS = [
    # RSI
    (r"^rsi_(\d+)$", {
        "mt5_handle_var":  "int handle_rsi_{tf}_{p};",
        "mt5_handle_init": "handle_rsi_{tf}_{p} = iRSI(NULL,{mt5_tf},{p},PRICE_CLOSE); if(handle_rsi_{tf}_{p}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double buf_rsi_{tf}_{p}[1]; CopyBuffer(handle_rsi_{tf}_{p},0,0,1,buf_rsi_{tf}_{p}); double val_{var} = buf_rsi_{tf}_{p}[0];",
        "tradovate_code":  "ta.rsi(df_m{tv_tf}['close'], length={p}).iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "RSI({p}) on {tf}",
    }),
    # ADX
    (r"^adx_(\d+)$", {
        "mt5_handle_var":  "int handle_adx_{tf}_{p};",
        "mt5_handle_init": "handle_adx_{tf}_{p} = iADX(NULL,{mt5_tf},{p}); if(handle_adx_{tf}_{p}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double buf_adx_{tf}_{p}[1]; CopyBuffer(handle_adx_{tf}_{p},0,0,1,buf_adx_{tf}_{p}); double val_{var} = buf_adx_{tf}_{p}[0];",
        "tradovate_code":  "ta.adx(df_m{tv_tf}['high'], df_m{tv_tf}['low'], df_m{tv_tf}['close'], length={p})['ADX_{p}'].iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "ADX({p}) on {tf}",
    }),
    # CCI
    (r"^cci_(\d+)$", {
        "mt5_handle_var":  "int handle_cci_{tf}_{p};",
        "mt5_handle_init": "handle_cci_{tf}_{p} = iCCI(NULL,{mt5_tf},{p},PRICE_TYPICAL); if(handle_cci_{tf}_{p}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double buf_cci_{tf}_{p}[1]; CopyBuffer(handle_cci_{tf}_{p},0,0,1,buf_cci_{tf}_{p}); double val_{var} = buf_cci_{tf}_{p}[0];",
        "tradovate_code":  "ta.cci(df_m{tv_tf}['high'], df_m{tv_tf}['low'], df_m{tv_tf}['close'], length={p}).iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "CCI({p}) on {tf}",
    }),
    # ATR
    (r"^atr_(\d+)$", {
        "mt5_handle_var":  "int handle_atr_{tf}_{p};",
        "mt5_handle_init": "handle_atr_{tf}_{p} = iATR(NULL,{mt5_tf},{p}); if(handle_atr_{tf}_{p}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double buf_atr_{tf}_{p}[1]; CopyBuffer(handle_atr_{tf}_{p},0,0,1,buf_atr_{tf}_{p}); double val_{var} = buf_atr_{tf}_{p}[0];",
        "tradovate_code":  "ta.atr(df_m{tv_tf}['high'], df_m{tv_tf}['low'], df_m{tv_tf}['close'], length={p}).iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "ATR({p}) on {tf}",
    }),
    # MACD diff
    (r"^macd_fast_diff$", {
        "mt5_handle_var":  "int handle_macd_{tf};",
        "mt5_handle_init": "handle_macd_{tf} = iMACD(NULL,{mt5_tf},12,26,9,PRICE_CLOSE); if(handle_macd_{tf}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double buf_macd_{tf}[1]; CopyBuffer(handle_macd_{tf},2,0,1,buf_macd_{tf}); double val_{var} = buf_macd_{tf}[0];",
        "tradovate_code":  "ta.macd(df_m{tv_tf}['close'])['MACDh_12_26_9'].iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "MACD histogram on {tf}",
    }),
    # SMA distance
    (r"^sma_(\d+)_distance$", {
        "mt5_code":       "(iClose(NULL,{mt5_tf},1) - iMA(NULL,{mt5_tf},{p},0,MODE_SMA,PRICE_CLOSE,1)) / _Point",
        "tradovate_code": "(df_m{tv_tf}['close'].iloc[-1] - ta.sma(df_m{tv_tf}['close'], length={p}).iloc[-1])",
        "custom_indicator_mt5": False,
        "description": "Distance from SMA({p}) on {tf}",
    }),
    # Bollinger Band width
    (r"^bb_(\d+)_(\d+(?:\.\d+)?)_width$", {
        "mt5_handle_var":  "int handle_bb_{tf}_{p1}_{p2s};",
        "mt5_handle_init": "handle_bb_{tf}_{p1}_{p2s} = iBands(NULL,{mt5_tf},{p1},0,{p2},PRICE_CLOSE); if(handle_bb_{tf}_{p1}_{p2s}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double buf_bb_u_{tf}[1],buf_bb_l_{tf}[1]; CopyBuffer(handle_bb_{tf}_{p1}_{p2s},1,0,1,buf_bb_u_{tf}); CopyBuffer(handle_bb_{tf}_{p1}_{p2s},2,0,1,buf_bb_l_{tf}); double val_{var} = buf_bb_u_{tf}[0] - buf_bb_l_{tf}[0];",
        "tradovate_code":  "ta.bbands(df_m{tv_tf}['close'], length={p1}, std={p2})['BBB_{p1}_{p2}_0'].iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "Bollinger Band({p1},{p2}) width on {tf}",
    }),
    # Aroon
    (r"^aroon_(?:down|up)$", {
        "mt5_handle_var":  "int handle_aroon_{tf};",
        "mt5_handle_init": "handle_aroon_{tf} = iCustom(NULL,{mt5_tf},\"Aroon\",14); if(handle_aroon_{tf}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double buf_aroon_{tf}[1]; CopyBuffer(handle_aroon_{tf},0,0,1,buf_aroon_{tf}); double val_{var} = buf_aroon_{tf}[0];",
        "tradovate_code":  "ta.aroon(df_m{tv_tf}['high'], df_m{tv_tf}['low'], length=14)['AROOND_14'].iloc[-1]",
        "custom_indicator_mt5": True,
        "description": "Aroon on {tf} (custom indicator)",
    }),
    # Bears Power
    (r"^bear_power$", {
        "mt5_handle_var":  "int handle_bears_{tf};",
        "mt5_handle_init": "handle_bears_{tf} = iBearsPower(NULL,{mt5_tf},13); if(handle_bears_{tf}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double buf_bears_{tf}[1]; CopyBuffer(handle_bears_{tf},0,0,1,buf_bears_{tf}); double val_{var} = buf_bears_{tf}[0];",
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
        "mt5_buffer_read": "double buf_uo_{tf}[1]; CopyBuffer(handle_uo_{tf},0,0,1,buf_uo_{tf}); double val_{var} = buf_uo_{tf}[0];",
        "tradovate_code":  "ta.uo(df_m{tv_tf}['high'], df_m{tv_tf}['low'], df_m{tv_tf}['close']).iloc[-1]",
        "custom_indicator_mt5": True,
        "description": "Ultimate Oscillator on {tf} (custom indicator)",
    }),
    # DPO
    (r"^dpo$", {
        "mt5_handle_var":  "int handle_dpo_{tf};",
        "mt5_handle_init": "handle_dpo_{tf} = iCustom(NULL,{mt5_tf},\"DPO\",20); if(handle_dpo_{tf}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double buf_dpo_{tf}[1]; CopyBuffer(handle_dpo_{tf},0,0,1,buf_dpo_{tf}); double val_{var} = buf_dpo_{tf}[0];",
        "tradovate_code":  "ta.dpo(df_m{tv_tf}['close'], length=20).iloc[-1]",
        "custom_indicator_mt5": True,
        "description": "DPO(20) on {tf} (custom indicator)",
    }),
    # KST
    (r"^kst$", {
        "mt5_handle_var":  "int handle_kst_{tf};",
        "mt5_handle_init": "handle_kst_{tf} = iCustom(NULL,{mt5_tf},\"KST\",10,15,20,30); if(handle_kst_{tf}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double buf_kst_{tf}[1]; CopyBuffer(handle_kst_{tf},0,0,1,buf_kst_{tf}); double val_{var} = buf_kst_{tf}[0];",
        "tradovate_code":  "ta.kst(df_m{tv_tf}['close'])['KST_10_15_20_30_10_10_10_15'].iloc[-1]",
        "custom_indicator_mt5": True,
        "description": "KST on {tf} (custom indicator)",
    }),
    # VPT
    (r"^vpt$", {
        "mt5_handle_var":  "int handle_vpt_{tf};",
        "mt5_handle_init": "handle_vpt_{tf} = iCustom(NULL,{mt5_tf},\"VPT\"); if(handle_vpt_{tf}==INVALID_HANDLE) return(INIT_FAILED);",
        "mt5_buffer_read": "double buf_vpt_{tf}[1]; CopyBuffer(handle_vpt_{tf},0,0,1,buf_vpt_{tf}); double val_{var} = buf_vpt_{tf}[0];",
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
    (r"^distance_from_high$", {
        "mt5_code":       "(iHigh(NULL,{mt5_tf},1)-iClose(NULL,{mt5_tf},1))/_Point",
        "tradovate_code": "df_m{tv_tf}['high'].iloc[-1] - df_m{tv_tf}['close'].iloc[-1]",
        "custom_indicator_mt5": False,
        "description": "Distance from bar high on {tf}",
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
]


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

    if template is None:
        # Unknown indicator — generate placeholder
        return {
            'var_name':        var_name,
            'handle_var':      f"// UNKNOWN: {feature_name}",
            'handle_init':     f"// TODO: add handle for {feature_name}",
            'read_code':       f"double val_{var_name} = 0.0; // TODO: compute {feature_name}",
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
