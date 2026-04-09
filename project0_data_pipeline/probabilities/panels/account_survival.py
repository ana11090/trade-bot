import tkinter as tk
from tkinter import ttk
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import state
import helpers

_frame        = None
_result_frame = None
_horizon_var  = None
_dd_var       = None
_sims_var     = None
_target_var   = None
_conf_var     = None
_last_sim     = None


def build_panel(parent):
    global _frame, _result_frame, _horizon_var, _dd_var, _sims_var, _target_var, _conf_var

    _frame = tk.Frame(parent, bg="#f0f2f5")

    tk.Label(_frame, text="Account Survival & Profit Forecast",
             bg="#f0f2f5", fg="#16213e",
             font=("Segoe UI", 15, "bold"), pady=16).pack(anchor="w", padx=24)

    tk.Label(_frame,
             text="Runs your real trade history thousands of times in random order (Monte Carlo) "
                  "to estimate how likely your account is to survive, how much profit you could "
                  "realistically make, and how deep drawdowns could get — within a time window you choose.",
             bg="#f0f2f5", fg="#444", font=("Segoe UI", 9),
             wraplength=820, justify="left").pack(anchor="w", padx=24, pady=(0, 10))

    # ── Section A ─────────────────────────────────────────────────────────────
    _section_label(_frame, "Section A — Survival Probability & Drawdown Forecast")
    tk.Label(_frame,
             text="Set your time window, the maximum loss you can accept (e.g. prop firm limit), "
                  "and the number of simulation runs. More simulations = more accurate, but slower.",
             bg="#f0f2f5", fg="#666", font=("Segoe UI", 9),
             wraplength=820, justify="left").pack(anchor="w", padx=24, pady=(0, 4))

    inputs_a = tk.Frame(_frame, bg="#f0f2f5")
    inputs_a.pack(fill="x", padx=24)

    _horizon_var = tk.StringVar(value="30")
    _dd_var      = tk.StringVar(value="10")
    _sims_var    = tk.StringVar(value="1000")

    row1 = tk.Frame(inputs_a, bg="#f0f2f5")
    row1.pack(fill="x", pady=4)
    _input_pair(row1, "Time horizon (days):", _horizon_var, 6)
    _input_pair(row1, "Max drawdown allowed (%):", _dd_var, 6)
    _input_pair(row1, "Simulations:", _sims_var, 8)

    tk.Button(inputs_a, text="Run Simulation",
              bg="#e94560", fg="white", font=("Segoe UI", 10, "bold"),
              bd=0, padx=16, pady=7, cursor="hand2",
              command=_on_run_sim).pack(anchor="w", pady=(6, 12))

    ttk.Separator(_frame, orient="horizontal").pack(fill="x", padx=24, pady=4)

    # ── Section B ─────────────────────────────────────────────────────────────
    _section_label(_frame, "Section B — How long to reach a profit target?")
    tk.Label(_frame,
             text="After running Section A, enter a profit target (% of starting balance) and "
                  "what % of simulations must reach it. Example: 'I want 10% profit — in how "
                  "many days will 80% of scenarios achieve that?'",
             bg="#f0f2f5", fg="#666", font=("Segoe UI", 9),
             wraplength=820, justify="left").pack(anchor="w", padx=24, pady=(0, 4))

    inputs_b = tk.Frame(_frame, bg="#f0f2f5")
    inputs_b.pack(fill="x", padx=24)

    _target_var = tk.StringVar(value="10")
    _conf_var   = tk.StringVar(value="50")

    row2 = tk.Frame(inputs_b, bg="#f0f2f5")
    row2.pack(fill="x", pady=4)
    _input_pair(row2, "Target profit (%):", _target_var, 6)
    _input_pair(row2, "% of scenarios that must reach it:", _conf_var, 6)

    tk.Button(inputs_b, text="Calculate",
              bg="#1a73e8", fg="white", font=("Segoe UI", 10, "bold"),
              bd=0, padx=16, pady=7, cursor="hand2",
              command=_on_calc_target).pack(anchor="w", pady=(6, 12))

    ttk.Separator(_frame, orient="horizontal").pack(fill="x", padx=24, pady=4)

    _result_frame = tk.Frame(_frame, bg="#f0f2f5")
    _result_frame.pack(fill="both", expand=True, padx=24, pady=8)

    return _frame


