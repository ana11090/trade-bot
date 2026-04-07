import tkinter as tk
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import state
import helpers

# ── Module-level refs set in build_panel() ────────────────────────────────────
s_fig1    = None
s_ax_pie  = None
s_ax_bar  = None
s_canvas1 = None

s_fig2    = None
s_ax_hist = None
s_canvas2 = None

_stat_labels = {}

STAT_DEFS = [
    ("total_trades",   "Total Trades",          0, 0),
    ("winners",        "Winners",               0, 1),
    ("losers",         "Losers",                0, 2),
    ("breakeven",      "Break-even",            0, 3),
    ("win_rate",       "Win Rate",              1, 0),
    ("profit_factor",  "Profit Factor",         1, 1),
    ("avg_win",        "Avg Win (USD)",         1, 2),
    ("avg_loss",       "Avg Loss (USD)",        1, 3),
    ("avg_win_pct",    "Avg Win (%)",           2, 0),
    ("avg_loss_pct",   "Avg Loss (%)",          2, 1),
    ("largest_win",    "Largest Win",           2, 2),
    ("largest_loss",   "Largest Loss",          2, 3),
    ("net_profit",     "Net Profit",            3, 0),
    ("net_pct",        "Net Return",            3, 1),
]


# ─────────────────────────────────────────────────────────────────────────────
# CHART BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_stats_charts():
    df = helpers.get_scaled_df()
    if df is not None:
        df = df.sort_values("open_dt").reset_index(drop=True)

    s_ax_pie.clear()
    s_ax_bar.clear()
    s_ax_hist.clear()
    for ax in (s_ax_pie, s_ax_bar, s_ax_hist):
        ax.set_facecolor("#fafafa")

    if df is None or "profit_scaled" not in df.columns:
        for ax in (s_ax_pie, s_ax_bar, s_ax_hist):
            ax.text(0.5, 0.5, "No data — run the pipeline first",
                    ha="center", va="center", transform=ax.transAxes, color="#aaaaaa")
        s_canvas1.draw()
        s_canvas2.draw()
        for lbl in _stat_labels.values():
            lbl.configure(text="—", fg="#1a1a2a")
        return

    profits = df["profit_scaled"].dropna()

    winners   = profits[profits > 0]
    losers    = profits[profits < 0]
    breakeven = profits[profits == 0]

    n_total = len(profits)
    n_win   = len(winners)
    n_loss  = len(losers)
    n_be    = len(breakeven)
    win_rate      = (n_win / n_total * 100) if n_total > 0 else 0
    avg_win       = winners.mean()  if n_win  > 0 else 0
    avg_loss      = losers.mean()   if n_loss > 0 else 0
    largest_win   = winners.max()   if n_win  > 0 else 0
    largest_loss  = losers.min()    if n_loss > 0 else 0
    gross_profit  = winners.sum()   if n_win  > 0 else 0
    gross_loss    = abs(losers.sum()) if n_loss > 0 else 0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    net_profit    = profits.sum()

    try:
        deposit = float(state.starting_balance.get())
    except ValueError:
        deposit = 0.0
    net_pct = (net_profit / deposit * 100) if deposit != 0 else 0

    # WHY: Compounding % only makes sense when the balance entering the trade
    #      was POSITIVE. Dividing by zero or negative balance gives nonsense.
    # CHANGED: April 2026 — mask non-positive balance entries
    running_bal  = deposit + df["profit_scaled"].shift(1).fillna(0).cumsum()
    valid_mask   = running_bal > 0
    safe_balance = running_bal.where(valid_mask, float("nan"))
    pct_series   = df["profit_scaled"] / safe_balance * 100

    win_mask  = (df["profit_scaled"] > 0) & valid_mask
    loss_mask = (df["profit_scaled"] < 0) & valid_mask
    avg_win_pct  = pct_series[win_mask].mean()  if win_mask.any()  else 0
    avg_loss_pct = pct_series[loss_mask].mean() if loss_mask.any() else 0
    if pd.isna(avg_win_pct):  avg_win_pct  = 0
    if pd.isna(avg_loss_pct): avg_loss_pct = 0

    pf_text   = f"{profit_factor:.2f}" if profit_factor != float("inf") else "∞"
    net_color = "#27ae60" if net_profit >= 0 else "#e94560"
    _stat_labels["total_trades"].configure(text=str(n_total),                     fg="#1a1a2a")
    _stat_labels["winners"].configure(text=str(n_win),                            fg="#27ae60")
    _stat_labels["losers"].configure(text=str(n_loss),                            fg="#e94560")
    _stat_labels["breakeven"].configure(text=str(n_be),                           fg="#1a1a2a")
    _stat_labels["win_rate"].configure(text=f"{win_rate:.1f}%",                   fg="#1a1a2a")
    _stat_labels["profit_factor"].configure(text=pf_text,                         fg="#1a1a2a")
    _stat_labels["avg_win"].configure(text=f"+{avg_win:.2f}",                     fg="#27ae60")
    _stat_labels["avg_loss"].configure(text=f"{avg_loss:.2f}",                    fg="#e94560")
    _stat_labels["avg_win_pct"].configure(text=f"+{avg_win_pct:.2f}%",            fg="#27ae60")
    _stat_labels["avg_loss_pct"].configure(text=f"{avg_loss_pct:.2f}%",           fg="#e94560")
    _stat_labels["largest_win"].configure(text=f"+{largest_win:.2f}",             fg="#27ae60")
    _stat_labels["largest_loss"].configure(text=f"{largest_loss:.2f}",            fg="#e94560")
    _stat_labels["net_profit"].configure(text=f"{net_profit:+.2f}",               fg=net_color)
    _stat_labels["net_pct"].configure(text=f"{net_pct:+.2f}%",                    fg=net_color)

    # pie chart
    sizes  = [n_win, n_loss, n_be]
    colors = ["#27ae60", "#e94560", "#aaaaaa"]
    labels = ["Winners", "Losers", "Break-even"]
    non_zero = [(s, c, l) for s, c, l in zip(sizes, colors, labels) if s > 0]
    if non_zero:
        sz, co, la = zip(*non_zero)
        s_ax_pie.pie(sz, labels=la, colors=co, autopct="%1.1f%%",
                     textprops={"fontsize": 8}, startangle=90)
    s_ax_pie.set_title("Trade Outcome", fontsize=9)

    # avg win vs avg loss bar
    s_ax_bar.bar(["Avg Win"],  [avg_win],  color="#27ae60", zorder=2)
    s_ax_bar.bar(["Avg Loss"], [avg_loss], color="#e94560", zorder=2)
    s_ax_bar.axhline(0, color="#cccccc", linewidth=0.8)
    s_ax_bar.set_title("Avg Win vs Avg Loss (USD)", fontsize=9)
    s_ax_bar.set_ylabel("USD", fontsize=8)
    s_ax_bar.grid(axis="y", alpha=0.25, zorder=0)
    s_ax_bar.tick_params(labelsize=8)

    _bar_annot1 = helpers._make_annot(s_ax_bar)
    _bar_dep1   = deposit
    def on_bar_hover(event, _ax=s_ax_bar, _ann=_bar_annot1, _dep=_bar_dep1):
        if event.inaxes != _ax:
            _ann.set_visible(False)
            s_canvas1.draw_idle()
            return
        vis = False
        for bar in _ax.patches:
            if bar.contains(event)[0]:
                val  = bar.get_height()
                pct  = (val / _dep * 100) if _dep else 0
                sign = "+" if val >= 0 else ""
                _ann.xy = (bar.get_x() + bar.get_width() / 2, val)
                _ann.set_text(f"{sign}{val:.2f} USD\n{sign}{pct:.2f}% of deposit")
                _ann.set_visible(True)
                vis = True
                break
        if not vis:
            _ann.set_visible(False)
        s_canvas1.draw_idle()
    helpers._reconnect(s_canvas1, "motion_notify_event", on_bar_hover)

    s_fig1.tight_layout(pad=1.2)
    s_canvas1.draw()

    # profit distribution histogram
    n_bins = min(40, max(10, n_total // 20))
    counts, edges, patches = s_ax_hist.hist(profits, bins=n_bins, edgecolor="white", linewidth=0.4)
    for patch, left_edge in zip(patches, edges[:-1]):
        patch.set_facecolor("#27ae60" if left_edge >= 0 else "#e94560")
    s_ax_hist.axvline(0, color="#888888", linewidth=0.9, linestyle="--")
    s_ax_hist.set_xlabel("Profit per trade (USD)", fontsize=9)
    s_ax_hist.set_ylabel("Number of trades", fontsize=9)
    s_ax_hist.set_title("Profit Distribution", fontsize=10)
    s_ax_hist.grid(axis="y", alpha=0.25)
    s_ax_hist.tick_params(labelsize=8)

    _hist_annot2 = helpers._make_annot(s_ax_hist)
    _hist_total2 = n_total
    def on_hist_hover(event, _ax=s_ax_hist, _ann=_hist_annot2, _tot=_hist_total2):
        if event.inaxes != _ax:
            _ann.set_visible(False)
            s_canvas2.draw_idle()
            return
        vis = False
        for bar in _ax.patches:
            if bar.contains(event)[0]:
                left  = bar.get_x()
                right = left + bar.get_width()
                cnt   = int(round(bar.get_height()))
                pct   = cnt / _tot * 100 if _tot else 0
                _ann.xy = (left + bar.get_width() / 2, bar.get_height())
                _ann.set_text(f"${left:.2f} to ${right:.2f}\n{cnt} trades ({pct:.1f}%)")
                _ann.set_visible(True)
                vis = True
                break
        if not vis:
            _ann.set_visible(False)
        s_canvas2.draw_idle()
    helpers._reconnect(s_canvas2, "motion_notify_event", on_hist_hover)

    s_fig2.tight_layout(pad=1.2)
    s_canvas2.draw()


# ─────────────────────────────────────────────────────────────────────────────
# PANEL BUILD
# ─────────────────────────────────────────────────────────────────────────────

def _stat_cell(parent, row, col, title, value_text):
    cell = tk.Frame(parent, bg="white")
    cell.grid(row=row, column=col, padx=16, pady=6, sticky="w")
    tk.Label(cell, text=title, bg="white", fg="#888888",
             font=("Segoe UI", 8)).pack(anchor="w")
    lbl = tk.Label(cell, text=value_text, bg="white", fg="#1a1a2a",
                   font=("Segoe UI", 13, "bold"))
    lbl.pack(anchor="w")
    return lbl


def build_panel(content):
    global s_fig1, s_ax_pie, s_ax_bar, s_canvas1
    global s_fig2, s_ax_hist, s_canvas2

    frame = tk.Frame(content, bg="#f0f2f5")

    tk.Label(frame, text="Trade Statistics", bg="#f0f2f5", fg="#1a1a2a",
             font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(24, 2))
    tk.Label(frame, text="Key performance metrics across all trades.",
             bg="#f0f2f5", fg="#666666", font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(0, 16))

    # ── Summary metrics card ───────────────────────────────────────────────────
    stats_card = tk.Frame(frame, bg="white", bd=1, relief="solid")
    stats_card.pack(fill="x", padx=20, pady=(0, 10))
    tk.Label(stats_card, text="Summary", bg="white", fg="#1a1a2a",
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 10))

    stats_grid = tk.Frame(stats_card, bg="white")
    stats_grid.pack(fill="x", padx=16, pady=(0, 16))
    for key, title, row, col in STAT_DEFS:
        _stat_labels[key] = _stat_cell(stats_grid, row, col, title, "—")

    # ── Charts card ────────────────────────────────────────────────────────────
    charts_card = tk.Frame(frame, bg="white", bd=1, relief="solid")
    charts_card.pack(fill="x", padx=20, pady=(0, 10))
    tk.Label(charts_card, text="Win / Loss Breakdown", bg="white", fg="#1a1a2a",
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
    tk.Label(charts_card,
             text="Left: share of winning, losing and break-even trades by count. "
                  "Right: average profit of a winning trade vs average loss of a losing trade (in USD). "
                  "The % equivalents are shown in the summary table above.",
             bg="white", fg="#888888", font=("Segoe UI", 9),
             wraplength=600, justify="left").pack(anchor="w", padx=16, pady=(0, 8))

    s_fig1 = Figure(figsize=(7, 3), dpi=90)
    s_fig1.patch.set_facecolor("white")
    s_ax_pie = s_fig1.add_subplot(121)
    s_ax_bar = s_fig1.add_subplot(122)
    s_canvas1 = FigureCanvasTkAgg(s_fig1, master=charts_card)
    s_canvas1.get_tk_widget().pack(fill="x", padx=16, pady=(0, 14))

    # ── Distribution card ──────────────────────────────────────────────────────
    dist_card = tk.Frame(frame, bg="white", bd=1, relief="solid")
    dist_card.pack(fill="x", padx=20, pady=(0, 20))
    tk.Label(dist_card, text="Profit Distribution", bg="white", fg="#1a1a2a",
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
    tk.Label(dist_card,
             text="How often each profit/loss range occurs. "
                  "A well-distributed histogram with a rightward skew is a healthy sign. "
                  "Bars to the right of zero are winning trades, bars to the left are losing trades.",
             bg="white", fg="#888888", font=("Segoe UI", 9),
             wraplength=600, justify="left").pack(anchor="w", padx=16, pady=(0, 8))

    s_fig2 = Figure(figsize=(7, 2.8), dpi=90)
    s_fig2.patch.set_facecolor("white")
    s_ax_hist = s_fig2.add_subplot(111)
    s_canvas2 = FigureCanvasTkAgg(s_fig2, master=dist_card)
    s_canvas2.get_tk_widget().pack(fill="x", padx=16, pady=(0, 14))

    return frame


# ─────────────────────────────────────────────────────────────────────────────
# REFRESH
# ─────────────────────────────────────────────────────────────────────────────

def refresh():
    build_stats_charts()
