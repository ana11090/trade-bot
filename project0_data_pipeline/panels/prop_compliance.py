import tkinter as tk
from tkinter import ttk
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import state
import helpers
from widgets import _sep, _rule_box

# ── Firm defaults ──────────────────────────────────────────────────────────────
_FIRM_DD_DEFAULTS = {
    # WHY: lock_at_gain_pct is the gain % that locks the DD floor at the
    #      starting balance. For Leveraged this equals trailing_pct (6%),
    #      but other firms may differ — so it's explicit now.
    # CHANGED: April 2026 — explicit lock threshold
    "Levereged": {"trailing_pct": "6", "daily_pct": "5", "lock_at_gain_pct": "6"},
}

# ── Module-level refs set in build_panel() ────────────────────────────────────
min_hold_var = None
firm_var     = None
total_dd_var  = None
daily_dd_var  = None
lock_gain_var = None   # gain % at which DD floor locks at starting balance

p7_hold_results     = None
p7_dd_results       = None
p7_firm_rules_frame = None
p7_dd_row           = None
p7_firm_row         = None

p7_fig    = None
p7_ax     = None
p7_canvas = None

p7_dd_fig1 = None
p7_dd_ax1  = None
p7_dd_c1   = None

p7_dd_fig2 = None
p7_dd_ax2  = None
p7_dd_c2   = None

_p7_labels          = {}
_p7_dd_labels       = {}
_p7_dd_title_labels = {}


# ─────────────────────────────────────────────────────────────────────────────
# FIRM CHANGE HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def _on_firm_change(*_):
    firm = firm_var.get()
    if not firm:
        return
    defs = _FIRM_DD_DEFAULTS.get(firm, {})
    if "trailing_pct"     in defs: total_dd_var.set(defs["trailing_pct"])
    if "daily_pct"        in defs: daily_dd_var.set(defs["daily_pct"])
    if "lock_at_gain_pct" in defs and lock_gain_var is not None:
        lock_gain_var.set(defs["lock_at_gain_pct"])
    p7_firm_rules_frame.pack(fill="x", after=p7_firm_row)
    p7_dd_row.pack(fill="x", padx=16, pady=(0, 14), after=p7_firm_rules_frame)


