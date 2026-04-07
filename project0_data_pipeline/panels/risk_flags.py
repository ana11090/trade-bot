import tkinter as tk
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import helpers

# ── Module-level refs set in build_panel() ────────────────────────────────────
r6_fig1 = None
r6_ax1  = None
r6_c1   = None

r6_fig2 = None
r6_ax2  = None
r6_c2   = None

r6_fig3 = None
r6_ax3  = None
r6_c3   = None

_streak_lbl = {}


# ─────────────────────────────────────────────────────────────────────────────
# CHART BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_risk_charts():
    df = helpers.get_scaled_df()
    if df is not None:
        df = df.sort_values("open_dt").reset_index(drop=True)
    for ax in (r6_ax1, r6_ax2, r6_ax3):
        ax.clear()
        ax.set_facecolor("#fafafa")
    if df is None:
        for ax in (r6_ax1, r6_ax2, r6_ax3):
            ax.text(0.5, 0.5, "No data — run the pipeline first",
                    ha="center", va="center", transform=ax.transAxes, color="#aaaaaa")
        for c in (r6_c1, r6_c2, r6_c3):
            c.draw()
        return

    # ── Lot size over time ─────────────────────────────────────────────────────
    if "Lots" in df.columns:
        lots = pd.to_numeric(df["Lots"], errors="coerce")
        wins = df["profit_scaled"] > 0
        r6_ax1.plot(df.index, lots, color="#cccccc", linewidth=0.7, zorder=1)
        r6_ax1.scatter(df.index[wins],  lots[wins],  color="#27ae60", s=10, zorder=3, label="Win")
        r6_ax1.scatter(df.index[~wins], lots[~wins], color="#e94560", s=10, zorder=3, label="Loss")
        r6_ax1.set_xlabel("Trade #", fontsize=9)
        r6_ax1.set_ylabel("Lots", fontsize=9)
        r6_ax1.set_title("Lot Size per Trade", fontsize=10)
        r6_ax1.legend(fontsize=8)
        r6_ax1.grid(True, alpha=0.2)
        r6_ax1.tick_params(labelsize=8)

        _sc_lots   = lots.values
        _sc_prof   = df["profit_scaled"].values
        _lot_annot = helpers._make_annot(r6_ax1)
        def on_lot_hover(event, _ax=r6_ax1, _ann=_lot_annot,
                         _lts=_sc_lots, _pr=_sc_prof):
            if event.inaxes != _ax:
                _ann.set_visible(False)
                r6_c1.draw_idle()
                return
            best_i, best_d = -1, float("inf")
            xlim = _ax.get_xlim()
            ylim = _ax.get_ylim()
            xr = max(xlim[1] - xlim[0], 1e-9)
            yr = max(ylim[1] - ylim[0], 1e-9)
            for idx in range(len(_lts)):
                if pd.isna(_lts[idx]):
                    continue
                dx = (idx - event.xdata) / xr
                dy = (_lts[idx] - event.ydata) / yr
                d = dx*dx + dy*dy
                if d < best_d:
                    best_d, best_i = d, idx
            if best_i >= 0 and best_d < 0.005:
                lt   = _lts[best_i]
                pr   = _pr[best_i]
                sign = "+" if pr >= 0 else ""
                _ann.xy = (best_i, lt)
                _ann.set_text(f"Trade #{best_i}\nLots: {lt:.2f}\nProfit: {sign}{pr:.2f} USD")
                _ann.set_visible(True)
            else:
                _ann.set_visible(False)
            r6_c1.draw_idle()
        helpers._reconnect(r6_c1, "motion_notify_event", on_lot_hover)

    else:
        r6_ax1.text(0.5, 0.5, "No Lots column found", ha="center", va="center",
                    transform=r6_ax1.transAxes, color="#aaaaaa")

    # ── Streak analysis ────────────────────────────────────────────────────────
    # WHY: Break-even trades (v == 0) shouldn't interrupt a winning or losing
    #      streak. W,W,W,BE,W,W is intuitively a 5-win streak, not "max 3".
    #      Streak numbers will be HIGHER after this fix for users with BEs.
    # CHANGED: April 2026 — exclude break-evens from streak segmentation
    outcomes = []
    for v in df["profit_scaled"].dropna():
        if v > 0:   outcomes.append(1)
        elif v < 0: outcomes.append(-1)
        # break-even (v == 0) skipped — does not interrupt streaks

    win_streaks, loss_streaks = [], []
    if outcomes:
        seg_dir, seg_len = outcomes[0], 1
        for o in outcomes[1:]:
            if o == seg_dir:
                seg_len += 1
            else:
                if seg_dir == 1:   win_streaks.append(seg_len)
                elif seg_dir == -1: loss_streaks.append(seg_len)
                seg_dir, seg_len = o, 1
        if seg_dir == 1:   win_streaks.append(seg_len)
        elif seg_dir == -1: loss_streaks.append(seg_len)

    max_win  = max(win_streaks,  default=0)
    max_loss = max(loss_streaks, default=0)
    avg_win  = sum(win_streaks)  / len(win_streaks)  if win_streaks  else 0
    avg_loss = sum(loss_streaks) / len(loss_streaks) if loss_streaks else 0

    _streak_lbl["max_win"].configure( text=f"{max_win} trades",      fg="#27ae60")
    _streak_lbl["max_loss"].configure(text=f"{max_loss} trades",     fg="#e94560")
    _streak_lbl["avg_win"].configure( text=f"{avg_win:.1f} trades",  fg="#27ae60")
    _streak_lbl["avg_loss"].configure(text=f"{avg_loss:.1f} trades", fg="#e94560")

    # one bar per streak segment
    all_segments = []
    if outcomes:
        seg_dir2, seg_len2 = outcomes[0], 1
        for o in outcomes[1:]:
            if o == seg_dir2:
                seg_len2 += 1
            else:
                all_segments.append((seg_dir2, seg_len2))
                seg_dir2, seg_len2 = o, 1
        all_segments.append((seg_dir2, seg_len2))

    for i, (direction, length) in enumerate(all_segments):
        if direction == 1:
            r6_ax2.bar(i, length,  color="#27ae60", width=0.8, zorder=2)
        elif direction == -1:
            r6_ax2.bar(i, -length, color="#e94560", width=0.8, zorder=2)

    r6_ax2.axhline(0, color="#333333", linewidth=0.8)
    r6_ax2.set_xlabel("Streak number (left = oldest,  right = most recent)", fontsize=9)
    r6_ax2.set_ylabel("Consecutive trades", fontsize=9)
    r6_ax2.set_title("Win / Loss Streaks — green bar = wins in a row,  red bar = losses in a row",
                     fontsize=10)
    r6_ax2.grid(axis="y", alpha=0.2, zorder=0)
    r6_ax2.tick_params(labelsize=8)
    r6_ax2.set_xticks([])

    _seg_data  = list(all_segments)
    _str_annot = helpers._make_annot(r6_ax2)
    def on_streak_hover(event, _ax=r6_ax2, _ann=_str_annot, _segs=_seg_data):
        if event.inaxes != _ax:
            _ann.set_visible(False)
            r6_c2.draw_idle()
            return
        vis = False
        for bar in _ax.patches:
            if bar.contains(event)[0]:
                i = int(round(bar.get_x() + bar.get_width() / 2))
                if 0 <= i < len(_segs):
                    direction, length = _segs[i]
                    kind = "wins" if direction == 1 else "losses"
                    _ann.xy = (bar.get_x() + bar.get_width() / 2, bar.get_height())
                    _ann.set_text(f"Streak #{i + 1}\n{length} {kind} in a row")
                    _ann.set_visible(True)
                    vis = True
                break
        if not vis:
            _ann.set_visible(False)
        r6_c2.draw_idle()
    helpers._reconnect(r6_c2, "motion_notify_event", on_streak_hover)

    # ── Duration distribution ──────────────────────────────────────────────────
    dc = helpers._dur_col(df)
    if dc:
        mins   = df[dc].apply(helpers._dur_to_secs).dropna() / 60
        n_bins = min(50, max(10, len(mins) // 20))
        r6_ax3.hist(mins, bins=n_bins, color="#4a90d9", edgecolor="white", linewidth=0.3)
        r6_ax3.axvline(2, color="#e94560", linewidth=1.2, linestyle="--", label="2 min threshold")
        r6_ax3.set_xlabel("Duration (minutes)", fontsize=9)
        r6_ax3.set_ylabel("Trades", fontsize=9)
        r6_ax3.set_title("Trade Duration Distribution", fontsize=10)
        r6_ax3.legend(fontsize=8)
        r6_ax3.grid(axis="y", alpha=0.2)
        r6_ax3.tick_params(labelsize=8)

        _dur_total3 = len(mins)
        _dur_annot3 = helpers._make_annot(r6_ax3)
        def on_dur_hover(event, _ax=r6_ax3, _ann=_dur_annot3, _tot=_dur_total3):
            if event.inaxes != _ax:
                _ann.set_visible(False)
                r6_c3.draw_idle()
                return
            vis = False
            for bar in _ax.patches:
                if bar.contains(event)[0]:
                    left  = bar.get_x()
                    right = left + bar.get_width()
                    cnt   = int(round(bar.get_height()))
                    pct   = cnt / _tot * 100 if _tot else 0
                    _ann.xy = (left + bar.get_width() / 2, bar.get_height())
                    _ann.set_text(f"{left:.1f}–{right:.1f} min\n{cnt} trades ({pct:.1f}%)")
                    _ann.set_visible(True)
                    vis = True
                    break
            if not vis:
                _ann.set_visible(False)
            r6_c3.draw_idle()
        helpers._reconnect(r6_c3, "motion_notify_event", on_dur_hover)

    else:
        r6_ax3.text(0.5, 0.5, "No Duration column found", ha="center", va="center",
                    transform=r6_ax3.transAxes, color="#aaaaaa")

    r6_fig1.tight_layout(pad=1.2)
    r6_c1.draw()
    r6_fig2.tight_layout(pad=1.5)
    r6_c2.draw()
    r6_fig3.tight_layout(pad=1.2)
    r6_c3.draw()


# ─────────────────────────────────────────────────────────────────────────────
# PANEL BUILD
# ─────────────────────────────────────────────────────────────────────────────

def build_panel(content):
    global r6_fig1, r6_ax1, r6_c1
    global r6_fig2, r6_ax2, r6_c2
    global r6_fig3, r6_ax3, r6_c3

    frame = tk.Frame(content, bg="#f0f2f5")
    tk.Label(frame, text="Risk & Red Flags", bg="#f0f2f5", fg="#1a1a2a",
             font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(24, 2))
    tk.Label(frame,
             text="Checks that reveal dangerous patterns before you trust a bot with real money.",
             bg="#f0f2f5", fg="#666666", font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(0, 16))

    # ── Lot size card ──────────────────────────────────────────────────────────
    lot_card = tk.Frame(frame, bg="white", bd=1, relief="solid")
    lot_card.pack(fill="x", padx=20, pady=(0, 10))
    tk.Label(lot_card, text="Lot Size per Trade", bg="white", fg="#1a1a2a",
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
    tk.Label(lot_card,
             text="If lot sizes increase after losses the bot is using martingale — a major red flag. "
                  "Martingale bots look profitable for months then blow an account in one bad streak. "
                  "A safe bot uses consistent lot sizes. Green dots = winning trades, red = losing.",
             bg="white", fg="#888888", font=("Segoe UI", 9),
             wraplength=600, justify="left").pack(anchor="w", padx=16, pady=(0, 8))
    r6_fig1 = Figure(figsize=(7, 2.6), dpi=90)
    r6_fig1.patch.set_facecolor("white")
    r6_ax1 = r6_fig1.add_subplot(111)
    r6_c1  = FigureCanvasTkAgg(r6_fig1, master=lot_card)
    r6_c1.get_tk_widget().pack(fill="x", padx=16, pady=(0, 14))

    # ── Streak card ────────────────────────────────────────────────────────────
    streak_card = tk.Frame(frame, bg="white", bd=1, relief="solid")
    streak_card.pack(fill="x", padx=20, pady=(0, 10))
    tk.Label(streak_card, text="Win / Loss Streaks", bg="white", fg="#1a1a2a",
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
    tk.Label(streak_card,
             text="Each bar is one uninterrupted run of wins (green, above zero) or losses (red, below zero). "
                  "The height of the bar = how many trades in a row. "
                  "A tall red bar means many consecutive losses happened. "
                  "A bot with short, balanced bars is safer than one with deep red spikes.",
             bg="white", fg="#888888", font=("Segoe UI", 9),
             wraplength=600, justify="left").pack(anchor="w", padx=16, pady=(0, 8))

    streak_stats_row = tk.Frame(streak_card, bg="white")
    streak_stats_row.pack(fill="x", padx=16, pady=(0, 10))
    for key, title, col in [
        ("max_win",  "Longest win streak",  0),
        ("max_loss", "Longest loss streak", 1),
        ("avg_win",  "Avg win streak",      2),
        ("avg_loss", "Avg loss streak",     3),
    ]:
        cell = tk.Frame(streak_stats_row, bg="white")
        cell.grid(row=0, column=col, padx=16, sticky="w")
        tk.Label(cell, text=title, bg="white", fg="#888888", font=("Segoe UI", 8)).pack(anchor="w")
        lbl = tk.Label(cell, text="—", bg="white", fg="#1a1a2a", font=("Segoe UI", 13, "bold"))
        lbl.pack(anchor="w")
        _streak_lbl[key] = lbl

    r6_fig2 = Figure(figsize=(7, 2.8), dpi=90)
    r6_fig2.patch.set_facecolor("white")
    r6_ax2 = r6_fig2.add_subplot(111)
    r6_c2  = FigureCanvasTkAgg(r6_fig2, master=streak_card)
    r6_c2.get_tk_widget().pack(fill="x", padx=16, pady=(0, 14))

    # ── Duration card ──────────────────────────────────────────────────────────
    dur6_card = tk.Frame(frame, bg="white", bd=1, relief="solid")
    dur6_card.pack(fill="x", padx=20, pady=(0, 20))
    tk.Label(dur6_card, text="Trade Duration Distribution", bg="white", fg="#1a1a2a",
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
    tk.Label(dur6_card,
             text="How long trades are held. The red dashed line marks the 2-minute prop firm threshold. "
                  "Bars left of the line are at-risk trades. See Prop Compliance for full details.",
             bg="white", fg="#888888", font=("Segoe UI", 9),
             wraplength=600, justify="left").pack(anchor="w", padx=16, pady=(0, 8))
    r6_fig3 = Figure(figsize=(7, 2.6), dpi=90)
    r6_fig3.patch.set_facecolor("white")
    r6_ax3 = r6_fig3.add_subplot(111)
    r6_c3  = FigureCanvasTkAgg(r6_fig3, master=dur6_card)
    r6_c3.get_tk_widget().pack(fill="x", padx=16, pady=(0, 14))

    return frame


# ─────────────────────────────────────────────────────────────────────────────
# REFRESH
# ─────────────────────────────────────────────────────────────────────────────

def refresh():
    build_risk_charts()
