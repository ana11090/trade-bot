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

    # ── Frames ────────────────────────────────────────────────────────────────
    # Everything under "0 - Data Pipeline" lives here (hidden until btn0 click)
    project0_extras = tk.Frame(sidebar, bg="#16213e")

    # Stats submenu (inside project0_extras)
    stats_submenu = tk.Frame(project0_extras, bg=state.COL_SUB)

    # Probabilities submenu (inside project0_extras)
    prob_submenu = tk.Frame(project0_extras, bg=state.COL_SUB)

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

        # btn0
        if name == "pipeline":
            btn0.configure(bg=state.COL_ACTIVE, fg=state.FG_ACTIVE,
                           activebackground=state.COL_ACTIVE, activeforeground=state.FG_ACTIVE)
        else:
            btn0.configure(bg=state.COL_INACTIVE, fg=state.FG_INACTIVE,
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

        # auto-expand stats submenu when navigating directly to a stats sub-panel
        if is_stats_sub and not stats_open[0]:
            stats_submenu.pack(fill="x", after=stats_btn)
            stats_open[0] = True

        # auto-expand prob submenu when navigating directly to a prob sub-panel
        if is_prob_sub and not prob_open[0]:
            prob_submenu.pack(fill="x", after=prob_btn)
            prob_open[0] = True

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
    btn1 = _sidebar_btn(sidebar, "1 - Reverse Engineer", lambda: None)
    btn1.pack(fill="x")
    btn2 = _sidebar_btn(sidebar, "2 - Backtesting",      lambda: None)
    btn2.pack(fill="x")
    btn3 = _sidebar_btn(sidebar, "3 - Forward Bot",      lambda: None)
    btn3.pack(fill="x")

    return show_panel
