import sys
import os

# Ensure the folder containing this file is on the Python path
# so that "import state", "import helpers" etc. work from anywhere
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk

import state
from sidebar import build_sidebar
from helpers import make_copyable
from project0_data_pipeline.panels import (
    pipeline, performance, statistics, risk_flags, prop_compliance, cost_spread
)
from project0_data_pipeline.probabilities.panels import (
    account_survival, expected_value, breakeven, kelly, streaks, drawdown_recovery
)
from project1_reverse_engineering.panels import (
    configuration, run_scenarios, results
)
from project2_backtesting.panels import (
    configuration as p2_configuration,
    run_backtest_panel as p2_run_backtest,
    view_results as p2_view_results
)

# ─────────────────────────────────────────────────────────────────────────────
# MAIN WINDOW
# ─────────────────────────────────────────────────────────────────────────────
window = tk.Tk()
window.title("Trade Bot")
window.geometry("900x680")
state.window = window

# ─────────────────────────────────────────────────────────────────────────────
# MAIN AREA — scrollable canvas
# ─────────────────────────────────────────────────────────────────────────────
main_area = tk.Frame(window, bg="#f0f2f5")
main_area.pack(side="right", fill="both", expand=True)

canvas    = tk.Canvas(main_area, bg="#f0f2f5", highlightthickness=0)
scrollbar = ttk.Scrollbar(main_area, orient="vertical", command=canvas.yview)
canvas.configure(yscrollcommand=scrollbar.set)
scrollbar.pack(side="right", fill="y")
canvas.pack(side="left", fill="both", expand=True)

content        = tk.Frame(canvas, bg="#f0f2f5")
content_window = canvas.create_window((0, 0), window=content, anchor="nw")


def on_content_resize(event):
    canvas.configure(scrollregion=canvas.bbox("all"))


def on_canvas_resize(event):
    canvas.itemconfig(content_window, width=event.width)


content.bind("<Configure>", on_content_resize)
canvas.bind("<Configure>",  on_canvas_resize)


def scroll_canvas(event):
    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


canvas.bind("<MouseWheel>", scroll_canvas)
content.bind("<MouseWheel>", scroll_canvas)

# ─────────────────────────────────────────────────────────────────────────────
# BUILD ALL PANELS
# ─────────────────────────────────────────────────────────────────────────────
state.all_panels["pipeline"]      = pipeline.build_panel(content)
state.all_panels["panel4"]        = performance.build_panel(content)
state.all_panels["panel5"]        = statistics.build_panel(content)
state.all_panels["panel6"]        = risk_flags.build_panel(content)
state.all_panels["panel7"]        = prop_compliance.build_panel(content)
state.all_panels["panel8"]        = cost_spread.build_panel(content)
state.all_panels["account_survival"]  = account_survival.build_panel(content)
state.all_panels["expected_value"]    = expected_value.build_panel(content)
state.all_panels["breakeven"]         = breakeven.build_panel(content)
state.all_panels["kelly"]             = kelly.build_panel(content)
state.all_panels["streaks"]           = streaks.build_panel(content)
state.all_panels["drawdown_recovery"] = drawdown_recovery.build_panel(content)

# Project 1 - Reverse Engineering
state.all_panels["p1_config"]  = configuration.build_panel(content)
state.all_panels["p1_run"]     = run_scenarios.build_panel(content)
state.all_panels["p1_results"] = results.build_panel(content)

# Project 2 - Backtesting
state.all_panels["p2_config"]  = p2_configuration.build_panel(content)
state.all_panels["p2_run"]     = p2_run_backtest.build_panel(content)
state.all_panels["p2_results"] = p2_view_results.build_panel(content)

# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
refresh_map = {
    "panel4": performance.refresh,
    "panel5": statistics.refresh,
    "panel6": risk_flags.refresh,
    "panel7": prop_compliance.refresh,
    "panel8": cost_spread.refresh,
    "p1_config": configuration.refresh,
    "p1_run": run_scenarios.refresh,
    "p1_results": results.refresh,
    "p2_config": p2_configuration.refresh,
    "p2_run": p2_run_backtest.refresh,
    "p2_results": p2_view_results.refresh,
}
show_panel = build_sidebar(window, canvas, refresh_map)

# ─────────────────────────────────────────────────────────────────────────────
# MAKE ALL LABELS COPYABLE
# ─────────────────────────────────────────────────────────────────────────────
def _apply_copyable(widget):
    if isinstance(widget, tk.Label):
        make_copyable(widget)
    for child in widget.winfo_children():
        _apply_copyable(child)

_apply_copyable(window)

# ─────────────────────────────────────────────────────────────────────────────
# START
# ─────────────────────────────────────────────────────────────────────────────
show_panel("pipeline")

window.mainloop()
