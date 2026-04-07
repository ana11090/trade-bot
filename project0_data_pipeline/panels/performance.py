import tkinter as tk
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import state
import helpers

MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun",
               "Jul","Aug","Sep","Oct","Nov","Dec"]

# ── Module-level refs set in build_panel() ────────────────────────────────────
eq_fig           = None
eq_ax            = None
eq_canvas_widget = None

dd_fig           = None
dd_ax            = None
dd_canvas_widget = None
dd_back_btn      = None
dd_breadcrumb    = None

drilldown_state = {"level": "year", "year": None, "month": None}
dd_annot_holder = [None]
dd_bar_data     = []
_dd_bar_groups  = []


# ─────────────────────────────────────────────────────────────────────────────
# CHART BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def build_equity_chart():
    df = helpers.get_scaled_df()
    eq_ax.clear()
    eq_ax.set_facecolor("#fafafa")
    if df is None:
        eq_ax.text(0.5, 0.5, "No data — run the pipeline first",
                   ha="center", va="center", transform=eq_ax.transAxes, color="#aaaaaa")
    else:
        try:
            balance = float(state.starting_balance.get())
        except ValueError:
            balance = 0.0
        df         = df.sort_values("open_dt").reset_index(drop=True)
        cumulative = df["profit_scaled"].cumsum() + balance
        x          = list(range(len(cumulative)))
        eq_ax.plot(x, cumulative, color="#e94560", linewidth=1.4)
        eq_ax.fill_between(x, balance, cumulative,
                           where=(cumulative >= balance), alpha=0.08, color="#27ae60")
        eq_ax.fill_between(x, balance, cumulative,
                           where=(cumulative < balance),  alpha=0.08, color="#e94560")
        eq_ax.axhline(balance, color="#cccccc", linewidth=0.8, linestyle="--")
        eq_ax.set_ylabel("Balance (USD)", fontsize=9)
        eq_ax.set_title("Equity Curve", fontsize=10)
        eq_ax.grid(True, alpha=0.25)
        n       = len(df)
        n_ticks = min(8, n)
        indices = [int(i * (n - 1) / (n_ticks - 1)) for i in range(n_ticks)] if n_ticks > 1 else [0]
        eq_ax.set_xticks(indices)
        eq_ax.set_xticklabels(
            [f"#{i}\n{df['open_dt'].iloc[i].strftime('%b %Y') if pd.notna(df['open_dt'].iloc[i]) else ''}"
             for i in indices],
            fontsize=7.5, ha="center"
        )
        eq_ax.tick_params(axis="y", labelsize=8)

        _eq_x   = list(x)
        _eq_bal = list(cumulative)
        _eq_df  = df
        _eq_dep = balance
        eq_annot = helpers._make_annot(eq_ax)

        def on_eq_hover(event, _ax=eq_ax, _ann=eq_annot,
                        _ex=_eq_x, _eb=_eq_bal, _edf=_eq_df, _dep=_eq_dep):
            if event.inaxes != _ax or not _ex:
                _ann.set_visible(False)
                eq_canvas_widget.draw_idle()
                return
            idx = min(range(len(_ex)), key=lambda i: abs(_ex[i] - event.xdata))
            bal = _eb[idx]
            pct = (bal - _dep) / _dep * 100 if _dep > 0 else 0   # CHANGED: April 2026 — reject non-positive deposit
            sign = "+" if pct >= 0 else ""
            date_str = ""
            try:
                date_str = f"\n{_edf['open_dt'].iloc[idx].strftime('%d %b %Y')}"
            except Exception:
                pass
            _ann.xy = (_ex[idx], bal)
            _ann.set_text(f"Trade #{idx}{date_str}\nBalance: ${bal:,.2f}\n{sign}{pct:.2f}% from deposit")
            _ann.set_visible(True)
            eq_canvas_widget.draw_idle()

        helpers._reconnect(eq_canvas_widget, "motion_notify_event", on_eq_hover)

    eq_fig.tight_layout(pad=1.2)
    eq_canvas_widget.draw()


def _rebuild_drilldown_keys():
    """Return ordered list of keys (year/month/day) for the current drilldown level."""
    df = helpers.get_scaled_df()
    if df is None:
        return []
    level = drilldown_state["level"]
    if level == "year":
        grouped = df.dropna(subset=["open_dt"]).groupby(
            df["open_dt"].dt.year)["profit_scaled"].sum()
        return list(grouped.index)
    elif level == "month":
        year = drilldown_state["year"]
        mask = df["open_dt"].dt.year == year
        sub  = df[mask].dropna(subset=["open_dt"])
        grouped = sub.groupby(sub["open_dt"].dt.month)["profit_scaled"].sum()
        return list(grouped.index)
    elif level == "day":
        return []
    return []


