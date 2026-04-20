import tkinter as tk
from tkinter import ttk
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import state
import helpers

_frame        = None
_result_frame = None


def build_panel(parent):
    global _frame, _result_frame

    _frame = tk.Frame(parent, bg="#f0f2f5")

    tk.Label(_frame, text="Expected Value & Edge",
             bg="#f0f2f5", fg="#16213e",
             font=("Segoe UI", 15, "bold"), pady=16).pack(anchor="w", padx=24)

    tk.Label(_frame,
             text="Expected Value (EV) is the single most important number in trading. "
                  "It tells you the average profit or loss per trade. If EV is positive, "
                  "your strategy has a real edge — you make money on average. If it is negative "
                  "or zero, no amount of risk management can save the account long-term.",
             bg="#f0f2f5", fg="#444", font=("Segoe UI", 9),
             wraplength=820, justify="left").pack(anchor="w", padx=24, pady=(0, 10))

    tk.Button(_frame, text="Calculate",
              bg="#e94560", fg="white", font=("Segoe UI", 10, "bold"),
              bd=0, padx=16, pady=7, cursor="hand2",
              command=_on_calculate).pack(anchor="w", padx=24, pady=(0, 12))

    ttk.Separator(_frame, orient="horizontal").pack(fill="x", padx=24, pady=4)

    _result_frame = tk.Frame(_frame, bg="#f0f2f5")
    _result_frame.pack(fill="both", expand=True, padx=24, pady=8)

    return _frame


