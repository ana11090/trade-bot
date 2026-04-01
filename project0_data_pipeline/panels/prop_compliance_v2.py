"""
Prop Compliance Panel v2 — uses the prop firm engine to check all firms at once.
Replaces the old single-firm compliance panel.
"""
import tkinter as tk
from tkinter import ttk
import threading
import pandas as pd
import state

# Module-level refs
_tree             = None
_status_label     = None
_account_size_var = None
_info_label       = None

# Simulation section refs
_sim_tree         = None
_sim_status_label = None
_sim_mode_var     = None
_sim_samples_var  = None
_sim_size_var     = None
_sim_risk_var     = None
_sim_sl_var       = None
_sim_run_btn      = None
_sim_running      = False


def build_panel(content):
    global _tree, _status_label, _account_size_var, _info_label

    panel = tk.Frame(content, bg="#f0f2f5")

    # Title
    tk.Label(panel, text="Prop Firm Compliance", bg="#f0f2f5", fg="#1a1a2a",
             font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(24, 2))

    _info_label = tk.Label(panel, text="Select a trade history and run compliance check",
                           bg="#f0f2f5", fg="#666666", font=("Segoe UI", 10))
    _info_label.pack(anchor="w", padx=20, pady=(0, 16))

    # ── Static compliance check ──────────────────────────────────────────────
    card = tk.Frame(panel, bg="white", bd=1, relief="solid")
    card.pack(fill="x", padx=20, pady=(0, 10))

    controls = tk.Frame(card, bg="white")
    controls.pack(fill="x", padx=16, pady=12)

    tk.Label(controls, text="Account size:", bg="white",
             font=("Segoe UI", 10)).pack(side="left")

    _account_size_var = tk.StringVar(value="100000")
    sizes = ["5000", "10000", "25000", "50000", "100000", "200000"]
    size_menu = tk.OptionMenu(controls, _account_size_var, *sizes)
    size_menu.configure(font=("Segoe UI", 10), bd=1, relief="solid")
    size_menu.pack(side="left", padx=(6, 12))

    run_btn = tk.Button(controls, text="Run Compliance Check",
                        bg="#e94560", fg="white",
                        activebackground="#c73850", activeforeground="white",
                        font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=4,
                        command=_run_check)
    run_btn.pack(side="left")

    _status_label = tk.Label(card, text="", bg="white", fg="#666666",
                             font=("Segoe UI", 9))
    _status_label.pack(anchor="w", padx=16, pady=(0, 8))

    # Results treeview
    tree_frame = tk.Frame(panel, bg="#f0f2f5")
    tree_frame.pack(fill="both", expand=True, padx=20, pady=(0, 10))

    cols = ("firm", "challenge", "result", "profit", "max_dd", "days", "reason")
    _tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=12)

    _tree.heading("firm",      text="Firm")
    _tree.heading("challenge", text="Challenge")
    _tree.heading("result",    text="Result")
    _tree.heading("profit",    text="Profit %")
    _tree.heading("max_dd",    text="Max DD %")
    _tree.heading("days",      text="Days")
    _tree.heading("reason",    text="Failure Reason")

    _tree.column("firm",      width=120, minwidth=100)
    _tree.column("challenge", width=150, minwidth=120)
    _tree.column("result",    width=60,  minwidth=50,  anchor="center")
    _tree.column("profit",    width=70,  minwidth=60,  anchor="center")
    _tree.column("max_dd",    width=70,  minwidth=60,  anchor="center")
    _tree.column("days",      width=50,  minwidth=40,  anchor="center")
    _tree.column("reason",    width=250, minwidth=150)

    scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=_tree.yview)
    _tree.configure(yscrollcommand=scrollbar.set)
    _tree.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    _tree.tag_configure("pass", background="#EAF3DE")
    _tree.tag_configure("fail", background="#FCEBEB")

    # ── Simulation section ───────────────────────────────────────────────────
    _build_simulation_section(panel)

    return panel


