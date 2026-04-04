import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk

import state
from sidebar import build_sidebar
from helpers import make_copyable
from project0_data_pipeline.panels import (
    pipeline, performance, statistics, risk_flags, cost_spread
)
from project0_data_pipeline.panels import prop_compliance_v2 as prop_compliance
from project0_data_pipeline.panels import prop_explorer
from project0_data_pipeline.panels import compare_histories
from project0_data_pipeline.panels import lifecycle_simulator
from project0_data_pipeline.probabilities.panels import (
    account_survival, expected_value, breakeven, kelly, streaks, drawdown_recovery
)
from project1_reverse_engineering.panels import (
    configuration, run_scenarios, results, robot_analysis,
    strategy_builder as p1_strategy_builder,
    xgboost_panel as p1_xgboost,
)
from project2_backtesting.panels import (
    configuration as p2_configuration,
    run_backtest_panel as p2_run_backtest,
    view_results as p2_view_results,
    prop_firm_test as p2_prop_test,
    strategy_refiner_panel as p2_refiner,
    strategy_validator_panel as p2_validator,
    saved_rules_panel as p2_saved,
    strategy_playground as p2_playground,
)
from project3_live_trading.panels import (
    ea_generator_panel as p3_generator,
    live_monitor_panel as p3_monitor,
)
from project4_strategy_creation.panels import (
    scratch_panel as p4_scratch,
)

# ─────────────────────────────────────────────────────────────────────────────
# MAIN WINDOW
# ─────────────────────────────────────────────────────────────────────────────
window = tk.Tk()
window.title("Trade Bot")
window.geometry("900x680")
window.lift()  # Bring window to front
window.attributes('-topmost', True)  # Make window appear on top
window.after_idle(window.attributes, '-topmost', False)  # Disable topmost after appearing
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


# Throttled resize — avoid recalculating scrollregion on every widget create
_resize_after = [None]

def _update_scrollregion():
    canvas.configure(scrollregion=canvas.bbox("all"))
    _resize_after[0] = None

def on_content_resize(event):
    if _resize_after[0]:
        window.after_cancel(_resize_after[0])
    _resize_after[0] = window.after(16, _update_scrollregion)

def on_canvas_resize(event):
    canvas.itemconfig(content_window, width=event.width, height=event.height)

content.bind("<Configure>", on_content_resize)
canvas.bind("<Configure>",  on_canvas_resize)


# ─────────────────────────────────────────────────────────────────────────────
# SMART SCROLL ROUTER — one handler, routes to nearest scrollable Canvas
# ─────────────────────────────────────────────────────────────────────────────
def _route_scroll(event):
    """Walk widget hierarchy to find the innermost scrollable Canvas."""
    delta = int(-1 * (event.delta / 120)) if event.delta else 0
    if not delta:
        return
    w = event.widget
    while w is not None:
        if isinstance(w, tk.Canvas):
            try:
                y0, y1 = w.yview()
                if y0 > 0.0 or y1 < 1.0:
                    w.yview_scroll(delta, "units")
                    return
            except tk.TclError:
                pass
        w = getattr(w, 'master', None)
    canvas.yview_scroll(delta, "units")

def _route_scroll_up(event):
    w = event.widget
    while w is not None:
        if isinstance(w, tk.Canvas):
            try:
                y0, y1 = w.yview()
                if y0 > 0.0 or y1 < 1.0:
                    w.yview_scroll(-3, "units"); return
            except tk.TclError:
                pass
        w = getattr(w, 'master', None)
    canvas.yview_scroll(-3, "units")

def _route_scroll_down(event):
    w = event.widget
    while w is not None:
        if isinstance(w, tk.Canvas):
            try:
                y0, y1 = w.yview()
                if y0 > 0.0 or y1 < 1.0:
                    w.yview_scroll(3, "units"); return
            except tk.TclError:
                pass
        w = getattr(w, 'master', None)
    canvas.yview_scroll(3, "units")


# ─────────────────────────────────────────────────────────────────────────────
# COPYABLE LABELS — applied per-panel when built, not all upfront
# ─────────────────────────────────────────────────────────────────────────────
def _apply_copyable(widget):
    if isinstance(widget, tk.Label):
        make_copyable(widget)
    for child in widget.winfo_children():
        _apply_copyable(child)


