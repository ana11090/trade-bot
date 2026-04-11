import tkinter as tk
from tkinter import ttk
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import state
import helpers

# ── Module-level refs set in build_panel() ────────────────────────────────────
pip_value_var    = None
_p8_labels       = {}
p8_breakeven_lbl = None
p8_tree          = None
p8_fig           = None
p8_ax            = None
p8_canvas        = None


# ─────────────────────────────────────────────────────────────────────────────
# CHART BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_cost_charts():
    df = helpers.get_scaled_df()
    if df is not None:
        df = df.sort_values("open_dt").reset_index(drop=True)
    p8_ax.clear()
    p8_ax.set_facecolor("#fafafa")

    if df is None:
        p8_ax.text(0.5, 0.5, "No data — run the pipeline first",
                   ha="center", va="center", transform=p8_ax.transAxes, color="#aaaaaa")
        p8_canvas.draw()
        return

    # WHY: Old scale dict had Micro: 0.1 which is WRONG. Micro accounts
    #      trade smaller lots, but broker P&L is already reported in
    #      account currency — no scaling needed. The 0.1 made Micro
    #      account commission/swap values look 10× smaller than reality.
    #      Same bug Phase 12 fixed in helpers.py get_scaled_df(). That
    #      fix covered profit_scaled (via get_scaled_df above) but this
    #      local dict was missed — it only applies to Commission/Swap.
    # CHANGED: April 2026 — fix Micro scale (audit MED — Family #6)
    scale   = {"Standard": 1.0, "Cent": 0.01, "Micro": 1.0}.get(state.account_type.get(), 1.0)
    profits = df["profit_scaled"].fillna(0)

    has_comm  = "Commission" in df.columns
    swap_col  = next((c for c in ["Swap", "Interest"] if c in df.columns), None)
    comm_vals = (pd.to_numeric(df["Commission"], errors="coerce").fillna(0) * scale
                 if has_comm else pd.Series([0.0] * len(df)))
    swap_vals = (pd.to_numeric(df[swap_col], errors="coerce").fillna(0) * scale
                 if swap_col else pd.Series([0.0] * len(df)))

    gross      = profits.sum()
    comm_total = comm_vals.sum()
    swap_total = swap_vals.sum()
    net        = gross + comm_total + swap_total
    ecn_mode   = has_comm and comm_total != 0

    try:
        deposit = float(state.starting_balance.get())
    except ValueError:
        deposit = 0.0

    _p8_labels["gross_profit"].configure(text=f"{gross:+.2f} USD",
                                          fg="#27ae60" if gross >= 0 else "#e94560")
    _p8_labels["total_commission"].configure(
        text=f"{comm_total:.2f} USD" if ecn_mode else "0.00  (spread account)",
        fg="#e94560" if comm_total < 0 else "#1a1a2a")
    _p8_labels["total_swap"].configure(
        text=f"{swap_total:.2f} USD" if swap_col else "N/A — no Swap column",
        fg="#e94560" if swap_total < 0 else "#1a1a2a")
    _p8_labels["net_profit"].configure(text=f"{net:+.2f} USD",
                                        fg="#27ae60" if net >= 0 else "#e94560")
    # WHY: Some broker export formats (e.g. MT4 with ECN commission) already
    #      subtract commission from the Profit column before export. Adding the
    #      Commission column on top would double-count. Standard exports also
    #      vary — some include slippage in Profit, others report it as a
    #      separate column. The user must verify their broker's convention
    #      before trusting the cost breakdown.
    # CHANGED: April 2026 — unconditional cost-convention warning + ECN
    #          double-count warning made more prominent (audit LOW, Phase 21)
    if ecn_mode:
        note_text = ("ECN — full breakdown available.  ⚠ VERIFY: If your broker "
                     "already deducted commission from the Profit column on "
                     "export, this panel double-counts. Check broker docs.")
    else:
        note_text = ("Standard spread — costs hidden in price.  ⚠ VERIFY: "
                     "Some 'Standard' exports include commission in Profit, "
                     "others list it separately. Check broker docs.")
    _p8_labels["account_note"].configure(
        text=note_text,
        fg="#f39c12")  # always amber/warning color, never green

    try:
        pip_val = float(pip_value_var.get())
    except ValueError:
        pip_val = 10.0
    avg_lots     = pd.to_numeric(df["Lots"], errors="coerce").mean() if "Lots" in df.columns else 1.0
    n_trades     = len(df)
    cost_per_pip = pip_val * avg_lots

    be = (-net / (cost_per_pip * n_trades)) if cost_per_pip * n_trades != 0 else 0
    be_color = "#27ae60" if be > 1.0 else "#e94560"
    p8_breakeven_lbl.configure(
        text=f"Break-even extra spread: {be:.2f} pips   (avg {avg_lots:.2f} lots across {n_trades} trades)",
        fg=be_color)

    for row in p8_tree.get_children():
        p8_tree.delete(row)

    sc_labels, sc_profits = [], []
    extra_pips_list = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0]
    for extra in extra_pips_list:
        total_extra = extra * cost_per_pip * n_trades
        adj_net     = net - total_extra
        adj_pct     = (adj_net / deposit * 100) if deposit != 0 else 0
        sc_labels.append(f"+{extra:.1f}")
        sc_profits.append(adj_net)
        p8_tree.insert("", "end", values=(
            f"+{extra:.1f}", f"{extra * cost_per_pip:.2f}",
            f"{total_extra:.2f}", f"{adj_net:+.2f}", f"{adj_pct:+.2f}%"
        ))

    colors_b = ["#27ae60" if v >= 0 else "#e94560" for v in sc_profits]
    p8_ax.bar(sc_labels, sc_profits, color=colors_b, zorder=2)
    p8_ax.axhline(0, color="#888888", linewidth=0.8)
    p8_ax.set_xlabel("Extra spread added (pips)", fontsize=9)
    p8_ax.set_ylabel("Net profit (USD)", fontsize=9)
    p8_ax.set_title("Net Profit vs Extra Spread", fontsize=10)
    p8_ax.grid(axis="y", alpha=0.2, zorder=0)
    p8_ax.tick_params(labelsize=8)

    _p8_labels_list  = list(sc_labels)
    _p8_profits_list = list(sc_profits)
    _p8_dep          = deposit
    _p8_annot        = helpers._make_annot(p8_ax)
    def on_p8_hover(event, _ax=p8_ax, _ann=_p8_annot,
                    _labs=_p8_labels_list, _pros=_p8_profits_list,
                    _dep=_p8_dep, _extras=extra_pips_list):
        if event.inaxes != _ax:
            _ann.set_visible(False)
            p8_canvas.draw_idle()
            return
        vis = False
        for idx, bar in enumerate(_ax.patches):
            if bar.contains(event)[0]:
                val  = _pros[idx] if idx < len(_pros) else 0
                ep   = _extras[idx] if idx < len(_extras) else 0
                pct  = (val / _dep * 100) if _dep else 0
                sign = "+" if val >= 0 else ""
                _ann.xy = (bar.get_x() + bar.get_width() / 2, val)
                _ann.set_text(f"+{ep:.1f} pips extra spread\nNet profit: {sign}{val:.2f} USD\n{sign}{pct:.2f}% of deposit")
                _ann.set_visible(True)
                vis = True
                break
        if not vis:
            _ann.set_visible(False)
        p8_canvas.draw_idle()
    helpers._reconnect(p8_canvas, "motion_notify_event", on_p8_hover)

    p8_fig.tight_layout(pad=1.2)
    p8_canvas.draw()


