"""
Lifecycle Simulator Panel — standalone panel for prop firm probability simulation.
Simulates starting a fresh challenge at every possible date (sliding window or Monte Carlo)
to estimate real pass probability, funded survival, and expected value.
"""
import tkinter as tk
from tkinter import ttk
import threading
import inspect
import pandas as pd
import state

# Module-level refs
_tree         = None
_status_label = None
_mode_var     = None
_samples_var  = None
_size_var     = None
_risk_var     = None
_sl_var       = None
_run_btn      = None
_running      = False


def build_panel(content):
    global _tree, _status_label, _mode_var, _samples_var
    global _size_var, _risk_var, _sl_var, _run_btn

    panel = tk.Frame(content, bg="#f0f2f5")

    tk.Label(panel, text="Lifecycle Simulator", bg="#f0f2f5", fg="#1a1a2a",
             font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(24, 2))

    tk.Label(panel,
             text="Simulate starting a fresh challenge at every possible date to estimate real pass probability",
             bg="#f0f2f5", fg="#666666", font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(0, 16))

    # ── Controls card ────────────────────────────────────────────────────────
    card = tk.Frame(panel, bg="white", bd=1, relief="solid")
    card.pack(fill="x", padx=20, pady=(0, 10))

    row1 = tk.Frame(card, bg="white")
    row1.pack(fill="x", padx=16, pady=(12, 6))

    tk.Label(row1, text="Mode:", bg="white", font=("Segoe UI", 10)).pack(side="left")
    _mode_var = tk.StringVar(value="sliding_window")
    mode_menu = tk.OptionMenu(row1, _mode_var, "sliding_window", "monte_carlo",
                              command=_on_mode_change)
    mode_menu.configure(font=("Segoe UI", 10), bd=1, relief="solid")
    mode_menu.pack(side="left", padx=(6, 20))

    tk.Label(row1, text="Samples (MC):", bg="white", font=("Segoe UI", 10)).pack(side="left")
    _samples_var = tk.StringVar(value="200")
    samples_entry = tk.Entry(row1, textvariable=_samples_var, width=6,
                             font=("Segoe UI", 10), bd=1, relief="solid", state="disabled")
    samples_entry.pack(side="left", padx=(6, 20))

    tk.Label(row1, text="Account size:", bg="white", font=("Segoe UI", 10)).pack(side="left")
    _size_var = tk.StringVar(value="100000")
    sizes = ["5000", "10000", "25000", "50000", "100000", "200000"]
    size_menu = tk.OptionMenu(row1, _size_var, *sizes)
    size_menu.configure(font=("Segoe UI", 10), bd=1, relief="solid")
    size_menu.pack(side="left", padx=(6, 20))

    tk.Label(row1, text="Risk %:", bg="white", font=("Segoe UI", 10)).pack(side="left")
    _risk_var = tk.StringVar(value="1.0")
    risk_menu = tk.OptionMenu(row1, _risk_var, "0.5", "1.0", "1.5", "2.0", "3.0")
    risk_menu.configure(font=("Segoe UI", 10), bd=1, relief="solid")
    risk_menu.pack(side="left", padx=(4, 12))

    tk.Label(row1, text="SL pips:", bg="white", font=("Segoe UI", 10)).pack(side="left")
    _sl_var = tk.StringVar(value="150")
    sl_entry = tk.Entry(row1, textvariable=_sl_var, width=6,
                        font=("Segoe UI", 10), bd=1, relief="solid")
    sl_entry.pack(side="left", padx=(4, 20))

    _run_btn = tk.Button(row1, text="Run Simulation",
                         bg="#2d8a4e", fg="white",
                         activebackground="#1e6b3c", activeforeground="white",
                         font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=4,
                         command=_run_simulation)
    _run_btn.pack(side="left")

    # Store entry ref for enable/disable toggling
    _mode_var._samples_entry = samples_entry

    _status_label = tk.Label(card, text="", bg="white", fg="#666666",
                              font=("Segoe UI", 9))
    _status_label.pack(anchor="w", padx=16, pady=(0, 8))

    # ── How it works explanation ─────────────────────────────────────────────
    info_card = tk.Frame(panel, bg="#f8f9fc", bd=1, relief="solid")
    info_card.pack(fill="x", padx=20, pady=(0, 12))
    tk.Label(info_card,
             text="How it works: Instead of running all trades through one challenge, "
                  "the simulator starts a fresh challenge at every date in the history. "
                  "Windows that run out of trades before hitting the profit target count as failures — "
                  "this reveals the true probability a robot would have passed if it started on that day.",
             bg="#f8f9fc", fg="#444466", font=("Segoe UI", 9),
             wraplength=820, justify="left").pack(padx=12, pady=8, anchor="w")

    # ── Results treeview ─────────────────────────────────────────────────────
    tree_frame = tk.Frame(panel, bg="#f0f2f5")
    tree_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))

    cols = ("firm", "challenge", "pass_rate", "avg_days",
            "funded_months", "per_month", "fee", "expected", "roi")
    _tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=18)

    _tree.heading("firm",          text="Firm")
    _tree.heading("challenge",     text="Challenge")
    _tree.heading("pass_rate",     text="Pass Rate")
    _tree.heading("avg_days",      text="Avg Days")
    _tree.heading("funded_months", text="Funded Months")
    _tree.heading("per_month",     text="$/Month")
    _tree.heading("fee",           text="Fee")
    _tree.heading("expected",      text="Expected $")
    _tree.heading("roi",           text="ROI")

    _tree.column("firm",          width=130, minwidth=100)
    _tree.column("challenge",     width=160, minwidth=120)
    _tree.column("pass_rate",     width=75,  minwidth=60,  anchor="center")
    _tree.column("avg_days",      width=75,  minwidth=60,  anchor="center")
    _tree.column("funded_months", width=100, minwidth=70,  anchor="center")
    _tree.column("per_month",     width=90,  minwidth=70,  anchor="center")
    _tree.column("fee",           width=60,  minwidth=50,  anchor="center")
    _tree.column("expected",      width=90,  minwidth=70,  anchor="center")
    _tree.column("roi",           width=75,  minwidth=60,  anchor="center")

    scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=_tree.yview)
    _tree.configure(yscrollcommand=scrollbar.set)
    _tree.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    _tree.tag_configure("good",   background="#EAF3DE")
    _tree.tag_configure("medium", background="#FFF8E8")
    _tree.tag_configure("poor",   background="#FCEBEB")

    return panel