# ─────────────────────────────────────────────────────────────────────────────
# POP-OUT CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
# Mapping of panel keys to their display titles and raw builder functions (for pop-out)
_POPOUT_CONFIG = {}  # Will be populated below


# ─────────────────────────────────────────────────────────────────────────────
# LAZY PANEL BUILDER — wraps each builder to apply copyable + re-assert scroll
# ─────────────────────────────────────────────────────────────────────────────
def _make_builder(build_fn, panel_key=None):
    def _build():
        panel = build_fn()
        _apply_copyable(panel)
        # Re-assert smart scroll router: panel's build_panel may call bind_all
        window.bind_all("<MouseWheel>", _route_scroll)
        window.bind_all("<Button-4>", _route_scroll_up)
        window.bind_all("<Button-5>", _route_scroll_down)

        # Add pop-out button if configured
        if panel_key and panel_key in _POPOUT_CONFIG:
            from shared.popout import add_popout_button
            config = _POPOUT_CONFIG[panel_key]
            try:
                add_popout_button(panel, config['title'], config['builder'])
            except Exception:
                pass  # Some panels may not support it

        return panel
    return _build


# ─────────────────────────────────────────────────────────────────────────────
# REGISTER LAZY PANEL BUILDERS — panels built on first access
# ─────────────────────────────────────────────────────────────────────────────
# Configure pop-out for selected panels (title and raw builder function)
_POPOUT_CONFIG = {
    "pipeline":          {"title": "Data Pipeline",       "builder": pipeline.build_panel},
    "panel4":            {"title": "Performance",         "builder": performance.build_panel},
    "panel5":            {"title": "Statistics",          "builder": statistics.build_panel},
    "panel6":            {"title": "Risk & Flags",        "builder": risk_flags.build_panel},
    "panel7":            {"title": "Prop Compliance",     "builder": prop_compliance.build_panel},
    "panel8":            {"title": "Cost & Spread",       "builder": cost_spread.build_panel},
    "p1_config":         {"title": "P1 Configuration",    "builder": configuration.build_panel},
    "p1_run":            {"title": "P1 Run Scenarios",    "builder": run_scenarios.build_panel},
    "p1_results":        {"title": "P1 Results",          "builder": results.build_panel},
    "p1_analysis":       {"title": "Robot Analysis",      "builder": robot_analysis.build_panel},
    "p1_xgboost":        {"title": "P1 XGBoost",          "builder": p1_xgboost.build_panel},
    "p1_search":         {"title": "Strategy Search",     "builder": p1_strategy_builder.build_panel},
    "p2_config":         {"title": "P2 Configuration",    "builder": p2_configuration.build_panel},
    "p2_run":            {"title": "Run Backtest",        "builder": p2_run_backtest.build_panel},
    "p2_results":        {"title": "View Results",        "builder": p2_view_results.build_panel},
    "p2_prop_test":      {"title": "Prop Firm Test",      "builder": p2_prop_test.build_panel},
    "p2_refiner":        {"title": "Strategy Refiner",    "builder": p2_refiner.build_panel},
    "p2_validator":      {"title": "Strategy Validator",  "builder": p2_validator.build_panel},
    "p2_saved":          {"title": "Saved Rules",         "builder": p2_saved.build_panel},
    "p2_playground":     {"title": "Strategy Playground", "builder": p2_playground.build_panel},
    "p3_generator":      {"title": "EA Generator",        "builder": p3_generator.build_panel},
    "p3_monitor":        {"title": "Live Monitor",        "builder": p3_monitor.build_panel},
    "p4_scratch":        {"title": "Build from Scratch",  "builder": p4_scratch.build_panel},
    "prop_explorer":     {"title": "Prop Explorer",       "builder": prop_explorer.build_panel},
    "compare_histories": {"title": "Compare Histories",   "builder": compare_histories.build_panel},
    "lifecycle_sim":     {"title": "Lifecycle Simulator", "builder": lifecycle_simulator.build_panel},
}

