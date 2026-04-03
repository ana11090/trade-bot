"""
EA Generator Panel — pick a validated strategy, configure platform/prop firm settings,
and generate a complete MetaTrader 5 (.mq5) or Tradovate (Python) bot.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import sys
import json
import threading

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

PROP_FIRM_PRESETS = {
    "FTMO":        {"daily_dd_pct": 5.0,  "total_dd_pct": 10.0, "safety_pct": 80.0, "consistency_pct": 0.0,  "max_per_day": 5},
    "Topstep":     {"daily_dd_pct": 3.0,  "total_dd_pct": 6.0,  "safety_pct": 80.0, "consistency_pct": 0.0,  "max_per_day": 3},
    "Apex":        {"daily_dd_pct": 3.0,  "total_dd_pct": 6.0,  "safety_pct": 80.0, "consistency_pct": 30.0, "max_per_day": 4},
    "FundedNext":  {"daily_dd_pct": 5.0,  "total_dd_pct": 10.0, "safety_pct": 80.0, "consistency_pct": 0.0,  "max_per_day": 5},
    "The5ers":     {"daily_dd_pct": 4.0,  "total_dd_pct": 8.0,  "safety_pct": 80.0, "consistency_pct": 0.0,  "max_per_day": 5},
    "Custom":      {"daily_dd_pct": 5.0,  "total_dd_pct": 10.0, "safety_pct": 80.0, "consistency_pct": 0.0,  "max_per_day": 5},
}

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
_daily_dd_var     = None
_total_dd_var     = None
_safety_var       = None
_consistency_var  = None
_max_day_var      = None
_session_vars     = {}
_day_vars         = {}

# Per-condition threshold entry vars: list of (feature, op, tk.StringVar)
_condition_vars   = []

# Exit vars
_sl_var = _tp_var = _trail_var = None


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
    """Load full strategy data from backtest_matrix.json."""
    try:
        path = os.path.join(project_root, 'project2_backtesting', 'outputs', 'backtest_matrix.json')
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data['results'][idx]
    except Exception:
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

    # Update condition threshold vars
    _refresh_condition_vars(idx)


def _refresh_condition_vars(idx):
    global _condition_vars
    if not _condition_frame:
        return
    # Clear existing
    for w in _condition_frame.winfo_children():
        w.destroy()
    _condition_vars.clear()

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

    # Exit params
    strat_data = _get_strategy_data(idx)
    exit_params = strat_data.get('exit_strategy_params', {'sl_pips': 150, 'tp_pips': 300})
    if _sl_var:    _sl_var.set(str(exit_params.get('sl_pips', 150)))
    if _tp_var:    _tp_var.set(str(exit_params.get('tp_pips', 300)))
    if _trail_var: _trail_var.set(str(exit_params.get('trail_pips', exit_params.get('trail_distance_pips', 100))))


_condition_frame = None


def _apply_firm_preset(firm_name):
    preset = PROP_FIRM_PRESETS.get(firm_name, PROP_FIRM_PRESETS['Custom'])
    if _daily_dd_var:  _daily_dd_var.set(str(preset['daily_dd_pct']))
    if _total_dd_var:  _total_dd_var.set(str(preset['total_dd_pct']))
    if _safety_var:    _safety_var.set(str(preset['safety_pct']))
    if _consistency_var: _consistency_var.set(str(preset['consistency_pct']))
    if _max_day_var:   _max_day_var.set(str(preset['max_per_day']))


def _generate():
    idx = _get_selected_index()
    if idx is None:
        messagebox.showerror("No Strategy", "Select a strategy first.")
        return

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
        firm_name = _firm_var.get() if _firm_var else 'Custom'
        prop_firm = dict(PROP_FIRM_PRESETS.get(firm_name, PROP_FIRM_PRESETS['Custom']))
        prop_firm['name'] = firm_name
        if _daily_dd_var:   prop_firm['daily_dd_pct']    = float(_daily_dd_var.get())
        if _total_dd_var:   prop_firm['total_dd_pct']    = float(_total_dd_var.get())
        if _safety_var:     prop_firm['safety_pct']      = float(_safety_var.get())
        if _consistency_var: prop_firm['consistency_pct'] = float(_consistency_var.get())
        if _max_day_var:    prop_firm['max_per_day']      = int(_max_day_var.get())
    except ValueError:
        messagebox.showerror("Invalid Settings", "Check prop firm settings are valid numbers.")
        return

    session_filter = [s for s, var in _session_vars.items() if var.get()]
    day_filter     = [i + 1 for i, (d, var) in enumerate(_day_vars.items()) if var.get()]

    try:
        strategy = {
            'rules':                strat_data.get('rules', []),
            'exit_name':            strat_data.get('exit_name', 'FixedSLTP'),
            'exit_strategy_params': strat_data.get('exit_strategy_params', {}),
            'stats':                {
                'win_rate':      strat_data.get('win_rate', 0) / 100.0,
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

        from project3_live_trading.ea_generator import generate_ea
        code = generate_ea(
            strategy=strategy,
            platform=platform,
            prop_firm=prop_firm,
            symbol=_symbol_var.get() if _symbol_var else 'XAUUSD',
            magic_number=magic,
            risk_per_trade_pct=float(_risk_var.get()) if _risk_var else 1.0,
            max_trades_per_day=int(_max_day_var.get()) if _max_day_var else 5,
            session_filter=session_filter,
            day_filter=day_filter,
            cooldown_minutes=int(_cooldown_var.get()) if _cooldown_var else 60,
            news_filter_minutes=int(_news_min_var.get()) if _news_min_var else 5,
            max_spread_pips=float(_spread_var.get()) if _spread_var else 5.0,
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
    messagebox.showinfo("Saved", f"Saved to:\n{path}")


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
    global _news_cb_var, _news_min_var, _firm_var
    global _daily_dd_var, _total_dd_var, _safety_var, _consistency_var, _max_day_var
    global _session_vars, _day_vars, _condition_frame
    global _sl_var, _tp_var, _trail_var

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
        labels = [s['label'] for s in _strategies]
        dd = ttk.Combobox(sel_frame, textvariable=_strategy_var,
                          values=labels, state="readonly", width=70)
        dd.pack(anchor="w")
        dd.bind("<<ComboboxSelected>>", lambda e: _update_strat_info())

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

    # Prop firm settings
    pf_frame = _section("Prop Firm Settings")
    firm_row = tk.Frame(pf_frame, bg=WHITE)
    firm_row.pack(fill="x", pady=2)
    tk.Label(firm_row, text="Preset:", font=("Segoe UI", 9),
             bg=WHITE, fg=DARK, width=24, anchor="w").pack(side=tk.LEFT)
    _firm_var = tk.StringVar(value="FTMO")
    firm_dd = ttk.Combobox(firm_row, textvariable=_firm_var,
                            values=list(PROP_FIRM_PRESETS.keys()), state="readonly", width=16)
    firm_dd.pack(side=tk.LEFT, padx=(0, 8))
    firm_dd.bind("<<ComboboxSelected>>", lambda e: _apply_firm_preset(_firm_var.get()))
    _daily_dd_var    = _field(pf_frame, "Daily DD limit %:", "5.0", 6)
    _total_dd_var    = _field(pf_frame, "Total DD limit %:", "10.0", 6)
    _safety_var      = _field(pf_frame, "Daily safety %:", "80.0", 6)
    _consistency_var = _field(pf_frame, "Consistency rule %:", "0.0", 6)
    _max_day_var     = _field(pf_frame, "Max trades/day:", "5", 4)

    # Trading settings
    tr_frame = _section("Trading Settings")
    _symbol_var   = _field(tr_frame, "Symbol:", "XAUUSD", 10)
    _magic_var    = _field(tr_frame, "Magic number:", "12345", 8)
    _risk_var     = _field(tr_frame, "Risk per trade %:", "1.0", 6)
    _spread_var   = _field(tr_frame, "Max spread (pips):", "5.0", 6)
    _cooldown_var = _field(tr_frame, "Cooldown (minutes):", "60", 6)

    sess_row = tk.Frame(tr_frame, bg=WHITE)
    sess_row.pack(fill="x", pady=3)
    tk.Label(sess_row, text="Sessions:", font=("Segoe UI", 9), bg=WHITE, fg=DARK,
             width=24, anchor="w").pack(side=tk.LEFT)
    for sess in ["Asian", "London", "New York"]:
        var = tk.BooleanVar(value=(sess in ("London", "New York")))
        _session_vars[sess] = var
        tk.Checkbutton(sess_row, text=sess, variable=var, bg=WHITE,
                       font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=4)

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
    _news_cb_var  = tk.BooleanVar(value=True)
    tk.Checkbutton(news_row, variable=_news_cb_var, bg=WHITE).pack(side=tk.LEFT)
    _news_min_var = tk.StringVar(value="5")
    tk.Entry(news_row, textvariable=_news_min_var, width=4).pack(side=tk.LEFT, padx=2)
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

    # Generate button
    gen_frame = tk.Frame(sf, bg=BG, pady=8)
    gen_frame.pack(fill="x", padx=5)

    _generate_btn = tk.Button(gen_frame, text="⚙️ Generate Expert Advisor",
                              command=_generate,
                              bg="#667eea", fg="white", font=("Segoe UI", 11, "bold"),
                              relief=tk.FLAT, cursor="hand2", padx=20, pady=10)
    _generate_btn.pack(side=tk.LEFT, padx=(5, 8))

    _status_lbl = tk.Label(gen_frame, text="Ready",
                            font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY)
    _status_lbl.pack(side=tk.LEFT)

    # Output code area
    out_frame = tk.Frame(sf, bg=WHITE, padx=10, pady=8)
    out_frame.pack(fill="x", padx=5, pady=(5, 0))

    out_hdr = tk.Frame(out_frame, bg=WHITE)
    out_hdr.pack(fill="x", pady=(0, 6))
    tk.Label(out_hdr, text="Generated Code", font=("Segoe UI", 10, "bold"),
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
        labels = [s['label'] for s in _strategies]
        if _strategy_var.get() not in labels:
            _strategy_var.set(labels[0])
        _update_strat_info()
