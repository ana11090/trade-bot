"""
Compare Trade Histories — cross-robot prop firm pass-rate matrix.
Uses the lifecycle simulator (Monte Carlo, 50 samples) to show pass rates.
"""
import tkinter as tk
from tkinter import ttk
import threading
import inspect
import pandas as pd
import state

_tree             = None
_status_label     = None
_account_size_var = None
_risk_var         = None
_sl_var           = None
_safety_var       = None
_summary_frame    = None
_info_label       = None
_tree_frame       = None
_run_btn          = None
_running          = False


def build_panel(content):
    global _tree, _status_label, _account_size_var, _risk_var, _sl_var
    global _safety_var, _summary_frame, _info_label, _tree_frame, _run_btn

    panel = tk.Frame(content, bg="#f0f2f5")

    tk.Label(panel, text="Compare Trade Histories", bg="#f0f2f5", fg="#1a1a2a",
             font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(24, 2))

    _info_label = tk.Label(panel,
                           text="Compare multiple robots against all prop firms",
                           bg="#f0f2f5", fg="#666666", font=("Segoe UI", 10))
    _info_label.pack(anchor="w", padx=20, pady=(0, 12))

    # Controls card
    card = tk.Frame(panel, bg="white", bd=1, relief="solid")
    card.pack(fill="x", padx=20, pady=(0, 10))

    row1 = tk.Frame(card, bg="white")
    row1.pack(fill="x", padx=16, pady=(12, 4))

    tk.Label(row1, text="Account size:", bg="white",
             font=("Segoe UI", 10)).pack(side="left")
    _account_size_var = tk.StringVar(value="100000")
    sizes = ["5000", "10000", "25000", "50000", "100000", "200000"]
    size_menu = tk.OptionMenu(row1, _account_size_var, *sizes)
    size_menu.configure(font=("Segoe UI", 10), bd=1, relief="solid")
    size_menu.pack(side="left", padx=(6, 16))

    _run_btn = tk.Button(row1, text="Run Comparison",
                         bg="#e94560", fg="white",
                         activebackground="#c73850", activeforeground="white",
                         font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=4,
                         command=_run_comparison)
    _run_btn.pack(side="left")

    row2 = tk.Frame(card, bg="white")
    row2.pack(fill="x", padx=16, pady=(0, 4))

    tk.Label(row2, text="Risk %:", bg="white", font=("Segoe UI", 10)).pack(side="left")
    _risk_var = tk.StringVar(value="1.0")
    risk_menu = tk.OptionMenu(row2, _risk_var, "0.5", "1.0", "1.5", "2.0", "3.0")
    risk_menu.configure(font=("Segoe UI", 10), bd=1, relief="solid")
    risk_menu.pack(side="left", padx=(4, 16))

    tk.Label(row2, text="SL pips:", bg="white", font=("Segoe UI", 10)).pack(side="left")
    _sl_var = tk.StringVar(value="150")
    sl_entry = tk.Entry(row2, textvariable=_sl_var, width=6,
                        font=("Segoe UI", 10), bd=1, relief="solid")
    sl_entry.pack(side="left", padx=(4, 16))

    tk.Label(row2, text="DD Safety:", bg="white", font=("Segoe UI", 10)).pack(side="left")
    _safety_var = tk.StringVar(value="80")
    safety_menu = tk.OptionMenu(row2, _safety_var, "60", "70", "80", "90", "100")
    safety_menu.configure(font=("Segoe UI", 10), bd=1, relief="solid")
    safety_menu.pack(side="left", padx=(4, 4))
    tk.Label(row2, text="% of daily limit  (50 Monte Carlo samples per challenge)",
             bg="white", font=("Segoe UI", 9), fg="#888888").pack(side="left", padx=(4, 0))

    _status_label = tk.Label(card, text="", bg="white", fg="#666666",
                             font=("Segoe UI", 9))
    _status_label.pack(anchor="w", padx=16, pady=(0, 8))

    # Methodology note
    note_box = tk.Frame(panel, bg="#f8f9fc", bd=1, relief="solid")
    note_box.pack(fill="x", padx=20, pady=(0, 10))
    tk.Label(note_box,
             text="This panel runs a Monte Carlo simulation (50 samples per combination) for each loaded "
                  "trade history against each prop firm. Cell values show the PASS RATE — the probability "
                  "of passing the challenge if started on a random day. Trades are rescaled using the Pips "
                  "column at your chosen Risk % and SL. For detailed methodology, see the "
                  "'Show full methodology' section in the Lifecycle Simulator panel.",
             bg="#f8f9fc", fg="#444466", font=("Segoe UI", 9),
             wraplength=820, justify="left").pack(padx=12, pady=8)

    # Tree area — rebuilt dynamically each run
    _tree_frame = tk.Frame(panel, bg="#f0f2f5")
    _tree_frame.pack(fill="both", expand=True, padx=20, pady=(0, 10))

    # Summary cards area
    _summary_frame = tk.Frame(panel, bg="#f0f2f5")
    _summary_frame.pack(fill="x", padx=20, pady=(0, 20))

    return panel


