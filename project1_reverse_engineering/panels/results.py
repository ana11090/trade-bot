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

    # WHY (Phase 50 Fix 1): Old code hardcoded 5 scenario values and
    #      defaulted to "H1" regardless of what the user actually
    #      ran. Users opening the panel after running only M15
    #      saw "No rules found for scenario H1". Glob the actual
    #      outputs/scenario_*/ directories to populate the combo
    #      with what exists, and pick the first existing one as
    #      the default. Fall back to the hardcoded list only if
    #      nothing exists yet.
    # CHANGED: April 2026 — Phase 50 Fix 1 — disk-driven scenario combo
    #          (audit Part D HIGH #95/97)
    import glob as _glob
    _outputs_dir = os.path.join(os.path.dirname(__file__), '..', 'outputs')
    _scenario_dirs = sorted(_glob.glob(os.path.join(_outputs_dir, 'scenario_*')))
    _scenario_names = [os.path.basename(d).replace('scenario_', '') for d in _scenario_dirs]
    if not _scenario_names:
        _scenario_names = ['M5', 'M15', 'H1', 'H4', 'H1_M15']
    _default_scenario = _scenario_names[0] if _scenario_names else 'H1'

    scenario_var = tk.StringVar(value=_default_scenario)
    scenario_combo = ttk.Combobox(scenario_frame, textvariable=scenario_var,
                                 values=_scenario_names,
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
    """Load and display scenario comparison.

    WHY (Phase 50 Fix 2): Old code only read scenario_comparison.txt
         and detected the winner via fragile regex on unstructured
         text ('WINNER:' in line / '★ WINNER' in line). Any change
         to the comparison file format silently broke winner
         detection. Try a JSON variant first (scenario_comparison.json)
         which carries the winner explicitly in a 'winner' key.
         Fall back to txt + regex if JSON doesn't exist.
    CHANGED: April 2026 — Phase 50 Fix 2 — JSON-first comparison
             (audit Part D HIGH #96)
    """
    comparison_text.delete('1.0', tk.END)

    # Try JSON format first
    _json_path = os.path.join(os.path.dirname(__file__), '../outputs/scenario_comparison.json')
    if os.path.exists(_json_path):
        try:
            import json as _json
            with open(_json_path, 'r', encoding='utf-8') as f:
                _comp = _json.load(f)
            comparison_text.insert(tk.END, "=== SCENARIO COMPARISON (JSON) ===\n\n")
            for _scenario, _stats in _comp.get('scenarios', {}).items():
                comparison_text.insert(tk.END, f"  {_scenario}: {_stats}\n")
            _winner = _comp.get('winner')
            if _winner:
                winner_label.config(text=f"{_winner}", fg="#155724")
                load_rules(_winner, rules_text)
                return
        except Exception:
            pass  # Fall through to txt loader

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

        # WHY (Phase 55 Fix 5): Old winner detection was two fragile
        #      regex patterns on unstructured text. A formatting change
        #      (e.g. extra spaces, different emoji) silently broke it.
        #      Try JSON-based lookup in analysis_report.json first;
        #      fall back to the text patterns as a last resort.
        # CHANGED: April 2026 — Phase 55 Fix 5 — hardened winner detection
        #          (audit Part D HIGH #96)
        winner = None
        try:
            import json as _json
            _rpt = os.path.join(os.path.dirname(__file__), '..', 'outputs', 'analysis_report.json')
            if os.path.exists(_rpt):
                with open(_rpt, 'r', encoding='utf-8') as _f:
                    _data = _json.load(_f)
                winner = _data.get('best_scenario') or _data.get('winner_scenario')
        except Exception:
            pass
        if not winner:
            for line in content.split('\n'):
                _line = line.strip()
                if 'WINNER:' in _line:
                    _parts = _line.split('WINNER:', 1)
                    if len(_parts) > 1:
                        winner = _parts[1].strip().split()[0] if _parts[1].strip() else None
                        break
                elif '★ WINNER' in _line or '* WINNER' in _line:
                    _parts = _line.split()
                    if _parts:
                        winner = _parts[0].lstrip('★* ')
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
        # WHY (Phase A.27): Old code read rule.get('samples', 0) but
        #      analyze.py::extract_rules writes the key 'coverage', never
        #      'samples'. The mismatch has existed since phase 48 (commit
        #      f4d57245) — every rule has shown "Samples: 0" in this
        #      panel ever since. Robot Analysis panel reads 'coverage'
        #      correctly. Read 'coverage' first, fall back to 'samples'
        #      only if some legacy writer is still emitting it.
        # CHANGED: April 2026 — Phase A.27 — read coverage key
        samples    = rule.get('coverage', rule.get('samples', 0))
        win_rate   = rule.get('win_rate', 0)
        conditions = rule.get('conditions', [])
        # WHY (Phase A.27): Display the actual count under a label that
        #      matches what every other panel calls it ("trades"), so
        #      users don't compare apples to oranges between panels.
        # CHANGED: April 2026 — Phase A.27 — relabel Samples → Trades
        rules_text.insert('end', f"Rule {i}: {prediction}\n")
        rules_text.insert('end', f"  Trades: {samples}, Confidence: {confidence:.2f}, "
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

    # WHY (Phase 41 Fix 3b + Phase 50 Fix 4): Try the newer
    #      analysis_report.json first. Phase 50 refinement: if the
    #      JSON exists but is empty/stale AND the legacy txt is
    #      newer, prefer the legacy txt — covers users who ran the
    #      legacy run_scenarios pipeline AFTER trying Robot Analysis.
    # CHANGED: April 2026 — Phase 41 Fix 3b / Phase 50 Fix 4
    #          (audit Part D CRITICAL #3, HIGH #93)
    _json_path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), '..', 'outputs', 'analysis_report.json')
    )
    _legacy_path = os.path.join(
        os.path.dirname(__file__), f'../outputs/scenario_{scenario}/rules_report.txt'
    )
    _use_json = True
    try:
        if os.path.exists(_json_path) and os.path.exists(_legacy_path):
            if os.path.getmtime(_legacy_path) > os.path.getmtime(_json_path):
                _use_json = False
    except Exception:
        pass

    if _use_json and _load_analysis_json_rules(rules_text):
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

        # WHY (Phase 55 Fix 6): Old code only read the legacy
        #      scenario_{name}/validation_report.txt which the current
        #      analyze.py pipeline never writes. Users always saw an
        #      empty validation section. Try analysis_report.json first
        #      (which contains match_rate and validation metrics from
        #      the current pipeline); fall back to the legacy txt for
        #      users still running the older per-scenario pipeline.
        # CHANGED: April 2026 — Phase 55 Fix 6 — try analysis_report first
        #          (audit Part D HIGH #94)
        import json as _json
        _showed_validation = False
        _rpt_path = os.path.join(os.path.dirname(__file__), '..', 'outputs', 'analysis_report.json')
        if os.path.exists(_rpt_path):
            try:
                with open(_rpt_path, 'r', encoding='utf-8') as _f:
                    _rpt = _json.load(_f)
                _val = _rpt.get('validation', {}) or _rpt.get('match_results', {})
                if _val:
                    rules_text.insert(tk.END, "\n\n" + "=" * 60 + "\n")
                    rules_text.insert(tk.END, "VALIDATION (analysis_report.json)\n")
                    rules_text.insert(tk.END, "=" * 60 + "\n\n")
                    for _k, _v in _val.items():
                        rules_text.insert(tk.END, f"  {_k}: {_v}\n")
                    _showed_validation = True
            except Exception:
                pass

        if not _showed_validation:
            # Also try to load validation report (Phase 50 legacy path)
            _val_json_path = os.path.join(os.path.dirname(__file__), f'../outputs/scenario_{scenario}/validation_report.json')
            validation_file = os.path.join(os.path.dirname(__file__), f'../outputs/scenario_{scenario}/validation_report.txt')

            if os.path.exists(_val_json_path):
                rules_text.insert(tk.END, "\n\n" + "=" * 60 + "\n")
                rules_text.insert(tk.END, "VALIDATION REPORT (JSON)\n")
                rules_text.insert(tk.END, "=" * 60 + "\n\n")
                try:
                    with open(_val_json_path, 'r', encoding='utf-8') as f:
                        _vdata = _json.load(f)
                    _verdict = _vdata.get('verdict', '?')
                    _wfa     = _vdata.get('walk_forward', {})
                    _mc      = _vdata.get('monte_carlo',  {})
                    rules_text.insert(tk.END, f"Verdict: {_verdict}\n\n")
                    if _wfa:
                        rules_text.insert(tk.END, f"Walk-forward: {_wfa}\n\n")
                    if _mc:
                        rules_text.insert(tk.END, f"Monte Carlo: {_mc}\n\n")
                except Exception as _e:
                    rules_text.insert(tk.END, f"Error reading validation JSON: {_e}\n")
            elif os.path.exists(validation_file):
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