# ─────────────────────────────────────────────────────────────────────────────
# CHART BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def build_compliance_chart():
    df = helpers.get_scaled_df()
    if df is not None:
        df = df.sort_values("open_dt").reset_index(drop=True)
    p7_ax.clear()
    p7_ax.set_facecolor("#fafafa")
    try:
        min_secs = int(min_hold_var.get())
    except ValueError:
        min_secs = 120

    if df is None:
        p7_ax.text(0.5, 0.5, "No data — run the pipeline first",
                   ha="center", va="center", transform=p7_ax.transAxes, color="#aaaaaa")
        p7_canvas.draw()
        for lbl in _p7_labels.values():
            lbl.configure(text="—", fg="#1a1a2a")
        return

    dc = helpers._dur_col(df)
    if dc is None:
        p7_ax.text(0.5, 0.5, "No Duration column found in data",
                   ha="center", va="center", transform=p7_ax.transAxes, color="#aaaaaa")
        p7_canvas.draw()
        return

    secs    = df[dc].apply(helpers._dur_to_secs)
    valid   = secs.dropna()
    sub     = valid < min_secs
    n_total = len(valid)
    n_sub   = int(sub.sum())
    pct_sub = n_sub / n_total * 100 if n_total > 0 else 0

    profits      = df.loc[secs.notna(), "profit_scaled"]
    profit_total = profits.sum()
    profit_sub_v = profits[sub].sum()
    # WHY: For losing strategies, profit_total < 0 and a positive sub_v gives
    #      a NEGATIVE percentage — verdict thresholds (> 25) never fire.
    #      Use abs() so the metric measures share-of-magnitude correctly.
    # CHANGED: April 2026 — use abs(profit_total) as denominator
    if abs(profit_total) > 0.01:
        pct_profit = (profit_sub_v / abs(profit_total)) * 100
    else:
        pct_profit = 0

    if pct_sub == 0:
        verdict, vcol = "PASS — no sub-threshold trades", "#27ae60"
    elif pct_profit > 25:
        verdict, vcol = "FAIL — strategy depends on speed", "#e94560"
    elif pct_sub < 5 and abs(pct_profit) < 10:
        verdict, vcol = "PASS — minor violations only", "#27ae60"
    else:
        verdict, vcol = "CAUTION — review required", "#f39c12"

    _p7_labels["total"].configure(text=str(n_total),                fg="#1a1a2a")
    _p7_labels["n_sub"].configure(text=str(n_sub),                  fg="#e94560" if n_sub > 0 else "#27ae60")
    _p7_labels["pct_sub"].configure(text=f"{pct_sub:.1f}%",         fg="#e94560" if pct_sub > 5 else "#1a1a2a")
    _p7_labels["profit_sub"].configure(text=f"{profit_sub_v:+.2f}", fg="#e94560" if profit_sub_v > 0 else "#27ae60")
    _p7_labels["pct_profit"].configure(
        text=f"{pct_profit:.1f}%",
        fg="#e94560" if pct_profit > 25 else "#f39c12" if pct_profit > 10 else "#27ae60")
    _p7_labels["verdict"].configure(text=verdict, fg=vcol)

    mins          = valid / 60
    min_threshold = min_secs / 60
    n_bins        = min(50, max(10, len(mins) // 20))
    counts, edges, patches = p7_ax.hist(mins, bins=n_bins, edgecolor="white", linewidth=0.3)
    for patch, le in zip(patches, edges[:-1]):
        patch.set_facecolor("#e94560" if le < min_threshold else "#4a90d9")
    p7_ax.axvline(min_threshold, color="#cc0000", linewidth=1.4, linestyle="--",
                  label=f"Min hold = {min_secs}s ({min_threshold:.1f} min)")
    p7_ax.set_xlabel("Duration (minutes)", fontsize=9)
    p7_ax.set_ylabel("Trades", fontsize=9)
    p7_ax.set_title("Hold Time Distribution", fontsize=10)
    p7_ax.legend(fontsize=8)
    p7_ax.tick_params(labelsize=8)
    p7_ax.grid(axis="y", alpha=0.2)

    _p7_total = n_total
    _p7_thr   = min_threshold
    _p7_annot = helpers._make_annot(p7_ax)
    def on_p7_hover(event, _ax=p7_ax, _ann=_p7_annot, _tot=_p7_total, _thr=_p7_thr):
        if event.inaxes != _ax:
            _ann.set_visible(False)
            p7_canvas.draw_idle()
            return
        vis = False
        for bar in _ax.patches:
            if bar.contains(event)[0]:
                left  = bar.get_x()
                right = left + bar.get_width()
                cnt   = int(round(bar.get_height()))
                pct   = cnt / _tot * 100 if _tot else 0
                flag  = "  ⚠ below threshold" if right <= _thr else ""
                _ann.xy = (left + bar.get_width() / 2, bar.get_height())
                _ann.set_text(f"{left:.1f}–{right:.1f} min\n{cnt} trades ({pct:.1f}%){flag}")
                _ann.set_visible(True)
                vis = True
                break
        if not vis:
            _ann.set_visible(False)
        p7_canvas.draw_idle()
    helpers._reconnect(p7_canvas, "motion_notify_event", on_p7_hover)

    p7_fig.tight_layout(pad=1.2)
    p7_canvas.draw()


def build_drawdown_charts():
    df = helpers.get_scaled_df()
    if df is not None:
        df = df.sort_values("open_dt").reset_index(drop=True)

    p7_dd_ax1.clear(); p7_dd_ax2.clear()
    p7_dd_ax1.set_facecolor("#fafafa"); p7_dd_ax2.set_facecolor("#fafafa")

    firm = firm_var.get()
    try:    trailing_pct = float(total_dd_var.get())
    except ValueError: trailing_pct = 6.0
    try:    daily_dd_pct = float(daily_dd_var.get())
    except ValueError: daily_dd_pct = 5.0
    try:    deposit = float(state.starting_balance.get())
    except ValueError: deposit = 0.0

    if df is None or deposit == 0:
        msg = ("No data — run the pipeline first" if df is None
               else "Set a starting balance in the pipeline first")
        for ax in (p7_dd_ax1, p7_dd_ax2):
            ax.text(0.5, 0.5, msg, ha="center", va="center",
                    transform=ax.transAxes, color="#aaaaaa")
        p7_dd_c1.draw(); p7_dd_c2.draw()
        for lbl in _p7_dd_labels.values():
            lbl.configure(text="—", fg="#1a1a2a")
        return

    # ── Daily P&L ─────────────────────────────────────────────────────────────
    if "close_dt" in df.columns:
        df["_date"] = pd.to_datetime(df["close_dt"], errors="coerce").dt.date
    else:
        df["_date"] = df["open_dt"].dt.date

    daily_pnl  = df.groupby("_date")["profit_scaled"].sum().sort_index()
    dates_list = list(daily_pnl.index)
    pnl_list   = list(daily_pnl.values)
    n_days     = len(dates_list)

    daily_limit_usd = -daily_dd_pct / 100.0 * deposit
    show_daily      = daily_dd_pct > 0

    breached_days = sum(1 for p in pnl_list if p <= daily_limit_usd) if show_daily else 0
    worst_day_pnl = min(pnl_list) if pnl_list else 0
    worst_day_pct = abs(worst_day_pnl) / deposit * 100 if deposit else 0

    bar_colors = []
    for p in pnl_list:
        if show_daily and p <= daily_limit_usd: bar_colors.append("#8B0000")
        elif p < 0:                             bar_colors.append("#e94560")
        else:                                   bar_colors.append("#27ae60")

    x_days = list(range(n_days))
    p7_dd_ax1.bar(x_days, pnl_list, color=bar_colors, zorder=2, width=0.7)
    if show_daily:
        p7_dd_ax1.axhline(daily_limit_usd, color="#cc0000", linewidth=1.4, linestyle="--",
                          zorder=3,
                          label=f"Daily limit: -{daily_dd_pct:.1f}% = ${abs(daily_limit_usd):,.0f}  "
                                f"(from starting balance)")
    p7_dd_ax1.axhline(0, color="#cccccc", linewidth=0.8)
    p7_dd_ax1.set_ylabel("Day P&L (USD)", fontsize=9)
    p7_dd_ax1.set_title(f"Daily P&L — {firm}  "
                        f"({'daily limit shown' if show_daily else 'no daily limit configured'})",
                        fontsize=10)
    if show_daily:
        p7_dd_ax1.legend(fontsize=8)
    p7_dd_ax1.grid(axis="y", alpha=0.2, zorder=0)
    p7_dd_ax1.tick_params(labelsize=8)
    n_t1    = min(8, n_days)
    tick_i1 = ([int(i * (n_days - 1) / (n_t1 - 1)) for i in range(n_t1)]
               if n_days > 1 else [0])
    p7_dd_ax1.set_xticks(tick_i1)
    p7_dd_ax1.set_xticklabels([str(dates_list[i]) for i in tick_i1],
                               fontsize=7, rotation=25, ha="right")
    p7_dd_ax1.text(0.01, 0.02,
                   "Approximation: Levereged snapshots max(balance, equity) at 23:00 GMT+3.\n"
                   "This chart uses closed-trade P&L per day — open positions are not visible in your export.",
                   transform=p7_dd_ax1.transAxes, fontsize=6.5,
                   color="#aaaaaa", va="bottom")

    # ── Trailing DD with lock ──────────────────────────────────────────────────
    # WHY: Old code used min(h - dd_amount, deposit) which only coincidentally
    #      worked when lock_threshold == trailing_pct. Explicit lock state is
    #      more accurate and generalizes to other firms.
    # CHANGED: April 2026 — explicit lock threshold + state tracking
    try:
        lock_gain_pct = float(lock_gain_var.get()) if lock_gain_var is not None else trailing_pct
    except (ValueError, AttributeError):
        lock_gain_pct = trailing_pct

    dd_amount    = deposit * trailing_pct / 100.0
    lock_trigger = deposit * (1.0 + lock_gain_pct / 100.0)

    closed_bal = list(deposit + df["profit_scaled"].cumsum().values)
    hwm        = []
    floor      = []
    cur_hwm    = closed_bal[0]
    locked     = False
    locked_at  = None

    for i, cb in enumerate(closed_bal):
        if not locked and cb > cur_hwm:
            cur_hwm = cb
        if not locked and cb >= lock_trigger:
            locked    = True
            locked_at = i
        hwm.append(cur_hwm)
        floor.append(deposit if locked else cur_hwm - dd_amount)

    buffer_usd   = [cb - f for cb, f in zip(closed_bal, floor)]
    buffer_pct   = [b / deposit * 100 if deposit > 0 else 0 for b in buffer_usd]
    breach_count = sum(1 for b in buffer_usd if b <= 0)

    n_trades = len(closed_bal)
    x_curve  = list(range(n_trades))

    p7_dd_ax2.plot(x_curve, closed_bal, color="#4a90d9", linewidth=1.3,
                   label="Closed balance", zorder=3)
    p7_dd_ax2.plot(x_curve, floor, color="#cc0000", linewidth=1.4, linestyle="--",
                   label=f"DD floor — breach level  ({trailing_pct:.1f}% trailing, locks at ${deposit:,.0f})",
                   zorder=3)
    p7_dd_ax2.fill_between(x_curve, closed_bal, floor,
                            where=[cb >= f for cb, f in zip(closed_bal, floor)],
                            color="#27ae60", alpha=0.12, label="Buffer (safe zone)")
    if breach_count > 0:
        p7_dd_ax2.fill_between(x_curve, closed_bal, floor,
                                where=[cb < f for cb, f in zip(closed_bal, floor)],
                                color="#e94560", alpha=0.5, label="BREACH")
    p7_dd_ax2.axhline(deposit, color="#888888", linewidth=0.9, linestyle=":",
                       label=f"Starting balance ${deposit:,.0f}  (floor locks here)")
    if locked_at is not None:
        p7_dd_ax2.axvline(locked_at, color="#f39c12", linewidth=1.2, linestyle=":",
                          label=f"Floor locked at trade #{locked_at}")
    p7_dd_ax2.set_ylabel("Balance (USD)", fontsize=9)
    p7_dd_ax2.set_xlabel("Trade #", fontsize=9)
    p7_dd_ax2.set_title(
        f"Levereged Trailing DD ({trailing_pct:.1f}%)  —  Closed Balance vs DD Floor",
        fontsize=10)
    p7_dd_ax2.legend(fontsize=7, loc="best")
    p7_dd_ax2.grid(axis="y", alpha=0.2)
    p7_dd_ax2.tick_params(labelsize=8)
    n_t2    = min(8, n_trades)
    tick_i2 = ([int(i * (n_trades - 1) / (n_t2 - 1)) for i in range(n_t2)]
               if n_trades > 1 else [0])
    p7_dd_ax2.set_xticks(tick_i2)
    p7_dd_ax2.set_xticklabels(
        [f"#{i}\n{df['open_dt'].iloc[i].strftime('%b %Y') if pd.notna(df['open_dt'].iloc[i]) else ''}"
         for i in tick_i2],
        fontsize=7.5, ha="center")

    # ── Scorecard ──────────────────────────────────────────────────────────────
    min_buf_usd = min(buffer_usd)
    min_buf_pct = min(buffer_pct)
    cur_buf_usd = buffer_usd[-1]
    cur_buf_pct = buffer_pct[-1]
    lock_txt    = (f"Trade #{locked_at}  (${closed_bal[locked_at]:,.0f} closed bal)"
                   if locked_at is not None else "Not yet — floor still trailing")

    if breach_count > 0:
        dd_verdict, dd_vcol = "FAIL — floor breached", "#e94560"
    elif breached_days > 0:
        dd_verdict, dd_vcol = "FAIL — daily DD breached", "#e94560"
    elif min_buf_pct < 1.5:
        dd_verdict, dd_vcol = "CAUTION — very close to floor", "#f39c12"
    elif min_buf_pct < 3.0:
        dd_verdict, dd_vcol = "CAUTION — low buffer at worst", "#f39c12"
    else:
        dd_verdict, dd_vcol = "PASS — within all limits", "#27ae60"

    _p7_dd_title_labels["worst_day"].configure(text="Worst Day Loss")
    _p7_dd_title_labels["days_breached"].configure(
        text=f"Days Breached Daily Limit ({daily_dd_pct:.1f}%)")
    _p7_dd_title_labels["max_total_dd"].configure(text="Closest to Floor (worst point)")
    _p7_dd_title_labels["current_dd"].configure(text="Current Buffer above Floor")
    _p7_dd_title_labels["dd_type_shown"].configure(text="Floor Locked Since")
    _p7_dd_title_labels["dd_verdict"].configure(text="Verdict")

    _p7_dd_labels["worst_day"].configure(
        text=f"{worst_day_pnl:+.2f} USD  ({-worst_day_pct:.2f}%)",
        fg="#e94560" if worst_day_pnl < 0 else "#27ae60")
    _p7_dd_labels["days_breached"].configure(
        text=str(breached_days) if show_daily else "N/A",
        fg="#e94560" if breached_days > 0 else "#27ae60")
    _p7_dd_labels["max_total_dd"].configure(
        text=f"${min_buf_usd:,.2f}  ({min_buf_pct:.2f}%)",
        fg="#e94560" if min_buf_usd <= 0 else
           "#f39c12" if min_buf_pct < 2 else "#27ae60")
    _p7_dd_labels["current_dd"].configure(
        text=f"${cur_buf_usd:,.2f}  ({cur_buf_pct:.2f}%)",
        fg="#e94560" if cur_buf_usd <= 0 else "#27ae60")
    _p7_dd_labels["dd_type_shown"].configure(text=lock_txt, fg="#1a1a2a")
    _p7_dd_labels["dd_verdict"].configure(text=dd_verdict, fg=dd_vcol)

    # ── Hover: daily bars ──────────────────────────────────────────────────────
    _dd1_ann  = helpers._make_annot(p7_dd_ax1)
    _h1_dates = dates_list; _h1_pnl = pnl_list
    _h1_lim   = daily_limit_usd; _h1_dep = deposit; _h1_show = show_daily
    def on_dd1_hover(event, _ax=p7_dd_ax1, _ann=_dd1_ann,
                     _dates=_h1_dates, _pnl=_h1_pnl,
                     _lim=_h1_lim, _dep=_h1_dep, _show=_h1_show):
        if event.inaxes != _ax:
            _ann.set_visible(False); p7_dd_c1.draw_idle(); return
        vis = False
        for idx, bar in enumerate(_ax.patches):
            if bar.contains(event)[0] and idx < len(_pnl):
                p    = _pnl[idx]
                pct  = p / _dep * 100 if _dep else 0
                sign = "+" if p >= 0 else ""
                flag = ("  BREACH!" if _show and p <= _lim else "")
                lim_txt = (f"\nDaily limit: {_lim:.2f} USD" if _show else "")
                _ann.xy = (bar.get_x() + bar.get_width() / 2, p)
                _ann.set_text(
                    f"{_dates[idx]}\n"
                    f"Day P&L: {sign}{p:.2f} USD ({sign}{pct:.2f}%)"
                    f"{lim_txt}{flag}")
                _ann.set_visible(True); vis = True; break
        if not vis: _ann.set_visible(False)
        p7_dd_c1.draw_idle()
    helpers._reconnect(p7_dd_c1, "motion_notify_event", on_dd1_hover)

    # ── Hover: balance vs floor chart ──────────────────────────────────────────
    _dd2_ann = helpers._make_annot(p7_dd_ax2)
    _h2_x    = x_curve; _h2_cb = closed_bal; _h2_fl = floor
    _h2_bu   = buffer_usd; _h2_bp = buffer_pct
    _h2_dep  = deposit; _h2_df = df
    def on_dd2_hover(event, _ax=p7_dd_ax2, _ann=_dd2_ann,
                     _dx=_h2_x, _cb=_h2_cb, _fl=_h2_fl,
                     _bu=_h2_bu, _bp=_h2_bp, _dep=_h2_dep, _ddf=_h2_df):
        if event.inaxes != _ax or not _dx:
            _ann.set_visible(False); p7_dd_c2.draw_idle(); return
        idx = min(range(len(_dx)), key=lambda i: abs(_dx[i] - event.xdata))
        cb = _cb[idx]; fl = _fl[idx]; bu = _bu[idx]; bp = _bp[idx]
        date_str = ""
        try:
            date_str = f"\n{_ddf['open_dt'].iloc[idx].strftime('%d %b %Y')}"
        except Exception:
            pass
        lock_note = ("  (floor locked)" if abs(fl - _dep) < 0.01 else "  (floor trailing)")
        flag = "  BREACH!" if bu <= 0 else ("  CAUTION!" if bp < 1.5 else "")
        _ann.xy = (_dx[idx], cb)
        _ann.set_text(
            f"Trade #{idx}{date_str}\n"
            f"Closed bal: ${cb:,.2f}\n"
            f"Floor: ${fl:,.2f}{lock_note}\n"
            f"Buffer: ${bu:,.2f}  ({bp:.2f}%){flag}")
        _ann.set_visible(True)
        p7_dd_c2.draw_idle()
    helpers._reconnect(p7_dd_c2, "motion_notify_event", on_dd2_hover)

    p7_dd_fig1.tight_layout(pad=1.2)
    p7_dd_c1.draw()
    p7_dd_fig2.tight_layout(pad=1.2)
    p7_dd_c2.draw()


# ─────────────────────────────────────────────────────────────────────────────
# PANEL BUILD
# ─────────────────────────────────────────────────────────────────────────────

def _run_hold_time():
    p7_hold_results.pack(fill="x")
    build_compliance_chart()


def _run_drawdown():
    p7_dd_results.pack(fill="x")
    build_drawdown_charts()


def build_panel(content):
    global min_hold_var, firm_var, total_dd_var, daily_dd_var, lock_gain_var
    global p7_hold_results, p7_dd_results
    global p7_firm_rules_frame, p7_dd_row, p7_firm_row
    global p7_fig, p7_ax, p7_canvas
    global p7_dd_fig1, p7_dd_ax1, p7_dd_c1
    global p7_dd_fig2, p7_dd_ax2, p7_dd_c2

    frame = tk.Frame(content, bg="#f0f2f5")
    tk.Label(frame, text="Prop Compliance", bg="#f0f2f5", fg="#1a1a2a",
             font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(24, 2))
    tk.Label(frame,
             text="Check whether the bot's patterns would pass prop firm rules.",
             bg="#f0f2f5", fg="#666666", font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(0, 16))

    # ═══════════════════════════════════════════════════════════════════════════
    # CARD 1 — Hold Time Rule
    # ═══════════════════════════════════════════════════════════════════════════
    p7_hold_card = tk.Frame(frame, bg="white", bd=1, relief="solid")
    p7_hold_card.pack(fill="x", padx=20, pady=(0, 14))

    tk.Label(p7_hold_card, text="Hold Time Rule", bg="white", fg="#1a1a2a",
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 6))

    _rule_box(p7_hold_card, [
        ("What is it?", True),
        ("Many prop firms ban trades closed in under 2 minutes (some use 5 min). Trades below", False),
        ("the threshold are treated as HFT and can be disqualified, voiding your profit.", False),
        ("What this panel checks:", True),
        ("How many of your trades are below the threshold, and — most importantly — what", False),
        ("percentage of your total profit came from those trades. If that number is high,", False),
        ("your strategy depends on speed and will fail on a firm with a minimum hold rule.", False),
        ("Verdict logic:", True),
        ("  FAIL  = sub-threshold trades produce > 25 % of total profit", False),
        ("  PASS  = no sub-threshold trades, or they contribute < 10 % of profit", False),
        ("  CAUTION = somewhere in between — review manually", False),
    ])

    p7_cfg_row = tk.Frame(p7_hold_card, bg="white")
    p7_cfg_row.pack(fill="x", padx=16, pady=(0, 14))
    tk.Label(p7_cfg_row, text="Min hold time:", bg="white", font=("Segoe UI", 10)).pack(side="left")
    min_hold_var = tk.StringVar(value="120")
    tk.Entry(p7_cfg_row, textvariable=min_hold_var, width=7, font=("Segoe UI", 10),
             bd=1, relief="solid").pack(side="left", padx=(6, 2))
    tk.Label(p7_cfg_row, text="seconds", bg="white", fg="#666666",
             font=("Segoe UI", 10)).pack(side="left")
    tk.Button(p7_cfg_row, text="Calculate", font=("Segoe UI", 10, "bold"),
              bg="#e94560", fg="white", bd=0, padx=14, pady=6,
              activebackground="#e94560", activeforeground="white",
              command=lambda: _run_hold_time()).pack(side="left", padx=(20, 0))
    tk.Label(p7_cfg_row, text="Default = 120 s  (2 min)",
             bg="white", fg="#aaaaaa", font=("Segoe UI", 9)).pack(side="left", padx=(12, 0))

    # results section — hidden until Calculate is pressed
    p7_hold_results = tk.Frame(p7_hold_card, bg="white")

    _sep(p7_hold_results)

    p7_grid = tk.Frame(p7_hold_results, bg="white")
    p7_grid.pack(fill="x", padx=16, pady=(0, 4))
    for key, title, row, col in [
        ("total",      "Total Trades",              0, 0),
        ("n_sub",      "Trades Below Threshold",    0, 1),
        ("pct_sub",    "% of Trades Below",         0, 2),
        ("profit_sub", "Profit from Sub-Threshold", 1, 0),
        ("pct_profit", "% of Total Profit",         1, 1),
        ("verdict",    "Verdict",                   1, 2),
    ]:
        cell = tk.Frame(p7_grid, bg="white")
        cell.grid(row=row, column=col, padx=16, pady=8, sticky="w")
        tk.Label(cell, text=title, bg="white", fg="#888888",
                 font=("Segoe UI", 8)).pack(anchor="w")
        lbl = tk.Label(cell, text="—", bg="white", fg="#1a1a2a",
                       font=("Segoe UI", 13, "bold"))
        lbl.pack(anchor="w")
        _p7_labels[key] = lbl

    _sep(p7_hold_results)

    tk.Label(p7_hold_results, text="Hold Time Distribution",
             bg="white", fg="#1a1a2a", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16, pady=(0, 4))
    tk.Label(p7_hold_results,
             text="Red bars = trades below the threshold.  Blue = pass.  Dashed line = your minimum.",
             bg="white", fg="#888888", font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(0, 6))
    p7_fig = Figure(figsize=(7, 2.6), dpi=90)
    p7_fig.patch.set_facecolor("white")
    p7_ax = p7_fig.add_subplot(111)
    p7_canvas = FigureCanvasTkAgg(p7_fig, master=p7_hold_results)
    p7_canvas.get_tk_widget().pack(fill="x", padx=16, pady=(0, 16))

    # ═══════════════════════════════════════════════════════════════════════════
    # CARD 2 — Drawdown Rules
    # ═══════════════════════════════════════════════════════════════════════════
    p7_dd_card = tk.Frame(frame, bg="white", bd=1, relief="solid")
    p7_dd_card.pack(fill="x", padx=20, pady=(0, 20))

    tk.Label(p7_dd_card, text="Drawdown Rules", bg="white", fg="#1a1a2a",
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 6))

    p7_firm_row = tk.Frame(p7_dd_card, bg="white")
    p7_firm_row.pack(fill="x", padx=16, pady=(0, 10))
    tk.Label(p7_firm_row, text="Prop Firm:", bg="white",
             font=("Segoe UI", 10, "bold")).pack(side="left")
    firm_var = tk.StringVar(value="")
    p7_firm_combo = ttk.Combobox(p7_firm_row, textvariable=firm_var, values=["Levereged"],
                                  width=16, state="readonly")
    p7_firm_combo.pack(side="left", padx=(6, 0))
    tk.Label(p7_firm_row, text="  — more firms coming", bg="white", fg="#aaaaaa",
             font=("Segoe UI", 9)).pack(side="left", padx=(8, 0))

    # firm rules area — shown when firm is selected
    p7_firm_rules_frame = tk.Frame(p7_dd_card, bg="white")

    _lev_trailing_lines = [
        ("Trailing Drawdown — how the floor works", True),
        ("The floor starts DD% below your starting balance and rises as your CLOSED balance", False),
        ("hits new highs (high-water mark). But the floor can NEVER go above your starting", False),
        ("balance — once your closed balance has gained DD%, the floor locks forever.", False),
        ("", False),
        ("Example  ($100,000 account, 6% DD):", True),
        ("  Start:            floor = $94,000   ( = $100k - $6k )", False),
        ("  Close bal $104k:  floor = $98,000   ( = $104k - $6k )", False),
        ("  Close bal $106k:  floor = $100,000  ( LOCKED — $106k - $6k = $100k = start )", False),
        ("  Close bal $110k:  floor = $100,000  ( still locked, never moves again )", False),
        ("", False),
        ("Breach = your equity drops below the floor at ANY moment.", False),
    ]
    _rule_box(p7_firm_rules_frame, _lev_trailing_lines)

    _lev_daily_lines = [
        ("Daily Drawdown — end-of-day snapshot rule", True),
        ("At 23:00 GMT+3 each day, Levereged records max(balance, equity).", False),
        ("This becomes the reference for the next day's allowed drawdown.", False),
        ("Daily limit = Daily DD% of STARTING balance (a fixed dollar amount, never changes).", False),
        ("", False),
        ("Example  ($100,000 account, 5% daily DD  =  $5,000 limit):", True),
        ("  Losing open trade:   balance $105k, equity $104k  ->  reference = $105k (balance wins)", False),
        ("  Winning open trade:  balance $105k, equity $106k  ->  reference = $106k (equity wins)", False),
        ("", False),
        ("Note: this panel uses closed-trade P&L per day as an approximation.", False),
        ("Open-position equity at 23:00 GMT+3 is not available in exported data.", False),
    ]
    _rule_box(p7_firm_rules_frame, _lev_daily_lines)

    # inputs + Calculate
    p7_dd_row = tk.Frame(p7_dd_card, bg="white")
    total_dd_var  = tk.StringVar(value="")
    daily_dd_var  = tk.StringVar(value="")
    lock_gain_var = tk.StringVar(value="6")
    tk.Label(p7_dd_row, text="Trailing DD %:", bg="white",
             font=("Segoe UI", 10)).pack(side="left")
    tk.Entry(p7_dd_row, textvariable=total_dd_var, width=5, font=("Segoe UI", 10),
             bd=1, relief="solid").pack(side="left", padx=(6, 2))
    tk.Label(p7_dd_row, text="%", bg="white", fg="#666666",
             font=("Segoe UI", 10)).pack(side="left")
    tk.Label(p7_dd_row, text="   Daily DD %:", bg="white",
             font=("Segoe UI", 10)).pack(side="left")
    tk.Entry(p7_dd_row, textvariable=daily_dd_var, width=5, font=("Segoe UI", 10),
             bd=1, relief="solid").pack(side="left", padx=(6, 2))
    tk.Label(p7_dd_row, text="%  (0 = no daily limit)",
             bg="white", fg="#aaaaaa", font=("Segoe UI", 9)).pack(side="left", padx=(2, 0))
    tk.Label(p7_dd_row, text="   Lock at gain %:", bg="white",
             font=("Segoe UI", 10)).pack(side="left")
    tk.Entry(p7_dd_row, textvariable=lock_gain_var, width=4, font=("Segoe UI", 10),
             bd=1, relief="solid").pack(side="left", padx=(6, 0))
    tk.Button(p7_dd_row, text="Calculate", font=("Segoe UI", 10, "bold"),
              bg="#e94560", fg="white", bd=0, padx=14, pady=6,
              activebackground="#e94560", activeforeground="white",
              command=lambda: _run_drawdown()).pack(side="left", padx=(20, 0))

    firm_var.trace_add("write", _on_firm_change)

    # results section — hidden until Calculate is pressed
    p7_dd_results = tk.Frame(p7_dd_card, bg="white")

    _sep(p7_dd_results)

    p7_dd_sgrid = tk.Frame(p7_dd_results, bg="white")
    p7_dd_sgrid.pack(fill="x", padx=16, pady=(0, 4))
    for _key, _title, _row, _col in [
        ("worst_day",     "Worst Day Loss",             0, 0),
        ("days_breached", "Days Breached Daily Limit",  0, 1),
        ("max_total_dd",  "Closest to Floor (worst)",   0, 2),
        ("current_dd",    "Current Buffer above Floor", 1, 0),
        ("dd_type_shown", "Floor Locked Since",         1, 1),
        ("dd_verdict",    "Verdict",                    1, 2),
    ]:
        _cell = tk.Frame(p7_dd_sgrid, bg="white")
        _cell.grid(row=_row, column=_col, padx=16, pady=8, sticky="w")
        _tlbl = tk.Label(_cell, text=_title, bg="white", fg="#888888",
                         font=("Segoe UI", 8))
        _tlbl.pack(anchor="w")
        _p7_dd_title_labels[_key] = _tlbl
        _lbl = tk.Label(_cell, text="—", bg="white", fg="#1a1a2a",
                        font=("Segoe UI", 13, "bold"))
        _lbl.pack(anchor="w")
        _p7_dd_labels[_key] = _lbl

    _sep(p7_dd_results)

    tk.Label(p7_dd_results, text="Daily P&L vs Daily Drawdown Limit",
             bg="white", fg="#1a1a2a", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16, pady=(0, 4))
    tk.Label(p7_dd_results,
             text="Each bar = one day's closed P&L.  Dark-red bar = daily limit breached that day.  Hover for details.",
             bg="white", fg="#888888", font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(0, 6))
    p7_dd_fig1 = Figure(figsize=(7, 2.6), dpi=90)
    p7_dd_fig1.patch.set_facecolor("white")
    p7_dd_ax1 = p7_dd_fig1.add_subplot(111)
    p7_dd_c1  = FigureCanvasTkAgg(p7_dd_fig1, master=p7_dd_results)
    p7_dd_c1.get_tk_widget().pack(fill="x", padx=16, pady=(0, 8))

    _sep(p7_dd_results)

    tk.Label(p7_dd_results, text="Closed Balance vs DD Floor",
             bg="white", fg="#1a1a2a", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16, pady=(0, 4))
    tk.Label(p7_dd_results,
             text="Blue = your closed balance.  Red dashed = the DD floor (rises then locks).  "
                  "Green = safe buffer.  Orange line = where the floor locked.  Hover for details.",
             bg="white", fg="#888888", font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(0, 6))
    p7_dd_fig2 = Figure(figsize=(7, 2.6), dpi=90)
    p7_dd_fig2.patch.set_facecolor("white")
    p7_dd_ax2 = p7_dd_fig2.add_subplot(111)
    p7_dd_c2  = FigureCanvasTkAgg(p7_dd_fig2, master=p7_dd_results)
    p7_dd_c2.get_tk_widget().pack(fill="x", padx=16, pady=(0, 16))

    return frame


# ─────────────────────────────────────────────────────────────────────────────
# REFRESH
# ─────────────────────────────────────────────────────────────────────────────

def refresh():
    if p7_hold_results.winfo_ismapped():
        build_compliance_chart()
    if p7_dd_results.winfo_ismapped():
        build_drawdown_charts()
