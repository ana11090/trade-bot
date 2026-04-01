"""
Compare Trade Histories — cross-robot prop firm compliance matrix.
"""
import tkinter as tk
from tkinter import ttk
import pandas as pd
import state

_tree             = None
_status_label     = None
_account_size_var = None
_summary_frame    = None
_info_label       = None
_tree_frame       = None   # module-level ref for dynamic rebuild


def build_panel(content):
    global _tree, _status_label, _account_size_var, _summary_frame, _info_label, _tree_frame

    panel = tk.Frame(content, bg="#f0f2f5")

    tk.Label(panel, text="Compare Trade Histories", bg="#f0f2f5", fg="#1a1a2a",
             font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(24, 2))

    _info_label = tk.Label(panel,
                           text="Compare multiple robots against all prop firms",
                           bg="#f0f2f5", fg="#666666", font=("Segoe UI", 10))
    _info_label.pack(anchor="w", padx=20, pady=(0, 16))

    # Controls card
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

    run_btn = tk.Button(controls, text="Run Comparison",
                        bg="#e94560", fg="white",
                        activebackground="#c73850", activeforeground="white",
                        font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=4,
                        command=_run_comparison)
    run_btn.pack(side="left")

    _status_label = tk.Label(card, text="", bg="white", fg="#666666",
                             font=("Segoe UI", 9))
    _status_label.pack(anchor="w", padx=16, pady=(0, 8))

    # Tree area — rebuilt dynamically each run
    _tree_frame = tk.Frame(panel, bg="#f0f2f5")
    _tree_frame.pack(fill="both", expand=True, padx=20, pady=(0, 10))

    # Summary cards area
    _summary_frame = tk.Frame(panel, bg="#f0f2f5")
    _summary_frame.pack(fill="x", padx=20, pady=(0, 20))

    return panel


def _run_comparison():
    global _tree
    from shared.trade_history_manager import list_trade_histories, get_history_trades_path
    from shared.prop_firm_engine import load_all_firms, check_compliance

    histories = list_trade_histories()
    if not histories:
        _status_label.configure(
            text="No trade histories loaded. Use '+ Load trades' first.", fg="#e94560")
        return

    try:
        account_size = int(_account_size_var.get())
    except ValueError:
        account_size = 100000

    _status_label.configure(text="Running comparison...", fg="#666666")
    _status_label.update_idletasks()

    firms = load_all_firms()

    # Pick one representative challenge per firm (first that matches account_size)
    challenges = []
    for firm_id, firm in sorted(firms.items(), key=lambda x: x[1].firm_name):
        matched = False
        for ch_info in firm.list_challenges():
            ch = firm.get_challenge(ch_info["challenge_id"])
            if ch and account_size in ch.get("account_sizes", []):
                challenges.append((firm_id, ch_info["challenge_id"],
                                   f"{firm.firm_name} — {ch_info['challenge_name']}"))
                matched = True
                break
        if not matched:
            for ch_info in firm.list_challenges():
                ch = firm.get_challenge(ch_info["challenge_id"])
                if ch and ch.get("account_sizes"):
                    challenges.append((firm_id, ch_info["challenge_id"],
                                       f"{firm.firm_name} — {ch_info['challenge_name']}"))
                    break

    # Clear and rebuild treeview with dynamic columns
    for w in _tree_frame.winfo_children():
        w.destroy()
    for w in _summary_frame.winfo_children():
        w.destroy()

    col_ids = ["robot"] + [f"ch_{i}" for i in range(len(challenges))]
    _tree = ttk.Treeview(_tree_frame, columns=col_ids, show="headings",
                         height=min(len(histories) + 2, 15))

    _tree.heading("robot", text="Trade History")
    _tree.column("robot", width=140, minwidth=120)
    for i, (fid, cid, label) in enumerate(challenges):
        _tree.heading(f"ch_{i}", text=label)
        _tree.column(f"ch_{i}", width=110, minwidth=80, anchor="center")

    _tree.tag_configure("pass_row", background="#f8fff8")
    _tree.tag_configure("fail_row", background="#fff8f8")

    h_scrollbar = ttk.Scrollbar(_tree_frame, orient="horizontal", command=_tree.xview)
    v_scrollbar = ttk.Scrollbar(_tree_frame, orient="vertical",   command=_tree.yview)
    _tree.configure(xscrollcommand=h_scrollbar.set, yscrollcommand=v_scrollbar.set)
    _tree.pack(side="left", fill="both", expand=True)
    v_scrollbar.pack(side="right", fill="y")

    best_robot = None
    best_count = -1

    for h in histories:
        hid = h["history_id"]
        try:
            trades_df = pd.read_csv(get_history_trades_path(hid))
        except Exception:
            continue

        row_values = [h["robot_name"]]
        pass_count = 0

        for i, (firm_id, challenge_id, _label) in enumerate(challenges):
            ch    = firms[firm_id].get_challenge(challenge_id)
            sizes = ch.get("account_sizes", []) if ch else []
            size  = account_size if account_size in sizes else (sizes[len(sizes) // 2] if sizes else account_size)
            result = check_compliance(trades_df, firm_id, challenge_id, size)
            if result and result.overall_passed:
                row_values.append("PASS")
                pass_count += 1
            else:
                row_values.append("FAIL")

        _tree.insert("", "end", values=row_values)

        if pass_count > best_count:
            best_count = pass_count
            best_robot = h["robot_name"]

    total_challenges = len(challenges)
    _status_label.configure(
        text=f"Compared {len(histories)} trade histories against {total_challenges} firms (${account_size:,})",
        fg="#2d8a4e")

    if best_robot and len(histories) > 1:
        summary_card = tk.Frame(_summary_frame, bg="white", bd=1, relief="solid")
        summary_card.pack(fill="x", pady=(0, 6))
        tk.Label(summary_card, text="Best overall", bg="white", fg="#666666",
                 font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(10, 0))
        tk.Label(summary_card,
                 text=f"{best_robot} — passes {best_count}/{total_challenges} firms",
                 bg="white", fg="#1a1a2a",
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(2, 10))


def refresh():
    from shared.trade_history_manager import list_trade_histories
    histories = list_trade_histories()
    if _info_label:
        count = len(histories)
        if count < 2:
            _info_label.configure(
                text=f"{count} trade history loaded — load at least 2 to compare. Use '+ Load trades' in the sidebar.")
        else:
            _info_label.configure(
                text=f"{count} trade histories loaded — select account size and run comparison")
