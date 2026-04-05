"""
Strategy Playground — interactively build and test trading rules.
Add/remove conditions, change values, see trades + profit + DD + prop firm results instantly.
"""
import tkinter as tk
from tkinter import ttk, messagebox
import os, sys, json, threading
import pandas as pd
import numpy as np

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)

BG = "#f0f2f5"
WHITE = "#ffffff"

# Pre-loaded data (loaded once, reused for every test)
_indicators = [None]  # DataFrame
_candles = [None]      # DataFrame
_data_loaded = [False]
_available_features = []


def _load_data_once():
    """Load indicator data once. Reuse for all tests."""
    if _data_loaded[0]:
        return

    from project2_backtesting.panels.configuration import load_config
    from project2_backtesting.strategy_backtester import build_multi_tf_indicators, _SMART_DEPENDENCIES
    from shared.data_utils import normalize_timestamp

    cfg = load_config()
    symbol = cfg.get('symbol', 'XAUUSD').lower()
    tf = cfg.get('winning_scenario', 'H1')
    data_dir = os.path.join(project_root, 'data')
    candle_path = os.path.join(data_dir, f'{symbol}_{tf}.csv')

    if not os.path.exists(candle_path):
        return

    candles = pd.read_csv(candle_path, encoding='utf-8-sig')
    if 'timestamp' not in candles.columns:
        candles = candles.rename(columns={candles.columns[0]: 'timestamp'})
    candles['timestamp'] = normalize_timestamp(candles['timestamp'])
    candles = candles.sort_values('timestamp').reset_index(drop=True)

    # Load all indicators (use cache if available)
    _ALL_TF = {tf: list(set(deps)) for tf, deps in _SMART_DEPENDENCIES.items()}
    indicators = build_multi_tf_indicators(data_dir, candles['timestamp'],
                                            required_indicators=_ALL_TF)

    # Compute SMART features
    try:
        from project1_reverse_engineering.smart_features import (
            _add_tf_divergences, _add_indicator_dynamics,
            _add_alignment_scores, _add_session_intelligence,
            _add_volatility_regimes, _add_price_action,
            _add_momentum_quality,
        )
        indicators['hour_of_day'] = candles['timestamp'].dt.hour
        indicators['open_time'] = candles['timestamp'].astype(str)
        indicators = _add_tf_divergences(indicators)
        indicators = _add_indicator_dynamics(indicators)
        indicators = _add_alignment_scores(indicators)
        indicators = _add_session_intelligence(indicators)
        indicators = _add_volatility_regimes(indicators)
        indicators = _add_price_action(indicators)
        indicators = _add_momentum_quality(indicators)
    except ImportError:
        pass

    # Compute REGIME features
    try:
        from project1_reverse_engineering.smart_features import _add_regime_features
        indicators = _add_regime_features(indicators)
    except (ImportError, AttributeError):
        pass

    _indicators[0] = indicators
    _candles[0] = candles
    _data_loaded[0] = True

    global _available_features
    _available_features = sorted([c for c in indicators.columns
                                   if c not in ('timestamp', 'hour_of_day', 'open_time')])