# ─────────────────────────────────────────────────────────────────────────────
# PANEL BUILD
# ─────────────────────────────────────────────────────────────────────────────

def build_panel(content):
    global pip_value_var, p8_breakeven_lbl, p8_tree, p8_fig, p8_ax, p8_canvas

    frame = tk.Frame(content, bg="#f0f2f5")
    tk.Label(frame, text="Cost & Spread", bg="#f0f2f5", fg="#1a1a2a",
             font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(24, 2))
    tk.Label(frame,
             text="Check whether the bot's edge survives real-world costs on a different broker or prop firm.",
             bg="#f0f2f5", fg="#666666", font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(0, 16))

    # ── Cost Breakdown card ────────────────────────────────────────────────────
    p8_cost_card = tk.Frame(frame, bg="white", bd=1, relief="solid")
    p8_cost_card.pack(fill="x", padx=20, pady=(0, 10))
    tk.Label(p8_cost_card, text="Cost Breakdown", bg="white", fg="#1a1a2a",
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
    tk.Label(p8_cost_card,
             text="Myfxbook separates Profit (raw price movement), Commission (ECN fee) and Swap/Interest "
                  "(overnight charge) as distinct columns. Net = Profit + Commission + Swap. "
                  "If Commission is zero, this is a standard spread account — broker costs are hidden inside the price "
                  "and cannot be separated from the edge.",
             bg="white", fg="#888888", font=("Segoe UI", 9),
             wraplength=600, justify="left").pack(anchor="w", padx=16, pady=(0, 8))
    p8_cost_grid = tk.Frame(p8_cost_card, bg="white")
    p8_cost_grid.pack(fill="x", padx=16, pady=(0, 16))
    for key, title, row, col in [
        ("gross_profit",     "Gross Profit (price movement)", 0, 0),
        ("total_commission", "Total Commission",              0, 1),
        ("total_swap",       "Total Swap / Interest",         0, 2),
        ("net_profit",       "Net Profit",                    1, 0),
        ("account_note",     "Account Type",                  1, 1),
    ]:
        cell = tk.Frame(p8_cost_grid, bg="white")
        cell.grid(row=row, column=col, padx=16, pady=8, sticky="w")
        tk.Label(cell, text=title, bg="white", fg="#888888",
                 font=("Segoe UI", 8)).pack(anchor="w")
        lbl = tk.Label(cell, text="—", bg="white", fg="#1a1a2a",
                       font=("Segoe UI", 13, "bold"))
        lbl.pack(anchor="w")
        _p8_labels[key] = lbl

    # ── Spread Survivability card ──────────────────────────────────────────────
    p8_spread_card = tk.Frame(frame, bg="white", bd=1, relief="solid")
    p8_spread_card.pack(fill="x", padx=20, pady=(0, 20))
    tk.Label(p8_spread_card, text="Spread Survivability", bg="white", fg="#1a1a2a",
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
    tk.Label(p8_spread_card,
             text="How does net profit change if the prop firm's spread is wider than the original broker? "
                  "The break-even spread is the maximum extra pips the bot can absorb before turning unprofitable. "
                  "For XAUUSD the default pip value is $10 per pip per 1.0 lot — adjust if your account uses different sizing.",
             bg="white", fg="#888888", font=("Segoe UI", 9),
             wraplength=600, justify="left").pack(anchor="w", padx=16, pady=(0, 8))

    p8_pip_row = tk.Frame(p8_spread_card, bg="white")
    p8_pip_row.pack(anchor="w", padx=16, pady=(0, 10))
    tk.Label(p8_pip_row, text="Pip value (USD per pip per 1.0 lot):",
             bg="white", font=("Segoe UI", 10)).pack(side="left")
    pip_value_var = tk.StringVar(value="10")
    tk.Entry(p8_pip_row, textvariable=pip_value_var, width=7, font=("Segoe UI", 10),
             bd=1, relief="solid").pack(side="left", padx=(6, 2))
    tk.Button(p8_pip_row, text="Recalculate", font=("Segoe UI", 10, "bold"),
              bg="#e94560", fg="white", bd=0, padx=14, pady=6,
              activebackground="#e94560", activeforeground="white",
              command=lambda: refresh()).pack(side="left", padx=(16, 0))

    p8_breakeven_lbl = tk.Label(p8_spread_card, text="Break-even extra spread: —",
                                  bg="white", fg="#1a1a2a", font=("Segoe UI", 12, "bold"))
    p8_breakeven_lbl.pack(anchor="w", padx=16, pady=(4, 8))

    p8_tree_frame = tk.Frame(p8_spread_card, bg="white")
    p8_tree_frame.pack(fill="x", padx=16, pady=(0, 10))
    p8_tree = ttk.Treeview(p8_tree_frame, show="headings", height=6)
    _P8_COLS = ["Extra Spread (pips)", "Cost/Trade (USD)", "Total Extra Cost",
                "Adjusted Net Profit", "Adjusted Return %"]
    p8_tree["columns"] = _P8_COLS
    for c in _P8_COLS:
        p8_tree.heading(c, text=c)
        p8_tree.column(c, width=128, anchor="center")
    p8_tree.pack(fill="x")

    p8_fig = Figure(figsize=(7, 2.6), dpi=90)
    p8_fig.patch.set_facecolor("white")
    p8_ax  = p8_fig.add_subplot(111)
    p8_canvas = FigureCanvasTkAgg(p8_fig, master=p8_spread_card)
    p8_canvas.get_tk_widget().pack(fill="x", padx=16, pady=(8, 14))

    return frame


# ─────────────────────────────────────────────────────────────────────────────
# REFRESH
# ─────────────────────────────────────────────────────────────────────────────

def refresh():
    build_cost_charts()
