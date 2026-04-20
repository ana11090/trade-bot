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

    tk.Label(_frame, text="Drawdown & Recovery Analysis",
             bg="#f0f2f5", fg="#16213e",
             font=("Segoe UI", 15, "bold"), pady=16).pack(anchor="w", padx=24)

    tk.Label(_frame,
             text="Drawdown is the drop from a peak equity high to a subsequent low. "
                  "It measures pain — how much you lost from your best point before recovering. "
                  "Recovery time tells you how long it takes to get back to that previous high. "
                  "Understanding your historical drawdowns helps you set realistic expectations "
                  "and decide whether the strategy's risk is acceptable.",
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

    cumulative   = np.cumsum(pnl)
    running_peak = np.maximum.accumulate(cumulative)
    drawdown_abs = cumulative - running_peak   # always <= 0
    drawdown_pct = drawdown_abs / starting_bal * 100

    # Max drawdown
    max_dd_abs = float(np.min(drawdown_abs))
    max_dd_pct = max_dd_abs / starting_bal * 100
    max_dd_idx = int(np.argmin(drawdown_abs))

    # Average drawdown (only when in drawdown)
    in_dd      = drawdown_abs[drawdown_abs < 0]
    avg_dd_abs = float(np.mean(in_dd)) if len(in_dd) > 0 else 0.0
    avg_dd_pct = avg_dd_abs / starting_bal * 100

    # Time spent in drawdown (% of trades)
    pct_in_dd = len(in_dd) / len(pnl) * 100

    # Recovery analysis: find all drawdown episodes
    episodes = _find_dd_episodes(drawdown_abs)

    recovery_trades_list = [ep["recovery"] for ep in episodes if ep["recovery"] is not None]
    unrecovered          = [ep for ep in episodes if ep["recovery"] is None]

    avg_recovery = float(np.mean(recovery_trades_list)) if recovery_trades_list else None
    max_recovery = int(max(recovery_trades_list))        if recovery_trades_list else None

    # Trades per day for time conversion
    # WHY: Old code divided by calendar span which overcounts weekends
    #      and holidays that the strategy never traded. Weekday-only
    #      strategies get trades_per_day ~30% too low, making all
    #      downstream recovery time estimates (avg_days, max_days) ~30%
    #      too long. Fix: count UNIQUE trading days instead. Same fix
    #      Phase 12 applied to account_survival.py and account_forecast.py.
    # CHANGED: April 2026 — trading-day rate (audit MED — Family #2)
    df_t = df.dropna(subset=["open_dt"]).sort_values("open_dt")
    trades_per_day = 1.0
    if len(df_t) >= 2:
        unique_trading_days = int(df_t["open_dt"].dt.floor("D").nunique())
        trades_per_day      = len(df_t) / max(unique_trading_days, 1)

    _clear_results()

    stats_frame = tk.Frame(_result_frame, bg="#f0f2f5")
    stats_frame.pack(fill="x", pady=(0, 10))

    def _row(label, value, note, vc="#16213e"):
        r = tk.Frame(stats_frame, bg="#f0f2f5")
        r.pack(fill="x", pady=2)
        tk.Label(r, text=f"{label}:", bg="#f0f2f5", fg="#555",
                 font=("Segoe UI", 9, "bold"), width=30, anchor="w").pack(side="left")
        tk.Label(r, text=value, bg="#f0f2f5", fg=vc,
                 font=("Segoe UI", 9, "bold"), width=22, anchor="w").pack(side="left")
        tk.Label(r, text=note, bg="#f0f2f5", fg="#888",
                 font=("Segoe UI", 8), anchor="w").pack(side="left")

    _row("Maximum drawdown", f"{max_dd_abs:+.2f}  ({max_dd_pct:.1f}%)",
         f"The worst peak-to-trough drop. Occurred at trade #{max_dd_idx}.",
         vc="#e94560")
    _row("Average drawdown", f"{avg_dd_abs:+.2f}  ({avg_dd_pct:.1f}%)",
         "Average depth when the account is in a drawdown.")
    _row("Time spent in drawdown", f"{pct_in_dd:.1f}% of trades",
         "How often the account was below its previous high.")
    _row("Drawdown episodes found", f"{len(episodes)}",
         f"Recovered: {len(recovery_trades_list)}   |   Still unrecovered: {len(unrecovered)}")

    if avg_recovery is not None:
        avg_days = avg_recovery / trades_per_day
        max_days = max_recovery / trades_per_day
        _row("Avg recovery time", f"{avg_recovery:.0f} trades  (~{avg_days:.0f} days)",
             "Average trades needed to return to previous equity high.")
        _row("Longest recovery", f"{max_recovery} trades  (~{max_days:.0f} days)",
             "The longest it took to recover from a drawdown.")
    else:
        # WHY: Old code said "No completed recoveries found" which gives no
        #      info about ongoing recovery progress. When drawdowns haven't
        #      recovered yet, the user needs to know how close they are —
        #      e.g., "75% recovered from trough" is way better than "still at
        #      trough" for the same unrecovered status. The best partial
        #      recovery shows how much the strategy has clawed back.
        # CHANGED: April 2026 — Phase 24 Fix 4 — show progress for unrecovered
        #          episodes (audit Part B #15 — HIGH severity)
        if len(unrecovered) == 0:
            # No episodes at all → pristine equity curve, never in drawdown
            _row("Recovery", "No drawdown episodes",
                 "The strategy never dropped below its previous equity peak.",
                 vc="#27ae60")
        else:
            # At least one unrecovered episode → compute how far we've climbed
            # back from the trough. progress = (latest_val - trough_val) / drop_size * 100
            # where drop_size = peak_val - trough_val. latest_val = cumulative[last_index].
            best_progress = -float("inf")
            for ep in unrecovered:
                trough_idx = ep["trough"]
                trough_val = cumulative[trough_idx]
                # The peak is running_peak at the trough index
                peak_val   = running_peak[trough_idx]
                drop_size  = peak_val - trough_val  # always > 0 for a real drawdown
                # Latest value is the final cumulative P&L
                latest_val = cumulative[-1]
                if drop_size > 0:
                    progress = (latest_val - trough_val) / drop_size * 100
                    best_progress = max(best_progress, progress)

            if best_progress == -float("inf") or best_progress <= 0:
                # Still at trough or worse
                _row("Recovery", "No completed recoveries — still at trough",
                     "The account is currently in a drawdown and has not climbed back.",
                     vc="#e94560")
            elif best_progress < 100:
                # Partial recovery
                _row("Recovery", f"No completed recoveries — {best_progress:.1f}% recovered from trough",
                     "The account is climbing back from the drawdown but hasn't reached the previous peak yet.",
                     vc="#f39c12")  # amber for partial progress
            else:
                # This shouldn't happen because if progress >= 100, the episode should be completed
                # but handle it gracefully
                _row("Recovery", "No completed recoveries (edge case — fully recovered but not detected)",
                     "The account appears to have recovered but episode is marked unrecovered.",
                     vc="#f39c12")

    # ── Charts ────────────────────────────────────────────────────────────────
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7))
    fig.patch.set_facecolor("#f0f2f5")

    x = np.arange(len(pnl))

    # Equity curve
    ax1.set_facecolor("#f8f9fb")
    ax1.plot(x, cumulative, color="#1a73e8", linewidth=1.5, label="Cumulative P&L")
    ax1.plot(x, running_peak, color="#27ae60", linewidth=1.0, linestyle="--",
             alpha=0.6, label="Running peak")
    ax1.fill_between(x, cumulative, running_peak, alpha=0.2, color="#e94560",
                     label="Drawdown area")
    ax1.axhline(0, color="#aaa", linewidth=0.7, linestyle=":")
    ax1.set_title("Equity Curve with Drawdown Areas\n"
                  "Blue = account balance. Green dashed = highest point reached. "
                  "Red fill = currently in a drawdown (below previous peak).",
                  fontsize=8, color="#333")
    ax1.set_ylabel("Cumulative P&L", fontsize=8)
    ax1.legend(fontsize=7)
    ax1.tick_params(labelsize=7)
    ax1.set_xticklabels([])

    # Drawdown over time
    ax2.set_facecolor("#f8f9fb")
    ax2.fill_between(x, drawdown_pct, 0, alpha=0.6, color="#e94560")
    ax2.plot(x, drawdown_pct, color="#c0392b", linewidth=1.0)
    ax2.axhline(max_dd_pct, color="#16213e", linewidth=1.0, linestyle="--",
                label=f"Max drawdown ({max_dd_pct:.1f}%)")
    ax2.axhline(avg_dd_pct, color="#e67e22", linewidth=1.0, linestyle=":",
                label=f"Avg drawdown ({avg_dd_pct:.1f}%)")
    ax2.set_title("Drawdown Over Time (%)\n"
                  "How far below the peak the account is at each trade. "
                  "Deeper = worse. Ideal = mostly near zero with short, shallow dips.",
                  fontsize=8, color="#333")
    ax2.set_xlabel("Trade number", fontsize=8)
    ax2.set_ylabel("Drawdown (%)", fontsize=8)
    ax2.legend(fontsize=7)
    ax2.tick_params(labelsize=7)

    plt.tight_layout(pad=1.5)
    cv = FigureCanvasTkAgg(fig, master=_result_frame)
    cv.draw()
    cv.get_tk_widget().pack(fill="both", expand=True)
    plt.close(fig)


def _find_dd_episodes(drawdown_abs):
    """Find discrete drawdown episodes and their recovery lengths."""
    episodes = []
    in_dd    = False
    start    = None
    trough   = None
    trough_v = 0.0

    for i, v in enumerate(drawdown_abs):
        if not in_dd and v < 0:
            in_dd    = True
            start    = i
            trough   = i
            trough_v = v
        elif in_dd:
            if v < trough_v:
                trough   = i
                trough_v = v
            if v >= 0:
                # WHY: i - start measured recovery from drawdown start, not from
                #      the trough. The useful number is how long it took to climb
                #      back from the worst point.
                # CHANGED: April 2026 — measure recovery from trough
                episodes.append({"start": start, "trough": trough,
                                  "depth": trough_v, "recovery": i - trough})
                in_dd = False

    if in_dd:
        episodes.append({"start": start, "trough": trough,
                          "depth": trough_v, "recovery": None})
    return episodes


def _clear_results():
    for w in _result_frame.winfo_children():
        w.destroy()

def _show_message(msg):
    _clear_results()
    tk.Label(_result_frame, text=msg, bg="#f0f2f5", fg="#e94560",
             font=("Segoe UI", 11), wraplength=700).pack(pady=20)

def refresh():
    pass