def build_drilldown_chart():
    df = helpers.get_scaled_df()
    dd_ax.clear()
    dd_ax.set_facecolor("#fafafa")
    dd_bar_data.clear()

    if df is None:
        dd_ax.text(0.5, 0.5, "No data — run the pipeline first",
                   ha="center", va="center", transform=dd_ax.transAxes, color="#aaaaaa")
        dd_canvas_widget.draw()
        return

    try:
        deposit = float(state.starting_balance.get())
    except ValueError:
        deposit = 0.0

    level   = drilldown_state["level"]
    df_all  = df.dropna(subset=["open_dt"]).sort_values("open_dt")

    if level == "year":
        grouped = df_all.groupby(df_all["open_dt"].dt.year)["profit_scaled"].sum()
        labels  = [str(y) for y in grouped.index]
        values  = grouped.values
        title   = "Profit by Year — click a bar to see months"
        dd_breadcrumb.configure(text="Year")
        dd_back_btn.pack_forget()
        running = deposit
        for v in values:
            dd_bar_data.append((v, running))
            running += v

    elif level == "month":
        year    = drilldown_state["year"]
        mask    = df["open_dt"].dt.year == year
        sub     = df[mask].dropna(subset=["open_dt"])
        grouped = sub.groupby(sub["open_dt"].dt.month)["profit_scaled"].sum()
        labels  = [MONTH_NAMES[m - 1] for m in grouped.index]
        values  = grouped.values
        title   = f"Profit by Month — {year} — click a bar to see days"
        dd_breadcrumb.configure(text=f"Year  ›  {year}")
        dd_back_btn.pack(side="right")
        all_years = df_all.groupby(df_all["open_dt"].dt.year)["profit_scaled"].sum()
        running   = deposit + sum(v for y, v in all_years.items() if y < year)
        for v in values:
            dd_bar_data.append((v, running))
            running += v

    elif level == "day":
        year    = drilldown_state["year"]
        month   = drilldown_state["month"]
        mask    = (df["open_dt"].dt.year == year) & (df["open_dt"].dt.month == month)
        sub     = df[mask].dropna(subset=["open_dt"])
        grouped = sub.groupby(sub["open_dt"].dt.day)["profit_scaled"].sum()
        labels  = [str(d) for d in grouped.index]
        values  = grouped.values
        title   = f"Profit by Day — {MONTH_NAMES[month - 1]} {year}"
        dd_breadcrumb.configure(text=f"Year  ›  {year}  ›  {MONTH_NAMES[month - 1]}")
        dd_back_btn.pack(side="right")
        all_years = df_all.groupby(df_all["open_dt"].dt.year)["profit_scaled"].sum()
        running   = deposit + sum(v for y, v in all_years.items() if y < year)
        yr_mask   = df_all["open_dt"].dt.year == year
        all_months = df_all[yr_mask].groupby(df_all[yr_mask]["open_dt"].dt.month)["profit_scaled"].sum()
        running  += sum(v for m, v in all_months.items() if m < month)
        for v in values:
            dd_bar_data.append((v, running))
            running += v

    colors = ["#27ae60" if v >= 0 else "#e94560" for v in values]
    dd_ax.bar(labels, values, color=colors, zorder=2)
    dd_ax.axhline(0, color="#cccccc", linewidth=0.8)
    dd_ax.set_ylabel("Profit (USD)", fontsize=9)
    dd_ax.set_title(title, fontsize=10)
    dd_ax.grid(axis="y", alpha=0.25, zorder=0)
    dd_ax.tick_params(labelsize=8)

    annot = dd_ax.annotate(
        "", xy=(0, 0), xytext=(10, 12), textcoords="offset points",
        bbox=dict(boxstyle="round,pad=0.45", fc="white", ec="#bbbbbb", lw=0.8, alpha=0.95),
        fontsize=9, zorder=10
    )
    annot.set_visible(False)
    dd_annot_holder[0] = annot

    dd_fig.tight_layout(pad=1.2)
    dd_canvas_widget.draw()


def on_dd_click(event):
    if event.inaxes != dd_ax:
        return
    level = drilldown_state["level"]
    if level == "day":
        return
    keys = _rebuild_drilldown_keys()
    for i, bar in enumerate(dd_ax.patches):
        if bar.contains(event)[0] and i < len(keys):
            if level == "year":
                drilldown_state["level"] = "month"
                drilldown_state["year"]  = keys[i]
            elif level == "month":
                drilldown_state["level"] = "day"
                drilldown_state["month"] = keys[i]
            build_drilldown_chart()
            return


def on_dd_back():
    level = drilldown_state["level"]
    if level == "day":
        drilldown_state["level"] = "month"
        drilldown_state["month"] = None
    elif level == "month":
        drilldown_state["level"] = "year"
        drilldown_state["year"]  = None
        drilldown_state["month"] = None
    build_drilldown_chart()


