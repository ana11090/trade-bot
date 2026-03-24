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

    tk.Label(_frame, text="Kelly Criterion — Position Sizing",
             bg="#f0f2f5", fg="#16213e",
             font=("Segoe UI", 15, "bold"), pady=16).pack(anchor="w", padx=24)

    tk.Label(_frame,
             text="The Kelly Criterion is a mathematical formula that tells you the optimal "
                  "percentage of your account to risk on each trade to maximise long-term growth. "
                  "Risk too little and you grow slowly. Risk too much and you risk blowing the account. "
                  "In practice, traders use Half-Kelly (50% of the Kelly value) to reduce volatility "
                  "while still growing efficiently.",
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

    if len(wins) == 0 or len(losses) == 0:
        _show_message("Need at least one win and one loss to calculate Kelly.")
        return

    win_rate = len(wins) / len(pnl)
    avg_win  = float(np.mean(wins))
    avg_loss = abs(float(np.mean(losses)))
    b        = avg_win / avg_loss      # win/loss ratio
    p        = win_rate
    q        = 1 - p

    kelly_pct = (p * b - q) / b * 100  # Kelly formula: f = (p*b - q) / b
    half_kelly    = kelly_pct / 2
    quarter_kelly = kelly_pct / 4

    kelly_dollar       = starting_bal * kelly_pct / 100
    half_kelly_dollar  = starting_bal * half_kelly / 100
    quarter_kelly_dollar = starting_bal * quarter_kelly / 100

    _clear_results()

    stats_frame = tk.Frame(_result_frame, bg="#f0f2f5")
    stats_frame.pack(fill="x", pady=(0, 10))

    def _row(label, value, note, vc="#16213e"):
        r = tk.Frame(stats_frame, bg="#f0f2f5")
        r.pack(fill="x", pady=2)
        tk.Label(r, text=f"{label}:", bg="#f0f2f5", fg="#555",
                 font=("Segoe UI", 9, "bold"), width=28, anchor="w").pack(side="left")
        tk.Label(r, text=value, bg="#f0f2f5", fg=vc,
                 font=("Segoe UI", 9, "bold"), width=22, anchor="w").pack(side="left")
        tk.Label(r, text=note, bg="#f0f2f5", fg="#888",
                 font=("Segoe UI", 8), anchor="w").pack(side="left")

    _row("Win rate", f"{win_rate*100:.1f}%", f"Avg win: {avg_win:+.2f}   |   Avg loss: {avg_loss:.2f}")
    _row("Win/Loss ratio (b)", f"{b:.2f}", "Average win divided by average loss")

    ttk.Separator(stats_frame, orient="horizontal").pack(fill="x", pady=6)

    if kelly_pct <= 0:
        tk.Label(stats_frame,
                 text="Kelly is zero or negative — the strategy has no positive edge. "
                      "Do not trade this strategy.",
                 bg="#f8d7da", fg="#721c24",
                 font=("Segoe UI", 10, "bold"),
                 padx=14, pady=10).pack(fill="x")
        return

    kc = "#27ae60" if kelly_pct <= 25 else "#e67e22" if kelly_pct <= 50 else "#e94560"
    _row("Full Kelly", f"{kelly_pct:.1f}%  (${kelly_dollar:,.2f})",
         "Mathematically optimal but very volatile. NOT recommended to use in full.",
         vc=kc)
    _row("Half-Kelly  ← recommended", f"{half_kelly:.1f}%  (${half_kelly_dollar:,.2f})",
         "Half of Kelly. Captures most of the growth with much lower drawdown risk.",
         vc="#27ae60")
    _row("Quarter-Kelly  ← conservative", f"{quarter_kelly:.1f}%  (${quarter_kelly_dollar:,.2f})",
         "Very safe. Slower growth but minimal risk of ruin.",
         vc="#27ae60")

    ttk.Separator(stats_frame, orient="horizontal").pack(fill="x", pady=6)

    # Interpretation
    if kelly_pct < 10:
        advice = (f"Full Kelly is only {kelly_pct:.1f}% — the edge exists but is thin. "
                  f"Use Half-Kelly ({half_kelly:.1f}%) and focus on improving win rate or avg win.")
    elif kelly_pct <= 25:
        advice = (f"Full Kelly is {kelly_pct:.1f}% — a solid edge. "
                  f"Half-Kelly ({half_kelly:.1f}%) is a good practical choice.")
    elif kelly_pct <= 50:
        advice = (f"Full Kelly is {kelly_pct:.1f}% — a strong edge but full Kelly is very risky at this level. "
                  f"Use Half-Kelly ({half_kelly:.1f}%) or Quarter-Kelly ({quarter_kelly:.1f}%).")
    else:
        advice = (f"Full Kelly is {kelly_pct:.1f}% — extremely high, which often signals a small sample size. "
                  f"Use Quarter-Kelly ({quarter_kelly:.1f}%) until you have more trades to confirm the edge.")

    tk.Label(_result_frame, text=advice,
             bg="#fff3cd", fg="#856404", font=("Segoe UI", 9),
             wraplength=820, justify="left", padx=14, pady=10).pack(fill="x", pady=(0, 10))

    # ── Chart: Kelly fraction vs growth rate ──────────────────────────────────
    tk.Label(_result_frame,
             text="Chart — Risk % vs Long-Term Growth Rate",
             bg="#f0f2f5", fg="#16213e",
             font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(4, 2))
    tk.Label(_result_frame,
             text="This chart shows what happens to long-term account growth as you change "
                  "how much you risk per trade. The peak of the curve is the Full Kelly point. "
                  "Going past the peak actually makes you grow SLOWER (or lose money). "
                  "The recommended zone is around Half-Kelly.",
             bg="#f0f2f5", fg="#666", font=("Segoe UI", 8),
             wraplength=820).pack(anchor="w", pady=(0, 6))

    fig, ax = plt.subplots(figsize=(10, 3.5))
    fig.patch.set_facecolor("#f0f2f5")
    ax.set_facecolor("#f8f9fb")

    fractions = np.linspace(0, min(1.0, kelly_pct/100 * 2.5), 300)
    # Log-growth per trade: G(f) = p*log(1 + f*b) + q*log(1 - f)
    with np.errstate(divide="ignore", invalid="ignore"):
        growth = p * np.log(1 + fractions * b) + q * np.log(1 - fractions)
    growth = np.where(fractions >= 1.0, np.nan, growth)

    ax.plot(fractions * 100, growth, color="#1a73e8", linewidth=2)
    ax.axvline(kelly_pct,      color="#e94560", linewidth=1.3, linestyle="--",
               label=f"Full Kelly ({kelly_pct:.1f}%)")
    ax.axvline(half_kelly,     color="#27ae60", linewidth=1.5, linestyle="-",
               label=f"Half-Kelly ({half_kelly:.1f}%)  ← recommended")
    ax.axvline(quarter_kelly,  color="#2ecc71", linewidth=1.0, linestyle=":",
               label=f"Quarter-Kelly ({quarter_kelly:.1f}%)")
    ax.axhline(0, color="#888", linewidth=0.7, linestyle=":")
    ax.set_xlabel("Risk per trade (% of account)", fontsize=8)
    ax.set_ylabel("Log-growth per trade", fontsize=8)
    ax.set_title("Kelly Growth Curve — Risk % vs Long-Term Growth\n"
                 "Above zero = growing. Below zero = shrinking long-term.",
                 fontsize=8, color="#333")
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
