"""
Prop Compliance Panel v2 — uses the prop firm engine to check all firms at once.
Replaces the old single-firm compliance panel.
"""
import tkinter as tk
from tkinter import ttk
import pandas as pd

# Module-level refs
_tree             = None
_status_label     = None
_account_size_var = None
_info_label       = None


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
    tree_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))

    cols = ("firm", "challenge", "result", "profit", "max_dd", "days", "reason")
    _tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=20)

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

    return panel


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
