import tkinter as tk
from tkinter import ttk
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import state
import helpers

# ── Module-level refs ─────────────────────────────────────────────────────────
_frame        = None
_result_frame = None
_horizon_var  = None
_dd_var       = None
_sims_var     = None
_target_var   = None
_conf_var     = None

_last_sim = None   # stores simulation results so Section B can reuse them


# ─────────────────────────────────────────────────────────────────────────────
# BUILD PANEL
# ─────────────────────────────────────────────────────────────────────────────

def build_panel(parent):
    global _frame, _result_frame, _horizon_var, _dd_var, _sims_var, _target_var, _conf_var

    _frame = tk.Frame(parent, bg="#f0f2f5")

    # ── Title + intro ─────────────────────────────────────────────────────────
    tk.Label(_frame, text="Account Survival & Profit Forecast",
             bg="#f0f2f5", fg="#16213e",
             font=("Segoe UI", 15, "bold"), pady=16).pack(anchor="w", padx=24)

    tk.Label(_frame,
             text=(
                 "This tool runs a Monte Carlo simulation: it randomly replays your real "
                 "trade history thousands of times to estimate how likely your account is to "
                 "survive, how much profit you could realistically make, and how deep the "
                 "drawdowns could get — all within a time window you choose."
             ),
             bg="#f0f2f5", fg="#444",
             font=("Segoe UI", 9), wraplength=820, justify="left").pack(anchor="w", padx=24, pady=(0, 10))

    # ── Section A ─────────────────────────────────────────────────────────────
    _section_label(_frame, "Section A — Survival Probability & Drawdown Forecast")

    tk.Label(_frame,
             text=(
                 "Set the time window you want to look at, the maximum loss you are willing "
                 "to accept (e.g. prop firm limit), and how many simulation runs to use. "
                 "More simulations = more accurate results but slower."
             ),
             bg="#f0f2f5", fg="#666",
             font=("Segoe UI", 9), wraplength=820, justify="left").pack(anchor="w", padx=24, pady=(0, 4))

    inputs_a = tk.Frame(_frame, bg="#f0f2f5")
    inputs_a.pack(fill="x", padx=24, pady=(0, 0))

    _horizon_var = tk.StringVar(value="30")
    _dd_var      = tk.StringVar(value="10")
    _sims_var    = tk.StringVar(value="1000")

    row1 = tk.Frame(inputs_a, bg="#f0f2f5")
    row1.pack(fill="x", pady=4)
    _input_pair(row1, "Time horizon (days):", _horizon_var, width=6)
    _input_pair(row1, "Max drawdown allowed (%):", _dd_var, width=6)
    _input_pair(row1, "Simulations:", _sims_var, width=8)

    tk.Button(inputs_a, text="Run Simulation",
              bg="#e94560", fg="white",
              font=("Segoe UI", 10, "bold"),
              bd=0, padx=16, pady=7, cursor="hand2",
              command=_on_run_sim).pack(anchor="w", pady=(6, 12))

    ttk.Separator(_frame, orient="horizontal").pack(fill="x", padx=24, pady=4)

    # ── Section B ─────────────────────────────────────────────────────────────
    _section_label(_frame, "Section B — How long to reach a profit target?")

    tk.Label(_frame,
             text=(
                 "After running Section A, enter a profit target (as % of your starting balance) "
                 "and a confidence level. The tool will calculate approximately how many days "
                 "of trading it would take to reach that target at the chosen confidence.\n"
                 "Example: 'I want a 10% profit with 80% confidence' — how long does that take?"
             ),
             bg="#f0f2f5", fg="#666",
             font=("Segoe UI", 9), wraplength=820, justify="left").pack(anchor="w", padx=24, pady=(0, 4))

    inputs_b = tk.Frame(_frame, bg="#f0f2f5")
    inputs_b.pack(fill="x", padx=24, pady=(0, 0))

    _target_var = tk.StringVar(value="10")
    _conf_var   = tk.StringVar(value="50")

    row2 = tk.Frame(inputs_b, bg="#f0f2f5")
    row2.pack(fill="x", pady=4)
    _input_pair(row2, "Target profit (%):", _target_var, width=6)
    _input_pair(row2, "Confidence (%):", _conf_var, width=6)

    tk.Button(inputs_b, text="Calculate",
              bg="#1a73e8", fg="white",
              font=("Segoe UI", 10, "bold"),
              bd=0, padx=16, pady=7, cursor="hand2",
              command=_on_calc_target).pack(anchor="w", pady=(6, 12))

    ttk.Separator(_frame, orient="horizontal").pack(fill="x", padx=24, pady=4)

    # ── Section C ─────────────────────────────────────────────────────────────
    _section_label(_frame, "Section C — Strategy Health (calculated from your trade history)")

    tk.Label(_frame,
             text=(
                 "These numbers are calculated directly from your loaded trade history — "
                 "no simulation needed. They tell you whether your strategy has a real edge "
                 "and how risky it is in practice."
             ),
             bg="#f0f2f5", fg="#666",
             font=("Segoe UI", 9), wraplength=820, justify="left").pack(anchor="w", padx=24, pady=(0, 4))

    tk.Button(_frame, text="Calculate Strategy Health",
              bg="#2ecc71", fg="white",
              font=("Segoe UI", 10, "bold"),
              bd=0, padx=16, pady=7, cursor="hand2",
              command=_on_calc_health).pack(anchor="w", padx=24, pady=(0, 12))

    ttk.Separator(_frame, orient="horizontal").pack(fill="x", padx=24, pady=4)

    # ── Result area ───────────────────────────────────────────────────────────
    _result_frame = tk.Frame(_frame, bg="#f0f2f5")
    _result_frame.pack(fill="both", expand=True, padx=24, pady=8)

    return _frame