def _on_calculate():
    df = helpers.get_scaled_df()
    if df is None or "profit_scaled" not in df.columns:
        _show_message("No data loaded. Load a file in Data Pipeline first.")
        return

    pnl = df["profit_scaled"].dropna().values
    if len(pnl) < 3:
        _show_message("Not enough trades.")
        return

    try:
        starting_bal = float(state.starting_balance.get()) if state.starting_balance else 10000.0
    except (ValueError, AttributeError):
        starting_bal = 10000.0

    wins   = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    flat   = pnl[pnl == 0]

    n_total  = len(pnl)
    n_wins   = len(wins)
    n_losses = len(losses)
    n_flat   = len(flat)

    ev          = float(np.mean(pnl))
    avg_win     = float(np.mean(wins))   if n_wins   > 0 else 0.0
    avg_loss    = float(np.mean(losses)) if n_losses > 0 else 0.0
    win_rate    = n_wins / n_total * 100
    total_won   = float(np.sum(wins))
    total_lost  = abs(float(np.sum(losses)))
    profit_factor = total_won / total_lost if total_lost > 0 else float("inf")
    win_loss_ratio = avg_win / abs(avg_loss) if avg_loss != 0 else float("inf")
    expectancy_pct = ev / starting_bal * 100

    _clear_results()

    # ── Summary metrics ───────────────────────────────────────────────────────
    ev_color = "#27ae60" if ev > 0 else "#e94560"
    pf_color = "#27ae60" if profit_factor >= 1.5 else "#e67e22" if profit_factor >= 1.0 else "#e94560"

    stats_frame = tk.Frame(_result_frame, bg="#f0f2f5")
    stats_frame.pack(fill="x", pady=(0, 12))

    def _row(label, value, note, vc="#16213e"):
        r = tk.Frame(stats_frame, bg="#f0f2f5")
        r.pack(fill="x", pady=2)
        tk.Label(r, text=f"{label}:", bg="#f0f2f5", fg="#555",
                 font=("Segoe UI", 9, "bold"), width=28, anchor="w").pack(side="left")
        tk.Label(r, text=value, bg="#f0f2f5", fg=vc,
                 font=("Segoe UI", 9, "bold"), width=16, anchor="w").pack(side="left")
        tk.Label(r, text=note, bg="#f0f2f5", fg="#888",
                 font=("Segoe UI", 8), anchor="w").pack(side="left")

    _row("Expected value per trade", f"{ev:+.2f}  ({expectancy_pct:+.3f}%)",
         "Average profit/loss per trade. MUST be positive for a real edge.", vc=ev_color)
    _row("Trades analysed", f"{n_total}",
         f"Wins: {n_wins}  |  Losses: {n_losses}  |  Break-even: {n_flat}")
    _row("Win rate", f"{win_rate:.1f}%",
         f"Average win: {avg_win:+.2f}   |   Average loss: {avg_loss:+.2f}")
    _row("Win/Loss ratio", f"{win_loss_ratio:.2f}",
         "Average win divided by average loss. Above 1.0 means wins are bigger than losses.")
    _row("Profit factor", f"{profit_factor:.2f}",
         "Total money won divided by total money lost. Above 1.5 = solid. Below 1.0 = losing strategy.",
         vc=pf_color)
    _row("Total won vs total lost",
         f"{total_won:+.2f}  vs  {total_lost:.2f}",
         "Raw totals across all trades.")

    # Verdict box
    if ev > 0 and profit_factor >= 1.5:
        verdict = "POSITIVE EDGE  — EV is positive and profit factor is strong. The strategy has a real statistical edge."
        vbg, vfg = "#d4edda", "#155724"
    elif ev > 0:
        verdict = "WEAK EDGE  — EV is positive but profit factor is low. The edge exists but is fragile."
        vbg, vfg = "#fff3cd", "#856404"
    else:
        verdict = "NO EDGE  — EV is negative or zero. The strategy loses money on average. No risk management can fix this."
        vbg, vfg = "#f8d7da", "#721c24"

    tk.Label(_result_frame, text=verdict,
             bg=vbg, fg=vfg, font=("Segoe UI", 10, "bold"),
             wraplength=820, justify="left", padx=14, pady=10).pack(fill="x", pady=(0, 10))

    # ── Charts ────────────────────────────────────────────────────────────────
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 3.8))
    fig.patch.set_facecolor("#f0f2f5")

    # Trade P&L histogram — green bins for profit, red for loss
    # WHY: colors list was computed but never applied; hist() used a flat
    #      "#1a73e8" for every bar, ignoring the intent.
    # CHANGED: April 2026 — apply per-bin colors via patch.set_facecolor
    ax1.set_facecolor("#f8f9fb")
    counts_h, edges_h, patches_h = ax1.hist(pnl, bins=40, edgecolor="white", alpha=0.85)
    for patch, left_edge in zip(patches_h, edges_h[:-1]):
        patch.set_facecolor("#27ae60" if left_edge >= 0 else "#e94560")
    ax1.axvline(0, color="#e94560", linewidth=1.3, linestyle="--", label="Break-even")
    ax1.axvline(ev, color="#16213e", linewidth=1.5,
                linestyle="-", label=f"EV ({ev:+.2f})")
    ax1.set_title("Trade P&L Distribution\n"
                  "Each bar = how many trades had that profit/loss. "
                  "Right of 0 = profitable trades. Left = losing trades.",
                  fontsize=8, color="#333")
    ax1.set_xlabel("P&L per trade", fontsize=8)
    ax1.set_ylabel("Number of trades", fontsize=8)
    ax1.legend(fontsize=7)
    ax1.tick_params(labelsize=7)

    # Cumulative P&L over time (actual history)
    ax2.set_facecolor("#f8f9fb")
    cumulative = np.cumsum(pnl)
    ax2.plot(cumulative, color="#1a73e8", linewidth=1.5, label="Cumulative P&L")
    ax2.axhline(0, color="#aaa", linewidth=0.7, linestyle=":")
    ax2.fill_between(range(len(cumulative)), cumulative, 0,
                     where=(cumulative >= 0), alpha=0.15, color="#27ae60")
    ax2.fill_between(range(len(cumulative)), cumulative, 0,
                     where=(cumulative < 0), alpha=0.15, color="#e94560")
    ax2.set_title("Cumulative P&L (actual trade history)\n"
                  "Your account balance over time. Upward slope = consistent profits. "
                  "Flat or downward = problem.",
                  fontsize=8, color="#333")
    ax2.set_xlabel("Trade number", fontsize=8)
    ax2.set_ylabel("Cumulative P&L", fontsize=8)
    ax2.tick_params(labelsize=7)

    plt.tight_layout(pad=1.5)
    cv = FigureCanvasTkAgg(fig, master=_result_frame)
    cv.draw()
    cv.get_tk_widget().pack(fill="both", expand=True)
    plt.close(fig)


def _clear_results():
    for w in _result_frame.winfo_children():
        w.destroy()

def _show_message(msg):
    _clear_results()
    tk.Label(_result_frame, text=msg, bg="#f0f2f5", fg="#e94560",
             font=("Segoe UI", 11), wraplength=700).pack(pady=20)

def refresh():
    pass