def build_panel(parent):
    panel = tk.Frame(parent, bg=BG)

    # Scrollable canvas
    canvas = tk.Canvas(panel, bg=BG, highlightthickness=0)
    scrollbar = tk.Scrollbar(panel, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side=tk.RIGHT, fill="y")
    canvas.pack(side=tk.LEFT, fill="both", expand=True)

    inner = tk.Frame(canvas, bg=BG)
    wid = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(wid, width=e.width))

    # Safe mousewheel binding — doesn't break other canvases
    def _on_enter(event):
        canvas.bind("<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        # Linux
        canvas.bind("<Button-4>", lambda e: canvas.yview_scroll(-3, "units"))
        canvas.bind("<Button-5>", lambda e: canvas.yview_scroll(3, "units"))

    def _on_leave(event):
        canvas.unbind("<MouseWheel>")
        canvas.unbind("<Button-4>")
        canvas.unbind("<Button-5>")

    canvas.bind("<Enter>", _on_enter)
    canvas.bind("<Leave>", _on_leave)

    # Title
    tk.Label(inner, text="🎮 Strategy Playground", font=("Arial", 16, "bold"),
             bg=BG, fg="#333").pack(pady=(15, 3))
    tk.Label(inner, text="Build rules interactively — add conditions, tweak values, see results instantly",
             font=("Arial", 10), bg=BG, fg="#666").pack(pady=(0, 10))

    # Loading status
    status_label = tk.Label(inner, text="Loading indicator data...", font=("Arial", 9),
                             bg=BG, fg="#e67e22")
    status_label.pack()

    # ── Three column layout ──────────────────────────────
    main_frame = tk.Frame(inner, bg=BG)
    main_frame.pack(fill="both", expand=True, padx=10, pady=5)
    main_frame.columnconfigure(0, weight=2)  # rule builder
    main_frame.columnconfigure(1, weight=2)  # results
    main_frame.columnconfigure(2, weight=1)  # prop firm

    # ═══════ LEFT COLUMN: Rule Builder ═══════
    left = tk.LabelFrame(main_frame, text="📝 Rule Builder", font=("Arial", 10, "bold"),
                          bg=WHITE, fg="#333", padx=10, pady=8)
    left.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

    # Direction + SL/TP row
    top_row = tk.Frame(left, bg=WHITE)
    top_row.pack(fill="x", pady=(0, 8))

    dir_var = tk.StringVar(value="BUY")
    tk.Label(top_row, text="Dir:", font=("Arial", 9), bg=WHITE).pack(side=tk.LEFT)
    ttk.Combobox(top_row, textvariable=dir_var, values=["BUY", "SELL"],
                  width=5, state="readonly").pack(side=tk.LEFT, padx=3)

    sl_var = tk.StringVar(value="150")
    tk.Label(top_row, text="SL:", font=("Arial", 9), bg=WHITE).pack(side=tk.LEFT, padx=(10,0))
    tk.Entry(top_row, textvariable=sl_var, width=6, font=("Arial", 9)).pack(side=tk.LEFT, padx=3)

    tp_var = tk.StringVar(value="300")
    tk.Label(top_row, text="TP:", font=("Arial", 9), bg=WHITE).pack(side=tk.LEFT, padx=(10,0))
    tk.Entry(top_row, textvariable=tp_var, width=6, font=("Arial", 9)).pack(side=tk.LEFT, padx=3)

    hold_var = tk.StringVar(value="50")
    tk.Label(top_row, text="Hold:", font=("Arial", 9), bg=WHITE).pack(side=tk.LEFT, padx=(10,0))
    tk.Entry(top_row, textvariable=hold_var, width=5, font=("Arial", 9)).pack(side=tk.LEFT, padx=3)

    # Conditions list
    tk.Label(left, text="CONDITIONS:", font=("Arial", 9, "bold"), bg=WHITE, fg="#555").pack(anchor="w")

    cond_frame = tk.Frame(left, bg=WHITE)
    cond_frame.pack(fill="x", pady=5)

    condition_rows = []  # list of {frame, feat_var, op_var, val_var}

    def _add_condition(feature="H1_rsi_14", operator=">", value="60"):
        row_frame = tk.Frame(cond_frame, bg="#f8f9fa", padx=3, pady=2)
        row_frame.pack(fill="x", pady=1)

        idx = len(condition_rows) + 1
        tk.Label(row_frame, text=f"#{idx}", font=("Arial", 8, "bold"),
                 bg="#f8f9fa", fg="#667eea", width=3).pack(side=tk.LEFT)

        feat_var = tk.StringVar(value=feature)
        feat_combo = ttk.Combobox(row_frame, textvariable=feat_var,
                                   values=_available_features, width=25)
        feat_combo.pack(side=tk.LEFT, padx=2)

        op_var = tk.StringVar(value=operator)
        ttk.Combobox(row_frame, textvariable=op_var,
                      values=[">", ">=", "<", "<="], width=3, state="readonly").pack(side=tk.LEFT, padx=2)

        val_var = tk.StringVar(value=value)
        tk.Entry(row_frame, textvariable=val_var, width=10, font=("Arial", 9)).pack(side=tk.LEFT, padx=2)

        row_data = {'frame': row_frame, 'feat_var': feat_var, 'op_var': op_var, 'val_var': val_var}
        condition_rows.append(row_data)

        def _remove(rd=row_data):
            rd['frame'].destroy()
            condition_rows.remove(rd)

        tk.Button(row_frame, text="🗑️", font=("Arial", 7), bg="#dc3545", fg="white",
                  relief=tk.FLAT, padx=3, command=_remove).pack(side=tk.RIGHT)

    # Add condition button
    tk.Button(left, text="+ Add Condition", font=("Arial", 9, "bold"),
              bg="#28a745", fg="white", relief=tk.FLAT, cursor="hand2", padx=12, pady=4,
              command=lambda: _add_condition()).pack(anchor="w", pady=5)

    # Load from existing rule
    load_row = tk.Frame(left, bg=WHITE)
    load_row.pack(fill="x", pady=(8, 0))
    tk.Label(load_row, text="Load from:", font=("Arial", 8), bg=WHITE, fg="#666").pack(side=tk.LEFT)

    rule_source_var = tk.StringVar(value="")
    rule_source_combo = ttk.Combobox(load_row, textvariable=rule_source_var, width=30, state="readonly")
    rule_source_combo.pack(side=tk.LEFT, padx=5)

    def _load_rule_list():
        """Populate the rule source dropdown."""
        rules_list = []
        report = os.path.join(project_root, 'project1_reverse_engineering', 'outputs', 'analysis_report.json')
        if os.path.exists(report):
            try:
                with open(report, encoding='utf-8') as f:
                    data = json.load(f)
                for i, r in enumerate(data.get('rules', [])):
                    if r.get('prediction') == 'WIN':
                        wr = r.get('win_rate', 0)
                        rules_list.append((f"Rule {i+1} (WR {wr:.0%})", r))
            except Exception:
                pass

        scratch = os.path.join(project_root, 'project4_strategy_creation', 'outputs', 'discovery_scratch.json')
        if os.path.exists(scratch):
            try:
                with open(scratch, encoding='utf-8') as f:
                    data = json.load(f)
                for i, r in enumerate(data.get('rules', [])):
                    rules_list.append((f"Scratch {i+1} (WR {r.get('win_rate',0):.0%})", r))
            except Exception:
                pass

        # Load optimizer rules from playground export
        playground_rules = os.path.join(project_root, 'project2_backtesting', 'outputs', '_playground_rules.json')
        if os.path.exists(playground_rules):
            try:
                with open(playground_rules, encoding='utf-8') as f:
                    data = json.load(f)
                opt_rules = data.get('rules', [])
                source = data.get('source', 'optimizer')
                # Each rule becomes a selectable option
                for i, r in enumerate(opt_rules):
                    wr = r.get('win_rate', 0)
                    label = f"🎯 Optimizer Rule {i+1} (WR {wr:.0%})" if wr else f"🎯 Optimizer Rule {i+1}"
                    rules_list.append((label, r))
            except Exception:
                pass

        return rules_list

    _rule_options = _load_rule_list()
    rule_source_combo['values'] = [r[0] for r in _rule_options]

    def _on_load_rule():
        idx = rule_source_combo.current()
        if idx < 0 or idx >= len(_rule_options):
            return
        _, rule = _rule_options[idx]
        # Clear existing conditions
        for rd in condition_rows[:]:
            rd['frame'].destroy()
        condition_rows.clear()
        # Add conditions from rule
        for cond in rule.get('conditions', []):
            _add_condition(cond['feature'], cond['operator'], str(cond['value']))

    tk.Button(load_row, text="Load", font=("Arial", 8, "bold"),
              bg="#667eea", fg="white", relief=tk.FLAT, padx=8,
              command=_on_load_rule).pack(side=tk.LEFT, padx=3)

    # ═══════ MIDDLE COLUMN: Results ═══════
    mid = tk.LabelFrame(main_frame, text="📊 Results", font=("Arial", 10, "bold"),
                         bg=WHITE, fg="#333", padx=10, pady=8)
    mid.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)

    # Test button — BIG and prominent
    test_btn = tk.Button(mid, text="▶ Test Strategy", font=("Arial", 12, "bold"),
                          bg="#28a745", fg="white", relief=tk.FLAT, cursor="hand2",
                          padx=20, pady=8)
    test_btn.pack(fill="x", pady=(0, 10))

    # Stats display
    stats_var = tk.StringVar(value="Click [▶ Test Strategy] to see results")
    stats_label = tk.Label(mid, textvariable=stats_var, font=("Courier", 9),
                            bg=WHITE, fg="#333", justify=tk.LEFT, anchor="nw")
    stats_label.pack(fill="x")

    # Trade list (scrollable)
    tk.Label(mid, text="TRADES:", font=("Arial", 9, "bold"), bg=WHITE, fg="#555").pack(anchor="w", pady=(10, 3))

    trade_canvas = tk.Canvas(mid, bg=WHITE, highlightthickness=0, height=300)
    trade_sb = tk.Scrollbar(mid, orient="vertical", command=trade_canvas.yview)
    trade_canvas.configure(yscrollcommand=trade_sb.set)
    trade_sb.pack(side=tk.RIGHT, fill="y")
    trade_canvas.pack(side=tk.LEFT, fill="both", expand=True)

    trade_inner = tk.Frame(trade_canvas, bg=WHITE)
    trade_wid = trade_canvas.create_window((0, 0), window=trade_inner, anchor="nw")
    trade_inner.bind("<Configure>", lambda e: trade_canvas.configure(scrollregion=trade_canvas.bbox("all")))
    trade_canvas.bind("<Configure>", lambda e: trade_canvas.itemconfig(trade_wid, width=e.width))

    # ═══════ RIGHT COLUMN: Prop Firm ═══════
    right = tk.LabelFrame(main_frame, text="🏦 Prop Firm Test", font=("Arial", 10, "bold"),
                           bg=WHITE, fg="#333", padx=10, pady=8)
    right.grid(row=0, column=2, sticky="nsew", padx=5, pady=5)

    # Firm selector
    firm_var = tk.StringVar(value="FTMO")
    acct_var = tk.StringVar(value="100000")
    risk_var = tk.StringVar(value="1.0")

    # Load firm names
    prop_dir = os.path.join(project_root, 'prop_firms')
    firm_names = []
    firm_configs = {}
    if os.path.isdir(prop_dir):
        for fp in sorted(os.listdir(prop_dir)):
            if fp.endswith('.json'):
                try:
                    with open(os.path.join(prop_dir, fp), encoding='utf-8') as fh:
                        fd = json.load(fh)
                    name = fd.get('firm_name', fp)
                    firm_names.append(name)
                    firm_configs[name] = fd
                except Exception:
                    pass

    tk.Label(right, text="Firm:", font=("Arial", 9), bg=WHITE).pack(anchor="w")
    ttk.Combobox(right, textvariable=firm_var, values=firm_names,
                  width=18, state="readonly").pack(fill="x", pady=2)

    tk.Label(right, text="Account $:", font=("Arial", 9), bg=WHITE).pack(anchor="w", pady=(5,0))
    tk.Entry(right, textvariable=acct_var, width=12, font=("Arial", 9)).pack(fill="x", pady=2)

    tk.Label(right, text="Risk %:", font=("Arial", 9), bg=WHITE).pack(anchor="w", pady=(5,0))
    tk.Entry(right, textvariable=risk_var, width=8, font=("Arial", 9)).pack(fill="x", pady=2)

    prop_result_var = tk.StringVar(value="Run test to see results")
    prop_label = tk.Label(right, textvariable=prop_result_var, font=("Courier", 9),
                           bg=WHITE, fg="#333", justify=tk.LEFT, anchor="nw", wraplength=250)
    prop_label.pack(fill="both", expand=True, pady=(10, 0))

    # Save current strategy button
    def _save_current():
        if not condition_rows:
            messagebox.showwarning("Empty", "Add at least one condition first.")
            return
        rule = {
            'conditions': [{'feature': r['feat_var'].get(), 'operator': r['op_var'].get(),
                            'value': float(r['val_var'].get())} for r in condition_rows],
            'prediction': 'WIN',
            'win_rate': 0, 'avg_pips': 0, 'coverage': 0,
        }
        try:
            from shared.saved_rules import save_rule
            rid = save_rule(rule, source="Playground", notes=f"Dir={dir_var.get()} SL={sl_var.get()} TP={tp_var.get()}")
            messagebox.showinfo("Saved", f"Rule saved! (ID: {rid})")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    tk.Button(right, text="💾 Save This Strategy", font=("Arial", 9, "bold"),
              bg="#667eea", fg="white", relief=tk.FLAT, padx=10, pady=5,
              command=_save_current).pack(fill="x", pady=(10, 0))

    # ═══════ TEST BUTTON ACTION ═══════
    def _run_test():
        if not _data_loaded[0]:
            messagebox.showwarning("Loading", "Indicator data still loading. Please wait.")
            return

        if not condition_rows:
            stats_var.set("Add at least one condition!")
            return

        conditions = []
        for rd in condition_rows:
            try:
                conditions.append({
                    'feature': rd['feat_var'].get(),
                    'operator': rd['op_var'].get(),
                    'value': float(rd['val_var'].get()),
                })
            except ValueError:
                stats_var.set(f"Invalid value in condition: {rd['val_var'].get()}")
                return

        from project2_backtesting.playground_engine import quick_backtest, simulate_prop_firm

        result = quick_backtest(
            _indicators[0], _candles[0], conditions,
            direction=dir_var.get(),
            sl_pips=float(sl_var.get()),
            tp_pips=float(tp_var.get()),
            max_hold_candles=int(hold_var.get()),
        )

        if result.get('error'):
            stats_var.set(f"ERROR: {result['error']}")
            return

        # Update stats
        stats_text = (
            f"Trades:    {result['total_trades']}\n"
            f"Win Rate:  {result['win_rate']:.1%} ({result['total_wins']}W / {result['total_losses']}L)\n"
            f"Net Pips:  {result['net_pips']:+,.0f}\n"
            f"Avg Pips:  {result['avg_pips']:+,.1f}\n"
            f"PF:        {result['profit_factor']:.2f}\n"
            f"Max DD:    {result['max_drawdown_pips']:,.0f} pips\n"
            f"Best:      {result['best_trade']:+,.0f} pips\n"
            f"Worst:     {result['worst_trade']:+,.0f} pips\n"
            f"Avg Hold:  {result['avg_hold']:.1f} candles"
        )
        stats_var.set(stats_text)

        # Update trade list
        for w in trade_inner.winfo_children():
            w.destroy()

        for t in result['trades'][:200]:  # show max 200
            pnl = t['pnl_pips']
            color = "#28a745" if pnl > 0 else "#dc3545"
            icon = "✅" if pnl > 0 else "❌"
            line = f"{t['entry_time'][:16]} {t['direction']} {pnl:+.0f} pips {t['exit_reason']} {icon}"
            tk.Label(trade_inner, text=line, font=("Courier", 8),
                     bg=WHITE, fg=color, anchor="w").pack(fill="x")

        trade_canvas.configure(scrollregion=trade_canvas.bbox("all"))

        # Prop firm simulation
        firm_name = firm_var.get()
        fc = firm_configs.get(firm_name)
        if fc:
            c = fc['challenges'][0]
            daily_dd = c['funded']['max_daily_drawdown_pct']
            total_dd = c['funded']['max_total_drawdown_pct']
        else:
            daily_dd = 5.0
            total_dd = 10.0

        prop = simulate_prop_firm(
            result['trades'],
            account_size=float(acct_var.get()),
            risk_pct=float(risk_var.get()),
            daily_dd_pct=daily_dd,
            total_dd_pct=total_dd,
        )

        if prop['passed']:
            prop_text = (
                f"✅ PASSED\n\n"
                f"Final: ${prop['final_balance']:,.0f}\n"
                f"Profit: {prop['final_pct']:+.1f}%\n"
                f"Daily DD: {prop['worst_daily_dd']:.1f}%\n"
                f"  (limit: {daily_dd}%) ✅\n"
                f"Total DD: {prop['worst_total_dd']:.1f}%\n"
                f"  (limit: {total_dd}%) ✅"
            )
            prop_label.config(fg="#28a745")
        else:
            prop_text = (
                f"❌ BLOWN\n\n"
                f"{prop['reason']}\n\n"
                f"Daily DD: {prop['worst_daily_dd']:.1f}%\n"
                f"  (limit: {daily_dd}%)\n"
                f"Total DD: {prop['worst_total_dd']:.1f}%\n"
                f"  (limit: {total_dd}%)"
            )
            prop_label.config(fg="#dc3545")

        prop_result_var.set(prop_text)

    test_btn.config(command=_run_test)

    # ═══════ Load data in background ═══════
    def _bg_load():
        try:
            _load_data_once()
            status_label.config(text=f"✅ Data loaded: {len(_candles[0]):,} candles, {len(_available_features)} features",
                                fg="#28a745")
        except Exception as e:
            status_label.config(text=f"❌ Failed to load data: {e}", fg="#dc3545")

    threading.Thread(target=_bg_load, daemon=True).start()

    # Add one default condition
    _add_condition("H1_rsi_14", ">", "60")

    return panel


def refresh():
    pass