# ─────────────────────────────────────────────────────────────────────────────
# SECTION A — MONTE CARLO SIMULATION
# ─────────────────────────────────────────────────────────────────────────────

def _on_run_sim():
    global _last_sim

    df = helpers.get_scaled_df()
    if df is None or "profit_scaled" not in df.columns:
        _show_message("No data loaded. Please load a file in the Data Pipeline first.")
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

    # Trade frequency: how many trades per trading day
    df_t = df.dropna(subset=["open_dt"]).sort_values("open_dt")
    if len(df_t) >= 2:
        # WHY: Calendar span over-counts the denominator — weekends + holidays have
        #      no trades, giving ~30% too-low rate for weekday-only strategies.
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

    # ── Run simulations ───────────────────────────────────────────────────────
    rng           = np.random.default_rng()
    all_paths     = []
    all_drawdowns = []
    final_pnls    = []
    survived      = 0

    for _ in range(n_sims):
        sampled      = rng.choice(trade_pnl, size=n_trades, replace=True)
        cumulative   = np.cumsum(sampled)
        running_peak = np.maximum.accumulate(cumulative)
        drawdown     = cumulative - running_peak  # always <= 0

        # WHY: Old code checked `cumulative < ruin_threshold` — absolute loss
        #      from start, not peak-to-trough DD. Profitable strategies that
        #      pulled back from a peak were not correctly caught.
        # CHANGED: April 2026 — use drawdown for ruin check (same fix as
        #          account_survival.py)
        ruined = bool(np.any(drawdown < ruin_threshold))

        final_pnls.append(float(cumulative[-1]))
        all_paths.append(cumulative)
        all_drawdowns.append(drawdown)

        if not ruined:
            survived += 1

    all_paths     = np.array(all_paths)
    all_drawdowns = np.array(all_drawdowns)
    survival_pct  = survived / n_sims * 100

    # Day axis for drawdown chart (trade number → day number)
    day_axis = np.arange(n_trades) / trades_per_day

    # Store for Section B
    _last_sim = {
        "all_paths":       all_paths,
        "all_drawdowns":   all_drawdowns,
        "final_pnls":      final_pnls,
        "trades_per_day":  trades_per_day,
        "starting_bal":    starting_bal,
        "trade_pnl":       trade_pnl,
        "n_trades":        n_trades,
        "horizon_days":    horizon_days,
        "max_dd_pct":      max_dd_pct,
        "ruin_threshold":  ruin_threshold,
        "n_sims":          n_sims,
    }

    # ── Summary numbers ───────────────────────────────────────────────────────
    _clear_results()

    if final_pnls:
        median_pnl = float(np.median(final_pnls))
        p10_pnl    = float(np.percentile(final_pnls, 10))
        p90_pnl    = float(np.percentile(final_pnls, 90))
        median_pct = median_pnl / starting_bal * 100
        pct_profit = sum(1 for v in final_pnls if v > 0) / len(final_pnls) * 100
    else:
        median_pnl = p10_pnl = p90_pnl = median_pct = pct_profit = 0.0

    # Worst median drawdown over the horizon
    median_dd     = float(np.median(np.min(all_drawdowns, axis=1)))
    median_dd_pct = median_dd / starting_bal * 100

    # Summary bar
    summary_lines = [
        f"  Survival probability ({horizon_days} days, max {max_dd_pct*100:.0f}% drawdown):   {survival_pct:.1f}%",
        f"  Simulated trades per run:   {n_trades}   ({trades_per_day:.1f} trades/day from your history)",
        f"  Median profit — surviving accounts:   {median_pnl:+.2f}  ({median_pct:+.1f}% of balance)",
        f"  Range (10th → 90th percentile):   {p10_pnl:+.2f}  →  {p90_pnl:+.2f}",
        f"  Survivors that ended in profit:   {pct_profit:.1f}%",
        f"  Typical deepest drawdown (median across all runs):   {median_dd:+.2f}  ({median_dd_pct:+.1f}%)",
    ]
    tk.Label(_result_frame, text="\n".join(summary_lines),
             bg="#e8f0fe", fg="#16213e",
             font=("Segoe UI", 9), justify="left",
             padx=14, pady=10).pack(fill="x", pady=(0, 10))

    # ── Chart row 1: Fan chart + P&L distribution ─────────────────────────────
    _chart_description(
        _result_frame,
        "Chart 1 — Equity Fan Chart",
        "Each shaded band shows the range of possible account balances over time based on your "
        "real trade outcomes replayed randomly. The dark blue line is the median (50% of runs "
        "are above it, 50% below). The inner band covers the middle 50% of outcomes "
        "(25th–75th percentile). The outer band covers 80% of outcomes (10th–90th percentile). "
        "The red dashed line is the ruin level — if a simulation crosses it, that account is blown."
    )
    _chart_description(
        _result_frame,
        "Chart 2 — Final P&L Distribution",
        "This histogram shows the distribution of final profit/loss values at the end of the "
        "horizon, counting only the accounts that survived. Each bar = how many simulated runs "
        "ended with that P&L. The red dashed line is break-even (zero profit). The more the "
        "histogram sits to the right of that line, the more consistently profitable your strategy is.",
        side="right"
    )

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
    # WHY: Ruin is triggered when peak-to-trough drawdown exceeds the limit.
    #      A horizontal line at ruin_threshold on the cumulative-P&L fan chart
    #      is misleading — it only equals the ruin boundary when the peak is 0.
    #      After any gain the real trigger is higher. The ruin line is correctly
    #      shown on the drawdown chart (ax3) below.
    # CHANGED: April 2026 — removed mismatched ruin line from equity fan chart
    ax1.axhline(0, color="#aaaaaa", linewidth=0.7, linestyle=":")
    ax1.set_title(f"Equity Fan Chart  ({horizon_days} days / ~{n_trades} trades)",
                  fontsize=10, color="#333")
    ax1.set_xlabel("Trade number", fontsize=8)
    ax1.set_ylabel("Cumulative P&L", fontsize=8)
    ax1.legend(fontsize=7)
    ax1.tick_params(labelsize=7)

    # P&L distribution
    ax2.set_facecolor("#f8f9fb")
    if final_pnls:
        ax2.hist(final_pnls, bins=40, color="#1a73e8", alpha=0.72, edgecolor="white")
        ax2.axvline(0, color="#e94560", linewidth=1.3, linestyle="--", label="Break-even (0)")
        ax2.axvline(np.median(final_pnls), color="#16213e", linewidth=1.5,
                    linestyle="-", label=f"Median ({np.median(final_pnls):+.2f})")
        ax2.set_title(f"Final P&L Distribution  (surviving runs: {survived}/{n_sims})",
                      fontsize=10, color="#333")
        ax2.set_xlabel("P&L at end of period", fontsize=8)
        ax2.set_ylabel("Number of simulations", fontsize=8)
        ax2.legend(fontsize=7)
    else:
        ax2.text(0.5, 0.5, "No surviving runs\n(all accounts blown)",
                 ha="center", va="center", transform=ax2.transAxes,
                 fontsize=11, color="#e94560")
        ax2.set_title("Final P&L Distribution", fontsize=10, color="#333")
    ax2.tick_params(labelsize=7)

    plt.tight_layout(pad=1.5)
    cv1 = FigureCanvasTkAgg(fig1, master=_result_frame)
    cv1.draw()
    cv1.get_tk_widget().pack(fill="both", expand=True, pady=(0, 12))
    plt.close(fig1)

    # ── Chart row 2: Drawdown per day ─────────────────────────────────────────
    _chart_description(
        _result_frame,
        "Chart 3 — Drawdown per Day",
        "This chart shows how much your account could drop below its own peak value on any "
        "given day. The Y-axis is in currency units (same as your P&L). The dark red line is "
        "the median drawdown at each day — half of simulations are worse than this, half are better. "
        "The shaded band is the typical range (25th–75th percentile). "
        "A drawdown of −500 on day 10 means the account is 500 below its highest point reached so far. "
        "This is different from total loss — it measures peak-to-current drop. "
        "The red dashed line is your maximum allowed drawdown limit."
    )

    fig2, ax3 = plt.subplots(figsize=(11, 3.2))
    fig2.patch.set_facecolor("#f0f2f5")
    ax3.set_facecolor("#f8f9fb")

    dd_p25 = np.percentile(all_drawdowns, 25, axis=0)
    dd_p50 = np.percentile(all_drawdowns, 50, axis=0)
    dd_p75 = np.percentile(all_drawdowns, 75, axis=0)
    dd_p10 = np.percentile(all_drawdowns, 10, axis=0)
    dd_p90 = np.percentile(all_drawdowns, 90, axis=0)

    ax3.fill_between(day_axis, dd_p10, dd_p90, alpha=0.10, color="#c0392b", label="10–90th pct")
    ax3.fill_between(day_axis, dd_p25, dd_p75, alpha=0.20, color="#c0392b", label="25–75th pct (typical range)")
    ax3.plot(day_axis, dd_p50, color="#c0392b", linewidth=2, label="Median drawdown")
    ax3.axhline(ruin_threshold, color="#e94560", linewidth=1.3,
                linestyle="--", label=f"Max allowed drawdown ({ruin_threshold:+.0f})")
    ax3.axhline(0, color="#aaaaaa", linewidth=0.7, linestyle=":")
    ax3.set_title(f"Drawdown per Day  (peak-to-current drop, over {horizon_days} days)",
                  fontsize=10, color="#333")
    ax3.set_xlabel("Day", fontsize=8)
    ax3.set_ylabel("Drawdown (currency)", fontsize=8)
    ax3.legend(fontsize=7)
    ax3.tick_params(labelsize=7)

    plt.tight_layout(pad=1.5)
    cv2 = FigureCanvasTkAgg(fig2, master=_result_frame)
    cv2.draw()
    cv2.get_tk_widget().pack(fill="both", expand=True)
    plt.close(fig2)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION B — HOW LONG TO REACH TARGET?
