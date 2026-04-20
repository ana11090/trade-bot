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

    # WHY: The Kelly formula returns a BET FRACTION — the fraction of
    #      bankroll to put at risk of being lost ENTIRELY. For trading
    #      with a stop loss, the bet's downside is the SL distance in
    #      dollars (avg_loss), NOT the full position. So the equivalent
    #      per-trade account risk is kelly_fraction × avg_loss / account.
    #      Example: Kelly=25%, avg_loss=$200, account=$10k →
    #      account_risk = 0.25 × 200 / 10000 = 0.5% per trade.
    #      Users who read "25%" as "risk 25% per trade" would over-bet
    #      by 50×. Relabel as bet fraction and add a separate risk row.
    # CHANGED: April 2026 — distinguish bet fraction from per-trade risk
    #                       (audit bug #13)
    full_risk_pct    = (kelly_pct    / 100) * avg_loss / starting_bal * 100
    half_risk_pct    = (half_kelly   / 100) * avg_loss / starting_bal * 100
    quarter_risk_pct = (quarter_kelly/ 100) * avg_loss / starting_bal * 100

    kc = "#27ae60" if kelly_pct <= 25 else "#e67e22" if kelly_pct <= 50 else "#e94560"

    # Header for the bet-fraction block
    tk.Label(stats_frame,
             text="Bet fraction (fraction of bankroll the Kelly formula says to wager)",
             bg="#f0f2f5", fg="#555",
             font=("Segoe UI", 9, "italic")).pack(anchor="w", pady=(4, 2))

    _row("Full Kelly (bet fraction)", f"{kelly_pct:.1f}%  (${kelly_dollar:,.2f})",
         "Mathematically optimal bet fraction — NOT a per-trade risk figure.",
         vc=kc)
    _row("Half-Kelly (bet fraction)", f"{half_kelly:.1f}%  (${half_kelly_dollar:,.2f})",
         "Half of the Kelly bet fraction. Captures most growth, lower drawdown.",
         vc="#27ae60")
    _row("Quarter-Kelly (bet fraction)", f"{quarter_kelly:.1f}%  (${quarter_kelly_dollar:,.2f})",
         "Quarter Kelly bet fraction. Very conservative.",
         vc="#27ae60")

    ttk.Separator(stats_frame, orient="horizontal").pack(fill="x", pady=4)

    # WHY: This is the number the user should actually enter as their
    #      per-trade risk. Different from the bet fraction above.
    # CHANGED: April 2026 — add equivalent per-trade risk values
    tk.Label(stats_frame,
             text=f"Equivalent per-trade risk (use THIS in your risk settings — based on avg loss ${avg_loss:.2f})",
             bg="#f0f2f5", fg="#16213e",
             font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(4, 2))

    _row("Full Kelly → risk per trade",
         f"{full_risk_pct:.2f}%",
         "This is the per-trade account risk equivalent to Full Kelly.",
         vc=kc)
    _row("Half-Kelly → risk per trade  ← recommended",
         f"{half_risk_pct:.2f}%",
         "Use this number in your risk settings. Balanced growth vs volatility.",
         vc="#27ae60")
    _row("Quarter-Kelly → risk per trade",
         f"{quarter_risk_pct:.2f}%",
         "Conservative per-trade risk. Very safe, slower growth.",
         vc="#27ae60")

    ttk.Separator(stats_frame, orient="horizontal").pack(fill="x", pady=6)

    # WHY: Advice text now refers to the per-trade risk number (the
    #      thing the user should actually enter), not the bet fraction.
    # CHANGED: April 2026 — rewrite advice in terms of per-trade risk
    # Interpretation
    if kelly_pct < 10:
        advice = (f"Kelly edge is thin (full Kelly bet fraction = {kelly_pct:.1f}%). "
                  f"Recommended per-trade risk: {half_risk_pct:.2f}% (half-Kelly equivalent). "
                  f"Focus on improving win rate or avg win before increasing risk.")
    elif kelly_pct <= 25:
        advice = (f"Solid edge (full Kelly bet fraction = {kelly_pct:.1f}%). "
                  f"Recommended per-trade risk: {half_risk_pct:.2f}% (half-Kelly equivalent). "
                  f"This is the number to enter in your risk settings.")
    elif kelly_pct <= 50:
        advice = (f"Strong edge (full Kelly bet fraction = {kelly_pct:.1f}%) but full Kelly is very risky. "
                  f"Recommended per-trade risk: {half_risk_pct:.2f}% (half-Kelly) or "
                  f"{quarter_risk_pct:.2f}% (quarter-Kelly) for lower volatility.")
    else:
        advice = (f"Extremely high Kelly bet fraction ({kelly_pct:.1f}%) — often a sign of small sample size. "
                  f"Use conservative per-trade risk: {quarter_risk_pct:.2f}% (quarter-Kelly equivalent) "
                  f"until you have more trades to confirm the edge.")

    tk.Label(_result_frame, text=advice,
             bg="#fff3cd", fg="#856404", font=("Segoe UI", 9),
             wraplength=820, justify="left", padx=14, pady=10).pack(fill="x", pady=(0, 10))

    # ── Chart: Kelly fraction vs growth rate ──────────────────────────────────
    # WHY: Chart x-axis is bet fraction, not risk-per-trade. Label
    #      accordingly so users don't misread the x-values.
    # CHANGED: April 2026 — clarify x-axis is bet fraction
    tk.Label(_result_frame,
             text="Chart — Bet Fraction vs Long-Term Growth Rate",
             bg="#f0f2f5", fg="#16213e",
             font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(4, 2))
    tk.Label(_result_frame,
             text="This chart shows long-term account growth as a function of the BET FRACTION "
                  "(fraction of bankroll wagered on each trade, assuming full bet at risk). "
                  "The peak is the Full Kelly point. Going past the peak actually makes you "
                  "grow SLOWER. The x-axis shows bet fraction, NOT per-trade account risk — "
                  "see the 'Equivalent per-trade risk' rows above for the number to actually "
                  "use in your risk settings.",
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
