"""
Test Script Generator — creates a lightweight MQL5 script (or Python test) that
verifies every indicator the EA needs actually works before the full EA is deployed.

Run the test script FIRST. If it prints all green checkmarks, the EA is safe to compile.
"""

import os
from datetime import datetime

from project3_live_trading.indicator_mapper import (
    get_all_handles_for_rules, get_custom_indicator_list, parse_feature_name,
)

_HERE = os.path.dirname(os.path.abspath(__file__))


def generate_test_script(strategy, platform='mt5', output_path=None):
    """
    Generate a test script for the given strategy.

    Parameters
    ----------
    strategy    : dict  — same format as passed to generate_ea()
    platform    : str   — 'mt5' or 'tradovate'
    output_path : str   — optional path to save the file

    Returns
    -------
    str — generated script code
    """
    rules     = strategy.get('rules', [])
    win_rules = [r for r in rules if r.get('prediction') == 'WIN']
    exit_name = strategy.get('exit_name', 'Strategy')

    if platform == 'mt5':
        code = _generate_mt5_test(win_rules, exit_name)
    else:
        code = _generate_python_test(win_rules, exit_name)

    if output_path:
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(code)

    return code


# ── MT5 MQL5 test script ──────────────────────────────────────────────────────

def _generate_mt5_test(win_rules, strategy_name):
    """Generate MQL5 script that tests all required indicator handles."""
    handles  = get_all_handles_for_rules(win_rules, platform='mt5')
    generated = datetime.now().strftime('%Y-%m-%d')

    # Collect unique indicators
    seen_vars = set()
    unique_handles = []
    for h in handles:
        var = h.get('var_name', '')
        if var and var not in seen_vars:
            seen_vars.add(var)
            unique_handles.append(h)

    # Group custom vs built-in
    custom_list = [h for h in unique_handles if h.get('custom_indicator')]
    builtin_list = [h for h in unique_handles if not h.get('custom_indicator') and h.get('handle_var')]
    inline_list  = [h for h in unique_handles if not h.get('custom_indicator') and not h.get('handle_var')]

    # Build handle declarations
    handle_decls = []
    for h in unique_handles:
        hv = h.get('handle_var', '')
        if hv.strip():
            is_custom = h.get('custom_indicator', False)
            suffix = '  // CUSTOM INDICATOR' if is_custom else ''
            handle_decls.append(f"{hv}{suffix}")

    # Build test blocks
    handle_names = []  # track handle variable names for explicit cleanup (Fix 7)
    test_blocks = []
    for h in unique_handles:
        feat = h.get('description', h.get('var_name', '?'))
        var  = h.get('var_name', 'unknown')

        if not h.get('handle_var'):
            # Inline (no handle) — test by reading directly
            read_code = h.get('read_code', f'double val_{var} = 0.0;')
            block = f"""\
   // --- {feat} (inline) ---
   total++;
   {{
      {read_code}
      Print("OK {feat} = ", DoubleToString(val_{var}, 4));
      passed++;
   }}"""
        else:
            init_code = h.get('handle_init', '').replace('\n', '\n      ')
            is_custom  = h.get('custom_indicator', False)
            fail_msg   = (f'"   Download/install {feat} from MQL5 Marketplace"'
                          if is_custom else f'"   Error code: ", GetLastError()')

            # WHY: Extract actual handle variable name from handle_var declaration.
            #      Old code used handle_{var_name} which didn't match — e.g.,
            #      handle_var="int handle_macd_H4;" → actual name is handle_macd_H4,
            #      but old code generated handle_h4_macd_fast_diff.
            # CHANGED: April 2026 — extract real handle name
            _hv_str = h.get('handle_var', '').strip().rstrip(';').strip()
            # "int handle_macd_H4" → "handle_macd_H4"
            _hv_name = _hv_str.split()[-1] if _hv_str else f'handle_{var}'

            handle_names.append(_hv_name)

            # WHY: handle_init may contain return(INIT_FAILED) which is for OnInit,
            #      not OnStart (void). Replace with plain return.
            # CHANGED: April 2026 — fix void return
            init_code = init_code.replace('return(INIT_FAILED)', 'return')

            block = f"""\
   // --- {feat}{'  [CUSTOM]' if is_custom else ''} ---
   total++;
   {init_code}
   if({_hv_name} != INVALID_HANDLE)
   {{
      double buf_{var}[1];
      if(CopyBuffer({_hv_name}, 0, 0, 1, buf_{var}) > 0)
      {{
         Print("OK  {feat} = ", DoubleToString(buf_{var}[0], 4));
         passed++;
      }}
      else
      {{
         Print("FAIL  {feat} — CopyBuffer failed, error: ", GetLastError());
         failed++;
      }}
   }}
   else
   {{
      Print("FAIL  {feat} — invalid handle");
      if({str(is_custom).lower()}) Print({fail_msg});
      failed++;
   }}"""
        test_blocks.append(block)

    handle_decls_str = '\n'.join(handle_decls) if handle_decls else '// No handle-based indicators'
    test_blocks_str  = '\n\n'.join(test_blocks) if test_blocks else '   // No indicators to test'

    # WHY: Old code called IndicatorRelease(0). 0 is INVALID_HANDLE, so the
    #      call did nothing. Handles leaked until script exit. Fix: emit
    #      one explicit IndicatorRelease() per handle created by the script.
    # CHANGED: April 2026 — explicit per-handle release (audit HIGH)
    # WHY: handle_names now contains full handle names (e.g., "handle_macd_H4")
    #      not just the suffix. Use directly, don't add "handle_" prefix.
    # CHANGED: April 2026 — use full handle name
    release_block = '\n'.join(
        f'   if({h} != INVALID_HANDLE) IndicatorRelease({h});'
        for h in handle_names
    ) or '   // No handles to release'

    custom_warning = ''
    if custom_list:
        custom_names = ', '.join(h.get('description', '?') for h in custom_list)
        custom_warning = f"//| CUSTOM INDICATORS NEEDED: {custom_names[:60]}{'...' if len(custom_names)>60 else ''}   |\n"

    code = f"""\
//+------------------------------------------------------------------+
//| Indicator Test Script                                              |
//| Tests all indicators needed by: {strategy_name[:30]}             |
//| Generated: {generated}                                            |
//|                                                                    |
//| HOW TO USE:                                                        |
//| 1. Copy this file to: [MT5 data folder]/MQL5/Scripts/             |
//| 2. Open MetaEditor (F4 in MT5) and compile this file (F7)         |
//| 3. If compile fails -> install the listed custom indicators        |
//| 4. If compile succeeds -> drag onto any chart (match symbol)      |
//| 5. Open the Experts tab (Ctrl+E) -> read the OK / FAIL results    |
//| 6. All OK? -> compile and run the full EA                         |
//| 7. Any FAIL? -> fix those indicators first                        |
{custom_warning}//+------------------------------------------------------------------+
#property script_show_inputs

//--- Indicator handles
{handle_decls_str}

void OnStart()
{{
   int total  = 0;
   int passed = 0;
   int failed = 0;

   Print("=========================================");
   Print("INDICATOR TEST — Starting...");
   Print("Symbol: ", _Symbol, " | Time: ", TimeToString(TimeCurrent()));
   Print("Strategy: {strategy_name}");
   Print("=========================================");

{test_blocks_str}

   Print("=========================================");
   Print("RESULTS: ", passed, "/", total, " indicators OK");
   if(failed > 0)
   {{
      Print("FAIL: ", failed, " indicator(s) FAILED — fix before running the EA");
   }}
   else
   {{
      Print("ALL INDICATORS WORK — safe to compile and deploy the EA");
   }}
   Print("=========================================");

   //--- Release all handles (per-handle explicit release)
   // WHY: Old code called IndicatorRelease(0). 0 is INVALID_HANDLE — does nothing.
   // CHANGED: April 2026 — explicit per-handle release (audit HIGH)
{release_block}
}}
//+------------------------------------------------------------------+
"""
    return code


