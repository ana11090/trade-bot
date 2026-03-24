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

    tk.Label(_frame, text="Break-even Analysis",
             bg="#f0f2f5", fg="#16213e",
             font=("Segoe UI", 15, "bold"), pady=16).pack(anchor="w", padx=24)

    tk.Label(_frame,
             text="Break-even analysis tells you the minimum win rate your strategy needs "
                  "to not lose money, given your average win and loss sizes. It also shows "
                  "you exactly how much margin you have before the strategy stops working — "
                  "and what happens if your performance degrades slightly.",
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

    wins   = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    if len(wins) == 0 or len(losses) == 0:
        _show_message("Need at least one win and one loss to calculate break-even.")
        return

    avg_win  = float(np.mean(wins))
    avg_loss = abs(float(np.mean(losses)))
    win_rate = len(wins) / len(pnl) * 100

    # Break-even win rate: W * avg_win = (1-W) * avg_loss → W = avg_loss / (avg_win + avg_loss)
    be_wr = avg_loss / (avg_win + avg_loss) * 100
    margin = win_rate - be_wr  # positive = safe, negative = already losing

    _clear_results()

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

    _row("Your current win rate", f"{win_rate:.1f}%",
         f"Average win: {avg_win:+.2f}   |   Average loss: {avg_loss:.2f}")
    _row("Break-even win rate", f"{be_wr:.1f}%",
         "The minimum win rate for zero net profit.",
         vc="#e67e22")
    mc = "#27ae60" if margin >= 5 else "#e67e22" if margin >= 0 else "#e94560"
    _row("Safety margin", f"{margin:+.1f}%",
         "How far your win rate is above break-even. Positive = safe. Negative = losing money.",
         vc=mc)

    if margin >= 5:
        verdict = f"SAFE  — Your win rate is {margin:.1f}% above break-even. Your win rate can drop by up to {margin:.1f}% before you start losing money."
        vbg, vfg = "#d4edda", "#155724"
    elif margin >= 0:
        verdict = f"MARGINAL  — Only {margin:.1f}% above break-even. A small drop in win rate or increase in losses will push you into negative territory."
        vbg, vfg = "#fff3cd", "#856404"
    else:
        verdict = f"LOSING  — You are {abs(margin):.1f}% below break-even. The strategy is currently losing money on average."
        vbg, vfg = "#f8d7da", "#721c24"

    tk.Label(_result_frame, text=verdict,
             bg=vbg, fg=vfg, font=("Segoe UI", 10, "bold"),
             wraplength=820, justify="left", padx=14, pady=10).pack(fill="x", pady=(0, 10))

    # ── Sensitivity table ─────────────────────────────────────────────────────
    tk.Label(_result_frame,
             text="Sensitivity Analysis — What if your win rate changes?",
             bg="#f0f2f5", fg="#16213e",
             font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(4, 2))
    tk.Label(_result_frame,
             text="This table shows the expected P&L per trade at different win rates, "
                  "keeping your average win and loss sizes fixed.",
             bg="#f0f2f5", fg="#666", font=("Segoe UI", 8),
             wraplength=820).pack(anchor="w", pady=(0, 6))

    tbl = tk.Frame(_result_frame, bg="#f0f2f5")
    tbl.pack(fill="x", pady=(0, 10))

    headers = ["Win Rate", "EV per trade", "vs current"]
    for col, h in enumerate(headers):
        tk.Label(tbl, text=h, bg="#dde3ed", fg="#333",
                 font=("Segoe UI", 8, "bold"),
                 width=16, anchor="center", relief="flat",
                 padx=6, pady=4).grid(row=0, column=col, sticky="ew", padx=1, pady=1)

    test_wrs = [max(0, win_rate - 20), max(0, win_rate - 10), max(0, win_rate - 5),
                win_rate, min(100, win_rate + 5), min(100, win_rate + 10)]
    for i, wr in enumerate(test_wrs, start=1):
        w = wr / 100
        ev = w * avg_win - (1 - w) * avg_loss
        delta = ev - (win_rate/100 * avg_win - (1 - win_rate/100) * avg_loss)
        bg = "#d4edda" if ev > 0 else "#f8d7da"
        lbl = "← current" if abs(wr - win_rate) < 0.01 else ""
        for col, val in enumerate([f"{wr:.1f}%", f"{ev:+.2f}", f"{delta:+.2f}  {lbl}"]):
            tk.Label(tbl, text=val, bg=bg, fg="#333",
                     font=("Segoe UI", 8),
                     width=16, anchor="center",
                     padx=6, pady=3).grid(row=i, column=col, sticky="ew", padx=1, pady=1)

    # ── Chart: win rate vs EV ─────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(10, 3.2))
    fig.patch.set_facecolor("#f0f2f5")
    ax.set_facecolor("#f8f9fb")

    wrs  = np.linspace(0, 100, 200)
    evs  = (wrs/100) * avg_win - (1 - wrs/100) * avg_loss
    ax.plot(wrs, evs, color="#1a73e8", linewidth=2)
    ax.fill_between(wrs, evs, 0, where=(evs >= 0), alpha=0.15, color="#27ae60")
    ax.fill_between(wrs, evs, 0, where=(evs < 0),  alpha=0.15, color="#e94560")
    ax.axvline(win_rate, color="#16213e", linewidth=1.5, linestyle="-",
               label=f"Your win rate ({win_rate:.1f}%)")
    ax.axvline(be_wr, color="#e67e22", linewidth=1.3, linestyle="--",
               label=f"Break-even ({be_wr:.1f}%)")
    ax.axhline(0, color="#888", linewidth=0.7, linestyle=":")
    ax.set_title("Win Rate vs Expected Value per Trade\n"
                 "Above the horizontal line = profitable. Orange line = the minimum win rate needed.",
                 fontsize=8, color="#333")
    ax.set_xlabel("Win rate (%)", fontsize=8)
    ax.set_ylabel("EV per trade", fontsize=8)
    ax.legend(fontsize=7)
    ax.tick_params(labelsize=7)

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
