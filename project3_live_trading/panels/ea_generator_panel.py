"""
EA Generator Panel — pick a validated strategy, configure platform/prop firm settings,
and generate a complete MetaTrader 5 (.mq5) or Tradovate (Python) bot.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import sys
import json
import shutil

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)

import state

BG      = "#f0f2f5"
WHITE   = "white"
GREEN   = "#2d8a4e"
RED     = "#e94560"
AMBER   = "#996600"
DARK    = "#1a1a2a"
GREY    = "#666666"
MIDGREY = "#555566"

# ── Load prop firms dynamically from JSON files ──────────────────────────────
# WHY: No hardcoding. Adding a firm = drop JSON in prop_firms/.
#      Each firm has its own DD mechanics, trading_rules, and restrictions.
def _load_firms():
    """Load all prop firms from prop_firms/*.json. Returns {display_name: full_data}."""
    import glob
    prop_dir = os.path.join(project_root, 'prop_firms')
    firms = {}
    for fp in sorted(glob.glob(os.path.join(prop_dir, '*.json'))):
        try:
            with open(fp, encoding='utf-8') as f:
                data = json.load(f)
            firms[data.get('firm_name', data.get('firm_id', '?'))] = data
        except Exception:
            pass
    firms['Custom'] = {
        'firm_id': 'custom', 'firm_name': 'Custom',
        'challenges': [{'challenge_id': 'custom', 'challenge_name': 'Custom',
                         'account_sizes': [10000, 25000, 50000, 100000],
                         'phases': [{'phase_name': 'Evaluation', 'profit_target_pct': 8.0,
                                     'max_daily_drawdown_pct': 5.0, 'max_total_drawdown_pct': 10.0}],
                         'funded': {'profit_split_pct': 80, 'max_total_drawdown_pct': 10.0,
                                    'max_daily_drawdown_pct': 5.0, 'payout_period_days': 14}}],
        'trading_rules': [], 'drawdown_mechanics': {},
    }
    return firms

_FIRMS = _load_firms()

# ── Module-level state ────────────────────────────────────────────────────────
_strategies       = []
_strategy_var     = None
_platform_var     = None   # 'mt5' or 'tradovate'
_strat_info_lbl   = None
_badge_lbl        = None
_scroll_canvas    = None
_code_text        = None
_generate_btn     = None
_status_lbl       = None

# Settings vars
_symbol_var       = None
_magic_var        = None
_risk_var         = None
_spread_var       = None
_cooldown_var     = None
_news_cb_var      = None
_news_min_var     = None
_firm_var         = None
_ea_stage_var     = None   # 'Evaluation' or 'Funded'
_ea_challenge_var = None
_ea_account_var   = None
_rules_info_lbl   = None
_dd_info_lbl      = None   # shows DD alert levels vs blow levels
_daily_dd_var     = None
_total_dd_var     = None
_safety_var       = None
_consistency_var  = None
_max_day_var      = None
_session_vars     = {}
_day_vars         = {}

_auto_min_hold = [0]     # WHY: Stores optimizer's min_hold value for generate call

# Per-condition threshold entry vars: list of (feature, op, tk.StringVar)
_condition_vars   = []

# Exit vars
_sl_var = _tp_var = _trail_var = None

# Test script state
_test_script_text  = None   # ScrolledText showing generated test script
_test_generated    = False  # True once Step 1 is done
_step2_btn         = None   # Full EA button (only enabled after Step 1)


def _load_strategies():
    global _strategies
    try:
        from project2_backtesting.strategy_refiner import load_strategy_list
        _strategies = load_strategy_list()
    except Exception as e:
        print(f"[ea_gen_panel] {e}")
        _strategies = []


def _get_selected_index():
    if not _strategies or _strategy_var is None:
        return None
    val = _strategy_var.get()
    for s in _strategies:
        if s['label'] == val:
            return s['index']
    return None


def _get_strategy_data(idx):
    """Load full strategy data from backtest matrix, saved rules, or optimizer output.

    WHY: The index can be:
      - Integer (0, 1, 2, ...): direct backtest matrix index
      - "saved_N": load from saved_rules.json entry #N
      - "optimizer_latest": load from _validator_optimized.json

    CHANGED: April 2026 — EA generator supports saved rules and optimizer results
    """
    # ── Saved rules ──
    if isinstance(idx, str) and idx.startswith('saved_'):
        try:
            rule_id = int(idx.split('_')[1])
            from shared.saved_rules import load_all
            for entry in load_all():
                if entry.get('id') == rule_id:
                    rule = entry.get('rule', {})
                    result = dict(rule)
                    # Ensure 'rules' has rule dicts with conditions
                    if not result.get('rules') or not any(r.get('conditions') for r in result.get('rules', [])):
                        opt = result.get('optimized_rules', [])
                        if opt and any(r.get('conditions') for r in opt):
                            result['rules'] = opt
                    if not result.get('rules') or not any(r.get('conditions') for r in result.get('rules', [])):
                        conds = result.get('conditions', [])
                        if conds:
                            result['rules'] = [{'prediction': 'WIN', 'conditions': conds}]
                    # Normalize keys
                    if not result.get('exit_params') and result.get('exit_strategy_params'):
                        result['exit_params'] = result['exit_strategy_params']
                    if not result.get('exit_strategy_params') and result.get('exit_params'):
                        result['exit_strategy_params'] = result['exit_params']
                    if not result.get('entry_tf'):
                        result['entry_tf'] = result.get('entry_timeframe', '')

                    # WHY: Old optimizer saves have empty rules/exit/direction.
                    #      Resolve from backtest_matrix or analysis_report.
                    # CHANGED: April 2026 — resolve missing data
                    _needs_resolution = (not result.get('exit_name') and not result.get('exit_class'))
                    if _needs_resolution:
                        try:
                            _combo = result.get('rule_combo', '')
                            # Skip resolution if combo is empty (prevents matching everything)
                            if _combo and len(_combo) > 3:  # Valid combo names are longer than 3 chars
                                _mp = os.path.join(project_root, 'project2_backtesting',
                                                    'outputs', 'backtest_matrix.json')
                                if os.path.exists(_mp):
                                    import os.path as _osp
                                    # Only proceed if file is reasonably sized (< 50MB)
                                    if _osp.getsize(_mp) < 50 * 1024 * 1024:
                                        with open(_mp, 'r', encoding='utf-8') as _mf:
                                            _md = json.load(_mf)
                                        _results = _md.get('results', []) or _md.get('matrix', [])
                                        # Limit search to first 100 entries to prevent hangs
                                        for _res in _results[:100]:
                                            _res_combo = str(_res.get('rule_combo', ''))
                                            # WHY: Old saves have optimizer-assigned names
                                            #      like 'Min hold 30m' which don't match
                                            #      matrix names like 'All rules combined (BUY)'.
                                            #      Try exact match first, then partial match,
                                            #      then fall back to best-trade-count match.
                                            # CHANGED: April 2026 — fuzzy rule_combo matching
                                            if (_res_combo == _combo or
                                                _combo in _res_combo or
                                                _res_combo in _combo):
                                                result['exit_name'] = _res.get('exit_name', '')
                                                result['exit_class'] = _res.get('exit_class', '')
                                                result['exit_params'] = _res.get('exit_params', {})
                                                result['exit_strategy_params'] = _res.get('exit_params', {})
                                                if not result.get('direction'):
                                                    if '(BUY)' in _res_combo: result['direction'] = 'BUY'
                                                    elif '(SELL)' in _res_combo: result['direction'] = 'SELL'
                                                if not result.get('rules') or not any(r.get('conditions') for r in result.get('rules', [])):
                                                    result['rules'] = _res.get('rules', [])
                                                    result['optimized_rules'] = _res.get('rules', [])
                                                print(f"[EA GEN] Resolved exit/rules from matrix: "
                                                      f"exit={result.get('exit_name', '?')}, rules={len(result.get('rules', []))}")
                                                break
                                        # Fallback: if no match by name, use the result with
                                        # the most trades (likely the main strategy)
                                        if not result.get('exit_name') and _results:
                                            _best = max(_results, key=lambda x: x.get('total_trades', x.get('trade_count', 0)))
                                            result['exit_name'] = _best.get('exit_name', '')
                                            result['exit_class'] = _best.get('exit_class', '')
                                            result['exit_params'] = _best.get('exit_params', {})
                                            result['exit_strategy_params'] = _best.get('exit_params', {})
                                            if not result.get('direction'):
                                                _bc = str(_best.get('rule_combo', ''))
                                                if '(BUY)' in _bc: result['direction'] = 'BUY'
                                                elif '(SELL)' in _bc: result['direction'] = 'SELL'
                                            if not result.get('rules') or not any(r.get('conditions') for r in result.get('rules', [])):
                                                result['rules'] = _best.get('rules', [])
                                                result['optimized_rules'] = _best.get('rules', [])
                                            print(f"[EA GEN] Fallback: resolved from best result "
                                                  f"({_best.get('rule_combo','?')}, "
                                                  f"{_best.get('total_trades', _best.get('trade_count', 0))} trades)")
                        except Exception as _e:
                            print(f"[EA GEN] Could not resolve missing data: {_e}")

                    # Auto-fill risk settings from saved rule
                    _rs = result.get('risk_settings', {})
                    if _rs:
                        if _rs.get('risk_pct') and _risk_var:
                            _risk_var.set(str(_rs['risk_pct']))
                        if _rs.get('account_size') and _ea_account_var:
                            try:
                                _ea_account_var.set(str(int(_rs['account_size'])))
                            except (ValueError, TypeError):
                                pass
                        if _rs.get('firm') and _firm_var:
                            try: _firm_var.set(_rs['firm'])
                            except Exception: pass
                        if _rs.get('stage') and _ea_stage_var:
                            try: _ea_stage_var.set(_rs['stage'])
                            except Exception: pass

                    print(f"[EA GEN] Loaded saved rule #{rule_id}: "
                          f"{len(result.get('rules', []))} rules, "
                          f"exit={result.get('exit_name', result.get('exit_class', '?'))}")
                    return result
        except Exception as e:
            print(f"[EA GEN] Error loading saved rule {idx}: {e}")
            import traceback; traceback.print_exc()
        return {}

    # ── Optimizer latest ──
    if isinstance(idx, str) and idx == 'optimizer_latest':
        try:
            opt_path = os.path.join(project_root, 'project2_backtesting',
                                     'outputs', '_validator_optimized.json')
            if os.path.exists(opt_path):
                with open(opt_path, 'r', encoding='utf-8') as f:
                    result = json.load(f)
                if not result.get('exit_strategy_params') and result.get('exit_params'):
                    result['exit_strategy_params'] = result['exit_params']
                return result
        except Exception:
            pass
        return {}

    # ── Separator ──
    if isinstance(idx, str) and idx.startswith('__separator'):
        return {}

    # ── Integer index → backtest matrix ──
    try:
        if isinstance(idx, str) and idx.isdigit():
            idx = int(idx)
        path = os.path.join(project_root, 'project2_backtesting', 'outputs', 'backtest_matrix.json')
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        results = data.get('results', []) or data.get('matrix', [])
        if isinstance(idx, int) and 0 <= idx < len(results):
            r = results[idx]
            if not r.get('exit_strategy_params') and r.get('exit_params'):
                r['exit_strategy_params'] = r['exit_params']
            return r
    except Exception:
        pass
    return {}


def _update_strat_info():
    global _strat_info_lbl, _badge_lbl, _condition_vars
    if not _strat_info_lbl:
        return
    idx = _get_selected_index()
    if idx is None:
        return

    for s in _strategies:
        if s['index'] == idx:
            text = (f"{s['total_trades']} trades  |  WR {s['win_rate']:.1f}%  |  "
                    f"net {s['net_total_pips']:+.0f} pips  |  PF {s['net_profit_factor']:.2f}")
            _strat_info_lbl.configure(text=text, fg=MIDGREY)
            break

    if _badge_lbl:
        try:
            from project2_backtesting.strategy_validator import get_validation_for_strategy
            result = get_validation_for_strategy(idx)
            if result:
                combined = result.get('combined', {})
                grade = combined.get('grade', '?')
                score = combined.get('confidence_score', 0)
                colors = {'A': '#28a745', 'B': '#2d8a4e', 'C': '#996600', 'D': '#e67e00', 'F': '#e94560'}
                _badge_lbl.configure(
                    text=f"Validation: Grade {grade} ({score}/100)",
                    fg=colors.get(grade, GREY))
            else:
                _badge_lbl.configure(text="Not validated — run Strategy Validator first", fg=AMBER)
        except Exception:
            _badge_lbl.configure(text="", fg=GREY)

    # WHY: Old code overwrote validation badge with analysis_report stale check.
    #      The stale check applies to the REPORT, not the selected strategy.
    #      A validated Grade A strategy would show "Stale rules" because the
    #      analysis_report is old — confusing and wrong.
    #      Only show stale check for strategies that come FROM the analysis_report
    #      (source == 'backtest'), not saved rules or optimizer results.
    # CHANGED: April 2026 — don't overwrite validation with stale check
    # (Stale check is already in _generate() at line 696 where it matters.)

    # Update condition threshold vars
    _refresh_condition_vars(idx)

    # WHY (leverage): Warn the user when the current risk%/SL/account
    #      combination exceeds what the prop firm's leverage allows for
    #      this instrument — before they generate and deploy the EA.
    # CHANGED: April 2026 — margin awareness in EA panel
    try:
        from shared.prop_firm_engine import get_leverage_for_symbol, get_instrument_type
        _firm_name = _firm_var.get() if _firm_var else ''
        _firm_d    = _FIRMS.get(_firm_name, {})
        _sym       = _symbol_var.get() if _symbol_var else 'XAUUSD'
        _leverage  = get_leverage_for_symbol(_firm_d, _sym)
        _acct      = float(_ea_account_var.get()) if _ea_account_var else 10000
        _risk_pct  = float(_risk_var.get()) if _risk_var else 1.0

        # Approximate contract size and pip_value from instrument type
        _inst = get_instrument_type(_sym)
        if _inst == 'metals':
            _contract = 100.0
            _approx_price = 3300.0
        else:
            _contract = 100000.0
            _approx_price = 1.10
        # WHY: Read pip_value from strategy data, not hardcoded.
        # CHANGED: April 2026 — strategy-driven pip_value
        _pip_val = float(strat_data.get('pip_value_per_lot',
                  strat_data.get('run_settings', {}).get('pip_value_per_lot', 1.0)))

        _margin_per_lot = (_contract * _approx_price) / _leverage
        _risk_dollars   = _acct * _risk_pct / 100.0
        _default_sl     = float(_sl_var.get()) if _sl_var else 150.0
        _lot_estimate   = _risk_dollars / (_default_sl * _pip_val) if _default_sl > 0 else 0
        _margin_needed  = _lot_estimate * _margin_per_lot

        if _margin_needed > _acct * 0.95 and _strat_info_lbl:
            _max_risk = (_acct * 0.95 / _margin_per_lot) * _default_sl * _pip_val / _acct * 100
            _warn = (f"  ⚠ Margin: {_risk_pct}% risk ≈ {_lot_estimate:.2f} lots needs "
                     f"${_margin_needed:,.0f} margin (1:{_leverage} {_inst}). "
                     f"Max safe risk: {_max_risk:.1f}%")
            _current = _strat_info_lbl.cget('text')
            if '⚠ Margin:' not in _current:
                _strat_info_lbl.configure(text=_current + f"\n{_warn}", fg=MIDGREY)
    except Exception:
        pass


def _auto_fill_risk(strat_data):
    """Auto-fill risk management fields from strategy's saved risk_settings.

    WHY: The Strategy Refiner sets risk % based on the prop firm and stage
         (e.g., FTMO Funded → 0.5%). This was used during optimization but
         wasn't flowing to the EA Generator — user had to re-enter manually,
         risking mismatches. Now auto-fills from saved settings.
    CHANGED: April 2026 — risk auto-fill
    """
    # WHY: Rule carries risk_pct directly (margin-capped or firm value).
    #      Check rule-level first before falling back to risk_settings or config.
    # CHANGED: April 2026 — risk from rule
    _direct_risk = 0
    for _dr in strat_data.get('rules', []):
        _drv = _dr.get('risk_pct', 0)
        if _drv and float(_drv) > 0:
            _direct_risk = float(_drv)
            break
    if not _direct_risk:
        _direct_risk = float(strat_data.get('risk_pct', 0))
    if _direct_risk > 0 and _risk_var:
        _risk_var.set(str(_direct_risk))
        print(f"[EA GEN] Risk from rule: {_direct_risk}%")
    rs = strat_data.get('risk_settings', {})
    if not rs:
        # WHY: P1 config has firm-derived risk. P2 config has user-set risk.
        #      Try P1 first (firm-derived), then P2 (user-set).
        # CHANGED: April 2026 — P1 config fallback for risk
        try:
            import importlib.util as _rf_ilu
            _rf_cl_path = os.path.join(project_root,
                'project1_reverse_engineering', 'config_loader.py')
            _rf_spec = _rf_ilu.spec_from_file_location('_rf_cl', _rf_cl_path)
            _rf_mod = _rf_ilu.module_from_spec(_rf_spec)
            _rf_spec.loader.exec_module(_rf_mod)
            _rf_p1 = _rf_mod.load()
            _p1_risk = float(_rf_p1.get('risk_pct', 0))
            _p1_acct = int(float(_rf_p1.get('prop_firm_account', 0)))
            _p1_firm = _rf_p1.get('prop_firm_name', '')
            _p1_stage = _rf_p1.get('prop_firm_stage', '')
            if _p1_risk > 0:
                rs = {
                    'risk_pct': _p1_risk,
                    'account_size': _p1_acct if _p1_acct > 0 else 10000,
                    'firm': _p1_firm,
                    'stage': _p1_stage,
                }
        except Exception:
            pass

        if not rs:
            try:
                cfg_path = os.path.join(project_root, 'project2_backtesting', 'backtest_config.json')
                if os.path.exists(cfg_path):
                    with open(cfg_path, 'r', encoding='utf-8') as f:
                        cfg = json.load(f)
                    rs = {
                        'risk_pct': float(cfg.get('risk_pct', 1.0)),
                        'account_size': int(float(cfg.get('starting_capital', 10000))),
                        'firm': '',
                        'stage': '',
                    }
            except Exception:
                return

    # Risk %
    risk = rs.get('risk_pct')

    # WHY: If no risk in strategy or config, derive from prop firm trading rules.
    #      Firm says risk_pct_range: [0.8, 1.5] for eval — use 0.8 (conservative).
    # CHANGED: April 2026 — auto-fill risk from firm
    if not risk:
        try:
            import sys as _rf_sys
            _rf_p1_dir = os.path.join(project_root, 'project1_reverse_engineering')
            if _rf_p1_dir not in _rf_sys.path:
                _rf_sys.path.insert(0, _rf_p1_dir)
            import config_loader as _rf_cl
            _rf_cfg = _rf_cl.load()
            _rf_firm_id = _rf_cfg.get('prop_firm_id', '')
            _rf_stage = _rf_cfg.get('prop_firm_stage', 'Evaluation').lower()
            if _rf_firm_id:
                from shared.prop_firm_engine import load_all_firms
                _rf_firms = load_all_firms()
                if _rf_firm_id in _rf_firms:
                    _rf_data = _rf_firms[_rf_firm_id].config
                    for _tr in _rf_data.get('trading_rules', []):
                        if _tr.get('stage') not in (_rf_stage, 'both'):
                            continue
                        _params = _tr.get('parameters', {})
                        if _tr.get('type') in ('eval_settings', 'eval_strategy'):
                            risk = _params.get('risk_pct_range', [1.0])[0]
                            print(f"[EA PANEL] Risk from firm rules ({_rf_stage}): {risk}%")
                            break
                        elif _tr.get('type') == 'funded_accumulate':
                            risk = _params.get('risk_pct', 0.5)
                            print(f"[EA PANEL] Risk from firm rules (funded): {risk}%")
                            break
        except Exception as _rfe:
            print(f"[EA PANEL] Could not read firm risk: {_rfe}")

    if risk and _risk_var:
        _risk_var.set(str(risk))

    # Account size
    acct = rs.get('account_size')
    if acct and _ea_account_var:
        try:
            _ea_account_var.set(str(int(acct)))
        except (ValueError, TypeError):
            pass

    # Firm
    firm = rs.get('firm', '')
    if firm and _firm_var:
        try:
            _firm_var.set(firm)
        except Exception:
            pass

    # Stage
    stage = rs.get('stage', '')
    if stage and _ea_stage_var:
        try:
            _ea_stage_var.set(stage)
        except Exception:
            pass

    # Symbol from backtest config
    try:
        cfg_path = os.path.join(project_root, 'project2_backtesting', 'backtest_config.json')
        if os.path.exists(cfg_path):
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            sym = cfg.get('symbol', '')
            if sym and _symbol_var:
                _symbol_var.set(sym)
    except Exception:
        pass

    _parts = []
    if risk: _parts.append(f"risk={risk}%")
    if acct: _parts.append(f"account=${acct:,}")
    if firm: _parts.append(f"firm={firm}")
    if stage: _parts.append(f"stage={stage}")
    if _parts:
        print(f"[EA GEN] Auto-filled risk settings: {', '.join(_parts)}")


def _refresh_condition_vars(idx):
    global _condition_vars
    if not _condition_frame:
        return
    # Clear existing
    for w in _condition_frame.winfo_children():
        w.destroy()
    _condition_vars.clear()

    # WHY: Load strategy data ONCE — old code called _get_strategy_data
    #      twice (line 415 and 442), reading JSON from disk each time.
    # CHANGED: April 2026 — single load
    strat_data = _get_strategy_data(idx)
    rules = strat_data.get('rules', [])
    win_rules = [r for r in rules if r.get('prediction') == 'WIN']

    if not win_rules:
        tk.Label(_condition_frame, text="No conditions found — load a strategy first.",
                 font=("Segoe UI", 9, "italic"), bg=WHITE, fg=GREY).pack(anchor="w")
        return

    for ri, rule in enumerate(win_rules, 1):
        tk.Label(_condition_frame, text=f"Rule {ri}:",
                 font=("Segoe UI", 9, "bold"), bg=WHITE, fg=DARK).pack(anchor="w", pady=(4, 0))
        for cond in rule.get('conditions', []):
            feat = cond.get('feature', '')
            op   = cond.get('operator', '>')
            val  = cond.get('value', 0)
            var  = tk.StringVar(value=f"{val:.6f}")
            _condition_vars.append((feat, op, var))

            row = tk.Frame(_condition_frame, bg=WHITE)
            row.pack(fill="x", pady=1, padx=(12, 0))
            tk.Label(row, text=f"{feat} {op}", font=("Consolas", 8),
                     bg=WHITE, fg=MIDGREY, width=40, anchor="w").pack(side=tk.LEFT)
            tk.Entry(row, textvariable=var, width=14, font=("Consolas", 8)
                     ).pack(side=tk.LEFT, padx=(4, 0))

    # Exit params — from same strat_data (no second load)
    exit_name = strat_data.get('exit_name', strat_data.get('exit_class', 'FixedSLTP'))
    exit_params = strat_data.get('exit_strategy_params', strat_data.get('exit_params', {}))

    # WHY: TimeBased exit has no tp_pips. Old code defaulted to 300,
    #      making user think TP was active. Show 0 for exits without TP.
    # CHANGED: April 2026 — exit-aware defaults
    if exit_name in ('TimeBased', 'Time-Based', 'IndicatorExit', 'Indicator Exit'):
        _default_tp = 0
    else:
        _default_tp = 300

    if _sl_var:    _sl_var.set(str(exit_params.get('sl_pips', 150)))
    if _tp_var:    _tp_var.set(str(exit_params.get('tp_pips', _default_tp)))
    if _trail_var: _trail_var.set(str(exit_params.get('trail_pips', exit_params.get('trail_distance_pips', 100))))

    # Show exit strategy name
    tk.Label(_condition_frame, text=f"\nExit: {exit_name}",
             font=("Segoe UI", 9, "bold"), bg=WHITE, fg="#667eea").pack(anchor="w", pady=(8, 0))
    for k, v in exit_params.items():
        if k != 'pip_size':
            tk.Label(_condition_frame, text=f"  {k}: {v}",
                     font=("Consolas", 8), bg=WHITE, fg=MIDGREY).pack(anchor="w", padx=(12, 0))

    # ── Auto-fill filters from optimizer or saved rules ───────────────────
    # WHY: The optimizer finds the best filters (max_trades_per_day, sessions,
    #      cooldown, etc.) but these values were lost when moving to the EA
    #      generator. This auto-fills the filter fields so the EA matches
    #      the optimized strategy exactly.
    # CHANGED: April 2026 — optimizer filters flow to EA generator
    _auto_fill_filters(idx, strat_data)

    # ── Auto-fill entry timeframe ─────────────────────────────────────────
    # WHY: Rules discovered on M15 need PERIOD_M15 in the EA. The entry TF
    #      should be read from the strategy data, not defaulted to H1.
    #      With multi-TF backtest, each row now carries its own entry_tf —
    #      check that first (highest priority).
    # CHANGED: April 2026 — multi-TF support + entry TF flows to EA
    entry_tf = (
        strat_data.get('entry_tf') or                  # multi-TF backtest row
        strat_data.get('entry_timeframe') or           # legacy field name
        # WHY: Same as view_results.py fix — stats are flattened to top level
        # CHANGED: April 2026 — read flattened stats from strat_data top level
        (strat_data.get('stats') or strat_data).get('entry_tf') or # nested fallback
        None
    )
    if not entry_tf:
        try:
            report_path = os.path.join(project_root, 'project1_reverse_engineering',
                                        'outputs', 'analysis_report.json')
            if os.path.exists(report_path):
                with open(report_path, 'r') as f:
                    report = json.load(f)
                entry_tf = report.get('entry_timeframe')
        except Exception:
            pass
    if not entry_tf:
        try:
            from project2_backtesting.panels.configuration import load_config as _lc
            entry_tf = _lc().get('winning_scenario', 'H1')
        except Exception:
            entry_tf = 'H1'
    print(f"[EA GEN] Using entry timeframe: {entry_tf}")

    # ── Auto-fill risk management from saved strategy ────────────────
    # WHY: The strategy was optimized at a specific risk/firm/stage.
    #      Auto-fill so the EA matches exactly. User can still edit.
    # CHANGED: April 2026 — risk auto-fill from saved strategy
    _auto_fill_risk(strat_data)

    # ── Leverage info from saved strategy ──────────────────────────
    # WHY: Show the leverage constraint that was active during the
    #      backtest so the user knows the lot sizes in the EA match
    #      what was tested. Reads from top-level leverage key (set by
    #      View Results save) or from run_settings fallback.
    # CHANGED: April 2026 — strategy leverage in EA generator
    _strat_lev = strat_data.get('leverage', 0)
    _strat_firm = strat_data.get('firm_name', strat_data.get('firm_id', ''))
    _rs = strat_data.get('run_settings', {})
    if not _strat_lev:
        _strat_lev = _rs.get('leverage', 0)
    if not _strat_firm:
        _strat_firm = _rs.get('firm_name', _rs.get('firm_id', ''))
    if _strat_lev > 0 and _condition_frame:
        _lev_label = f"Leverage: 1:{_strat_lev}"
        if _strat_firm:
            _lev_label += f"  ({_strat_firm})"
        tk.Label(_condition_frame, text=_lev_label,
                 font=("Segoe UI", 9), bg=WHITE, fg="#e67e00").pack(anchor="w", pady=(4, 0))


_condition_frame = None


def _auto_fill_filters(idx, strat_data):
    """Auto-fill trading filter fields from the strategy's own data ONLY.

    WHY: Old code searched saved_rules.json and _validator_optimized.json
         for matching filters using OR logic (rule_combo OR exit_name).
         This loaded filters from DIFFERENT strategies that happened to
         share the same exit name. A min_hold=30m from an optimizer run
         on "All rules combined" would apply to "Rule 1" just because
         both used Time-Based exit.
    CHANGED: April 2026 — only use strategy's own filters, no guessing
    """
    # Only use filters that are PART OF this strategy's data
    filters = strat_data.get('filters_applied')

    # For saved rules: check the rule's own filters
    if not filters and isinstance(idx, str) and idx.startswith('saved_'):
        filters = strat_data.get('filters', strat_data.get('filters_applied'))

    # For optimizer_latest: use its own filters
    if not filters and isinstance(idx, str) and idx == 'optimizer_latest':
        filters = strat_data.get('filters', strat_data.get('filters_applied'))

    if not filters:
        # No filters for this strategy — reset to defaults
        if _cooldown_var: _cooldown_var.set("0")
        _auto_min_hold[0] = 0
        return

    # Apply filters to UI fields
    applied = []

    max_per_day = filters.get('max_trades_per_day')
    if max_per_day and _max_day_var:
        _max_day_var.set(str(int(max_per_day)))
        applied.append(f"max {max_per_day} trades/day")

    cooldown = filters.get('cooldown_minutes')
    if cooldown and _cooldown_var:
        _cooldown_var.set(str(int(cooldown)))
        applied.append(f"cooldown {cooldown}min")
    else:
        if _cooldown_var: _cooldown_var.set("0")

    min_hold = filters.get('min_hold_minutes')
    if min_hold and min_hold > 0:
        _auto_min_hold[0] = int(min_hold)
        applied.append(f"min hold {min_hold}min")
    else:
        _auto_min_hold[0] = 0
        # Fall back to firm JSON restriction if available
        try:
            _fd = _FIRMS.get(_firm_var.get() if _firm_var else '', {})
            if _fd:
                _restrictions = _fd.get('challenges', [{}])[0].get('restrictions', {})
                _min_sec = int(_restrictions.get('min_trade_duration_seconds', 0))
                if _min_sec > 0:
                    _auto_min_hold[0] = max(1, _min_sec // 60)
        except Exception:
            pass

    sessions = filters.get('sessions', [])
    if sessions and _session_vars:
        for var in _session_vars.values():
            var.set(False)
        for sess in sessions:
            if sess in _session_vars:
                _session_vars[sess].set(True)
        applied.append(f"sessions: {', '.join(sessions)}")

    days = filters.get('days', [])
    if days and _day_vars:
        for var in _day_vars.values():
            var.set(False)
        day_names = list(_day_vars.keys())
        for d in days:
            if isinstance(d, int) and 1 <= d <= len(day_names):
                list(_day_vars.values())[d - 1].set(True)
        applied.append(f"days: {days}")

    if applied and _strat_info_lbl:
        current = _strat_info_lbl.cget('text')
        _strat_info_lbl.configure(
            text=current + f"\n    Optimizer filters loaded: {', '.join(applied)}")
    elif _strat_info_lbl:
        current = _strat_info_lbl.cget('text')
        # Remove old filter text if no filters
        if 'Optimizer filters' in current:
            current = current.split('\n')[0]
            _strat_info_lbl.configure(text=current)

    print(f"[EA GEN] Filters for {idx}: {filters if filters else 'none'}")




def _generate_test():
    """Step 1 — generate test script."""
    global _test_generated

    idx = _get_selected_index()
    if idx is None:
        messagebox.showerror("No Strategy", "Select a strategy first.")
        return

    strat_data = _get_strategy_data(idx)
    if not strat_data:
        messagebox.showerror("No Data", "Strategy data not found. Run the backtest first.")
        return

    platform = _platform_var.get() if _platform_var else 'mt5'
    strategy = {
        'rules':    strat_data.get('rules', []),
        'exit_name': strat_data.get('exit_name', 'Strategy'),
    }

    try:
        from project3_live_trading.test_script_generator import generate_test_script
        code = generate_test_script(strategy, platform=platform)
    except Exception as e:
        messagebox.showerror("Test Script Error", str(e))
        return

    if _test_script_text:
        _test_script_text.configure(state="normal")
        _test_script_text.delete("1.0", "end")
        _test_script_text.insert("end", code)
        _test_script_text.configure(state="disabled")

    if _status_lbl:
        _status_lbl.configure(
            text=f"Test script generated ({len(code)} chars). Run it in MT5, then use Step 2.",
            fg="#2980b9")

    # Enable Step 2
    _test_generated = True
    if _step2_btn:
        _step2_btn.configure(state="normal", bg=GREEN)


def _save_test_script():
    if not _test_script_text:
        return
    code = _test_script_text.get("1.0", "end-1c")
    if not code.strip():
        messagebox.showinfo("No Code", "Generate the test script first (Step 1).")
        return
    platform = _platform_var.get() if _platform_var else 'mt5'
    ext   = ".mq5" if platform == 'mt5' else ".py"
    ftype = [("MQL5 Script", "*.mq5")] if platform == 'mt5' else [("Python files", "*.py")]
    path  = filedialog.asksaveasfilename(
        title="Save Test Script",
        defaultextension=ext,
        filetypes=ftype + [("All files", "*.*")],
        initialfile=f"test_indicators{ext}",
    )
    if path:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(code)
        messagebox.showinfo("Saved", f"Test script saved to:\n{path}")


def _generate():
    idx = _get_selected_index()
    if idx is None:
        messagebox.showerror("No Strategy", "Select a strategy first.")
        return

    # ── Stale rules check ─────────────────────────────────────────────────
    # WHY: Generating an EA from stale rules means the entry_timeframe might
    #      be wrong (H1 instead of M15), or direction missing. The EA would
    #      look at wrong candles or miss SELL signals.
    # CHANGED: April 2026 — warn before generating from stale data
    try:
        from shared.stale_check import check_analysis_report, format_warning
        stale = check_analysis_report()
        if stale['is_stale']:
            warning = format_warning(stale)
            proceed = messagebox.askyesno(
                "Stale Rules Warning",
                f"{warning}\n\nGenerate EA anyway?",
                icon='warning',
            )
            if not proceed:
                return
    except ImportError:
        pass

    strat_data = _get_strategy_data(idx)
    if not strat_data:
        messagebox.showerror("No Data", "Strategy data not found. Run the backtest first.")
        return

    platform = _platform_var.get() if _platform_var else 'mt5'

    # Collect overridden condition values back into rules
    rules = strat_data.get('rules', [])
    win_rules = [r for r in rules if r.get('prediction') == 'WIN']
    cvar_idx = 0
    for rule in win_rules:
        for cond in rule.get('conditions', []):
            if cvar_idx < len(_condition_vars):
                feat_check, op_check, var = _condition_vars[cvar_idx]
                try:
                    cond['value'] = float(var.get())
                except ValueError:
                    pass
                cvar_idx += 1

    try:
        # WHY: Pass the full firm data + trading_rules + drawdown_mechanics to the generator.
        #      The generator reads each rule type and generates MQL5 code for it.
        #      No hardcoding of firm-specific behavior.
        firm_name = _firm_var.get() if _firm_var else 'Custom'
        firm_data = _FIRMS.get(firm_name, _FIRMS.get('Custom', {}))
        stage = _ea_stage_var.get().lower() if _ea_stage_var else 'evaluation'

        # Find the selected challenge
        chs = firm_data.get('challenges', [])
        ch_name = _ea_challenge_var.get() if _ea_challenge_var else ''
        challenge = next((c for c in chs if c.get('challenge_name', c.get('challenge_id')) == ch_name),
                        chs[0] if chs else {})

        try:
            acct_size = int(_ea_account_var.get()) if _ea_account_var else 10000
        except Exception:
            acct_size = 10000

        prop_firm = {
            'name': firm_name,
            'firm_data': firm_data,
            'challenge': challenge,
            'stage': stage,
            'account_size': acct_size,
            'daily_dd_pct': float(_daily_dd_var.get()) if _daily_dd_var else 3.0,
            'total_dd_pct': float(_total_dd_var.get()) if _total_dd_var else 6.0,
            'trading_rules': firm_data.get('trading_rules', []),
            'drawdown_mechanics': firm_data.get('drawdown_mechanics', {}),
            'restrictions': challenge.get('restrictions', {}),
        }
    except ValueError:
        messagebox.showerror("Invalid Settings", "Check prop firm settings are valid numbers.")
        return

    session_filter = [s for s, var in _session_vars.items() if var.get()]
    # WHY: All sessions checked = no filter (same as none checked).
    # CHANGED: April 2026 — all-checked means no filter
    if len(session_filter) >= 3:
        session_filter = []
    day_filter     = [i + 1 for i, (d, var) in enumerate(_day_vars.items()) if var.get()]

    try:
        # WHY: Read exit_params from both possible key names. Include direction.
        # CHANGED: April 2026
        _exit_params = (strat_data.get('exit_params') or
                        strat_data.get('exit_strategy_params') or {})
        _exit_name = strat_data.get('exit_name', strat_data.get('exit_class', 'FixedSLTP'))
        _direction = strat_data.get('direction', '')
        if not _direction:
            for _r in strat_data.get('rules', strat_data.get('optimized_rules', [])):
                if _r.get('action') in ('BUY', 'SELL'):
                    _direction = _r['action']
                    break
        if not _direction:
            _direction = 'BUY'

        strategy = {
            'rules':                strat_data.get('rules', []),
            'exit_name':            _exit_name,
            'exit_strategy_params': _exit_params,
            'rule_combo':           strat_data.get('rule_combo', ''),
            'direction':            _direction,
            'filters_applied':      strat_data.get('filters_applied', {}),
            # WHY: Pass regime filter conditions so the EA can replicate
            #      the backtest's regime gating. Read from three sources:
            #      1. Per-rule regime_filter key (Phase A.43)
            #      2. run_settings.regime_filter_conditions
            #      3. Empty = no regime filter
            # CHANGED: April 2026 — regime filter in EA
            'regime_filter_conditions': [],
            'stats':                {
                # WHY: Backtest results store WR as percentage (49.7),
                #      optimizer saves store WR as decimal (0.497).
                #      Normalize to fraction (0.497) for the EA stats dict.
                # CHANGED: April 2026 — handle both WR formats
                'win_rate':      strat_data.get('win_rate', 0) / 100.0
                                 if strat_data.get('win_rate', 0) > 1.0
                                 else strat_data.get('win_rate', 0),
                'total_pips':    strat_data.get('net_total_pips', 0),
                'profit_factor': strat_data.get('net_profit_factor', 0),
            },
        }
        try:
            from project2_backtesting.strategy_validator import get_validation_for_strategy
            val_result = get_validation_for_strategy(idx)
            if val_result:
                combined = val_result.get('combined', {})
                strategy['validation'] = {
                    'grade': combined.get('grade', 'N/A'),
                    'score': combined.get('confidence_score', 0),
                }
        except Exception:
            pass

        try:
            magic = int(_magic_var.get()) if _magic_var else 12345
        except Exception:
            magic = 12345

        # WHY: With multi-TF backtest, each strategy row carries its own entry_tf.
        #      Use that first. Fall back to analysis_report, then global config.
        # CHANGED: April 2026 — multi-TF support
        entry_tf = (
            strat_data.get('entry_tf') or
            strat_data.get('entry_timeframe') or
            # WHY: Same as view_results.py fix — stats are flattened to top level
            # CHANGED: April 2026 — read flattened stats from strat_data top level
            (strat_data.get('stats') or strat_data).get('entry_tf') or
            None
        )
        if not entry_tf:
            try:
                _rpt_path = os.path.join(project_root, 'project1_reverse_engineering',
                                         'outputs', 'analysis_report.json')
                if os.path.exists(_rpt_path):
                    import json as _j
                    with open(_rpt_path, 'r') as _f:
                        _rpt = _j.load(_f)
                    entry_tf = _rpt.get('entry_timeframe')
            except Exception:
                pass
        if not entry_tf:
            try:
                from project2_backtesting.panels.configuration import load_config
                _cfg = load_config()
                entry_tf = _cfg.get('winning_scenario', 'H1')
            except Exception:
                entry_tf = 'H1'
        print(f"[EA GEN] Generating EA with entry timeframe: {entry_tf}")

        # WHY: Propagate direction from the selected strategy so the EA
        #      generator emits Buy OR Sell (not hardcoded BUY). Reads from
        #      strategy dict first; falls back to None which lets
        #      generate_ea do its own lookup.
        # CHANGED: April 2026 — fix hardcoded BUY (audit bug #9)
        _strat_direction = strategy.get('direction')
        if _strat_direction:
            print(f"[EA GEN PANEL] Strategy direction: {_strat_direction}")
        else:
            print(f"[EA GEN PANEL] WARNING: selected strategy has no direction field")

        # Extract regime conditions from strategy data
        _ea_regime = []
        # WHY: Check run_settings FIRST to see if regime was ON during backtest.
        #      Old code checked per-rule regime_filter first — but auto-save
        #      metadata embeds conditions into rules even when regime was OFF.
        #      This caused the EA to have a regime filter that blocked 99.8%
        #      of trades even though the backtest filtered 0%.
        # CHANGED: April 2026 — check if regime was actually enabled
        _rs = strat_data.get('run_settings', {})
        _regime_was_enabled = _rs.get('regime_filter_enabled', False)

        if _regime_was_enabled:
            # Regime was ON during backtest — embed conditions
            # Source 1: run_settings conditions (most reliable)
            _rf_conds = _rs.get('regime_filter_conditions', [])
            if _rf_conds:
                _ea_regime = _rf_conds
            # Source 2: per-rule regime_filter (fallback)
            if not _ea_regime:
                for _r in strategy.get('rules', []):
                    _rf = _r.get('regime_filter')
                    if _rf and isinstance(_rf, list) and len(_rf) > 0:
                        _ea_regime = _rf
                        break
            # Source 3: top-level (last resort)
            if not _ea_regime:
                _ea_regime = strat_data.get('regime_filter_conditions', [])
        else:
            # Regime was OFF during backtest — DO NOT embed any conditions
            _ea_regime = []
            print(f"[EA GEN] Regime filter was OFF during backtest — not embedding in EA")

        strategy['regime_filter_conditions'] = _ea_regime
        if _ea_regime:
            print(f"[EA GEN] Regime filter: {len(_ea_regime)} conditions embedded from strategy data")
        else:
            if _regime_was_enabled:
                print(f"[EA GEN] Regime filter: none (strategy had no regime filter or filtered 0%)")

        from project3_live_trading.ea_generator import generate_ea
        code = generate_ea(
            strategy=strategy,
            platform=platform,
            prop_firm=prop_firm,
            stage=stage,
            entry_timeframe=entry_tf,
            symbol=_symbol_var.get() if _symbol_var else 'XAUUSD',
            magic_number=magic,
            risk_per_trade_pct=float(_risk_var.get()) if _risk_var else 1.0,
            max_trades_per_day=int(_max_day_var.get()) if _max_day_var else 0,
            session_filter=session_filter,
            day_filter=day_filter,
            cooldown_minutes=int(_cooldown_var.get()) if _cooldown_var else 0,
            min_hold_minutes=_auto_min_hold[0],
            news_filter_minutes=int(_news_min_var.get()) if _news_min_var else 0,
            max_spread_pips=float(_spread_var.get()) if _spread_var else 5.0,
            direction=_strat_direction,
            # WHY: Leverage from strategy rule, not firm dropdown.
            #      Rule was backtested at this leverage — EA must match.
            # CHANGED: April 2026 — leverage from strategy
            # WHY: Check top-level, run_settings, AND rules[0] for leverage.
            #      Rules carry leverage=10 from the backfill. strat_data may not.
            # CHANGED: April 2026 — check rules for leverage
            leverage=int(
                strat_data.get('leverage', 0) or
                strat_data.get('run_settings', {}).get('leverage', 0) or
                (strat_data.get('rules', [{}])[0].get('leverage', 0) if strat_data.get('rules') else 0) or
                0
            ),
        )
    except Exception as e:
        import traceback; traceback.print_exc()
        messagebox.showerror("Generation Error", str(e))
        return

    # Display code
    if _code_text:
        _code_text.configure(state="normal")
        _code_text.delete("1.0", "end")
        _code_text.insert("end", code)
        _code_text.configure(state="disabled")

    if _status_lbl:
        ext = ".mq5" if platform == 'mt5' else ".py"
        _status_lbl.configure(
            text=f"Generated {platform.upper()} {'MQL5' if platform=='mt5' else 'Python'} bot ({len(code)} chars)",
            fg=GREEN)

    # WHY: Update status of saved rules to 'deployed' after EA generation
    # CHANGED: April 2026 — lifecycle status tracking
    try:
        from shared.saved_rules import update_rule_field
        _updated_count = 0
        for rule in strategy.get('rules', []):
            _entry_id = rule.get('_saved_entry_id')
            _rule_id = rule.get('_saved_rule_id')
            if _entry_id or _rule_id:
                _id_to_update = _rule_id if _rule_id else _entry_id
                try:
                    update_rule_field(_id_to_update, 'status', 'deployed')
                    _updated_count += 1
                except Exception as _ue:
                    print(f"[STATUS] Could not update status for rule {_id_to_update}: {_ue}")
        if _updated_count > 0:
            print(f"[STATUS] Updated {_updated_count} saved rules to 'deployed' status")
    except Exception as _se:
        print(f"[STATUS] Could not update rule statuses: {_se}")

    # Check for custom indicators
    rules_all = strat_data.get('rules', [])
    try:
        from project3_live_trading.indicator_mapper import get_custom_indicator_list
        custom = get_custom_indicator_list(rules_all)
        if custom and platform == 'mt5':
            msg = "⚠️ Custom indicators needed for MT5:\n" + "\n".join(f"  • {c}" for c in custom)
            msg += "\n\nInstall these from the MQL5 Marketplace before using this EA."
            messagebox.showwarning("Custom Indicators Required", msg)
    except Exception:
        pass


def _save_file():
    if not _code_text:
        return
    code = _code_text.get("1.0", "end-1c")
    if not code.strip():
        messagebox.showinfo("No Code", "Generate the EA first.")
        return
    platform = _platform_var.get() if _platform_var else 'mt5'
    ext   = ".mq5" if platform == 'mt5' else ".py"
    ftype = [("MQL5 files", "*.mq5")] if platform == 'mt5' else [("Python files", "*.py")]
    path  = filedialog.asksaveasfilename(
        title="Save Generated EA",
        defaultextension=ext,
        filetypes=ftype + [("All files", "*.*")]
    )
    if not path:
        return
    with open(path, 'w', encoding='utf-8') as f:
        f.write(code)
    _vpath = path.rsplit('.', 1)[0] + '_verification.txt'
    _extra = f"\n\nVerification report:\n{_vpath}" if os.path.exists(_vpath) else ""
    messagebox.showinfo("Saved", f"Saved to:\n{path}{_extra}")


def _copy_to_clipboard():
    if not _code_text or not state.window:
        return
    code = _code_text.get("1.0", "end-1c")
    state.window.clipboard_clear()
    state.window.clipboard_append(code)
    messagebox.showinfo("Copied", "Code copied to clipboard.")


# ─────────────────────────────────────────────────────────────────────────────
# Panel builder
# ─────────────────────────────────────────────────────────────────────────────

def build_panel(parent):
    global _strategy_var, _strat_info_lbl, _badge_lbl, _scroll_canvas
    global _platform_var, _code_text, _generate_btn, _status_lbl
    global _symbol_var, _magic_var, _risk_var, _spread_var, _cooldown_var
    global _news_cb_var, _news_min_var, _firm_var, _ea_stage_var, _ea_challenge_var
    global _ea_account_var, _rules_info_lbl, _dd_info_lbl
    global _daily_dd_var, _total_dd_var, _safety_var, _consistency_var, _max_day_var
    global _session_vars, _day_vars, _condition_frame
    global _sl_var, _tp_var, _trail_var
    global _test_script_text, _step2_btn

    _load_strategies()

    panel = tk.Frame(parent, bg=BG)

    # ── Header ────────────────────────────────────────────────────────────────
    hdr = tk.Frame(panel, bg=WHITE, pady=16)
    hdr.pack(fill="x", padx=20, pady=(20, 10))
    tk.Label(hdr, text="🤖 EA Generator",
             bg=WHITE, fg=DARK, font=("Segoe UI", 18, "bold")).pack()
    tk.Label(hdr, text="Convert your validated strategy into a MetaTrader robot",
             bg=WHITE, fg=GREY, font=("Segoe UI", 11)).pack(pady=(4, 0))

    # ── Strategy selector ─────────────────────────────────────────────────────
    sel_frame = tk.Frame(panel, bg=WHITE, padx=20, pady=12)
    sel_frame.pack(fill="x", padx=20, pady=(0, 5))

    tk.Label(sel_frame, text="Strategy", font=("Segoe UI", 11, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))

    if not _strategies:
        tk.Label(sel_frame, text="No backtest results. Run the backtest first.",
                 font=("Segoe UI", 10, "italic"), bg=WHITE, fg=RED).pack(anchor="w")
        _strategy_var = tk.StringVar(value="")
    else:
        _strategy_var = tk.StringVar(value=_strategies[0]['label'])
        # WHY: Separator rows were selectable in dropdown. Selecting one
        #      returned __separator__ index → empty data → confusing.
        # CHANGED: April 2026 — filter separators from dropdown
        labels = [s['label'] for s in _strategies if s.get('source') != 'separator']
        dd = ttk.Combobox(sel_frame, textvariable=_strategy_var,
                          values=labels, state="readonly", width=70)
        dd.pack(anchor="w")
        dd.bind("<<ComboboxSelected>>", lambda e: _update_strat_info())

        def _refresh_strategies():
            global _strategies
            _load_strategies()
            new_labels = [s['label'] for s in _strategies if s.get('source') != 'separator']
            dd['values'] = new_labels
            if new_labels and _strategy_var.get() not in new_labels:
                _strategy_var.set(new_labels[0])
            _update_strat_info()
            print(f"[EA GEN] Refreshed — {len(new_labels)} strategies")

        tk.Button(sel_frame, text="🔄 Refresh", font=("Segoe UI", 8),
                  bg="#3498db", fg="white", relief=tk.FLAT, padx=8,
                  command=_refresh_strategies).pack(anchor="w", pady=(4, 0))

    _strat_info_lbl = tk.Label(sel_frame, text="", font=("Segoe UI", 9),
                                bg=WHITE, fg=MIDGREY)
    _strat_info_lbl.pack(anchor="w", pady=(4, 0))

    _badge_lbl = tk.Label(sel_frame, text="", font=("Segoe UI", 9, "italic"),
                           bg=WHITE, fg=GREY)
    _badge_lbl.pack(anchor="w", pady=(2, 0))

    # ── Platform selector ─────────────────────────────────────────────────────
    plat_frame = tk.Frame(panel, bg=WHITE, padx=20, pady=12)
    plat_frame.pack(fill="x", padx=20, pady=(0, 5))
    tk.Label(plat_frame, text="Platform", font=("Segoe UI", 11, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 8))

    btn_row = tk.Frame(plat_frame, bg=WHITE)
    btn_row.pack(anchor="w")
    _platform_var = tk.StringVar(value='mt5')

    def _plat_btn(text, val):
        b = tk.Button(btn_row, text=text, width=22,
                      command=lambda v=val: [_platform_var.set(v), _refresh_plat_btns()],
                      font=("Segoe UI", 10, "bold"), relief=tk.FLAT, cursor="hand2",
                      pady=8, padx=10)
        b.pack(side=tk.LEFT, padx=(0, 8))
        return b

    _mt5_btn  = _plat_btn("MetaTrader 5 (.mq5)", 'mt5')
    _tv_btn   = _plat_btn("Tradovate (Python)", 'tradovate')

    def _refresh_plat_btns():
        p = _platform_var.get()
        _mt5_btn.configure(bg="#667eea" if p == 'mt5' else "#e0e0e0",
                           fg="white" if p == 'mt5' else DARK)
        _tv_btn.configure(bg="#667eea" if p == 'tradovate' else "#e0e0e0",
                          fg="white" if p == 'tradovate' else DARK)
    _refresh_plat_btns()

    # ── Scrollable settings area ──────────────────────────────────────────────
    _scroll_canvas = tk.Canvas(panel, bg=BG, highlightthickness=0)
    vscroll = tk.Scrollbar(panel, orient="vertical", command=_scroll_canvas.yview)
    scroll_frame = tk.Frame(_scroll_canvas, bg=BG)
    scroll_frame.bind("<Configure>",
                      lambda e: _scroll_canvas.configure(scrollregion=_scroll_canvas.bbox("all")))
    cwin = _scroll_canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    _scroll_canvas.configure(yscrollcommand=vscroll.set)
    _scroll_canvas.pack(side="left", fill="both", expand=True, padx=(20, 0))
    vscroll.pack(side="right", fill="y", padx=(0, 20))

    def _mw(e): _scroll_canvas.yview_scroll(int(-1*(e.delta/120)), "units")
    _scroll_canvas.bind_all("<MouseWheel>", _mw)
    _scroll_canvas.bind_all("<Button-4>", lambda e: _scroll_canvas.yview_scroll(-3, "units"))
    _scroll_canvas.bind_all("<Button-5>", lambda e: _scroll_canvas.yview_scroll(3, "units"))
    _scroll_canvas.bind("<Configure>", lambda e: _scroll_canvas.itemconfig(cwin, width=e.width))

    sf = scroll_frame

    def _section(title):
        f = tk.Frame(sf, bg=WHITE, padx=20, pady=10)
        f.pack(fill="x", padx=5, pady=(5, 0))
        tk.Label(f, text=title, font=("Segoe UI", 10, "bold"), bg=WHITE, fg=DARK
                 ).pack(anchor="w", pady=(0, 6))
        return f

    def _field(parent, label, default, width=8):
        var = tk.StringVar(value=default)
        r = tk.Frame(parent, bg=WHITE)
        r.pack(fill="x", pady=2)
        tk.Label(r, text=label, font=("Segoe UI", 9), bg=WHITE, fg=DARK,
                 width=24, anchor="w").pack(side=tk.LEFT)
        tk.Entry(r, textvariable=var, width=width).pack(side=tk.LEFT)
        return var

    # ── Prop Firm Settings — from JSON ────────────────────────────────────
    # WHY: User selects firm → challenge → account → stage.
    #      Panel shows DD ALERT levels (where EA stops) vs BLOW levels (where firm closes account).
    #      All values come from JSON, not hardcoded.
    pf_frame = _section("Prop Firm Settings")

    # Row 1: Firm + Stage
    r1 = tk.Frame(pf_frame, bg=WHITE)
    r1.pack(fill="x", pady=2)
    tk.Label(r1, text="Prop Firm:", font=("Segoe UI", 9, "bold"),
             bg=WHITE, fg=DARK, width=16, anchor="w").pack(side=tk.LEFT)
    _firm_var = tk.StringVar(value=list(_FIRMS.keys())[0])
    ttk.Combobox(r1, textvariable=_firm_var,
                 values=list(_FIRMS.keys()), state="readonly", width=20).pack(side=tk.LEFT, padx=(0,10))

    tk.Label(r1, text="Stage:", font=("Segoe UI", 9, "bold"),
             bg=WHITE, fg=DARK).pack(side=tk.LEFT, padx=(5,3))
    _ea_stage_var = tk.StringVar(value="Evaluation")
    ttk.Combobox(r1, textvariable=_ea_stage_var,
                 values=["Evaluation", "Funded"], state="readonly", width=12).pack(side=tk.LEFT)

    # Row 2: Challenge + Account
    r2 = tk.Frame(pf_frame, bg=WHITE)
    r2.pack(fill="x", pady=2)
    tk.Label(r2, text="Challenge:", font=("Segoe UI", 9),
             bg=WHITE, fg=DARK, width=16, anchor="w").pack(side=tk.LEFT)
    _ea_challenge_var = tk.StringVar(value="")
    _ch_dd = ttk.Combobox(r2, textvariable=_ea_challenge_var, values=[], state="readonly", width=25)
    _ch_dd.pack(side=tk.LEFT, padx=(0,10))

    tk.Label(r2, text="Account:", font=("Segoe UI", 9),
             bg=WHITE, fg=DARK).pack(side=tk.LEFT, padx=(5,3))
    _ea_account_var = tk.StringVar(value="10000")
    _acct_dd = ttk.Combobox(r2, textvariable=_ea_account_var, values=[], state="readonly", width=12)
    _acct_dd.pack(side=tk.LEFT)

    # DD settings — auto-filled from JSON, editable
    # WHY: "BLOW" = where firm closes your account. EA stops BEFORE this (alert levels in JSON).
    _daily_dd_var = _field(pf_frame, "Daily DD limit % (BLOW):", "3.0", 6)
    _total_dd_var = _field(pf_frame, "Total DD limit % (BLOW):", "6.0", 6)
    _safety_var   = None   # removed — EA uses rules-driven alert levels, not a safety %
    _consistency_var = None

    # DD info display — shows alert vs blow levels
    # WHY: User needs to see BOTH the level where the EA stops (alert)
    #      and the level where the firm closes the account (blow).
    _dd_info_lbl = tk.Label(pf_frame, text="", font=("Segoe UI", 8),
                             bg="#fff3cd", fg="#856404", wraplength=550,
                             justify="left", padx=8, pady=5, relief="solid", bd=1)
    _dd_info_lbl.pack(fill="x", pady=(4,0))

    # Trading rules info
    _rules_info_lbl = tk.Label(pf_frame, text="", font=("Segoe UI", 8),
                                bg=WHITE, fg="#1a5276", wraplength=550, justify="left")
    _rules_info_lbl.pack(fill="x", pady=(4,0))

    # Max trades/day — from user's strategy, NOT from JSON ranges
    _max_day_var = _field(pf_frame, "Max trades/day (0=unlimited):", "0", 4)

    # ── Callbacks ─────────────────────────────────────────────────────────
    def _on_firm_change(*_):
        fd = _FIRMS.get(_firm_var.get(), {})
        chs = fd.get('challenges', [])
        names = [c.get('challenge_name', c.get('challenge_id','?')) for c in chs]
        _ch_dd['values'] = names
        if names:
            _ea_challenge_var.set(names[0])

    def _on_challenge_or_stage_change(*_):
        fd = _FIRMS.get(_firm_var.get(), {})
        chs = fd.get('challenges', [])
        ch = next((c for c in chs if c.get('challenge_name', c.get('challenge_id')) == _ea_challenge_var.get()), None)
        if not ch:
            return
        stage = _ea_stage_var.get().lower()

        # Account sizes
        sizes = ch.get('account_sizes', [10000])
        _acct_dd['values'] = [str(s) for s in sizes]
        if _ea_account_var.get() not in [str(s) for s in sizes]:
            _ea_account_var.set(str(sizes[0]))

        # DD limits — depends on stage
        if stage == "funded":
            funded = ch.get('funded', {})
            _daily_dd_var.set(str(funded.get('max_daily_drawdown_pct', 3.0) or 3.0))
            _total_dd_var.set(str(funded.get('max_total_drawdown_pct', 6.0) or 6.0))
        else:
            phases = ch.get('phases', [])
            if phases:
                _daily_dd_var.set(str(phases[0].get('max_daily_drawdown_pct', 3.0) or 3.0))
                _total_dd_var.set(str(phases[0].get('max_total_drawdown_pct', 6.0) or 6.0))

        try:
            acct = int(_ea_account_var.get())
        except Exception:
            acct = 10000

        # Show DD alert vs blow levels
        # WHY: The EA stops at the ALERT level (buffer before blow).
        #      The firm closes the account at the BLOW level.
        rules = fd.get('trading_rules', [])
        stage_rules = [r for r in rules if r.get('stage','') == stage]

        blow_daily = float(_daily_dd_var.get())
        blow_total = float(_total_dd_var.get())
        alert_daily = blow_daily
        alert_total = blow_total
        emergency = None

        for r in stage_rules:
            p = r.get('parameters', {})
            if 'daily_dd_alert_pct' in p:
                alert_daily = p['daily_dd_alert_pct']
            if 'total_dd_alert_pct' in p:
                alert_total = p['total_dd_alert_pct']
            if 'emergency_total_dd_pct' in p:
                emergency = p['emergency_total_dd_pct']

        dd_text = (
            f"\u26a0\ufe0f DD Levels for {_firm_var.get()} \u2014 {stage.title()} (${acct:,}):\n"
            f"  Daily:  EA stops at {alert_daily}% (${acct*alert_daily/100:,.0f})  |  "
            f"Firm blows at {blow_daily}% (${acct*blow_daily/100:,.0f})\n"
            f"  Total:  EA stops at {alert_total}% (${acct*alert_total/100:,.0f})  |  "
            f"Firm blows at {blow_total}% (${acct*blow_total/100:,.0f})")
        if emergency:
            dd_text += (f"\n  Emergency: EA stops for PERIOD at {emergency}% "
                       f"(${acct*emergency/100:,.0f}) \u2014 protect account, lose period")
        _dd_info_lbl.config(text=dd_text)

        # Show trading rules + DD mechanics
        if stage_rules:
            lines = [f"📋 {_firm_var.get()} \u2014 {stage.title()} rules the EA will enforce:"]
            for r in stage_rules:
                rtype = r.get('type', '')
                desc = r.get('description', r.get('name', '?'))
                lines.append(f"  \u2022 [{rtype}] {desc}")
            rules_text = "\n".join(lines)
        else:
            rules_text = f"No special trading rules for {stage} \u2014 basic DD protection only."

        dd_mech = fd.get('drawdown_mechanics', {})
        if dd_mech:
            trailing = dd_mech.get('trailing_dd', {})
            daily_mech = dd_mech.get('daily_dd', {})
            if trailing:
                rules_text += f"\n\n\U0001f4d0 DD Mechanic: {trailing.get('description', '')}"
            if daily_mech:
                rules_text += f"\n\U0001f4d0 Daily DD: {daily_mech.get('description', '')}"

        _rules_info_lbl.config(text=rules_text)

    _firm_var.trace_add("write", _on_firm_change)
    _ea_challenge_var.trace_add("write", _on_challenge_or_stage_change)
    _ea_stage_var.trace_add("write", _on_challenge_or_stage_change)
    _ea_account_var.trace_add("write", _on_challenge_or_stage_change)
    _on_firm_change()

    # Trading settings
    tr_frame = _section("Trading Settings")
    _symbol_var   = _field(tr_frame, "Symbol:", "XAUUSD", 10)
    _magic_var    = _field(tr_frame, "Magic number:", "12345", 8)
    _risk_var     = _field(tr_frame, "Risk per trade %:", "1.0", 6)
    _spread_var   = _field(tr_frame, "Max spread (pips):", "5.0", 6)
    # WHY: Cooldown was 60 by default but the backtest doesn't test with
    #      any cooldown. Adding one in the EA = fewer trades than backtest
    #      showed = untested behavior. Default to 0 to match backtest.
    # CHANGED: April 2026 — match backtest defaults
    _cooldown_var = _field(tr_frame, "Cooldown (minutes):", "0", 6)

    sess_row = tk.Frame(tr_frame, bg=WHITE)
    sess_row.pack(fill="x", pady=3)
    tk.Label(sess_row, text="Sessions:", font=("Segoe UI", 9), bg=WHITE, fg=DARK,
             width=24, anchor="w").pack(side=tk.LEFT)
    # WHY: Old code defaulted London + New York checked. But the backtest
    #      has no session filter — trades 24/5. Defaulting sessions ON adds
    #      untested behavior (skips 22-24 GMT). Default all OFF = no filter.
    #      auto_fill_filters will enable specific sessions IF the optimizer
    #      found them profitable.
    # CHANGED: April 2026 — default no session filter (matches backtest)
    for sess in ["Asian", "London", "New York"]:
        var = tk.BooleanVar(value=True)
        _session_vars[sess] = var
        tk.Checkbutton(sess_row, text=sess, variable=var, bg=WHITE,
                       font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=4)
    tk.Label(sess_row, text="(all = no filter)",
             font=("Segoe UI", 8), bg=WHITE, fg="#aaa").pack(side=tk.LEFT, padx=(8, 0))

    day_row = tk.Frame(tr_frame, bg=WHITE)
    day_row.pack(fill="x", pady=3)
    tk.Label(day_row, text="Days:", font=("Segoe UI", 9), bg=WHITE, fg=DARK,
             width=24, anchor="w").pack(side=tk.LEFT)
    for day in ["Mon", "Tue", "Wed", "Thu", "Fri"]:
        var = tk.BooleanVar(value=True)
        _day_vars[day] = var
        tk.Checkbutton(day_row, text=day, variable=var, bg=WHITE,
                       font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=3)

    news_row = tk.Frame(tr_frame, bg=WHITE)
    news_row.pack(fill="x", pady=3)
    tk.Label(news_row, text="News filter:", font=("Segoe UI", 9), bg=WHITE, fg=DARK,
             width=24, anchor="w").pack(side=tk.LEFT)
    _news_cb_var  = tk.BooleanVar(value=False)
    tk.Checkbutton(news_row, variable=_news_cb_var, bg=WHITE).pack(side=tk.LEFT)
    # WHY: News filter was 5 min by default but the backtest doesn't
    #      skip candles around news. Adding it in the EA = missed trades
    #      the backtest counted = untested behavior. Default to 0.
    # CHANGED: April 2026 — match backtest defaults
    _news_min_var = tk.StringVar(value="0")
    tk.Entry(news_row, textvariable=_news_min_var, width=4).pack(side=tk.LEFT, padx=2)

    def _sync_news_cb(*_):
        try:
            _news_cb_var.set(int(_news_min_var.get()) > 0)
        except ValueError:
            pass
    _news_min_var.trace_add("write", _sync_news_cb)
    tk.Label(news_row, text="min before/after HIGH impact", font=("Segoe UI", 8),
             bg=WHITE, fg=GREY).pack(side=tk.LEFT)

    # Exit strategy settings
    exit_frame = _section("Exit Strategy")
    _sl_var    = _field(exit_frame, "SL (pips):", "150", 6)
    _tp_var    = _field(exit_frame, "TP (pips):", "300", 6)
    _trail_var = _field(exit_frame, "Trailing stop (pips, 0=off):", "0", 6)

    # Entry rule thresholds
    cond_section = _section("Entry Rule Thresholds (editable)")
    _condition_frame = tk.Frame(cond_section, bg=WHITE)
    _condition_frame.pack(fill="x")
    tk.Label(_condition_frame, text="Load a strategy to see conditions.",
             font=("Segoe UI", 9, "italic"), bg=WHITE, fg=GREY).pack(anchor="w")

    # ── Step 1: Test Script ───────────────────────────────────────────────────
    step1_section = tk.Frame(sf, bg=WHITE, padx=20, pady=12)
    step1_section.pack(fill="x", padx=5, pady=(5, 0))

    step1_hdr = tk.Frame(step1_section, bg=WHITE)
    step1_hdr.pack(fill="x", pady=(0, 6))
    tk.Label(step1_hdr, text="Step 1: Generate & Run Test Script",
             font=("Segoe UI", 10, "bold"), bg=WHITE, fg="#2980b9").pack(side=tk.LEFT)

    step1_btn_row = tk.Frame(step1_section, bg=WHITE)
    step1_btn_row.pack(fill="x", pady=(0, 6))
    tk.Button(step1_btn_row, text="Generate Test Script",
              command=_generate_test,
              bg="#2980b9", fg="white", font=("Segoe UI", 10, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=16, pady=8).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(step1_btn_row, text="Save Test Script",
              command=_save_test_script,
              bg=MIDGREY, fg="white", font=("Segoe UI", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=10, pady=5).pack(side=tk.LEFT, padx=(0, 8))

    # WHY: User needs to verify pip_value, spread, leverage on each new
    #      broker before backtesting. This script prints all values.
    # CHANGED: April 2026 — downloadable diagnostic script
    def _download_diagnostic():
        _diag_src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 'templates', 'diagnostic_compare.mq5')
        if not os.path.exists(_diag_src):
            messagebox.showerror("Not Found", f"diagnostic_compare.mq5 not found at:\n{_diag_src}")
            return
        _dst = filedialog.asksaveasfilename(
            title="Save Broker Diagnostic Script",
            defaultextension=".mq5",
            filetypes=[("MQL5 Script", "*.mq5"), ("All files", "*.*")],
            initialfile="diagnostic_compare.mq5",
        )
        if _dst:
            shutil.copy2(_diag_src, _dst)
            messagebox.showinfo("Saved",
                f"Diagnostic script saved to:\n{_dst}\n\n"
                "How to use:\n"
                "1. Copy to MT5 \u2192 MQL5/Scripts/\n"
                "2. Compile (F7)\n"
                "3. Drag onto chart\n"
                "4. Read Experts tab (Ctrl+E)")

    tk.Button(step1_btn_row, text="\U0001f50d Download Broker Diagnostic",
              command=_download_diagnostic,
              bg="#6c757d", fg="white", font=("Segoe UI", 9),
              relief=tk.FLAT, cursor="hand2", padx=10, pady=5).pack(side=tk.LEFT)

    _test_script_text = tk.Text(step1_section, height=10, font=("Consolas", 7),
                                 bg="#1a1a2a", fg="#e0e0e0", wrap="none", state="disabled")
    _test_script_text.pack(fill="x", pady=(4, 6))

    # Instructions box
    inst_bg = "#eaf4fb"
    inst_frame = tk.Frame(step1_section, bg=inst_bg, padx=12, pady=8)
    inst_frame.pack(fill="x")
    tk.Label(inst_frame, text="How to use the test script:", font=("Segoe UI", 9, "bold"),
             bg=inst_bg, fg=DARK).pack(anchor="w")
    instructions = (
        "For MT5:\n"
        "  1. Save the file to: [MT5 Data Folder]/MQL5/Scripts/\n"
        "  2. Open MetaEditor (press F4 in MT5) and compile (F7)\n"
        "  3. If compile fails -> install the listed custom indicators\n"
        "  4. If compile succeeds -> drag the script onto a chart\n"
        "  5. Open the Experts tab (Ctrl+E) -> read OK / FAIL results\n"
        "  6. All OK? -> proceed to Step 2\n\n"
        "For Tradovate:\n"
        "  1. Save the test file\n"
        # CHANGED: April 2026 — unified install hint (Phase 19c)
        "  2. Install requirements: pip install -r requirements.txt\n"
        "  3. Run: python test_indicators.py\n"
        "  4. Check output for OK and FAIL lines"
    )
    tk.Label(inst_frame, text=instructions, font=("Segoe UI", 8),
             bg=inst_bg, fg=MIDGREY, justify="left", anchor="w").pack(anchor="w")

    # ── Step 2: Full EA ────────────────────────────────────────────────────────
    step2_section = tk.Frame(sf, bg=WHITE, padx=20, pady=12)
    step2_section.pack(fill="x", padx=5, pady=(5, 0))

    step2_hdr = tk.Frame(step2_section, bg=WHITE)
    step2_hdr.pack(fill="x", pady=(0, 6))
    tk.Label(step2_hdr, text="Step 2: Generate Full Expert Advisor",
             font=("Segoe UI", 10, "bold"), bg=WHITE, fg=GREEN).pack(side=tk.LEFT)

    step2_btn_row = tk.Frame(step2_section, bg=WHITE)
    step2_btn_row.pack(fill="x", pady=(0, 4))

    _step2_btn = tk.Button(step2_btn_row, text="Generate Full EA  (run test script first)",
                           command=_generate,
                           bg="#a0a0a0", fg="white", font=("Segoe UI", 10, "bold"),
                           relief=tk.FLAT, cursor="hand2", padx=16, pady=8,
                           state="normal")  # always clickable, but visually grey until step 1 done
    _step2_btn.pack(side=tk.LEFT, padx=(0, 8))

    def _generate_both():
        """Generate Eval + Funded EAs for firms that have rules for both stages."""
        firm_name = _firm_var.get() if _firm_var else 'Custom'
        firm_data = _FIRMS.get(firm_name, {})
        trading_rules = firm_data.get('trading_rules', [])
        has_eval   = any(r.get('stage') == 'evaluation' for r in trading_rules)
        has_funded = any(r.get('stage') == 'funded' for r in trading_rules)

        if not (has_eval and has_funded):
            messagebox.showinfo("Not Available",
                f"{firm_name} does not have rules for both stages.\n"
                "Use the Stage selector + Generate for each stage individually.")
            return

        original_stage = _ea_stage_var.get() if _ea_stage_var else "Evaluation"
        try:
            for s in ["Evaluation", "Funded"]:
                _ea_stage_var.set(s)
                _on_challenge_or_stage_change()
                _generate()
            messagebox.showinfo("Generated Both",
                f"Generated Evaluation + Funded EAs for {firm_name}.\n"
                "Use 'Save File' to save the currently displayed code.")
        finally:
            _ea_stage_var.set(original_stage)
            _on_challenge_or_stage_change()

    tk.Button(step2_btn_row, text="Generate Both (Eval + Funded)",
              command=_generate_both,
              bg="#764ba2", fg="white", font=("Segoe UI", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=12, pady=7).pack(side=tk.LEFT)

    _generate_btn = _step2_btn  # alias for external disable/enable

    gen_frame = tk.Frame(sf, bg=BG, pady=4)
    gen_frame.pack(fill="x", padx=5)
    _status_lbl = tk.Label(gen_frame, text="Ready — generate test script first (Step 1)",
                            font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY)
    _status_lbl.pack(side=tk.LEFT, padx=5)

    # Output code area (Step 2 result)
    out_frame = tk.Frame(sf, bg=WHITE, padx=10, pady=8)
    out_frame.pack(fill="x", padx=5, pady=(5, 0))

    out_hdr = tk.Frame(out_frame, bg=WHITE)
    out_hdr.pack(fill="x", pady=(0, 6))
    tk.Label(out_hdr, text="Generated EA Code", font=("Segoe UI", 10, "bold"),
             bg=WHITE, fg=DARK).pack(side=tk.LEFT)
    tk.Button(out_hdr, text="Save File", command=_save_file,
              bg=GREEN, fg="white", font=("Segoe UI", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=10, pady=3).pack(side=tk.RIGHT, padx=(4, 0))
    tk.Button(out_hdr, text="Copy to Clipboard", command=_copy_to_clipboard,
              bg=MIDGREY, fg="white", font=("Segoe UI", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=10, pady=3).pack(side=tk.RIGHT, padx=(4, 0))

    _code_text = tk.Text(out_frame, height=20, font=("Consolas", 8),
                          bg="#1a1a2a", fg="#e0e0e0", insertbackground="white",
                          wrap="none", state="disabled")
    _code_text.pack(fill="x", padx=0)

    # Initial load
    _update_strat_info()

    return panel


def refresh():
    global _strategies, _strategy_var
    _load_strategies()
    if _strategy_var is not None and _strategies:
        labels = [s['label'] for s in _strategies if s.get('source') != 'separator']
        if _strategy_var.get() not in labels:
            _strategy_var.set(labels[0])
        _update_strat_info()