# ─────────────────────────────────────────────────────────────────────────────

def _on_calc_target():
    if _last_sim is None:
        _show_message("Run a simulation first (Section A) before calculating a target.")
        return

    try:
        target_pct = float(_target_var.get()) / 100.0
        conf_pct   = float(_conf_var.get()) / 100.0
        if not (0 < target_pct and 0 < conf_pct < 1):
            raise ValueError
    except ValueError:
        _show_message("Invalid inputs. Target must be > 0%, confidence between 1–99%.")
        return

    all_paths      = _last_sim["all_paths"]
    starting_bal   = _last_sim["starting_bal"]
    trades_per_day = _last_sim["trades_per_day"]
    ruin_threshold = _last_sim["ruin_threshold"]
    n_trades       = _last_sim["n_trades"]
    horizon_days   = _last_sim["horizon_days"]
    n_sims         = len(all_paths)

    target_abs = starting_bal * target_pct

    # For each simulation, find the first trade where the target is hit
    # (only if it hasn't been ruined before that point)
    first_hit_trades = []
    for path in all_paths:
        # WHY: np.minimum.accumulate is the running absolute minimum (P&L from
        #      start), not peak-to-trough drawdown. Ruin is a DD condition.
        # CHANGED: April 2026 — use drawdown for ruin detection
        running_peak_c = np.maximum.accumulate(path)
        drawdown_c     = path - running_peak_c
        ruin_indices   = np.where(drawdown_c < ruin_threshold)[0]
        ruin_at        = ruin_indices[0] if len(ruin_indices) > 0 else n_trades

        hit_indices = np.where(path >= target_abs)[0]
        if len(hit_indices) > 0 and hit_indices[0] < ruin_at:
            first_hit_trades.append(hit_indices[0])

    # Remove previous Section B result
    for w in _result_frame.winfo_children():
        if getattr(w, "_is_section_b", False):
            w.destroy()

    hit_rate_overall = len(first_hit_trades) / n_sims * 100

    if not first_hit_trades:
        msg = (
            f"Result: None of the {n_sims} simulated runs reached "
            f"{target_pct*100:.0f}% profit ({target_abs:+.2f}) within {horizon_days} days "
            f"without hitting the drawdown limit.\n"
            f"Try increasing the time horizon in Section A and re-running the simulation."
        )
    else:
        # Sort hit times; find the day by which conf_pct fraction of ALL sims have hit the target
        sorted_hits   = sorted(first_hit_trades)
        needed_count  = int(np.ceil(conf_pct * n_sims))

        if needed_count > len(sorted_hits):
            achieved_pct = len(sorted_hits) / n_sims * 100
            msg = (
                f"Result: Only {achieved_pct:.1f}% of simulations reached "
                f"{target_pct*100:.0f}% profit ({target_abs:+.2f}) within {horizon_days} days — "
                f"below your requested {conf_pct*100:.0f}% confidence.\n"
                f"Try increasing the time horizon in Section A and re-running the simulation, "
                f"or lower the confidence level."
            )
        else:
            result_trade = sorted_hits[needed_count - 1]
            result_days  = result_trade / trades_per_day
            msg = (
                f"Result: To reach {target_pct*100:.0f}% profit ({target_abs:+.2f}) "
                f"with {conf_pct*100:.0f}% confidence:\n"
                f"  approximately {result_days:.0f} day(s)  —  "
                f"{result_days/7:.1f} week(s)  —  "
                f"{result_days/30:.1f} month(s)\n\n"
                f"What this means: out of {n_sims} simulated runs, {conf_pct*100:.0f}% of them "
                f"hit the {target_pct*100:.0f}% profit target within {result_days:.0f} days "
                f"without ever crossing the drawdown limit. "
                f"Overall, {hit_rate_overall:.1f}% of all simulations reached the target "
                f"at some point within the {horizon_days}-day horizon."
            )

    lbl = tk.Label(_result_frame, text=msg,
                   bg="#fff8e1", fg="#5f4000",
                   font=("Segoe UI", 10),
                   wraplength=820, justify="left",
                   padx=14, pady=12)
    lbl._is_section_b = True
    lbl.pack(fill="x", pady=(10, 0))