def _build_simulation_section(panel):
    global _sim_tree, _sim_status_label, _sim_mode_var, _sim_samples_var
    global _sim_size_var, _sim_risk_var, _sim_sl_var, _sim_run_btn

    # Divider
    sep_frame = tk.Frame(panel, bg="#f0f2f5")
    sep_frame.pack(fill="x", padx=20, pady=(10, 0))
    tk.Frame(sep_frame, bg="#cccccc", height=1).pack(fill="x")

    tk.Label(panel, text="Lifecycle Simulator", bg="#f0f2f5", fg="#1a1a2a",
             font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=20, pady=(14, 2))

    tk.Label(panel,
             text="Simulate starting a fresh challenge at every possible date to estimate real pass probability",
             bg="#f0f2f5", fg="#666666", font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(0, 10))

    # Simulation controls card
    sim_card = tk.Frame(panel, bg="white", bd=1, relief="solid")
    sim_card.pack(fill="x", padx=20, pady=(0, 10))

    row1 = tk.Frame(sim_card, bg="white")
    row1.pack(fill="x", padx=16, pady=(12, 6))

    tk.Label(row1, text="Mode:", bg="white", font=("Segoe UI", 10)).pack(side="left")
    _sim_mode_var = tk.StringVar(value="sliding_window")
    mode_menu = tk.OptionMenu(row1, _sim_mode_var, "sliding_window", "monte_carlo",
                               command=_on_mode_change)
    mode_menu.configure(font=("Segoe UI", 10), bd=1, relief="solid")
    mode_menu.pack(side="left", padx=(6, 20))

    tk.Label(row1, text="Samples (MC):", bg="white", font=("Segoe UI", 10)).pack(side="left")
    _sim_samples_var = tk.StringVar(value="200")
    samples_entry = tk.Entry(row1, textvariable=_sim_samples_var, width=6,
                             font=("Segoe UI", 10), bd=1, relief="solid")
    samples_entry.pack(side="left", padx=(6, 20))
    samples_entry.configure(state="disabled")

    tk.Label(row1, text="Account size:", bg="white", font=("Segoe UI", 10)).pack(side="left")
    _sim_size_var = tk.StringVar(value="100000")
    sizes = ["5000", "10000", "25000", "50000", "100000", "200000"]
    sim_size_menu = tk.OptionMenu(row1, _sim_size_var, *sizes)
    sim_size_menu.configure(font=("Segoe UI", 10), bd=1, relief="solid")
    sim_size_menu.pack(side="left", padx=(6, 20))

    tk.Label(row1, text="Risk %:", bg="white", font=("Segoe UI", 10)).pack(side="left", padx=(12, 0))
    _sim_risk_var = tk.StringVar(value="1.0")
    risk_menu = tk.OptionMenu(row1, _sim_risk_var, "0.5", "1.0", "1.5", "2.0", "3.0")
    risk_menu.configure(font=("Segoe UI", 10), bd=1, relief="solid")
    risk_menu.pack(side="left", padx=(4, 12))

    tk.Label(row1, text="SL pips:", bg="white", font=("Segoe UI", 10)).pack(side="left")
    _sim_sl_var = tk.StringVar(value="150")
    sl_entry = tk.Entry(row1, textvariable=_sim_sl_var, width=6,
                        font=("Segoe UI", 10), bd=1, relief="solid")
    sl_entry.pack(side="left", padx=(4, 12))

    _sim_run_btn = tk.Button(row1, text="Run Simulation",
                             bg="#2d8a4e", fg="white",
                             activebackground="#1e6b3c", activeforeground="white",
                             font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=4,
                             command=_run_simulation)
    _sim_run_btn.pack(side="left")

    _sim_status_label = tk.Label(sim_card, text="", bg="white", fg="#666666",
                                  font=("Segoe UI", 9))
    _sim_status_label.pack(anchor="w", padx=16, pady=(0, 8))

    # Store samples_entry ref on the var for enable/disable toggling
    _sim_mode_var._samples_entry = samples_entry

    # Simulation results treeview
    sim_tree_frame = tk.Frame(panel, bg="#f0f2f5")
    sim_tree_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))

    sim_cols = ("firm", "challenge", "pass_rate", "avg_eval_days",
                "funded_days", "monthly_payout", "expected_roi", "attempts")
    _sim_tree = ttk.Treeview(sim_tree_frame, columns=sim_cols, show="headings", height=12)

    _sim_tree.heading("firm",           text="Firm")
    _sim_tree.heading("challenge",      text="Challenge")
    _sim_tree.heading("pass_rate",      text="Pass Rate")
    _sim_tree.heading("avg_eval_days",  text="Avg Eval Days")
    _sim_tree.heading("funded_days",    text="Avg Funded Days")
    _sim_tree.heading("monthly_payout", text="Monthly Payout")
    _sim_tree.heading("expected_roi",   text="Expected ROI")
    _sim_tree.heading("attempts",       text="Avg Attempts")

    _sim_tree.column("firm",           width=120, minwidth=100)
    _sim_tree.column("challenge",      width=150, minwidth=120)
    _sim_tree.column("pass_rate",      width=80,  minwidth=60,  anchor="center")
    _sim_tree.column("avg_eval_days",  width=90,  minwidth=70,  anchor="center")
    _sim_tree.column("funded_days",    width=90,  minwidth=70,  anchor="center")
    _sim_tree.column("monthly_payout", width=100, minwidth=80,  anchor="center")
    _sim_tree.column("expected_roi",   width=90,  minwidth=70,  anchor="center")
    _sim_tree.column("attempts",       width=80,  minwidth=60,  anchor="center")

    sim_scrollbar = ttk.Scrollbar(sim_tree_frame, orient="vertical", command=_sim_tree.yview)
    _sim_tree.configure(yscrollcommand=sim_scrollbar.set)
    _sim_tree.pack(side="left", fill="both", expand=True)
    sim_scrollbar.pack(side="right", fill="y")

    _sim_tree.tag_configure("good",    background="#EAF3DE")
    _sim_tree.tag_configure("medium",  background="#FFF8E8")
    _sim_tree.tag_configure("poor",    background="#FCEBEB")