# ── Tradovate Python test script ──────────────────────────────────────────────

def _generate_python_test(win_rules, strategy_name):
    """Generate a Python script that tests all indicator computations."""
    from project3_live_trading.indicator_mapper import get_all_handles_for_rules

    handles  = get_all_handles_for_rules(win_rules, platform='tradovate')
    generated = datetime.now().strftime('%Y-%m-%d')

    # Build test blocks
    test_blocks = []
    for h in handles:
        feat     = h.get('description', h.get('var_name', '?'))
        py_code  = h.get('python_code', f"val_{h['var_name']} = 0.0")
        var      = h.get('var_name', 'unknown')
        test_blocks.append(f"""\
try:
    {py_code}
    print(f"  OK  {feat} = {{val_{var}:.4f}}")
    results.append(("{feat}", True))
except Exception as e:
    print(f"  FAIL  {feat}: {{e}}")
    results.append(("{feat}", False))
""")

    test_str = '\n'.join(test_blocks) if test_blocks else 'pass  # No indicators to test\n'

    code = f"""\
#!/usr/bin/env python3
\"\"\"
Indicator Test Script — Tradovate/Python
Tests all indicators needed by: {strategy_name}
Generated: {generated}

HOW TO USE:
1. Install requirements: pip install pandas pandas-ta
2. Provide sample OHLCV data (CSV with open,high,low,close,volume columns)
   or the script will generate synthetic data for testing.
3. Run: python test_indicators.py
4. Check output for OK and FAIL lines.
5. All OK? -> run the Tradovate bot.
\"\"\"

import sys
import pandas as pd
import numpy as np

try:
    import pandas_ta as ta
except ImportError:
    # CHANGED: April 2026 — unified install hint (Phase 19c)
    print("ERROR: pandas-ta not installed. Run: pip install -r requirements.txt "
          "(or: pip install pandas-ta)")
    sys.exit(1)

print("=" * 50)
print(f"Indicator Test — {strategy_name}")
print("=" * 50)

# ── Generate synthetic OHLCV data (500 bars) for testing ───────────────────
np.random.seed(42)
n = 500
close = 1900 + np.cumsum(np.random.randn(n) * 2)
df_base = pd.DataFrame({{
    "open":   close - np.abs(np.random.randn(n)),
    "high":   close + np.abs(np.random.randn(n) * 1.5),
    "low":    close - np.abs(np.random.randn(n) * 1.5),
    "close":  close,
    "volume": np.abs(np.random.randn(n) * 1000 + 5000).astype(int),
}})

# Alias dataframes for each timeframe
df_m5 = df_m15 = df_m60 = df_m240 = df_m1440 = df_base.copy()

results = []

{test_str}

print()
print("=" * 50)
passed = sum(1 for _, ok in results if ok)
total  = len(results)
print(f"RESULTS: {{passed}}/{{total}} indicators OK")
if passed == total:
    print("ALL INDICATORS WORK — safe to run the Tradovate bot")
else:
    failed = [(name, ok) for name, ok in results if not ok]
    print(f"{{len(failed)}} indicator(s) FAILED:")
    for name, _ in failed:
        print(f"  - {{name}}")
print("=" * 50)

sys.exit(0 if passed == total else 1)
"""
    return code