# ─────────────────────────────────────────────────────────────────────────────
# SECTION C — STRATEGY HEALTH
# ─────────────────────────────────────────────────────────────────────────────

def _on_calc_health():
    df = helpers.get_scaled_df()
    if df is None or "profit_scaled" not in df.columns:
        _show_message("No data loaded. Please load a file in the Data Pipeline first.")
        return

    pnl = df["profit_scaled"].dropna().values
    if len(pnl) < 5:
        _show_message("Not enough trades to calculate strategy health.")
        return

    try:
        starting_bal = float(state.starting_balance.get()) if state.starting_balance else 10000.0
    except (ValueError, AttributeError):
        starting_bal = 10000.0

    wins   = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    win_rate    = len(wins) / len(pnl) * 100
    avg_win     = float(np.mean(wins))   if len(wins)   > 0 else 0.0
    avg_loss    = float(np.mean(losses)) if len(losses) > 0 else 0.0
    ev_per_trade = float(np.mean(pnl))

    # Break-even win rate: W% * avg_win + (1-W%) * avg_loss = 0
    # W = |avg_loss| / (avg_win + |avg_loss|)
    if avg_win > 0 and avg_loss < 0:
        breakeven_wr = abs(avg_loss) / (avg_win + abs(avg_loss)) * 100
    else:
        breakeven_wr = None

    # Kelly Criterion: f = W/|avg_loss| - (1-W)/avg_win  (fraction of account per trade)
    if avg_win > 0 and avg_loss < 0:
        w = len(wins) / len(pnl)
        kelly_pct = (w / abs(avg_loss) - (1 - w) / avg_win) * abs(avg_loss) * 100
        # Half-Kelly is the practical recommendation
        half_kelly = kelly_pct / 2
    else:
        kelly_pct = half_kelly = None

    # Consecutive loss probability — Monte Carlo per-horizon estimate
    loss_rate = 1 - len(wins) / len(pnl)

    def _p_streak_in_horizon(lr, streak_len, n_horizon, n_trials=2000):
        # WHY: loss_rate^N is the probability of ONE specific run of N losses —
        #      not the probability of seeing at least one such streak in H trades.
        #      The per-horizon probability is much higher and grows with the
        #      horizon. Monte Carlo gives the honest answer without approximation.
        # CHANGED: April 2026 — Monte Carlo streak probability (audit HIGH)
        rng_s = np.random.default_rng()
        hits = 0
        for _ in range(n_trials):
            outcomes = rng_s.random(n_horizon) < lr  # True = loss
            consec_s = 0
            for is_loss in outcomes:
                if is_loss:
                    consec_s += 1
                    if consec_s >= streak_len:
                        hits += 1
                        break
                else:
                    consec_s = 0
        return hits / n_trials * 100

    n_trades_horizon = len(pnl)
    consec = {n: _p_streak_in_horizon(loss_rate, n, n_trades_horizon)
              for n in [3, 5, 7, 10]}

    # Longest actual consecutive loss streak in the data
    max_streak = 0
    streak = 0
    for v in pnl:
        if v < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    # Max historical drawdown
    cumulative  = np.cumsum(pnl)
    running_max = np.maximum.accumulate(cumulative)
    drawdowns   = cumulative - running_max
    max_dd      = float(np.min(drawdowns))
    max_dd_pct  = max_dd / starting_bal * 100

    # Recovery time: average trades needed to recover from the worst drawdown
    # Find the drawdown trough and measure trades to new equity high
    trough_idx = int(np.argmin(drawdowns))
    recovery_trades = None
    if trough_idx < len(cumulative) - 1:
        trough_val = cumulative[trough_idx]
        peak_before = running_max[trough_idx]
        for i in range(trough_idx + 1, len(cumulative)):
            if cumulative[i] >= peak_before:
                recovery_trades = i - trough_idx
                break

    df_t = df.dropna(subset=["open_dt"]).sort_values("open_dt")
    trades_per_day = 1.0
    if len(df_t) >= 2:
        # WHY: Same calendar-day denominator bug as _on_run_sim — fix here too.
        # CHANGED: April 2026 — unique trading days (audit HIGH — Family #2)
        unique_trading_days_c = int(df_t["open_dt"].dt.floor("D").nunique())
        trades_per_day = len(df_t) / max(unique_trading_days_c, 1)

    recovery_days = recovery_trades / trades_per_day if recovery_trades else None

    # ── Remove old Section C results ──────────────────────────────────────────
    for w in _result_frame.winfo_children():
        if getattr(w, "_is_section_c", False):
            w.destroy()

    # ── Display ───────────────────────────────────────────────────────────────
    container = tk.Frame(_result_frame, bg="#f0f2f5")
    container._is_section_c = True
    container.pack(fill="x", pady=(0, 12))

    def _metric(parent, label, value, explanation, color="#16213e"):
        row = tk.Frame(parent, bg="#f0f2f5")
        row.pack(fill="x", pady=2)
        tk.Label(row, text=f"{label}:", bg="#f0f2f5", fg="#555",
                 font=("Segoe UI", 9, "bold"), width=32, anchor="w").pack(side="left")
        tk.Label(row, text=value, bg="#f0f2f5", fg=color,
                 font=("Segoe UI", 9, "bold"), width=18, anchor="w").pack(side="left")
        tk.Label(row, text=explanation, bg="#f0f2f5", fg="#888",
                 font=("Segoe UI", 8), anchor="w").pack(side="left")

    ev_color = "#27ae60" if ev_per_trade > 0 else "#e94560"
    _metric(container, "Expected value per trade",
            f"{ev_per_trade:+.2f}",
            "Average profit/loss per trade. Must be positive for a real edge.",
            color=ev_color)

    _metric(container, "Win rate",
            f"{win_rate:.1f}%",
            f"Avg win: {avg_win:+.2f}   |   Avg loss: {avg_loss:+.2f}")

    if breakeven_wr is not None:
        be_color = "#27ae60" if win_rate >= breakeven_wr else "#e94560"
        _metric(container, "Break-even win rate",
                f"{breakeven_wr:.1f}%",
                f"You need to win at least this % of trades to not lose money. "
                f"Your actual win rate is {'ABOVE' if win_rate >= breakeven_wr else 'BELOW'} this.",
                color=be_color)

    if kelly_pct is not None:
        # WHY: Kelly returns a BET FRACTION, not per-trade risk. The
        #      equivalent per-trade account risk for a trading strategy
        #      with stop-loss is kelly_fraction × avg_loss / account.
        #      Old label "Mathematically optimal risk per trade" was
        #      wrong — users following that number would over-bet
        #      by 10-100×.
        # CHANGED: April 2026 — distinguish bet fraction from risk per trade
        #                       (audit bug #13)
        kelly_color = "#27ae60" if kelly_pct > 0 else "#e94560"

        # Equivalent per-trade account risk
        full_risk_pct = (kelly_pct  / 100) * abs(avg_loss) / starting_bal * 100
        half_risk_pct = (half_kelly / 100) * abs(avg_loss) / starting_bal * 100

        _metric(container, "Kelly bet fraction (full)",
                f"{kelly_pct:.1f}% of bankroll",
                "Kelly formula output — the fraction of bankroll to WAGER (not risk per trade).",
                color=kelly_color)
        _metric(container, "Kelly bet fraction (half)",
                f"{half_kelly:.1f}% of bankroll",
                "Half the Kelly bet fraction. Lower volatility than full Kelly.",
                color=kelly_color)
        _metric(container, "→ Per-trade risk (full Kelly equiv.)",
                f"{full_risk_pct:.2f}% of account",
                f"Actual per-trade risk to enter in your settings (based on avg loss ${abs(avg_loss):.2f}).",
                color=kelly_color)
        _metric(container, "→ Per-trade risk (half-Kelly equiv.) ← recommended",
                f"{half_risk_pct:.2f}% of account",
                "The per-trade risk to actually use. Safer than full Kelly equivalent.",
                color="#27ae60")

    ttk.Separator(container, orient="horizontal").pack(fill="x", pady=6)

    tk.Label(container, text="Consecutive loss probability (based on your win rate):",
             bg="#f0f2f5", fg="#555", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(2, 2))
    for n, prob in consec.items():
        color = "#e94560" if prob > 10 else "#e67e22" if prob > 3 else "#27ae60"
        _metric(container, f"  {n} losses in a row",
                f"{prob:.2f}%",
                f"= 1 in every {1/prob*100:.0f} sequences of {n} trades" if prob > 0 else "",
                color=color)
    _metric(container, "  Longest actual streak in your data",
            f"{max_streak} losses",
            "The worst consecutive loss run that actually happened.")

    ttk.Separator(container, orient="horizontal").pack(fill="x", pady=6)

    _metric(container, "Max historical drawdown",
            f"{max_dd:+.2f}  ({max_dd_pct:.1f}%)",
            "The worst peak-to-trough drop in your real trade history.",
            color="#e94560")

    if recovery_days is not None:
        _metric(container, "Recovery time (from max drawdown)",
                f"~{recovery_trades} trades  (~{recovery_days:.0f} days)",
                "How long it took to recover back to the previous equity high.")
    else:
        _metric(container, "Recovery time (from max drawdown)",
                "Not recovered",
                "The account never fully recovered to its previous high within the data.",
                color="#e94560")


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _section_label(parent, text):
    tk.Label(parent, text=text,
             bg="#f0f2f5", fg="#16213e",
             font=("Segoe UI", 11, "bold"), pady=6).pack(anchor="w", padx=24)


def _chart_description(parent, title, body, side=None):
    """Render a bold chart title + description paragraph."""
    f = tk.Frame(parent, bg="#f0f2f5")
    f.pack(fill="x", pady=(6, 2))
    tk.Label(f, text=title,
             bg="#f0f2f5", fg="#16213e",
             font=("Segoe UI", 9, "bold")).pack(anchor="w")
    tk.Label(f, text=body,
             bg="#f0f2f5", fg="#555",
             font=("Segoe UI", 8), wraplength=820, justify="left").pack(anchor="w")


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
    tk.Label(_result_frame, text=msg,
             bg="#f0f2f5", fg="#e94560",
             font=("Segoe UI", 11), wraplength=700).pack(pady=20)


def refresh():
    pass