def on_dd_hover(event):
    annot = dd_annot_holder[0]
    if annot is None:
        return
    if event.inaxes != dd_ax:
        if annot.get_visible():
            annot.set_visible(False)
            dd_canvas_widget.draw_idle()
        return
    for i, bar in enumerate(dd_ax.patches):
        if bar.contains(event)[0]:
            val = bar.get_height()
            if i < len(dd_bar_data):
                _, start_bal = dd_bar_data[i]
                pct = (val / start_bal * 100) if start_bal != 0 else 0
            else:
                pct = 0.0
            sign = "+" if val >= 0 else ""
            annot.xy = (bar.get_x() + bar.get_width() / 2, val)
            annot.set_text(f"{sign}{val:.2f} USD\n{sign}{pct:.2f}% of period start")
            annot.set_visible(True)
            dd_canvas_widget.draw_idle()
            return
    if annot.get_visible():
        annot.set_visible(False)
        dd_canvas_widget.draw_idle()


# ─────────────────────────────────────────────────────────────────────────────
# PANEL BUILD
# ─────────────────────────────────────────────────────────────────────────────

def build_panel(content):
    global eq_fig, eq_ax, eq_canvas_widget
    global dd_fig, dd_ax, dd_canvas_widget, dd_back_btn, dd_breadcrumb

    frame = tk.Frame(content, bg="#f0f2f5")

    tk.Label(frame, text="Performance", bg="#f0f2f5", fg="#1a1a2a",
             font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(24, 2))
    tk.Label(frame, text="Equity curve and profit breakdown by time period. "
             "Click bars to drill down: Year → Month → Day.",
             bg="#f0f2f5", fg="#666666", font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(0, 16))

    # ── Equity Curve card ──────────────────────────────────────────────────────
    eq_card = tk.Frame(frame, bg="white", bd=1, relief="solid")
    eq_card.pack(fill="x", padx=20, pady=(0, 10))
    tk.Label(eq_card, text="Equity Curve", bg="white", fg="#1a1a2a",
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
    tk.Label(eq_card,
             text="Shows how your account balance grew or shrank trade by trade. "
                  "The line starts at your initial deposit. "
                  "Green shading = above starting balance. Red shading = below starting balance. "
                  "Dashed line = your initial deposit.",
             bg="white", fg="#888888", font=("Segoe UI", 9),
             wraplength=600, justify="left").pack(anchor="w", padx=16, pady=(0, 8))

    eq_fig = Figure(figsize=(7, 2.8), dpi=90)
    eq_ax  = eq_fig.add_subplot(111)
    eq_fig.patch.set_facecolor("white")
    eq_canvas_widget = FigureCanvasTkAgg(eq_fig, master=eq_card)
    eq_canvas_widget.get_tk_widget().pack(fill="x", padx=16, pady=(0, 14))

    # ── Drilldown card ─────────────────────────────────────────────────────────
    dd_card = tk.Frame(frame, bg="white", bd=1, relief="solid")
    dd_card.pack(fill="x", padx=20, pady=(0, 20))

    dd_header = tk.Frame(dd_card, bg="white")
    dd_header.pack(fill="x", padx=16, pady=(14, 0))
    tk.Label(dd_header, text="Profit Breakdown", bg="white", fg="#1a1a2a",
             font=("Segoe UI", 11, "bold")).pack(side="left")
    dd_breadcrumb = tk.Label(dd_header, text="Year", bg="white", fg="#888888",
                              font=("Segoe UI", 10))
    dd_breadcrumb.pack(side="left", padx=(12, 0))
    dd_back_btn = tk.Button(dd_header, text="← Back", font=("Segoe UI", 10),
                             bd=1, relief="solid", padx=10, pady=3,
                             activebackground="white", activeforeground="black")

    tk.Label(dd_card,
             text="Total profit per time period. Green = profit, red = loss. "
                  "Click any bar to drill down: Year → Month → Day. "
                  "Hover over a bar to see the value in USD and the return % for that period. "
                  "The % is calculated as profit ÷ account balance at the start of that period — "
                  "the same way prop firms and fund managers report it, not relative to the initial deposit.",
             bg="white", fg="#888888", font=("Segoe UI", 9),
             wraplength=600, justify="left").pack(anchor="w", padx=16, pady=(6, 0))

    dd_fig = Figure(figsize=(7, 2.8), dpi=90)
    dd_ax  = dd_fig.add_subplot(111)
    dd_fig.patch.set_facecolor("white")
    dd_canvas_widget = FigureCanvasTkAgg(dd_fig, master=dd_card)
    dd_canvas_widget.get_tk_widget().pack(fill="x", padx=16, pady=(8, 14))

    # wire up back button and click/hover events now that all refs are set
    dd_back_btn.configure(command=on_dd_back)
    dd_fig.canvas.mpl_connect("button_press_event", on_dd_click)
    dd_fig.canvas.mpl_connect("motion_notify_event", on_dd_hover)

    return frame


# ─────────────────────────────────────────────────────────────────────────────
# REFRESH
# ─────────────────────────────────────────────────────────────────────────────

def refresh():
    drilldown_state["level"] = "year"
    drilldown_state["year"]  = None
    drilldown_state["month"] = None
    build_equity_chart()
    build_drilldown_chart()
