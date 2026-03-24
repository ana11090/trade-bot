import tkinter as tk
from tkinter import ttk
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import helpers

_frame        = None
_result_frame = None


def build_panel(parent):
    global _frame, _result_frame

    _frame = tk.Frame(parent, bg="#f0f2f5")

    tk.Label(_frame, text="Streak Analysis — Consecutive Losses & Wins",
             bg="#f0f2f5", fg="#16213e",
             font=("Segoe UI", 15, "bold"), pady=16).pack(anchor="w", padx=24)

    tk.Label(_frame,
             text="Even a profitable strategy will have losing streaks. This panel shows "
                  "the mathematical probability of hitting N losses in a row, based on your "
                  "actual win rate. It also shows the actual streak history from your trade data "
                  "so you can see what has really happened versus what to expect in the future.",
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
    if len(pnl) < 5:
        _show_message("Not enough trades.")
        return

    wins   = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    n      = len(pnl)
    wr     = len(wins) / n
    lr     = 1 - wr

    # ── Streak extraction ─────────────────────────────────────────────────────
    loss_streaks = []
    win_streaks  = []
    streak = 0
    kind   = None

    for v in pnl:
        outcome = "W" if v > 0 else "L" if v < 0 else None
        if outcome == kind:
            streak += 1
        else:
            if streak > 0:
                (win_streaks if kind == "W" else loss_streaks).append(streak)
            streak = 1
            kind   = outcome
    if streak > 0 and kind is not None:
        (win_streaks if kind == "W" else loss_streaks).append(streak)

    max_loss_streak = max(loss_streaks) if loss_streaks else 0
    max_win_streak  = max(win_streaks)  if win_streaks  else 0
    avg_loss_streak = float(np.mean(loss_streaks)) if loss_streaks else 0
    avg_win_streak  = float(np.mean(win_streaks))  if win_streaks  else 0

    # Expected longest streak in N trades: E[max] ≈ log(N) / log(1/lr) - 1
    if lr > 0 and lr < 1:
        expected_max_loss = np.log(n) / np.log(1 / lr) if lr > 0 else 0
    else:
        expected_max_loss = 0

    # Probability of N consecutive losses
    streak_probs = {k: lr**k * 100 for k in [2, 3, 4, 5, 6, 7, 8, 10]}

    _clear_results()

    # ── Summary ───────────────────────────────────────────────────────────────
    stats_frame = tk.Frame(_result_frame, bg="#f0f2f5")
    stats_frame.pack(fill="x", pady=(0, 10))

    def _row(label, value, note, vc="#16213e"):
        r = tk.Frame(stats_frame, bg="#f0f2f5")
        r.pack(fill="x", pady=2)
        tk.Label(r, text=f"{label}:", bg="#f0f2f5", fg="#555",
                 font=("Segoe UI", 9, "bold"), width=30, anchor="w").pack(side="left")
        tk.Label(r, text=value, bg="#f0f2f5", fg=vc,
                 font=("Segoe UI", 9, "bold"), width=16, anchor="w").pack(side="left")
        tk.Label(r, text=note, bg="#f0f2f5", fg="#888",
                 font=("Segoe UI", 8), anchor="w").pack(side="left")

    _row("Win rate / Loss rate", f"{wr*100:.1f}% / {lr*100:.1f}%",
         f"{len(wins)} wins, {len(losses)} losses out of {n} trades")
    _row("Longest actual loss streak", f"{max_loss_streak} in a row",
         "The worst consecutive losing run in your real history.",
         vc="#e94560" if max_loss_streak >= 5 else "#e67e22")
    _row("Longest actual win streak",  f"{max_win_streak} in a row",
         "The best consecutive winning run in your real history.", vc="#27ae60")
    _row("Average loss streak length", f"{avg_loss_streak:.1f}",
         "How long losing streaks typically last.")
    _row("Expected max streak (in this sample)", f"~{expected_max_loss:.1f}",
         "Mathematically expected longest losing streak for this number of trades at your win rate.")

    ttk.Separator(stats_frame, orient="horizontal").pack(fill="x", pady=6)

    # ── Probability table ─────────────────────────────────────────────────────
    tk.Label(_result_frame,
             text="Probability of N consecutive losses",
             bg="#f0f2f5", fg="#16213e",
             font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(4, 2))
    tk.Label(_result_frame,
             text="This is the probability that any given sequence of N trades is all losses. "
                  "This does NOT mean it happens once — in 500 trades it may happen several times.",
             bg="#f0f2f5", fg="#666", font=("Segoe UI", 8),
             wraplength=820).pack(anchor="w", pady=(0, 6))

    tbl = tk.Frame(_result_frame, bg="#f0f2f5")
    tbl.pack(fill="x", pady=(0, 12))

    headers = ["Streak length", "Probability", "Meaning", "Actual occurrences"]
    for col, h in enumerate(headers):
        tk.Label(tbl, text=h, bg="#dde3ed", fg="#333",
                 font=("Segoe UI", 8, "bold"), anchor="center",
                 padx=8, pady=4).grid(row=0, column=col, sticky="ew", padx=1, pady=1)
    tbl.columnconfigure(2, weight=1)

    for i, (k, prob) in enumerate(streak_probs.items(), start=1):
        actual = sum(1 for s in loss_streaks if s >= k)
        bg = "#d4edda" if prob < 1 else "#fff3cd" if prob < 10 else "#f8d7da"
        meaning = (f"1 in {1/prob*100:.0f}" if prob > 0.1 else "< 1 in 1000") + " sequences"
        vals = [f"{k} losses in a row", f"{prob:.3f}%", meaning, f"{actual} times in data"]
        for col, val in enumerate(vals):
            tk.Label(tbl, text=val, bg=bg, fg="#333",
                     font=("Segoe UI", 8), anchor="center" if col != 2 else "w",
                     padx=6, pady=3).grid(row=i, column=col, sticky="ew", padx=1, pady=1)

    # ── Charts ────────────────────────────────────────────────────────────────
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 3.8))
    fig.patch.set_facecolor("#f0f2f5")

    # Loss streak distribution
    ax1.set_facecolor("#f8f9fb")
    if loss_streaks:
        max_s = max(loss_streaks)
        counts = [loss_streaks.count(i) for i in range(1, max_s + 1)]
        ax1.bar(range(1, max_s + 1), counts, color="#e94560", alpha=0.8, edgecolor="white")
    ax1.set_title("Loss Streak Distribution (actual data)\n"
                  "How many times each streak length actually occurred in your trade history.",
                  fontsize=8, color="#333")
    ax1.set_xlabel("Consecutive losses", fontsize=8)
    ax1.set_ylabel("Times occurred", fontsize=8)
    ax1.tick_params(labelsize=7)

    # Cumulative P&L coloured by win/loss outcome
    ax2.set_facecolor("#f8f9fb")
    cumulative = np.cumsum(pnl)
    ax2.plot(cumulative, color="#888", linewidth=0.8, alpha=0.5)
    # Highlight loss streaks in red
    streak_start = None
    for i, v in enumerate(pnl):
        if v < 0:
            if streak_start is None:
                streak_start = i
        else:
            if streak_start is not None:
                ax2.axvspan(streak_start, i, alpha=0.15, color="#e94560")
                streak_start = None
    if streak_start is not None:
        ax2.axvspan(streak_start, len(pnl), alpha=0.15, color="#e94560")
    ax2.set_title("Cumulative P&L with Loss Streaks Highlighted\n"
                  "Red zones = losing streaks. Shows where drawdowns occurred.",
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