def _on_mode_change(value):
    entry = getattr(_mode_var, '_samples_entry', None)
    if entry:
        entry.configure(state="normal" if value == "monte_carlo" else "disabled")


def _run_simulation():
    global _running
    if _running:
        return

    from shared.trade_history_manager import get_active_history, get_history_trades_path
    from shared.prop_firm_engine import load_all_firms

    active = get_active_history()
    if not active:
        _status_label.configure(
            text="No trade history selected. Use '+ Load trades' first.", fg="#e94560")
        return

    try:
        account_size = int(_size_var.get())
    except ValueError:
        account_size = 100000

    mode = _mode_var.get()
    try:
        num_samples = int(_samples_var.get())
    except ValueError:
        num_samples = 200

    try:
        risk_pct = float(_risk_var.get())
    except ValueError:
        risk_pct = 1.0

    try:
        sl_pips = float(_sl_var.get())
        sl_pips = max(1.0, sl_pips)
    except ValueError:
        sl_pips = 150.0

    trades_path = get_history_trades_path(active["history_id"])
    try:
        trades_df = pd.read_csv(trades_path)
    except Exception as e:
        _status_label.configure(text=f"Error loading trades: {e}", fg="#e94560")
        return

    firms = load_all_firms()

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
        _status_label.configure(text="No challenges match this account size.", fg="#e94560")
        return

    for item in _tree.get_children():
        _tree.delete(item)

    _running = True
    _run_btn.configure(state="disabled", text="Running...")
    _status_label.configure(
        text=f"Simulating {len(targets)} challenges ({mode})...", fg="#666666")

    def _worker():
        from shared.prop_firm_simulator import simulate_challenge
        results = []
        sig = inspect.signature(simulate_challenge)

        for idx, (firm_id, challenge_id, firm_name, challenge_name) in enumerate(targets):
            kwargs = dict(
                trades_df=trades_df,
                firm_id=firm_id,
                challenge_id=challenge_id,
                account_size=account_size,
                mode=mode,
                num_samples=num_samples,
                simulate_funded=True,
            )
            if "risk_per_trade_pct" in sig.parameters:
                kwargs["risk_per_trade_pct"] = risk_pct
            if "default_sl_pips" in sig.parameters:
                kwargs["default_sl_pips"] = sl_pips

            summary = simulate_challenge(**kwargs)
            if summary:
                results.append((firm_name, challenge_name, summary))

            state.window.after(0, lambda i=idx: _status_label.configure(
                text=f"Simulating {len(targets)} challenges ({mode})... {i+1}/{len(targets)}",
                fg="#666666"))

        state.window.after(0, lambda: _on_sim_done(results, len(targets), account_size))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def _on_sim_done(results, total, account_size):
    global _running
    _running = False
    _run_btn.configure(state="normal", text="Run Simulation")

    if not results:
        _status_label.configure(text="No simulation results.", fg="#e94560")
        return

    # Sort by expected net profit descending
    results.sort(
        key=lambda x: x[2].expected_net_profit if x[2].expected_net_profit is not None else -9999,
        reverse=True)

    for firm_name, challenge_name, s in results:
        pass_rate_pct = s.eval_pass_rate * 100

        if pass_rate_pct >= 60:
            tag = "good"
        elif pass_rate_pct >= 30:
            tag = "medium"
        else:
            tag = "poor"

        avg_days      = f"{s.eval_avg_days_to_pass:.0f}d" if s.eval_avg_days_to_pass else "—"
        funded_months = (f"{s.funded_avg_survival_days / 30:.1f}mo"
                         if s.funded_avg_survival_days else "—")
        per_month     = (f"${s.funded_avg_monthly_payout:,.0f}"
                         if s.funded_avg_monthly_payout else "—")
        fee           = (f"${s.challenge_fee:,.0f}" if s.challenge_fee else "—")
        expected      = (f"${s.expected_net_profit:,.0f}"
                         if s.expected_net_profit is not None else "—")
        roi           = (f"{s.expected_roi_pct:.0f}%"
                         if s.expected_roi_pct is not None else "—")

        _tree.insert("", "end", values=(
            firm_name,
            challenge_name,
            f"{pass_rate_pct:.1f}%",
            avg_days,
            funded_months,
            per_month,
            fee,
            expected,
            roi,
        ), tags=(tag,))

    lot_info = ""
    if results and hasattr(results[0][2], "calculated_lot_size"):
        first_s = results[0][2]
        lot_info = (f" | {first_s.calculated_lot_size:.2f} lots "
                    f"({first_s.risk_per_trade_pct}% risk, {first_s.default_sl_pips:.0f}-pip SL)")

    _status_label.configure(
        text=f"Done — {len(results)}/{total} challenges simulated (${account_size:,}){lot_info}",
        fg="#2d8a4e")


def refresh():
    from shared.trade_history_manager import get_active_history
    active = get_active_history()
    if _status_label and not _running:
        if active:
            _status_label.configure(
                text=f"Ready — {active['robot_name']} ({active['trade_count']} trades)",
                fg="#666666")
        else:
            _status_label.configure(
                text="No trade history selected. Use '+ Load trades' in the sidebar.",
                fg="#e94560")