def _run_comparison():
    global _running
    if _running:
        return

    from shared.trade_history_manager import list_trade_histories, get_history_trades_path
    from shared.prop_firm_engine import load_all_firms

    histories = list_trade_histories()
    if not histories:
        _status_label.configure(
            text="No trade histories loaded. Use '+ Load trades' first.", fg="#e94560")
        return

    try:
        account_size = int(_account_size_var.get())
    except ValueError:
        account_size = 100000

    try:
        risk_pct = float(_risk_var.get())
    except ValueError:
        risk_pct = 1.0

    try:
        sl_pips = float(_sl_var.get())
        sl_pips = max(1.0, sl_pips)
    except ValueError:
        sl_pips = 150.0

    try:
        safety = float(_safety_var.get())
    except ValueError:
        safety = 80.0

    firms = load_all_firms()

    # One representative challenge per firm (first that fits account_size)
    challenges = []
    for firm_id, firm in sorted(firms.items(), key=lambda x: x[1].firm_name):
        for ch_info in firm.list_challenges():
            ch = firm.get_challenge(ch_info["challenge_id"])
            if ch and account_size in ch.get("account_sizes", []):
                challenges.append((firm_id, ch_info["challenge_id"], firm.firm_name))
                break

    if not challenges:
        _status_label.configure(
            text="No challenges match this account size.", fg="#e94560")
        return

    # Clear old UI
    for w in _tree_frame.winfo_children():
        w.destroy()
    for w in _summary_frame.winfo_children():
        w.destroy()

    _running = True
    _run_btn.configure(state="disabled", text="Running...")
    total_combos = len(histories) * len(challenges)
    _status_label.configure(
        text=f"Simulating {total_combos} combinations...", fg="#666666")

    def _worker():
        from shared.prop_firm_simulator import simulate_challenge
        sig = inspect.signature(simulate_challenge)

        # {history_id: (robot_name, [(firm_name, pass_rate), ...])}
        results = {}
        combo_idx = 0

        for h in histories:
            hid = h["history_id"]
            try:
                trades_df = pd.read_csv(get_history_trades_path(hid))
            except Exception:
                continue

            history_rates = []
            for firm_id, challenge_id, firm_name in challenges:
                combo_idx += 1
                state.window.after(0, lambda i=combo_idx: _status_label.configure(
                    text=f"Simulating... {i}/{total_combos}", fg="#666666"))

                kwargs = dict(
                    trades_df=trades_df, firm_id=firm_id,
                    challenge_id=challenge_id, account_size=account_size,
                    mode="monte_carlo", num_samples=50, simulate_funded=False,
                )
                if "risk_per_trade_pct"  in sig.parameters: kwargs["risk_per_trade_pct"]  = risk_pct
                if "default_sl_pips"     in sig.parameters: kwargs["default_sl_pips"]     = sl_pips
                if "daily_dd_safety_pct" in sig.parameters: kwargs["daily_dd_safety_pct"] = safety

                result = simulate_challenge(**kwargs)
                rate = result.eval_pass_rate * 100 if result else 0.0
                history_rates.append((firm_name, rate))

            results[hid] = (h["robot_name"], history_rates)

        state.window.after(0, lambda: _on_compare_done(
            results, challenges, account_size, len(histories)))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def _on_compare_done(results, challenges, account_size, num_histories):
    global _running, _tree
    _running = False
    _run_btn.configure(state="normal", text="Run Comparison")

    if not results:
        _status_label.configure(text="No results.", fg="#e94560")
        return

    # Build treeview with dynamic firm columns
    col_ids = ["robot"] + [f"f_{i}" for i in range(len(challenges))]
    _tree = ttk.Treeview(_tree_frame, columns=col_ids, show="headings",
                         height=min(len(results) + 2, 15))

    _tree.heading("robot", text="Trade History")
    _tree.column("robot", width=150, minwidth=120)
    for i, (_, _, firm_name) in enumerate(challenges):
        _tree.heading(f"f_{i}", text=firm_name)
        _tree.column(f"f_{i}", width=100, minwidth=80, anchor="center")

    _tree.tag_configure("good",   background="#EAF3DE")
    _tree.tag_configure("medium", background="#FFF8E8")
    _tree.tag_configure("poor",   background="#FCEBEB")

    h_scrollbar = ttk.Scrollbar(_tree_frame, orient="horizontal", command=_tree.xview)
    v_scrollbar = ttk.Scrollbar(_tree_frame, orient="vertical",   command=_tree.yview)
    _tree.configure(xscrollcommand=h_scrollbar.set, yscrollcommand=v_scrollbar.set)
    _tree.pack(side="left", fill="both", expand=True)
    v_scrollbar.pack(side="right", fill="y")

    best_robot = None
    best_avg   = -1.0

    for hid, (robot_name, history_rates) in results.items():
        row_values = [robot_name]
        rates = []
        for firm_name, rate in history_rates:
            row_values.append(f"{rate:.0f}%")
            rates.append(rate)

        avg = sum(rates) / len(rates) if rates else 0.0
        tag = "good" if avg >= 60 else "medium" if avg >= 30 else "poor"
        _tree.insert("", "end", values=row_values, tags=(tag,))

        if avg > best_avg:
            best_avg   = avg
            best_robot = robot_name

    # Summary
    if best_robot and num_histories > 1:
        summary_card = tk.Frame(_summary_frame, bg="white", bd=1, relief="solid")
        summary_card.pack(fill="x", pady=(0, 6))
        tk.Label(summary_card, text="Best overall", bg="white", fg="#666666",
                 font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(10, 0))
        tk.Label(summary_card,
                 text=f"{best_robot} — avg pass rate {best_avg:.0f}% across {len(challenges)} firms",
                 bg="white", fg="#1a1a2a",
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(2, 10))
    elif best_robot:
        summary_card = tk.Frame(_summary_frame, bg="white", bd=1, relief="solid")
        summary_card.pack(fill="x", pady=(0, 6))
        tk.Label(summary_card, text="Analysis complete", bg="white", fg="#666666",
                 font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(10, 0))
        tk.Label(summary_card,
                 text=f"{best_robot} — avg pass rate {best_avg:.0f}% across {len(challenges)} firms",
                 bg="white", fg="#1a1a2a",
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(2, 10))

    _status_label.configure(
        text=f"Done — {len(results)} histories vs {len(challenges)} firms (${account_size:,})",
        fg="#2d8a4e")


def refresh():
    from shared.trade_history_manager import list_trade_histories
    histories = list_trade_histories()
    n = len(histories)
    if n == 0:
        msg = "Load trade histories with '+ Load trades' to start comparing"
    elif n == 1:
        msg = "1 trade history loaded — run analysis or load more to compare side by side"
    else:
        msg = f"{n} trade histories loaded — compare their pass rates across prop firms"
    if _info_label:
        _info_label.configure(text=msg)