state.panel_builders = {
    "pipeline":          _make_builder(lambda: pipeline.build_panel(content), "pipeline"),
    "panel4":            _make_builder(lambda: performance.build_panel(content), "panel4"),
    "panel5":            _make_builder(lambda: statistics.build_panel(content), "panel5"),
    "panel6":            _make_builder(lambda: risk_flags.build_panel(content), "panel6"),
    "panel7":            _make_builder(lambda: prop_compliance.build_panel(content), "panel7"),
    "panel8":            _make_builder(lambda: cost_spread.build_panel(content), "panel8"),
    "account_survival":  _make_builder(lambda: account_survival.build_panel(content)),
    "expected_value":    _make_builder(lambda: expected_value.build_panel(content)),
    "breakeven":         _make_builder(lambda: breakeven.build_panel(content)),
    "kelly":             _make_builder(lambda: kelly.build_panel(content)),
    "streaks":           _make_builder(lambda: streaks.build_panel(content)),
    "drawdown_recovery": _make_builder(lambda: drawdown_recovery.build_panel(content)),
    "p1_config":         _make_builder(lambda: configuration.build_panel(content), "p1_config"),
    "p1_run":            _make_builder(lambda: run_scenarios.build_panel(content), "p1_run"),
    "p1_results":        _make_builder(lambda: results.build_panel(content), "p1_results"),
    "p1_analysis":       _make_builder(lambda: robot_analysis.build_panel(content), "p1_analysis"),
    "p1_xgboost":        _make_builder(lambda: p1_xgboost.build_panel(content), "p1_xgboost"),
    "p1_search":         _make_builder(lambda: p1_strategy_builder.build_panel(content), "p1_search"),
    "p2_config":         _make_builder(lambda: p2_configuration.build_panel(content), "p2_config"),
    "p2_run":            _make_builder(lambda: p2_run_backtest.build_panel(content), "p2_run"),
    "p2_results":        _make_builder(lambda: p2_view_results.build_panel(content), "p2_results"),
    "p2_prop_test":      _make_builder(lambda: p2_prop_test.build_panel(content), "p2_prop_test"),
    "p2_refiner":        _make_builder(lambda: p2_refiner.build_panel(content), "p2_refiner"),
    "p2_validator":      _make_builder(lambda: p2_validator.build_panel(content), "p2_validator"),
    "p2_saved":          _make_builder(lambda: p2_saved.build_panel(content), "p2_saved"),
    "p2_playground":     _make_builder(lambda: p2_playground.build_panel(content), "p2_playground"),
    "p3_generator":      _make_builder(lambda: p3_generator.build_panel(content), "p3_generator"),
    "p3_monitor":        _make_builder(lambda: p3_monitor.build_panel(content), "p3_monitor"),
    "p4_scratch":        _make_builder(lambda: p4_scratch.build_panel(content), "p4_scratch"),
    "prop_explorer":     _make_builder(lambda: prop_explorer.build_panel(content), "prop_explorer"),
    "compare_histories": _make_builder(lambda: compare_histories.build_panel(content), "compare_histories"),
    "lifecycle_sim":     _make_builder(lambda: lifecycle_simulator.build_panel(content), "lifecycle_sim"),
}

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
    "p1_analysis": robot_analysis.refresh,
    "p1_xgboost": p1_xgboost.refresh,
    "p1_search": p1_strategy_builder.refresh,
    "p2_config": p2_configuration.refresh,
    "p2_run": p2_run_backtest.refresh,
    "p2_results": p2_view_results.refresh,
    "p2_prop_test": p2_prop_test.refresh,
    "p2_refiner":    p2_refiner.refresh,
    "p2_validator":  p2_validator.refresh,
    "p2_saved":      p2_saved.refresh,
    "p2_playground": p2_playground.refresh,
    "p3_generator":  p3_generator.refresh,
    "p3_monitor":    p3_monitor.refresh,
    "p4_scratch":    p4_scratch.refresh,
    "prop_explorer":     prop_explorer.refresh,
    "compare_histories": compare_histories.refresh,
    "lifecycle_sim":     lifecycle_simulator.refresh,
}
show_panel = build_sidebar(window, canvas, refresh_map)

# Register smart scroll router AFTER sidebar (overrides any bind_all from panels)
window.bind_all("<MouseWheel>", _route_scroll)
window.bind_all("<Button-4>", _route_scroll_up)
window.bind_all("<Button-5>", _route_scroll_down)

# ─────────────────────────────────────────────────────────────────────────────
# START
# ─────────────────────────────────────────────────────────────────────────────
show_panel("pipeline")

window.mainloop()
