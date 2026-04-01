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
_tree          = None
_status_label  = None
_mode_var      = None
_samples_var   = None
_size_var      = None
_risk_var      = None
_sl_var        = None
_safety_var    = None
_run_btn       = None
_summary_frame = None
_running       = False


def build_panel(content):
    global _tree, _status_label, _mode_var, _samples_var
    global _size_var, _risk_var, _sl_var, _safety_var, _run_btn, _summary_frame

    panel = tk.Frame(content, bg="#f0f2f5")

    tk.Label(panel, text="Lifecycle Simulator", bg="#f0f2f5", fg="#1a1a2a",
             font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(24, 2))

    tk.Label(panel,
             text="Simulate starting a fresh challenge at every possible date to estimate real pass probability",
             bg="#f0f2f5", fg="#666666", font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(0, 12))

    # ── Controls card ────────────────────────────────────────────────────────
    card = tk.Frame(panel, bg="white", bd=1, relief="solid")
    card.pack(fill="x", padx=20, pady=(0, 10))

    row1 = tk.Frame(card, bg="white")
    row1.pack(fill="x", padx=16, pady=(12, 4))

    tk.Label(row1, text="Mode:", bg="white", font=("Segoe UI", 10)).pack(side="left")
    _mode_var = tk.StringVar(value="sliding_window")
    mode_menu = tk.OptionMenu(row1, _mode_var, "sliding_window", "monte_carlo",
                              command=_on_mode_change)
    mode_menu.configure(font=("Segoe UI", 10), bd=1, relief="solid")
    mode_menu.pack(side="left", padx=(6, 16))

    tk.Label(row1, text="Samples (MC):", bg="white", font=("Segoe UI", 10)).pack(side="left")
    _samples_var = tk.StringVar(value="200")
    samples_entry = tk.Entry(row1, textvariable=_samples_var, width=6,
                             font=("Segoe UI", 10), bd=1, relief="solid", state="disabled")
    samples_entry.pack(side="left", padx=(6, 16))

    tk.Label(row1, text="Account size:", bg="white", font=("Segoe UI", 10)).pack(side="left")
    _size_var = tk.StringVar(value="100000")
    sizes = ["5000", "10000", "25000", "50000", "100000", "200000"]
    size_menu = tk.OptionMenu(row1, _size_var, *sizes)
    size_menu.configure(font=("Segoe UI", 10), bd=1, relief="solid")
    size_menu.pack(side="left", padx=(6, 16))

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
    tk.Label(row2, text="% of daily limit", bg="white",
             font=("Segoe UI", 9), fg="#888888").pack(side="left", padx=(0, 16))

    _run_btn = tk.Button(row2, text="Run Simulation",
                         bg="#2d8a4e", fg="white",
                         activebackground="#1e6b3c", activeforeground="white",
                         font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=4,
                         command=_run_simulation)
    _run_btn.pack(side="left")

    _mode_var._samples_entry = samples_entry

    _status_label = tk.Label(card, text="", bg="white", fg="#666666",
                              font=("Segoe UI", 9))
    _status_label.pack(anchor="w", padx=16, pady=(0, 8))

    # ── How it works ─────────────────────────────────────────────────────────
    info_box = tk.Frame(panel, bg="#f8f9fc", bd=1, relief="solid")
    info_box.pack(fill="x", padx=20, pady=(0, 8))
    tk.Label(info_box,
             text="How it works: The simulator starts a fresh challenge at every possible date in your "
                  "trade history (or random samples in Monte Carlo mode). For each start date, it plays "
                  "trades forward day by day — at the lot size matching your Risk % — until the profit "
                  "target is hit (PASS) or a drawdown limit is breached (FAIL). If the daily loss "
                  "approaches the firm's limit (DD Safety %), the bot stops trading for that day. "
                  "Trades that pass continue into a funded account simulation with payouts. "
                  "Expected $ accounts for challenge fees and retry costs.",
             bg="#f8f9fc", fg="#444466", font=("Segoe UI", 9),
             wraplength=820, justify="left").pack(padx=12, pady=8, anchor="w")

    # ── Column guide ──────────────────────────────────────────────────────────
    legend_box = tk.Frame(panel, bg="white", bd=1, relief="solid")
    legend_box.pack(fill="x", padx=20, pady=(0, 8))
    tk.Label(legend_box, text="Column guide:", bg="white", fg="#1a1a2a",
             font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(8, 4))
    legends = [
        ("Pass Rate",      "% of simulated challenges that hit the profit target before any drawdown breach"),
        ("Avg Days",       "Average calendar days to pass evaluation (successful attempts only)"),
        ("Funded Months",  "Average months the funded account survived before drawdown breach"),
        ("$/Month",        "Average monthly payout during funded period (profit × firm's split %)"),
        ("Fee",            "One-time challenge fee for this account size"),
        ("Expected $",     "Expected net profit after subtracting fee × average attempts needed"),
        ("ROI",            "(Expected profit ÷ Expected cost) × 100 — return on your challenge investment"),
    ]
    for label, desc in legends:
        row = tk.Frame(legend_box, bg="white")
        row.pack(fill="x", padx=12, pady=1)
        tk.Label(row, text=f"{label}:", bg="white", fg="#1a1a2a",
                 font=("Segoe UI", 8, "bold"), width=14, anchor="w").pack(side="left")
        tk.Label(row, text=desc, bg="white", fg="#666666",
                 font=("Segoe UI", 8), anchor="w").pack(side="left", fill="x")
    tk.Frame(legend_box, bg="white", height=4).pack()

    # ── Color guide ───────────────────────────────────────────────────────────
    color_row = tk.Frame(panel, bg="#f0f2f5")
    color_row.pack(fill="x", padx=20, pady=(0, 8))
    for bg_color, fg_color, text in [
        ("#EAF3DE", "#27500A", "60%+ pass rate — good candidate"),
        ("#FFF8E8", "#633806", "30–60% pass rate — risky, may need tweaking"),
        ("#FCEBEB", "#791F1F", "Below 30% — likely unprofitable after fees"),
    ]:
        dot = tk.Frame(color_row, bg=bg_color, width=14, height=14,
                       highlightthickness=1, highlightbackground=fg_color)
        dot.pack(side="left", padx=(0, 4))
        dot.pack_propagate(False)
        tk.Label(color_row, text=text, bg="#f0f2f5", fg=fg_color,
                 font=("Segoe UI", 8)).pack(side="left", padx=(0, 16))

    # ── Methodology (collapsible) ─────────────────────────────────────────────
    method_toggle_var = [False]

    def _toggle_methodology():
        if method_toggle_var[0]:
            method_content.pack_forget()
            method_btn.configure(text="▶ Show full methodology")
            method_toggle_var[0] = False
        else:
            method_content.pack(fill="x", padx=20, pady=(0, 8))
            method_btn.configure(text="▼ Hide methodology")
            method_toggle_var[0] = True

    method_btn = tk.Button(panel, text="▶ Show full methodology",
                           bg="#f0f2f5", fg="#534AB7", bd=0,
                           font=("Segoe UI", 9, "bold"), anchor="w",
                           activebackground="#f0f2f5", activeforeground="#3C3489",
                           command=_toggle_methodology)
    method_btn.pack(anchor="w", padx=20, pady=(0, 4))

    method_content = tk.Frame(panel, bg="white", bd=1, relief="solid")
    # Starts collapsed — packed by _toggle_methodology()

    methodology_sections = [
        ("Why this simulator exists",
         "A simple pass/fail check feeds ALL your trades through a challenge sequentially. "
         "With years of profitable trading, any target gets hit eventually — so everything shows PASS. "
         "This is useless. The simulator instead asks: 'If I started this challenge on a random day, "
         "what would actually happen?' It tests hundreds of different starting points to give you "
         "a realistic probability."),
        ("How trades are rescaled (Pips-based sizing)",
         "Your robot traded different lot sizes over time (e.g. 0.07 lots in 2020, 100 lots in 2026). "
         "We can't use the raw dollar profits — a $90,000 win on 100 lots is meaningless for a $100K account. "
         "Instead, we use the PIPS column (the raw price movement the robot captured) and recalculate "
         "the dollar value based on proper position sizing for the simulated account.\n\n"
         "Formula: lot_size = (account_size × risk%) ÷ (SL_pips × pip_value_per_lot)\n"
         "Then: trade_profit = pips × pip_value_per_lot × lot_size\n\n"
         "Example ($100K account, 1% risk, 150-pip SL, XAUUSD):\n"
         "  lot_size = ($100,000 × 0.01) ÷ (150 × $1.00) = 6.67 lots\n"
         "  A +908 pip trade = 908 × $1.00 × 6.67 = $6,056 profit\n"
         "  A -150 pip trade = -150 × $1.00 × 6.67 = -$1,000 loss (exactly 1% risk)"),
        ("Daily DD safety margin",
         "Each prop firm has a daily loss limit (e.g. FTMO = 5% = $5,000 on $100K). "
         "In real life, a smart trader stops trading BEFORE hitting this limit — not AT it. "
         "The DD Safety % controls how cautious the bot is.\n\n"
         "At 80% safety: the bot stops when daily loss reaches 80% of the limit ($4,000 on FTMO).\n"
         "At 100% safety: the bot never stops early (maximum aggressive — risks hitting the actual limit).\n"
         "At 60% safety: the bot stops at just 60% of the limit ($3,000) — very conservative.\n\n"
         "The bot processes trades ONE BY ONE within each day. On winning days, all trades are taken. "
         "On losing days, it stops early when approaching the danger zone."),
        ("Drawdown types explained",
         "Static: The limit is calculated from the starting balance and never moves. "
         "If you start at $100K with 10% max DD, the floor is always $90K even if your account grows. "
         "This is more forgiving — profit creates a buffer.\n\n"
         "Trailing: The limit follows your highest balance. If your account reaches $115K, the floor "
         "moves up to $105K. Any pullback from the peak counts against you.\n\n"
         "Trailing EOD: Same as trailing, but the floor only updates at market close, not intraday."),
        ("What 'Pass Rate' really means",
         "If the simulator runs 500 windows and 350 pass, the pass rate is 70%. "
         "This means: if you start this challenge on a random day, you have a 70% chance of passing. "
         "A higher pass rate doesn't always mean 'better' — also consider how fast it passes "
         "(fewer days = less fee time) and how much you earn once funded."),
        ("How Expected $ is calculated",
         "avg_attempts = 1 ÷ pass_rate  (e.g. 70% → 1.43 attempts on average)\n"
         "expected_cost = challenge_fee × avg_attempts\n"
         "expected_income = average total payouts across all funded simulations\n"
         "expected_profit = expected_income − expected_cost\n"
         "ROI = expected_profit ÷ expected_cost × 100%"),
        ("Simulation modes",
         "Sliding Window: Starts a fresh challenge at EVERY unique trading date. Most thorough — "
         "tests every possible scenario. Slower but complete.\n\n"
         "Monte Carlo: Randomly picks N starting dates. Faster but results have some randomness. "
         "Use for quick estimates; use sliding window for final decisions."),
        ("Settings guide",
         "Risk %: How much of the account to risk per trade. 1% is standard.\n"
         "SL pips: Stop-loss distance used to calculate lot size. Adjust if your robot uses a different SL.\n"
         "DD Safety %: How cautious the bot is about the daily loss limit. 80% is recommended."),
    ]

    for title, text in methodology_sections:
        tk.Label(method_content, text=title, bg="white", fg="#1a1a2a",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16, pady=(10, 2))
        tk.Label(method_content, text=text, bg="white", fg="#555566",
                 font=("Segoe UI", 9), wraplength=800, justify="left").pack(
                     anchor="w", padx=16, pady=(0, 4))
    tk.Frame(method_content, bg="white", height=12).pack()

    # ── Summary cards (populated after run) ───────────────────────────────────
    _summary_frame = tk.Frame(panel, bg="#f0f2f5")
    _summary_frame.pack(fill="x", padx=20, pady=(0, 8))

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

    try:
        safety = float(_safety_var.get())
    except ValueError:
        safety = 80.0

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
    for w in _summary_frame.winfo_children():
        w.destroy()

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
                trades_df=trades_df, firm_id=firm_id, challenge_id=challenge_id,
                account_size=account_size, mode=mode, num_samples=num_samples,
                simulate_funded=True,
            )
            if "risk_per_trade_pct" in sig.parameters:
                kwargs["risk_per_trade_pct"] = risk_pct
            if "default_sl_pips" in sig.parameters:
                kwargs["default_sl_pips"] = sl_pips
            if "daily_dd_safety_pct" in sig.parameters:
                kwargs["daily_dd_safety_pct"] = safety

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

    # ── Summary cards ────────────────────────────────────────────────────────
    for w in _summary_frame.winfo_children():
        w.destroy()

    cards = tk.Frame(_summary_frame, bg="#f0f2f5")
    cards.pack(fill="x")

    best_profit = max(results, key=lambda x: x[2].expected_net_profit or -999)
    best_pass   = max(results, key=lambda x: x[2].eval_pass_rate)
    good_count  = sum(1 for _, _, s in results if s.eval_pass_rate >= 0.6)

    def _card(parent, title, value, sub="", fg="#1a1a2a"):
        c = tk.Frame(parent, bg="white", bd=1, relief="solid")
        c.pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Label(c, text=title, bg="white", fg="#888888",
                 font=("Segoe UI", 8)).pack(anchor="w", padx=10, pady=(6, 0))
        tk.Label(c, text=value, bg="white", fg=fg,
                 font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=10)
        if sub:
            tk.Label(c, text=sub, bg="white", fg="#888888",
                     font=("Segoe UI", 8)).pack(anchor="w", padx=10, pady=(0, 6))
        else:
            tk.Frame(c, bg="white", height=6).pack()

    _card(cards, "Best opportunity", best_profit[0],
          f"Expected: ${best_profit[2].expected_net_profit or 0:,.0f}", "#2d8a4e")
    _card(cards, "Highest pass rate",
          f"{best_pass[2].eval_pass_rate * 100:.0f}%",
          f"{best_pass[0]} — {best_pass[1]}")
    _card(cards, "Good candidates", f"{good_count} / {len(results)}", "60%+ pass rate")

    # ── Results table ────────────────────────────────────────────────────────
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
            firm_name, challenge_name,
            f"{pass_rate_pct:.1f}%", avg_days, funded_months,
            per_month, fee, expected, roi,
        ), tags=(tag,))

    lot_info = ""
    if results and hasattr(results[0][2], "calculated_lot_size"):
        first_s = results[0][2]
        safety_str = (f", {first_s.daily_dd_safety_pct:.0f}% DD safety"
                      if hasattr(first_s, "daily_dd_safety_pct") else "")
        lot_info = (f" | {first_s.calculated_lot_size:.2f} lots "
                    f"({first_s.risk_per_trade_pct}% risk, "
                    f"{first_s.default_sl_pips:.0f}-pip SL{safety_str})")

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
