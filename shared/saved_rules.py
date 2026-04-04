"""
Saved Rules — bookmark any rule from anywhere in the app.
Rules are saved to saved_rules.json and can be loaded, deleted, or sent to backtester.
"""

import os
import json
from datetime import datetime

_SAVE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'saved_rules.json')


def load_all():
    """Load all saved rules. Returns list of dicts."""
    if os.path.exists(_SAVE_PATH):
        try:
            with open(_SAVE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_rule(rule, source="unknown", notes=""):
    """
    Save a rule for later.

    Args:
        rule: dict with 'conditions', 'prediction', 'win_rate', etc.
        source: where it came from ("Robot Analysis", "XGBoost", "Scratch", "Backtest Result", etc.)
        notes: optional user note
    """
    all_rules = load_all()

    entry = {
        "id": len(all_rules) + 1,
        "saved_at": datetime.now().isoformat(),
        "source": source,
        "notes": notes,
        "rule": rule,
    }

    all_rules.append(entry)

    with open(_SAVE_PATH, 'w', encoding='utf-8') as f:
        json.dump(all_rules, f, indent=2, default=str)

    return entry["id"]


def delete_rule(rule_id):
    """Delete a saved rule by ID."""
    all_rules = load_all()
    all_rules = [r for r in all_rules if r.get("id") != rule_id]

    with open(_SAVE_PATH, 'w', encoding='utf-8') as f:
        json.dump(all_rules, f, indent=2, default=str)


def delete_all():
    """Delete all saved rules."""
    with open(_SAVE_PATH, 'w', encoding='utf-8') as f:
        json.dump([], f)


def export_to_report(rule_ids=None):
    """
    Export saved rules to analysis_report.json format.
    If rule_ids is None, exports all saved rules.
    """
    all_rules = load_all()

    if rule_ids:
        selected = [r for r in all_rules if r.get("id") in rule_ids]
    else:
        selected = all_rules

    return [r["rule"] for r in selected]


def build_save_button(parent, rule, source="unknown", bg="#ffffff"):
    """
    Create a small save/bookmark button.
    Place next to any rule display in the app.

    Args:
        parent: parent tk widget
        rule: the rule dict to save
        source: label for where it came from
        bg: background color

    Returns: the button widget
    """
    import tkinter as tk
    from tkinter import messagebox, simpledialog

    def _save():
        notes = simpledialog.askstring("Save Rule", "Add a note (optional):", parent=parent)
        if notes is None:
            notes = ""
        rule_id = save_rule(rule, source=source, notes=notes)
        messagebox.showinfo("Saved", f"Rule saved! (ID: {rule_id})\n\nView in saved rules panel.")
        btn.config(text="💾 ✓", state="disabled")

    btn = tk.Button(
        parent, text="💾 Save",
        font=("Arial", 8, "bold"),
        bg="#667eea", fg="white",
        relief=tk.FLAT, cursor="hand2",
        padx=6, pady=1,
        command=_save,
    )

    return btn
