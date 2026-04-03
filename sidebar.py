import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox
import state


def build_sidebar(window, canvas, refresh_map):
    sidebar = tk.Frame(window, bg="#16213e", width=200)
    sidebar.pack(side="left", fill="y")
    sidebar.pack_propagate(False)

    tk.Label(sidebar, text="Trade Bot", bg="#16213e", fg="white",
             font=("Segoe UI", 14, "bold"), pady=16).pack()

    # ── Trade history selector ────────────────────────────────────────────────
    from shared.trade_history_manager import (
        list_trade_histories, get_active_history, set_active_history,
        load_trades, get_history_config, get_history_trades_path,
    )

    selector_frame = tk.Frame(sidebar, bg="#16213e")
    selector_frame.pack(fill="x", padx=10, pady=(0, 6))

    tk.Label(selector_frame, text="Active trade history", bg="#16213e", fg="#5a7a99",
             font=("Segoe UI", 8)).pack(anchor="w")

    history_var = tk.StringVar()

    histories     = list_trade_histories()
    history_names = [h["robot_name"] for h in histories] if histories else ["No histories"]
    active        = get_active_history()
    if active:
        history_var.set(active["robot_name"])
        state.active_history_id     = active["history_id"]
        state.active_history_config = active
    else:
        history_var.set(history_names[0])

    history_dropdown = tk.OptionMenu(selector_frame, history_var, *history_names)
    history_dropdown.configure(
        bg="#1e2d4e", fg="white", font=("Segoe UI", 10),
        activebackground="#1e2d4e", activeforeground="white",
        highlightthickness=0, bd=0)
    history_dropdown["menu"].configure(bg="#1e2d4e", fg="white", font=("Segoe UI", 10))
    history_dropdown.pack(fill="x")

    def _refresh_history_dropdown():
        fresh     = list_trade_histories()
        menu      = history_dropdown["menu"]
        menu.delete(0, "end")
        for h in fresh:
            menu.add_command(
                label=h["robot_name"],
                command=lambda name=h["robot_name"], hid=h["history_id"]:
                    _select_history(name, hid))
        current = get_active_history()
        if current:
            history_var.set(current["robot_name"])

    def _select_history(name, history_id):
        set_active_history(history_id)
        history_var.set(name)
        state.active_history_id     = history_id
        state.active_history_config = get_history_config(history_id)
        current_panel = state.active_panel[0]
        if current_panel and current_panel in refresh_map:
            refresh_map[current_panel]()

    def _load_new_trades():
        csv_path = filedialog.askopenfilename(
            title="Select trade history CSV",
            filetypes=[("CSV files", "*.csv"), ("Text files", "*.txt"), ("All files", "*.*")])
        if not csv_path:
            return
        robot_name = simpledialog.askstring(
            "Robot Name", "What robot produced these trades?", parent=window)
        if not robot_name:
            return
        try:
            result = load_trades(robot_name=robot_name, trades_csv_path=csv_path)
            state.active_history_id     = result["history_id"]
            state.active_history_config = result
            _refresh_history_dropdown()
            messagebox.showinfo(
                "Trades Loaded",
                f"Loaded {result['trade_count']} trades from '{robot_name}'\n"
                f"Date range: {result['date_range'].get('start', '?')} to "
                f"{result['date_range'].get('end', '?')}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load trades:\n{e}")

    load_btn = tk.Button(
        selector_frame, text="+ Load trades",
        bg="#e94560", fg="white",
        activebackground="#c73850", activeforeground="white",
        font=("Segoe UI", 9, "bold"), bd=0, pady=4,
        command=_load_new_trades)
    load_btn.pack(fill="x", pady=(4, 0))

    # ── Separator ─────────────────────────────────────────────────────────────
    tk.Frame(sidebar, bg="#2a3a5c", height=1).pack(fill="x", padx=10, pady=(4, 0))

    stats_open    = [False]
    prob_open     = [False]
    project1_open = [False]
    project2_open = [False]
    project3_open = [False]

    # ── Frames ────────────────────────────────────────────────────────────────
    project0_extras = tk.Frame(sidebar, bg="#16213e")
    stats_submenu   = tk.Frame(project0_extras, bg=state.COL_SUB)
    prob_submenu    = tk.Frame(project0_extras, bg=state.COL_SUB)
    project1_extras = tk.Frame(sidebar, bg="#16213e")
    project2_extras = tk.Frame(sidebar, bg="#16213e")
    project3_extras = tk.Frame(sidebar, bg="#16213e")

    # ── Button helpers ────────────────────────────────────────────────────────
    def _sidebar_btn(parent, text, cmd):
        return tk.Button(parent, text=text,
                         bg=state.COL_INACTIVE, fg=state.FG_INACTIVE,
                         activebackground=state.COL_INACTIVE, activeforeground=state.FG_INACTIVE,
                         font=("Segoe UI", 11), bd=0, pady=12, anchor="w", padx=16,
                         command=cmd)

    def _sub_btn(parent, text, cmd):
        return tk.Button(parent, text=text,
                         bg=state.COL_SUB, fg=state.FG_SUB,
                         activebackground=state.COL_SUB, activeforeground=state.FG_SUB,
                         font=("Segoe UI", 10), bd=0, pady=9, anchor="w", padx=30,
                         command=cmd)

    # ── show_panel ────────────────────────────────────────────────────────────
    def show_panel(name):
        for pframe in state.all_panels.values():
            pframe.pack_forget()
        if name in state.all_panels:
            state.all_panels[name].pack(fill="both", expand=True)

        is_stats_sub    = name in state.SUB_PANELS
        is_prob_sub     = name in state.PROB_SUB_PANELS
        is_project1_sub = name in state.PROJECT1_SUB_PANELS
        is_project2_sub = name in state.PROJECT2_SUB_PANELS
        is_project3_sub = name in state.PROJECT3_SUB_PANELS
        is_p0_extra     = name in state.PROJECT0_EXTRA_PANELS

        # btn0 — active for pipeline + sub-panels + new extra panels
        if name == "pipeline" or is_stats_sub or is_prob_sub or is_p0_extra:
            btn0.configure(bg=state.COL_PARENT if (is_stats_sub or is_prob_sub or is_p0_extra)
                           else state.COL_ACTIVE,
                           fg=state.FG_ACTIVE,
                           activebackground=state.COL_PARENT if (is_stats_sub or is_prob_sub or is_p0_extra)
                           else state.COL_ACTIVE,
                           activeforeground=state.FG_ACTIVE)
        else:
            btn0.configure(bg=state.COL_INACTIVE, fg=state.FG_INACTIVE,
                           activebackground=state.COL_INACTIVE, activeforeground=state.FG_INACTIVE)

        # btn1
        if is_project1_sub:
            btn1.configure(bg=state.COL_PARENT, fg="white",
                           activebackground=state.COL_PARENT, activeforeground="white")
        else:
            btn1.configure(bg=state.COL_INACTIVE, fg=state.FG_INACTIVE,
                           activebackground=state.COL_INACTIVE, activeforeground=state.FG_INACTIVE)

        # btn2
        if is_project2_sub:
            btn2.configure(bg=state.COL_PARENT, fg="white",
                           activebackground=state.COL_PARENT, activeforeground="white")
        else:
            btn2.configure(bg=state.COL_INACTIVE, fg=state.FG_INACTIVE,
                           activebackground=state.COL_INACTIVE, activeforeground=state.FG_INACTIVE)

        # btn3
        if is_project3_sub:
            btn3.configure(bg=state.COL_PARENT, fg="white",
                           activebackground=state.COL_PARENT, activeforeground="white")
        else:
            btn3.configure(bg=state.COL_INACTIVE, fg=state.FG_INACTIVE,
                           activebackground=state.COL_INACTIVE, activeforeground=state.FG_INACTIVE)

        # stats_btn
        if is_stats_sub:
            stats_btn.configure(bg=state.COL_PARENT, fg="white",
                                activebackground=state.COL_PARENT, activeforeground="white")
        else:
            stats_btn.configure(bg=state.COL_INACTIVE, fg=state.FG_INACTIVE,
                                activebackground=state.COL_INACTIVE, activeforeground=state.FG_INACTIVE)

        # prob_btn
        if is_prob_sub:
            prob_btn.configure(bg=state.COL_PARENT, fg="white",
                               activebackground=state.COL_PARENT, activeforeground="white")
        else:
            prob_btn.configure(bg=state.COL_INACTIVE, fg=state.FG_INACTIVE,
                               activebackground=state.COL_INACTIVE, activeforeground=state.FG_INACTIVE)

        # stats sub-button colors
        for pname, btn in STATS_BUTTONS.items():
            if pname == name:
                btn.configure(bg=state.COL_ACTIVE, fg=state.FG_ACTIVE,
                              activebackground=state.COL_ACTIVE, activeforeground=state.FG_ACTIVE)
            else:
                btn.configure(bg=state.COL_SUB, fg=state.FG_SUB,
                              activebackground=state.COL_SUB, activeforeground=state.FG_SUB)

        # prob sub-button colors
        for pname, btn in PROB_BUTTONS.items():
            if pname == name:
                btn.configure(bg=state.COL_ACTIVE, fg=state.FG_ACTIVE,
                              activebackground=state.COL_ACTIVE, activeforeground=state.FG_ACTIVE)
            else:
                btn.configure(bg=state.COL_SUB, fg=state.FG_SUB,
                              activebackground=state.COL_SUB, activeforeground=state.FG_SUB)

        # project0 extra button colors
        for pname, btn in PROJECT0_EXTRA_BUTTONS.items():
            if pname == name:
                btn.configure(bg=state.COL_ACTIVE, fg=state.FG_ACTIVE,
                              activebackground=state.COL_ACTIVE, activeforeground=state.FG_ACTIVE)
            else:
                btn.configure(bg=state.COL_INACTIVE, fg=state.FG_INACTIVE,
                              activebackground=state.COL_INACTIVE, activeforeground=state.FG_INACTIVE)

        # project1 sub-button colors
        for pname, btn in PROJECT1_BUTTONS.items():
            if pname == name:
                btn.configure(bg=state.COL_ACTIVE, fg=state.FG_ACTIVE,
                              activebackground=state.COL_ACTIVE, activeforeground=state.FG_ACTIVE)
            else:
                btn.configure(bg=state.COL_SUB, fg=state.FG_SUB,
                              activebackground=state.COL_SUB, activeforeground=state.FG_SUB)

        # project2 sub-button colors
        for pname, btn in PROJECT2_BUTTONS.items():
            if pname == name:
                btn.configure(bg=state.COL_ACTIVE, fg=state.FG_ACTIVE,
                              activebackground=state.COL_ACTIVE, activeforeground=state.FG_ACTIVE)
            else:
                btn.configure(bg=state.COL_SUB, fg=state.FG_SUB,
                              activebackground=state.COL_SUB, activeforeground=state.FG_SUB)

        # project3 sub-button colors
        for pname, btn in PROJECT3_BUTTONS.items():
            if pname == name:
                btn.configure(bg=state.COL_ACTIVE, fg=state.FG_ACTIVE,
                              activebackground=state.COL_ACTIVE, activeforeground=state.FG_ACTIVE)
            else:
                btn.configure(bg=state.COL_SUB, fg=state.FG_SUB,
                              activebackground=state.COL_SUB, activeforeground=state.FG_SUB)

        # auto-expand submenus when navigating directly
        if is_stats_sub and not stats_open[0]:
            stats_submenu.pack(fill="x", after=stats_btn)
            stats_open[0] = True
        if is_prob_sub and not prob_open[0]:
            prob_submenu.pack(fill="x", after=prob_btn)
            prob_open[0] = True
        if is_project1_sub and not project1_open[0]:
            project1_extras.pack(fill="x", after=btn1)
            project1_open[0] = True
        if is_project2_sub and not project2_open[0]:
            project2_extras.pack(fill="x", after=btn2)
            project2_open[0] = True
        if is_project3_sub and not project3_open[0]:
            project3_extras.pack(fill="x", after=btn3)
            project3_open[0] = True
        if is_p0_extra and not project0_extras.winfo_ismapped():
            project0_extras.pack(fill="x", after=btn0)

        canvas.yview_moveto(0)
        state.active_panel[0] = name
        if name in refresh_map:
            refresh_map[name]()

    # ── Toggles ───────────────────────────────────────────────────────────────
    def _toggle_stats():
        if stats_open[0]:
            stats_submenu.pack_forget()
            stats_open[0] = False
        else:
            stats_submenu.pack(fill="x", after=stats_btn)
            stats_open[0] = True

    def _toggle_prob():
        if prob_open[0]:
            prob_submenu.pack_forget()
            prob_open[0] = False
        else:
            prob_submenu.pack(fill="x", after=prob_btn)
            prob_open[0] = True

    def _on_pipeline_click():
        show_panel("pipeline")
        if not project0_extras.winfo_ismapped():
            project0_extras.pack(fill="x", after=btn0)

    def _toggle_project1():
        if project1_open[0]:
            project1_extras.pack_forget()
            project1_open[0] = False
        else:
            project1_extras.pack(fill="x", after=btn1)
            project1_open[0] = True
            show_panel("p1_config")

    def _toggle_project2():
        if project2_open[0]:
            project2_extras.pack_forget()
            project2_open[0] = False
        else:
            project2_extras.pack(fill="x", after=btn2)
            project2_open[0] = True
            show_panel("p2_config")

    def _toggle_project3():
        if project3_open[0]:
            project3_extras.pack_forget()
            project3_open[0] = False
        else:
            project3_extras.pack(fill="x", after=btn3)
            project3_open[0] = True
            show_panel("p3_generator")

    # ── Build buttons ─────────────────────────────────────────────────────────
    btn0 = _sidebar_btn(sidebar, "0 - Data Pipeline", _on_pipeline_click)
    btn0.pack(fill="x")

    # project0_extras contents
    stats_btn = _sidebar_btn(project0_extras, "Statistic transactions history", _toggle_stats)
    stats_btn.pack(fill="x")

    btn_p4 = _sub_btn(stats_submenu, "Performance",     lambda: show_panel("panel4"))
    btn_p4.pack(fill="x")
    btn_p5 = _sub_btn(stats_submenu, "Statistics",      lambda: show_panel("panel5"))
    btn_p5.pack(fill="x")
    btn_p6 = _sub_btn(stats_submenu, "Risk & Flags",    lambda: show_panel("panel6"))
    btn_p6.pack(fill="x")
    btn_p7 = _sub_btn(stats_submenu, "Prop Compliance", lambda: show_panel("panel7"))
    btn_p7.pack(fill="x")
    btn_p8 = _sub_btn(stats_submenu, "Cost & Spread",   lambda: show_panel("panel8"))
    btn_p8.pack(fill="x")

    STATS_BUTTONS = {
        "panel4": btn_p4, "panel5": btn_p5, "panel6": btn_p6,
        "panel7": btn_p7, "panel8": btn_p8,
    }

    prob_btn = _sidebar_btn(project0_extras, "Probabilities", _toggle_prob)
    prob_btn.pack(fill="x")

    btn_surv = _sub_btn(prob_submenu, "Account Survival",    lambda: show_panel("account_survival"))
    btn_surv.pack(fill="x")
    btn_ev   = _sub_btn(prob_submenu, "Expected Value",      lambda: show_panel("expected_value"))
    btn_ev.pack(fill="x")
    btn_be   = _sub_btn(prob_submenu, "Break-even Analysis", lambda: show_panel("breakeven"))
    btn_be.pack(fill="x")
    btn_kel  = _sub_btn(prob_submenu, "Kelly Criterion",     lambda: show_panel("kelly"))
    btn_kel.pack(fill="x")
    btn_str  = _sub_btn(prob_submenu, "Streak Analysis",     lambda: show_panel("streaks"))
    btn_str.pack(fill="x")
    btn_dd   = _sub_btn(prob_submenu, "Drawdown & Recovery", lambda: show_panel("drawdown_recovery"))
    btn_dd.pack(fill="x")

    PROB_BUTTONS = {
        "account_survival": btn_surv, "expected_value": btn_ev,
        "breakeven": btn_be, "kelly": btn_kel,
        "streaks": btn_str, "drawdown_recovery": btn_dd,
    }

    # New standalone Project 0 panels
    explorer_btn = _sidebar_btn(project0_extras, "Prop Firm Explorer",
                                lambda: show_panel("prop_explorer"))
    explorer_btn.pack(fill="x")

    sim_btn = _sidebar_btn(project0_extras, "Lifecycle Simulator",
                           lambda: show_panel("lifecycle_sim"))
    sim_btn.pack(fill="x")

    compare_btn = _sidebar_btn(project0_extras, "Compare Trade Histories",
                               lambda: show_panel("compare_histories"))
    compare_btn.pack(fill="x")

    PROJECT0_EXTRA_BUTTONS = {
        "prop_explorer":      explorer_btn,
        "lifecycle_sim":      sim_btn,
        "compare_histories":  compare_btn,
    }

    # ── Remaining project buttons (always visible) ────────────────────────────
    btn1 = _sidebar_btn(sidebar, "1 - Reverse Engineer", _toggle_project1)
    btn1.pack(fill="x")

    btn_p1_config  = _sub_btn(project1_extras, "⚙️ Configuration & Data", lambda: show_panel("p1_config"))
    btn_p1_config.pack(fill="x")
    btn_p1_run     = _sub_btn(project1_extras, "🚀 Run Scenarios",         lambda: show_panel("p1_run"))
    btn_p1_run.pack(fill="x")
    btn_p1_results = _sub_btn(project1_extras, "📊 View Results",          lambda: show_panel("p1_results"))
    btn_p1_results.pack(fill="x")
    btn_p1_analysis = _sub_btn(project1_extras, "🤖 Robot Analysis",       lambda: show_panel("p1_analysis"))
    btn_p1_analysis.pack(fill="x")
    btn_p1_search = _sub_btn(project1_extras, "🔍 Strategy Search",        lambda: show_panel("p1_search"))
    btn_p1_search.pack(fill="x")

    PROJECT1_BUTTONS = {
        "p1_config":   btn_p1_config,
        "p1_run":      btn_p1_run,
        "p1_results":  btn_p1_results,
        "p1_analysis": btn_p1_analysis,
        "p1_search":   btn_p1_search,
    }

    btn2 = _sidebar_btn(sidebar, "2 - Backtesting", _toggle_project2)
    btn2.pack(fill="x")

    btn_p2_config  = _sub_btn(project2_extras, "⚙️ Configuration", lambda: show_panel("p2_config"))
    btn_p2_config.pack(fill="x")
    btn_p2_run     = _sub_btn(project2_extras, "🚀 Run Backtest",   lambda: show_panel("p2_run"))
    btn_p2_run.pack(fill="x")
    btn_p2_results = _sub_btn(project2_extras, "📊 View Results",   lambda: show_panel("p2_results"))
    btn_p2_results.pack(fill="x")
    btn_p2_refiner = _sub_btn(project2_extras, "✂️ Strategy Refiner", lambda: show_panel("p2_refiner"))
    btn_p2_refiner.pack(fill="x")
    btn_p2_validator = _sub_btn(project2_extras, "✅ Strategy Validator", lambda: show_panel("p2_validator"))
    btn_p2_validator.pack(fill="x")
    btn_p2_prop = _sub_btn(project2_extras, "🏦 Prop Firm Test", lambda: show_panel("p2_prop_test"))
    btn_p2_prop.pack(fill="x")

    PROJECT2_BUTTONS = {
        "p2_config":    btn_p2_config,
        "p2_run":       btn_p2_run,
        "p2_results":   btn_p2_results,
        "p2_refiner":   btn_p2_refiner,
        "p2_validator": btn_p2_validator,
        "p2_prop_test": btn_p2_prop,
    }

    btn3 = _sidebar_btn(sidebar, "3 - Live Trading", _toggle_project3)
    btn3.pack(fill="x")

    btn_p3_generator = _sub_btn(project3_extras, "🤖 EA Generator",  lambda: show_panel("p3_generator"))
    btn_p3_generator.pack(fill="x")
    btn_p3_monitor   = _sub_btn(project3_extras, "📊 Live Monitor",   lambda: show_panel("p3_monitor"))
    btn_p3_monitor.pack(fill="x")

    PROJECT3_BUTTONS = {
        "p3_generator": btn_p3_generator,
        "p3_monitor":   btn_p3_monitor,
    }

    return show_panel