def _on_run_sim():
    global _last_sim

    df = helpers.get_scaled_df()
    if df is None or "profit_scaled" not in df.columns:
        _show_message("No data loaded. Load a file in Data Pipeline first.")
        return

    try:
        horizon_days = int(_horizon_var.get())
        max_dd_pct   = float(_dd_var.get()) / 100.0
        n_sims       = int(_sims_var.get())
        if horizon_days <= 0 or max_dd_pct <= 0 or n_sims <= 0:
            raise ValueError
    except ValueError:
        _show_message("Invalid inputs. Enter positive numbers for all fields.")
        return

    trade_pnl = df["profit_scaled"].dropna().values
    if len(trade_pnl) < 2:
        _show_message("Not enough trades to run a simulation.")
        return

    df_t = df.dropna(subset=["open_dt"]).sort_values("open_dt")
    if len(df_t) >= 2:
        # WHY: Calendar span over-counts the denominator — weekends + holidays have
        #      no trades so dividing by calendar days gives ~30% too-low rate for
        #      weekday-only strategies.  Unique trading days (dates with ≥1 trade)
        #      gives the real average.
        # CHANGED: April 2026 — unique trading days (audit HIGH — Family #2)
        unique_trading_days = int(df_t["open_dt"].dt.floor("D").nunique())
        trades_per_day = len(df_t) / max(unique_trading_days, 1)
    else:
        trades_per_day = 1.0

    n_trades = max(1, int(trades_per_day * horizon_days))

    try:
        starting_bal = float(state.starting_balance.get()) if state.starting_balance else 10000.0
    except (ValueError, AttributeError):
        starting_bal = 10000.0

    ruin_threshold = -starting_bal * max_dd_pct
    rng            = np.random.default_rng()
    n_hist         = len(trade_pnl)
    block_len      = max(5, int(np.sqrt(n_hist)))
    all_paths      = []
    all_drawdowns  = []
    final_pnls     = []
    survived       = 0

    def _sample_block_path(n_needed):
        # WHY: IID resampling (rng.choice) destroys serial dependence — losing
        #      streaks cluster in real trading, so IID gives optimistic survival
        #      rates. Moving-block bootstrap preserves local autocorrelation.
        # CHANGED: April 2026 — block bootstrap (audit HIGH)
        out = np.empty(n_needed, dtype=trade_pnl.dtype)
        pos = 0
        while pos < n_needed:
            start = int(rng.integers(0, n_hist))
            take  = min(block_len, n_needed - pos, n_hist - start)
            out[pos:pos + take] = trade_pnl[start:start + take]
            pos += take
        return out

    for _ in range(n_sims):
        sampled      = _sample_block_path(n_trades)
        cumulative   = np.cumsum(sampled)
        running_peak = np.maximum.accumulate(cumulative)
        drawdown     = cumulative - running_peak
        # WHY: Old code checked `cumulative < ruin_threshold` — absolute loss
        #      from start. Real account ruin = DD from peak exceeds limit.
        #      A profitable path that retraces -10% is NOT ruined by a $0
        #      reference; it IS ruined if the -10% DD hits the DD limit.
        # CHANGED: April 2026 — use drawdown for ruin check
        ruined = bool(np.any(drawdown < ruin_threshold))
        all_paths.append(cumulative)
        all_drawdowns.append(drawdown)
        if not ruined:
            survived += 1
            final_pnls.append(float(cumulative[-1]))

    all_paths     = np.array(all_paths)
    all_drawdowns = np.array(all_drawdowns)
    survival_pct  = survived / n_sims * 100
    day_axis      = np.arange(n_trades) / trades_per_day

    _last_sim = dict(all_paths=all_paths, all_drawdowns=all_drawdowns,
                     final_pnls=final_pnls, trades_per_day=trades_per_day,
                     starting_bal=starting_bal, trade_pnl=trade_pnl,
                     n_trades=n_trades, horizon_days=horizon_days,
                     max_dd_pct=max_dd_pct, ruin_threshold=ruin_threshold, n_sims=n_sims)

    _clear_results()

    if final_pnls:
        median_pnl = float(np.median(final_pnls))
        p10_pnl    = float(np.percentile(final_pnls, 10))
        p90_pnl    = float(np.percentile(final_pnls, 90))
        median_pct = median_pnl / starting_bal * 100
        pct_profit = sum(1 for v in final_pnls if v > 0) / len(final_pnls) * 100
        median_dd  = float(np.median(np.min(all_drawdowns, axis=1)))
        median_dd_pct = median_dd / starting_bal * 100
    else:
        median_pnl = p10_pnl = p90_pnl = median_pct = pct_profit = median_dd = median_dd_pct = 0.0

    lines = [
        f"  Survival probability ({horizon_days} days, max {max_dd_pct*100:.0f}% drawdown):   {survival_pct:.1f}%",
        f"  Trades per run: {n_trades}   ({trades_per_day:.1f} trades/day from history)",
        f"  Median P&L (survivors): {median_pnl:+.2f}  ({median_pct:+.1f}%)   |   10th–90th pct: {p10_pnl:+.2f} → {p90_pnl:+.2f}",
        f"  Survivors ending in profit: {pct_profit:.1f}%   |   Typical deepest drawdown: {median_dd:+.2f}  ({median_dd_pct:+.1f}%)",
    ]
    tk.Label(_result_frame, text="\n".join(lines),
             bg="#e8f0fe", fg="#16213e", font=("Segoe UI", 9),
             justify="left", padx=14, pady=10).pack(fill="x", pady=(0, 8))

    x = np.arange(n_trades)
    fig1, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 3.8))
    fig1.patch.set_facecolor("#f0f2f5")

    # Fan chart
    ax1.set_facecolor("#f8f9fb")
    p10p = np.percentile(all_paths, 10, axis=0)
    p25p = np.percentile(all_paths, 25, axis=0)
    p50p = np.percentile(all_paths, 50, axis=0)
    p75p = np.percentile(all_paths, 75, axis=0)
    p90p = np.percentile(all_paths, 90, axis=0)
    ax1.fill_between(x, p10p, p90p, alpha=0.12, color="#1a73e8", label="10–90th pct (80% of runs)")
    ax1.fill_between(x, p25p, p75p, alpha=0.22, color="#1a73e8", label="25–75th pct (50% of runs)")
    ax1.plot(x, p50p, color="#1a73e8", linewidth=2, label="Median path")
    # WHY: Ruin is a drawdown-from-peak condition, not an absolute P&L level.
    #      A fixed axhline at ruin_threshold on the cumulative-P&L fan chart is
    #      wrong — it would only be correct when the peak is at 0. After a +2000
    #      run, ruin triggers at +1000 (−1000 DD from peak), not at −1000.
    #      The ruin line belongs on the drawdown chart (ax2), not here.
    # CHANGED: April 2026 — removed mismatched ruin line from equity fan chart
    ax1.axhline(0, color="#aaa", linewidth=0.7, linestyle=":")
    ax1.set_title(f"Equity Fan Chart  ({horizon_days}d / {n_trades} trades)\n"
                  "Each band = a range of possible futures. Dark blue line = median outcome. "
                  "Red line = account blown.",
                  fontsize=8, color="#333")
    ax1.set_xlabel("Trade #", fontsize=8)
    ax1.set_ylabel("Cumulative P&L", fontsize=8)
    ax1.legend(fontsize=7)
    ax1.tick_params(labelsize=7)

    # P&L distribution
    ax2.set_facecolor("#f8f9fb")
    if final_pnls:
        ax2.hist(final_pnls, bins=40, color="#1a73e8", alpha=0.72, edgecolor="white")
        ax2.axvline(0, color="#e94560", linewidth=1.3, linestyle="--", label="Break-even")
        ax2.axvline(np.median(final_pnls), color="#16213e", linewidth=1.5,
                    linestyle="-", label=f"Median ({np.median(final_pnls):+.2f})")
        ax2.set_title(f"Final P&L Distribution  (survivors: {survived}/{n_sims})\n"
                      "Where accounts ended up after the full period. Right of 0 = in profit.",
                      fontsize=8, color="#333")
        ax2.set_xlabel("P&L at end of period", fontsize=8)
        ax2.set_ylabel("Number of simulations", fontsize=8)
        ax2.legend(fontsize=7)
    else:
        ax2.text(0.5, 0.5, "No surviving runs", ha="center", va="center",
                 transform=ax2.transAxes, fontsize=11, color="#e94560")
        ax2.set_title("Final P&L Distribution", fontsize=8, color="#333")
    ax2.tick_params(labelsize=7)

    plt.tight_layout(pad=1.5)
    FigureCanvasTkAgg(fig1, master=_result_frame).get_tk_widget().pack(fill="both", expand=True)
    plt.close(fig1)

    # Drawdown per day chart
    fig2, ax3 = plt.subplots(figsize=(11, 3.2))
    fig2.patch.set_facecolor("#f0f2f5")
    ax3.set_facecolor("#f8f9fb")
    dd_p10 = np.percentile(all_drawdowns, 10, axis=0)
    dd_p25 = np.percentile(all_drawdowns, 25, axis=0)
    dd_p50 = np.percentile(all_drawdowns, 50, axis=0)
    dd_p75 = np.percentile(all_drawdowns, 75, axis=0)
    dd_p90 = np.percentile(all_drawdowns, 90, axis=0)
    ax3.fill_between(day_axis, dd_p10, dd_p90, alpha=0.10, color="#c0392b", label="10–90th pct")
    ax3.fill_between(day_axis, dd_p25, dd_p75, alpha=0.20, color="#c0392b", label="25–75th pct (typical)")
    ax3.plot(day_axis, dd_p50, color="#c0392b", linewidth=2, label="Median drawdown")
    ax3.axhline(ruin_threshold, color="#e94560", linewidth=1.3,
                linestyle="--", label=f"Max allowed ({ruin_threshold:+.0f})")
    ax3.axhline(0, color="#aaa", linewidth=0.7, linestyle=":")
    ax3.set_title(f"Drawdown per Day  ({horizon_days} days)\n"
                  "How far your account could drop below its own peak on any given day. "
                  "This is not total loss — it's peak-to-current drop.",
                  fontsize=8, color="#333")
    ax3.set_xlabel("Day", fontsize=8)
    ax3.set_ylabel("Drawdown (currency)", fontsize=8)
    ax3.legend(fontsize=7)
    ax3.tick_params(labelsize=7)
    plt.tight_layout(pad=1.5)
    FigureCanvasTkAgg(fig2, master=_result_frame).get_tk_widget().pack(fill="both", expand=True, pady=(8, 0))
    plt.close(fig2)


