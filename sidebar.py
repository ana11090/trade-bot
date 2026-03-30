import tkinter as tk
import state


def build_sidebar(window, canvas, refresh_map):
    sidebar = tk.Frame(window, bg="#16213e", width=200)
    sidebar.pack(side="left", fill="y")
    sidebar.pack_propagate(False)

    tk.Label(sidebar, text="Trade Bot", bg="#16213e", fg="white",
             font=("Segoe UI", 14, "bold"), pady=20).pack()

    stats_open = [False]
    prob_open  = [False]
    project1_open = [False]
    project2_open = [False]

    # ── Frames ────────────────────────────────────────────────────────────────
    # Everything under "0 - Data Pipeline" lives here (hidden until btn0 click)
    project0_extras = tk.Frame(sidebar, bg="#16213e")

    # Stats submenu (inside project0_extras)
    stats_submenu = tk.Frame(project0_extras, bg=state.COL_SUB)

    # Probabilities submenu (inside project0_extras)
    prob_submenu = tk.Frame(project0_extras, bg=state.COL_SUB)

    # Everything under "1 - Reverse Engineer" lives here (hidden until btn1 click)
    project1_extras = tk.Frame(sidebar, bg="#16213e")

    # Everything under "2 - Backtesting" lives here (hidden until btn2 click)
    project2_extras = tk.Frame(sidebar, bg="#16213e")

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

        is_stats_sub = name in state.SUB_PANELS
        is_prob_sub  = name in state.PROB_SUB_PANELS
        is_project1_sub = name in state.PROJECT1_SUB_PANELS
        is_project2_sub = name in state.PROJECT2_SUB_PANELS

        # btn0
        if name == "pipeline":
            btn0.configure(bg=state.COL_ACTIVE, fg=state.FG_ACTIVE,
                           activebackground=state.COL_ACTIVE, activeforeground=state.FG_ACTIVE)
        else:
            btn0.configure(bg=state.COL_INACTIVE, fg=state.FG_INACTIVE,
                           activebackground=state.COL_INACTIVE, activeforeground=state.FG_INACTIVE)

        # btn1 (project1)
        if is_project1_sub:
            btn1.configure(bg=state.COL_PARENT, fg="white",
                          activebackground=state.COL_PARENT, activeforeground="white")
        else:
            btn1.configure(bg=state.COL_INACTIVE, fg=state.FG_INACTIVE,
                          activebackground=state.COL_INACTIVE, activeforeground=state.FG_INACTIVE)

        # btn2 (project2)
        if is_project2_sub:
            btn2.configure(bg=state.COL_PARENT, fg="white",
                          activebackground=state.COL_PARENT, activeforeground="white")
        else:
            btn2.configure(bg=state.COL_INACTIVE, fg=state.FG_INACTIVE,
                          activebackground=state.COL_INACTIVE, activeforeground=state.FG_INACTIVE)

        # stats_btn — lit when a stats sub-panel is active
        if is_stats_sub:
            stats_btn.configure(bg=state.COL_PARENT, fg="white",
                                activebackground=state.COL_PARENT, activeforeground="white")
        else:
            stats_btn.configure(bg=state.COL_INACTIVE, fg=state.FG_INACTIVE,
                                activebackground=state.COL_INACTIVE, activeforeground=state.FG_INACTIVE)

        # prob_btn — lit when a probabilities sub-panel is active
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

        # auto-expand stats submenu when navigating directly to a stats sub-panel
        if is_stats_sub and not stats_open[0]:
            stats_submenu.pack(fill="x", after=stats_btn)
            stats_open[0] = True

        # auto-expand prob submenu when navigating directly to a prob sub-panel
        if is_prob_sub and not prob_open[0]:
            prob_submenu.pack(fill="x", after=prob_btn)
            prob_open[0] = True

        # auto-expand project1 submenu when navigating directly to a project1 sub-panel
        if is_project1_sub and not project1_open[0]:
            project1_extras.pack(fill="x", after=btn1)
            project1_open[0] = True

        # auto-expand project2 submenu when navigating directly to a project2 sub-panel
        if is_project2_sub and not project2_open[0]:
            project2_extras.pack(fill="x", after=btn2)
            project2_open[0] = True

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

    # ── Pipeline click — reveals project0_extras ──────────────────────────────
    def _on_pipeline_click():
        show_panel("pipeline")
        if not project0_extras.winfo_ismapped():
            project0_extras.pack(fill="x", after=btn0)

    # ── Reverse Engineer click — reveals/hides project1_extras ────────────────
    def _toggle_project1():
        if project1_open[0]:
            project1_extras.pack_forget()
            project1_open[0] = False
        else:
            project1_extras.pack(fill="x", after=btn1)
            project1_open[0] = True
            # Show first sub-panel
            show_panel("p1_config")

    # ── Backtesting click — reveals/hides project2_extras ─────────────────────
    def _toggle_project2():
        if project2_open[0]:
            project2_extras.pack_forget()
            project2_open[0] = False
        else:
            project2_extras.pack(fill="x", after=btn2)
            project2_open[0] = True
            # Show first sub-panel
            show_panel("p2_config")

    # ── Build buttons ─────────────────────────────────────────────────────────
    btn0 = _sidebar_btn(sidebar, "0 - Data Pipeline", _on_pipeline_click)
    btn0.pack(fill="x")

    # project0_extras — hidden until btn0 is clicked
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

    # ── Remaining project buttons (always visible) ────────────────────────────
    btn1 = _sidebar_btn(sidebar, "1 - Reverse Engineer", _toggle_project1)
    btn1.pack(fill="x")

    # project1_extras — hidden until btn1 is clicked
    btn_p1_config = _sub_btn(project1_extras, "⚙️ Configuration & Data", lambda: show_panel("p1_config"))
    btn_p1_config.pack(fill="x")
    btn_p1_run = _sub_btn(project1_extras, "🚀 Run Scenarios", lambda: show_panel("p1_run"))
    btn_p1_run.pack(fill="x")
    btn_p1_results = _sub_btn(project1_extras, "📊 View Results", lambda: show_panel("p1_results"))
    btn_p1_results.pack(fill="x")

    PROJECT1_BUTTONS = {
        "p1_config": btn_p1_config,
        "p1_run": btn_p1_run,
        "p1_results": btn_p1_results,
    }

    btn2 = _sidebar_btn(sidebar, "2 - Backtesting", _toggle_project2)
    btn2.pack(fill="x")

    # project2_extras — hidden until btn2 is clicked
    btn_p2_config = _sub_btn(project2_extras, "⚙️ Configuration", lambda: show_panel("p2_config"))
    btn_p2_config.pack(fill="x")
    btn_p2_run = _sub_btn(project2_extras, "🚀 Run Backtest", lambda: show_panel("p2_run"))
    btn_p2_run.pack(fill="x")
    btn_p2_results = _sub_btn(project2_extras, "📊 View Results", lambda: show_panel("p2_results"))
    btn_p2_results.pack(fill="x")

    PROJECT2_BUTTONS = {
        "p2_config": btn_p2_config,
        "p2_run": btn_p2_run,
        "p2_results": btn_p2_results,
    }

    btn3 = _sidebar_btn(sidebar, "3 - Forward Bot",      lambda: None)
    btn3.pack(fill="x")

    return show_panel
