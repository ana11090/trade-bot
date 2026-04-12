"""
Results Panel for Project 1 - Reverse Engineering
View and compare scenario results
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

import state
from helpers import make_copyable


def build_panel(parent):
    """Build the results panel"""
    panel = tk.Frame(parent, bg="#f0f2f5")

    # Title
    title_frame = tk.Frame(panel, bg="white", pady=20)
    title_frame.pack(fill="x", padx=20, pady=(20, 10))

    tk.Label(title_frame, text="📊 View Results",
             bg="white", fg="#16213e",
             font=("Segoe UI", 18, "bold")).pack()

    tk.Label(title_frame, text="Compare scenarios and view discovered trading rules",
             bg="white", fg="#666",
             font=("Segoe UI", 11)).pack(pady=(5, 0))

    # Main content
    content_frame = tk.Frame(panel, bg="#f0f2f5")
    content_frame.pack(fill="both", expand=True, padx=20, pady=10)

    # Left column - Scenario comparison
    left_frame = tk.Frame(content_frame, bg="white", padx=20, pady=20)
    left_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))

    tk.Label(left_frame, text="🏆 Scenario Comparison",
             bg="white", fg="#16213e",
             font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 15))

    # Refresh button
    refresh_btn = tk.Button(left_frame, text="🔄 Refresh Results",
                           bg="#3498db", fg="white",
                           font=("Segoe UI", 10, "bold"),
                           bd=0, pady=8, cursor="hand2",
                           command=lambda: load_comparison(comparison_text, winner_label, rules_text))
    refresh_btn.pack(fill="x", pady=(0, 15))

    # Winner display
    winner_frame = tk.Frame(left_frame, bg="#d4edda", padx=15, pady=15)
    winner_frame.pack(fill="x", pady=(0, 15))

    tk.Label(winner_frame, text="🏆 Best Scenario:",
             bg="#d4edda", fg="#155724",
             font=("Segoe UI", 11, "bold")).pack(anchor="w")

    winner_label = tk.Label(winner_frame, text="Not determined yet",
                           bg="#d4edda", fg="#155724",
                           font=("Segoe UI", 12, "bold"))
    winner_label.pack(anchor="w", pady=(5, 0))
    make_copyable(winner_label)

    # Comparison table
    tk.Label(left_frame, text="Comparison Table:",
             bg="white", fg="#333",
             font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(10, 5))

    comparison_text = scrolledtext.ScrolledText(left_frame,
                                               height=15,
                                               font=("Consolas", 9),
                                               bg="#f8f9fa", fg="#333")
    comparison_text.pack(fill="both", expand=True)

    # Right column - Rules and details
    right_frame = tk.Frame(content_frame, bg="white", padx=20, pady=20)
    right_frame.pack(side="left", fill="both", expand=True, padx=(10, 0))

    tk.Label(right_frame, text="📜 Discovered Rules",
             bg="white", fg="#16213e",
             font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 15))

    # Scenario selector
    scenario_frame = tk.Frame(right_frame, bg="white")
    scenario_frame.pack(fill="x", pady=(0, 10))

    tk.Label(scenario_frame, text="View rules for:",
             bg="white", fg="#333",
             font=("Segoe UI", 10)).pack(side="left", padx=(0, 10))

    scenario_var = tk.StringVar(value="H1")
    scenario_combo = ttk.Combobox(scenario_frame, textvariable=scenario_var,
                                 values=['M5', 'M15', 'H1', 'H4', 'H1_M15'],
                                 state="readonly", width=10)
    scenario_combo.pack(side="left")

    view_rules_btn = tk.Button(scenario_frame, text="View Rules",
                              bg="#27ae60", fg="white",
                              font=("Segoe UI", 9, "bold"),
                              bd=0, pady=5, padx=15,
                              cursor="hand2",
                              command=lambda: load_rules(scenario_var.get(), rules_text))
    view_rules_btn.pack(side="left", padx=(10, 0))

    # Rules display
    rules_text = scrolledtext.ScrolledText(right_frame,
                                          height=25,
                                          font=("Consolas", 9),
                                          bg="#2c3e50", fg="#ecf0f1",
                                          insertbackground="white",
                                          wrap=tk.WORD)
    rules_text.pack(fill="both", expand=True)

    # Initial load
    load_comparison(comparison_text, winner_label, rules_text)

    return panel


def load_comparison(comparison_text, winner_label, rules_text):
    """Load and display scenario comparison"""
    comparison_text.delete('1.0', tk.END)

    comparison_file = os.path.join(os.path.dirname(__file__), '../outputs/scenario_comparison.txt')

    if not os.path.exists(comparison_file):
        comparison_text.insert(tk.END, "No comparison results found.\n\n")
        comparison_text.insert(tk.END, "Run scenarios first to generate comparison.\n")
        winner_label.config(text="Not determined yet")

        rules_text.delete('1.0', tk.END)
        rules_text.insert(tk.END, "Run scenarios first to see results.\n")
        return

    try:
        with open(comparison_file, 'r') as f:
            content = f.read()

        comparison_text.insert(tk.END, content)

        # Extract winner from content
        winner = None
        for line in content.split('\n'):
            if 'WINNER:' in line:
                winner = line.split('WINNER:')[1].strip()
                break
            elif '★ WINNER' in line:
                parts = line.split()
                if len(parts) > 0:
                    winner = parts[0]
                    break

        if winner:
            winner_label.config(text=f"{winner}", fg="#155724")

            # Load winner's rules automatically
            load_rules(winner, rules_text)
        else:
            winner_label.config(text="Not determined yet", fg="#856404")

    except Exception as e:
        comparison_text.insert(tk.END, f"Error loading comparison:\n{str(e)}")
        winner_label.config(text="Error", fg="#e74c3c")


def _load_analysis_json_rules(rules_text):
    """Render rules from the newer analysis_report.json format.

    WHY (Phase 41 Fix 3): Old load_rules only read the legacy
         scenario_{name}/rules_report.txt format. The newer analyze.py
         pipeline writes outputs/analysis_report.json in a different
         location with a different structure. Users running the newer
         Robot Analysis path saw "No rules found" perpetually because
         the panel only knew about the legacy format. Try the JSON
         first; fall back to legacy txt if not present.
    CHANGED: April 2026 — Phase 41 Fix 3 — read analysis_report.json
             (audit Part D CRITICAL #3)
    """
    import json as _json
    json_path = os.path.join(os.path.dirname(__file__), '..', 'outputs', 'analysis_report.json')
    json_path = os.path.normpath(json_path)
    if not os.path.exists(json_path):
        return False
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            report = _json.load(f)
    except Exception as e:
        rules_text.insert('1.0', f"Error reading {json_path}: {e}\n")
        return True

    rules_text.insert('end', f"=== ANALYSIS REPORT (analysis_report.json) ===\n\n")
    rules = report.get('rules', [])
    if not rules:
        rules_text.insert('end', "No rules in analysis_report.json yet.\n")
        rules_text.insert('end', "Run Robot Analysis from the Robot Analysis panel.\n")
        return True

    for i, rule in enumerate(rules, 1):
        prediction = rule.get('prediction', '?')
        confidence = rule.get('confidence', 0)
        samples    = rule.get('samples', 0)
        win_rate   = rule.get('win_rate', 0)
        conditions = rule.get('conditions', [])
        rules_text.insert('end', f"Rule {i}: {prediction}\n")
        rules_text.insert('end', f"  Samples: {samples}, Confidence: {confidence:.2f}, "
                                 f"Win Rate: {win_rate:.2%}\n")
        for cond in conditions:
            feat = cond.get('feature', '?')
            op   = cond.get('operator', '?')
            val  = cond.get('value', '?')
            rules_text.insert('end', f"  IF {feat} {op} {val}\n")
        rules_text.insert('end', "\n")
    return True


def load_rules(scenario, rules_text):
    """Load and display rules for a specific scenario"""
    rules_text.delete('1.0', tk.END)

    # WHY (Phase 41 Fix 3b): Try the newer analysis_report.json first.
    #      If it exists, render from there. Falls through to legacy
    #      rules_report.txt if not found, preserving backward compat
    #      with the old per-scenario pipeline.
    # CHANGED: April 2026 — Phase 41 Fix 3b — JSON-first load
    #          (audit Part D CRITICAL #3)
    if _load_analysis_json_rules(rules_text):
        return

    rules_file = os.path.join(os.path.dirname(__file__), f'../outputs/scenario_{scenario}/rules_report.txt')

    if not os.path.exists(rules_file):
        rules_text.insert(tk.END, f"No rules found for scenario {scenario}.\n\n")
        rules_text.insert(tk.END, f"Run the scenario first to generate rules.\n")
        return

    try:
        with open(rules_file, 'r', encoding='utf-8') as f:
            content = f.read()

        rules_text.insert(tk.END, content)

        # Also try to load validation report
        validation_file = os.path.join(os.path.dirname(__file__), f'../outputs/scenario_{scenario}/validation_report.txt')

        if os.path.exists(validation_file):
            rules_text.insert(tk.END, "\n\n" + "=" * 60 + "\n")
            rules_text.insert(tk.END, "VALIDATION REPORT\n")
            rules_text.insert(tk.END, "=" * 60 + "\n\n")

            with open(validation_file, 'r', encoding='utf-8') as f:
                validation_content = f.read()

            rules_text.insert(tk.END, validation_content)

    except Exception as e:
        rules_text.insert(tk.END, f"Error loading rules:\n{str(e)}")


def refresh():
    """Refresh the panel"""
    pass