def _on_calc_target():
    if _last_sim is None:
        _show_message("Run Section A first.")
        return
    try:
        target_pct = float(_target_var.get()) / 100.0
        conf_pct   = float(_conf_var.get()) / 100.0
        if not (0 < target_pct and 0 < conf_pct < 1):
            raise ValueError
    except ValueError:
        _show_message("Invalid inputs.")
        return

    all_paths      = _last_sim["all_paths"]
    starting_bal   = _last_sim["starting_bal"]
    trades_per_day = _last_sim["trades_per_day"]
    ruin_threshold = _last_sim["ruin_threshold"]
    n_trades       = _last_sim["n_trades"]
    horizon_days   = _last_sim["horizon_days"]
    n_sims         = len(all_paths)
    target_abs     = starting_bal * target_pct

    first_hit_trades = []
    for path in all_paths:
        # WHY: np.minimum.accumulate(path) measures absolute running minimum
        #      from start — not drawdown from peak. Ruin is a DD condition.
        # CHANGED: April 2026 — use peak-to-trough drawdown for ruin detection
        running_peak_b = np.maximum.accumulate(path)
        drawdown_b     = path - running_peak_b
        ruin_indices   = np.where(drawdown_b < ruin_threshold)[0]
        ruin_at        = ruin_indices[0] if len(ruin_indices) > 0 else n_trades
        hit_idxs = np.where(path >= target_abs)[0]
        if len(hit_idxs) > 0 and hit_idxs[0] < ruin_at:
            first_hit_trades.append(hit_idxs[0])

    for w in _result_frame.winfo_children():
        if getattr(w, "_is_b", False):
            w.destroy()

    hit_pct = len(first_hit_trades) / n_sims * 100
    if not first_hit_trades:
        msg = (f"None of the {n_sims} runs reached {target_pct*100:.0f}% profit within "
               f"{horizon_days} days. Increase the horizon in Section A and re-run.")
    else:
        needed = int(np.ceil(conf_pct * n_sims))
        if needed > len(first_hit_trades):
            msg = (f"Only {hit_pct:.1f}% of simulations reached the target within {horizon_days} days "
                   f"— below your {conf_pct*100:.0f}% requirement. Increase the horizon and re-run.")
        else:
            d = sorted(first_hit_trades)[needed - 1] / trades_per_day
            msg = (f"To reach {target_pct*100:.0f}% profit ({target_abs:+.2f}) in "
                   f"{conf_pct*100:.0f}% of scenarios:\n"
                   f"  {d:.0f} day(s)  —  {d/7:.1f} week(s)  —  {d/30:.1f} month(s)\n\n"
                   f"Meaning: {conf_pct*100:.0f}% of simulated accounts hit that profit within "
                   f"{d:.0f} days without being blown. Overall {hit_pct:.1f}% reached it at "
                   f"some point in the {horizon_days}-day window.")

    lbl = tk.Label(_result_frame, text=msg, bg="#fff8e1", fg="#5f4000",
                   font=("Segoe UI", 10), wraplength=820, justify="left", padx=14, pady=12)
    lbl._is_b = True
    lbl.pack(fill="x", pady=(10, 0))


def _section_label(parent, text):
    tk.Label(parent, text=text, bg="#f0f2f5", fg="#16213e",
             font=("Segoe UI", 11, "bold"), pady=6).pack(anchor="w", padx=24)

def _input_pair(row, label, var, width=8):
    tk.Label(row, text=label, bg="#f0f2f5", fg="#333",
             font=("Segoe UI", 10)).pack(side="left")
    tk.Entry(row, textvariable=var, width=width,
             font=("Segoe UI", 10)).pack(side="left", padx=(6, 20))

def _clear_results():
    for w in _result_frame.winfo_children():
        w.destroy()

def _show_message(msg):
    _clear_results()
    tk.Label(_result_frame, text=msg, bg="#f0f2f5", fg="#e94560",
             font=("Segoe UI", 11), wraplength=700).pack(pady=20)

def refresh():
    pass
