"""
Show trading rules reminder when a prop firm is selected.
Call from any panel that has a firm dropdown.
"""
import tkinter as tk
import os
import json
import glob

_PROP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'prop_firms')


def get_trading_rules(firm_name):
    """Load trading_rules for a firm. Returns list of rule dicts or empty list."""
    for fp in glob.glob(os.path.join(_PROP_DIR, '*.json')):
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get('firm_name') == firm_name:
                return data.get('trading_rules', [])
        except Exception:
            continue
    return []


def build_reminder_widget(parent, firm_name, stage=None, bg="#ffffff"):
    """
    Build a reminder widget showing trading rules for a firm.
    Returns a frame — pack/grid it in the parent.
    """
    rules = get_trading_rules(firm_name)
    if not rules:
        return None

    if stage:
        rules = [r for r in rules if r.get('stage') == stage.lower() or r.get('stage') == 'both']

    frame = tk.Frame(parent, bg="#fff3cd", padx=10, pady=8)

    tk.Label(frame, text=f"⚠️ {firm_name} — Special Rules:",
             font=("Arial", 9, "bold"), bg="#fff3cd", fg="#856404").pack(anchor="w")

    for rule in rules:
        txt = f"  • {rule['name']}: {rule['description']}"
        tk.Label(frame, text=txt, font=("Arial", 8), bg="#fff3cd", fg="#856404",
                 wraplength=600, justify=tk.LEFT, anchor="w").pack(anchor="w")

    return frame


def show_reminder_on_firm_change(firm_var, parent_frame, reminder_container, stage_var=None):
    """
    Bind to a firm dropdown — shows/hides reminder when firm changes.

    Args:
        firm_var: StringVar for firm dropdown
        parent_frame: parent to pack reminder into
        reminder_container: list with [current_widget] to track/destroy old reminder
        stage_var: optional StringVar for stage dropdown
    """
    def _update(*_):
        # Remove old reminder
        if reminder_container[0]:
            reminder_container[0].destroy()
            reminder_container[0] = None

        firm = firm_var.get()
        stage = stage_var.get().lower() if stage_var else None

        widget = build_reminder_widget(parent_frame, firm, stage)
        if widget:
            widget.pack(fill="x", pady=(5, 5))
            reminder_container[0] = widget

    firm_var.trace_add("write", _update)
    if stage_var:
        stage_var.trace_add("write", _update)
    _update()  # initial