def _on_mode_change(value):
    entry = getattr(_sim_mode_var, '_samples_entry', None)
    if entry:
        entry.configure(state="normal" if value == "monte_carlo" else "disabled")


def _run_simulation():
    global _sim_running
    if _sim_running:
        return

    from shared.trade_history_manager import get_active_history, get_history_trades_path
    from shared.prop_firm_engine import load_all_firms

    active = get_active_history()
    if not active:
        _sim_status_label.configure(
            text="No trade history selected. Use '+ Load trades' first.", fg="#e94560")
        return

    try:
        account_size = int(_sim_size_var.get())
    except ValueError:
        account_size = 100000

    mode = _sim_mode_var.get()
    try:
        num_samples = int(_sim_samples_var.get())
    except ValueError:
        num_samples = 200

    try:
        risk_pct = float(_sim_risk_var.get())
    except ValueError:
        risk_pct = 1.0

    try:
        sl_pips = float(_sim_sl_var.get())
        sl_pips = max(1.0, sl_pips)
    except ValueError:
        sl_pips = 150.0

    trades_path = get_history_trades_path(active["history_id"])
    try:
        trades_df = pd.read_csv(trades_path)
    except Exception as e:
        _sim_status_label.configure(text=f"Error loading trades: {e}", fg="#e94560")
        return

    firms = load_all_firms()

    # Collect all challenges that match or approximate account_size
    targets = []
    for firm_id, firm in sorted(firms.items(), key=lambda x: x[1].firm_name):
        for ch_info in firm.list_challenges():
            ch = firm.get_challenge(ch_info["challenge_id"])
            if not ch:
                continue
            sizes = ch.get("account_sizes", [])
            if account_size in sizes or not sizes:
                targets.append((firm_id, ch_info["challenge_id"],
                                 firm.firm_name, ch_info["challenge_name"]))

    if not targets:
        _sim_status_label.configure(
            text="No challenges match this account size.", fg="#e94560")
        return

    # Clear old results
    for item in _sim_tree.get_children():
        _sim_tree.delete(item)

    _sim_running = True
    _sim_run_btn.configure(state="disabled", text="Running...")
    _sim_status_label.configure(
        text=f"Simulating {len(targets)} challenges ({mode})...", fg="#666666")

    def _worker():
        from shared.prop_firm_simulator import simulate_challenge
        results = []
        for idx, (firm_id, challenge_id, firm_name, challenge_name) in enumerate(targets):
            summary = simulate_challenge(
                trades_df, firm_id, challenge_id, account_size,
                mode=mode, num_samples=num_samples, simulate_funded=True,
                risk_per_trade_pct=risk_pct,
                default_sl_pips=sl_pips,
                pip_value_per_lot=1.0,
            )
            if summary:
                results.append((firm_name, challenge_name, summary))
            state.window.after(0, lambda i=idx: _sim_status_label.configure(
                text=f"Simulating {len(targets)} challenges ({mode})... {i+1}/{len(targets)}",
                fg="#666666"))

        state.window.after(0, lambda: _on_sim_done(results, len(targets), account_size))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def _on_sim_done(results, total, account_size):
    global _sim_running
    _sim_running = False
    _sim_run_btn.configure(state="normal", text="Run Simulation")

    if not results:
        _sim_status_label.configure(text="No simulation results.", fg="#e94560")
        return

    # Sort by expected ROI descending
    results.sort(key=lambda x: x[2].expected_roi_pct if x[2].expected_roi_pct is not None else -9999,
                 reverse=True)

    for firm_name, challenge_name, s in results:
        pass_rate_pct = s.eval_pass_rate * 100

        # Tag based on pass rate
        if pass_rate_pct >= 60:
            tag = "good"
        elif pass_rate_pct >= 30:
            tag = "medium"
        else:
            tag = "poor"

        avg_eval   = f"{s.eval_avg_days_to_pass:.0f}d" if s.eval_avg_days_to_pass else "—"
        funded_d   = f"{s.funded_avg_survival_days:.0f}d" if s.funded_avg_survival_days else "—"
        monthly_p  = (f"${s.funded_avg_monthly_payout:,.0f}"
                      if s.funded_avg_monthly_payout else "—")
        roi        = (f"{s.expected_roi_pct:.0f}%"
                      if s.expected_roi_pct is not None else "—")
        attempts   = (f"{s.avg_attempts_to_pass:.1f}"
                      if s.avg_attempts_to_pass is not None else "—")

        _sim_tree.insert("", "end", values=(
            firm_name,
            challenge_name,
            f"{pass_rate_pct:.1f}%",
            avg_eval,
            funded_d,
            monthly_p,
            roi,
            attempts,
        ), tags=(tag,))

    lot_info = ""
    if results:
        first_s = results[0][2]
        lot_info = (f" | {first_s.calculated_lot_size:.2f} lots "
                    f"({first_s.risk_per_trade_pct}% risk, {first_s.default_sl_pips:.0f}-pip SL)")
    _sim_status_label.configure(
        text=f"Done — {len(results)}/{total} challenges simulated (${account_size:,}){lot_info}",
        fg="#2d8a4e")


def _run_check():
    from shared.trade_history_manager import get_active_history, get_history_trades_path
    from shared.prop_firm_engine import get_compliance_matrix

    active = get_active_history()
    if not active:
        _status_label.configure(
            text="No trade history selected. Use '+ Load trades' first.", fg="#e94560")
        return

    try:
        account_size = int(_account_size_var.get())
    except ValueError:
        account_size = 100000

    _status_label.configure(
        text=f"Running compliance check for {active['robot_name']}...", fg="#666666")
    _info_label.configure(
        text=f"{active['robot_name']} — {active['trade_count']} trades — {active['symbol']}")

    for item in _tree.get_children():
        _tree.delete(item)

    try:
        trades_path = get_history_trades_path(active["history_id"])
        trades_df   = pd.read_csv(trades_path)
        matrix      = get_compliance_matrix(trades_df, account_size=account_size)

        if matrix is None or len(matrix) == 0:
            _status_label.configure(
                text="No results — no challenges match this account size.", fg="#e94560")
            return

        for _, row in matrix.iterrows():
            tag         = "pass" if row["passed"] else "fail"
            result_text = "PASS" if row["passed"] else "FAIL"
            _tree.insert("", "end", values=(
                row["firm_name"],
                row["challenge_name"],
                result_text,
                f"{row['profit_achieved_pct']:.1f}%",
                f"{row['max_dd_hit_pct']:.1f}%",
                row["days_traded"],
                row.get("failure_reason", ""),
            ), tags=(tag,))

        pass_count = int(matrix["passed"].sum())
        total      = len(matrix)
        _status_label.configure(
            text=f"Done — {pass_count}/{total} challenges passed (${account_size:,} account)",
            fg="#2d8a4e" if pass_count > 0 else "#e94560")

    except Exception as e:
        _status_label.configure(text=f"Error: {e}", fg="#e94560")


def refresh():
    from shared.trade_history_manager import get_active_history
    active = get_active_history()
    if active and _info_label:
        _info_label.configure(
            text=f"{active['robot_name']} — {active['trade_count']} trades — {active['symbol']}")
    elif _info_label:
        _info_label.configure(text="No trade history selected")
