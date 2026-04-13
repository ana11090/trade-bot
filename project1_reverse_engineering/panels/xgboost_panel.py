"""
XGBoost Discovery Panel — Project 1 Reverse Engineering

Trains XGBoost on the feature matrix (with optional smart features) and
extracts human-readable IF/THEN rules that can replace the DT rules in the
analysis pipeline.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import os
import sys
import json
import threading

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)

import state

# ── Design tokens ─────────────────────────────────────────────────────────────
BG     = "#f0f2f5"
WHITE  = "white"
DARK   = "#16213e"
GREEN  = "#2d8a4e"
RED    = "#e94560"
AMBER  = "#e67e22"
BLUE   = "#2980b9"
GREY   = "#666666"
CARD   = "#ffffff"

# ── Module-level state ────────────────────────────────────────────────────────
_panel           = None
_run_btn         = None
_progress_bar    = None
_status_lbl      = None
_bot_entry_btn   = None  # Phase A.25
_notebook        = None          # ttk.Notebook for results tabs
_xgb_tab_text    = None          # ScrolledText in XGBoost tab
_dt_tab_text     = None          # ScrolledText in DT tab
_compare_frame   = None          # Frame in Compare tab
_smart_toggle    = None          # tk.BooleanVar
_use_smart_var   = None
_smart_frame     = None          # collapsible smart-features list frame
_smart_open      = [False]
_action_btns     = []            # buttons to enable/disable

# Param vars (set during build_panel)
_var_max_rules   = None
_var_max_depth   = None
_var_estimators  = None
_var_min_cov     = None
_var_min_wr      = None
_var_train_split = None

# WHY (Phase 48 Fix 7): Old _running was a bare bool. Two fast clicks
#      raced through `if _running: return` and both started training
#      threads, corrupting xgboost_result.json. Use threading.Lock.
# CHANGED: April 2026 — Phase 48 Fix 7 — thread-safe running flag
#          (audit Part D HIGH #78)
import threading as _threading
_running = False
_running_lock = _threading.Lock()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _p1_dir():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _outputs_dir():
    return os.path.join(_p1_dir(), 'outputs')


def _result_path():
    return os.path.join(_outputs_dir(), 'xgboost_result.json')


def _feature_matrix_exists():
    """Check whether a labeled feature matrix exists for XGBoost training.

    WHY (Phase 48 Fix 5): Old code checked a hardcoded scenario list
         then fell back to smart_feature_matrix.csv — but the smart
         matrix has features without LABELS, so run_xgboost_discovery
         crashed on missing 'label' column. Now: glob for any
         feature_matrix_labeled.csv under outputs/scenario_*/, and
         only fall back to smart_feature_matrix.csv if it has a label
         column.
    CHANGED: April 2026 — Phase 48 Fix 5 — label-aware existence check
             (audit Part D HIGH #75)
    """
    import glob
    pattern = os.path.join(_outputs_dir(), 'scenario_*', 'feature_matrix_labeled.csv')
    for p in glob.glob(pattern):
        if os.path.exists(p):
            return True
    smart = os.path.join(_p1_dir(), 'outputs', 'smart_feature_matrix.csv')
    if os.path.exists(smart):
        try:
            import pandas as pd
            _head = pd.read_csv(smart, nrows=1)
            return 'label' in _head.columns or 'is_winner' in _head.columns
        except Exception:
            return False
    return False


def _load_result():
    if not os.path.exists(_result_path()):
        return None
    with open(_result_path()) as f:
        return json.load(f)


def _set_running(running):
    """Atomically set the running flag. Returns True if state changed."""
    global _running
    with _running_lock:
        if running and _running:
            return False  # Already running, refuse to start
        _running = running
        changed = True
    state = "disabled" if running else "normal"
    if _run_btn:
        _run_btn.config(state=state)
    for btn in _action_btns:
        try:
            btn.config(state=state)
        except Exception:
            pass
    return changed


def _append_text(widget, text, tag=None):
    widget.config(state="normal")
    if tag:
        widget.insert("end", text, tag)
    else:
        widget.insert("end", text)
    widget.see("end")
    widget.config(state="disabled")


def _clear_text(widget):
    widget.config(state="normal")
    widget.delete("1.0", "end")
    widget.config(state="disabled")


def _scrolled_text(parent):
    """Return a read-only ScrolledText."""
    from tkinter import scrolledtext
    st = scrolledtext.ScrolledText(
        parent, bg="#1a1a2a", fg="#e0e0e0",
        font=("Consolas", 9), wrap="word",
        state="disabled",
    )
    st.tag_config("green",  foreground="#2ecc71")
    st.tag_config("red",    foreground="#e74c3c")
    st.tag_config("amber",  foreground="#f39c12")
    st.tag_config("blue",   foreground="#3498db")
    st.tag_config("header", foreground="#ffffff", font=("Consolas", 9, "bold"))
    st.tag_config("grey",   foreground="#aaaaaa")
    return st


# ── Status data card ──────────────────────────────────────────────────────────

def _build_data_status(parent):
    frame = tk.Frame(parent, bg=CARD, padx=15, pady=12)
    frame.pack(fill="x", padx=20, pady=(0, 10))

    tk.Label(frame, text="Data Status", bg=CARD, fg=DARK,
             font=("Segoe UI", 11, "bold")).pack(anchor="w")

    row = tk.Frame(frame, bg=CARD)
    row.pack(fill="x", pady=(6, 0))

    def _dot(color, label):
        tk.Label(row, text="●", bg=CARD, fg=color,
                 font=("Segoe UI", 10)).pack(side="left", padx=(0, 4))
        tk.Label(row, text=label, bg=CARD, fg=GREY,
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 16))

    fm_ok     = _feature_matrix_exists()
    result_ok = os.path.exists(_result_path())
    backup_ok = os.path.exists(os.path.join(_outputs_dir(), 'analysis_report_backup.json'))

    _dot(GREEN if fm_ok     else RED, "Feature matrix")
    _dot(GREEN if result_ok else AMBER, "XGBoost result")
    _dot(GREEN if backup_ok else GREY, "Backup (original rules)")


# ── Settings form ─────────────────────────────────────────────────────────────

def _build_settings(parent):
    global _var_max_rules, _var_max_depth, _var_estimators
    global _var_min_cov, _var_min_wr, _var_train_split, _use_smart_var

    frame = tk.Frame(parent, bg=CARD, padx=15, pady=12)
    frame.pack(fill="x", padx=20, pady=(0, 10))

    tk.Label(frame, text="Settings", bg=CARD, fg=DARK,
             font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 8))

    # Smart features checkbox
    # WHY (Phase 54 Fix 4): Old code defaulted Smart features ON
    #      without checking if the smart cache existed. If it didn't,
    #      run_xgboost_discovery either crashed or silently dropped
    #      the smart features. Default to True ONLY if a smart matrix
    #      file exists; else default False.
    # CHANGED: April 2026 — Phase 54 Fix 4 — smart-cache aware default
    #          (audit Part D MED #79)
    _smart_cache_path = os.path.join(_p1_dir(), 'outputs', 'smart_feature_matrix.csv')
    _smart_default = os.path.exists(_smart_cache_path)
    _use_smart_var = tk.BooleanVar(value=_smart_default)
    smart_row = tk.Frame(frame, bg=CARD)
    smart_row.pack(fill="x", pady=(0, 8))
    tk.Checkbutton(
        smart_row, text="Use smart features (~50 SMART_ columns)",
        variable=_use_smart_var,
        bg=CARD, fg=DARK, activebackground=CARD,
        font=("Segoe UI", 9),
        command=_on_smart_toggle,
    ).pack(side="left")

    # Numeric params
    # WHY: Phase 26 Fix 1 — Min win rate default raised from 0.55 to
    #      0.60. The 0.55 default was slightly too permissive — rules
    #      with 55-60% WR often pass the in-sample filter but fail
    #      walk-forward validation. 0.60 is the audit-suggested floor.
    #      Still user-editable.
    # CHANGED: April 2026 — Phase 26 Fix 1 — stricter default (audit Part B #22)
    params = [
        ("Max rules",        "_var_max_rules",   "25"),
        ("Max tree depth",   "_var_max_depth",   "4"),
        ("N estimators",     "_var_estimators",  "300"),
        ("Min coverage",     "_var_min_cov",     "10"),
        ("Min win rate",     "_var_min_wr",      "0.60"),
        ("Train split",      "_var_train_split", "0.70"),
    ]

    grid = tk.Frame(frame, bg=CARD)
    grid.pack(fill="x")

    for col_idx in range(3):
        grid.columnconfigure(col_idx * 2,     weight=1)
        grid.columnconfigure(col_idx * 2 + 1, weight=1)

    for idx, (label, varname, default) in enumerate(params):
        row_i = idx // 3
        col_i = (idx % 3) * 2
        var = tk.StringVar(value=default)
        globals()[varname] = var

        tk.Label(grid, text=label, bg=CARD, fg=GREY,
                 font=("Segoe UI", 9)).grid(row=row_i, column=col_i,
                                             sticky="e", padx=(8, 4), pady=3)
        tk.Entry(grid, textvariable=var, width=8,
                 font=("Segoe UI", 9)).grid(row=row_i, column=col_i + 1,
                                             sticky="w", padx=(0, 8), pady=3)


def _on_smart_toggle():
    pass  # reserved for future: show/hide smart preview button


# ── Run controls ──────────────────────────────────────────────────────────────

def _build_run_controls(parent):
    global _run_btn, _progress_bar, _status_lbl, _bot_entry_btn

    frame = tk.Frame(parent, bg=CARD, padx=15, pady=12)
    frame.pack(fill="x", padx=20, pady=(0, 10))

    _run_btn = tk.Button(
        frame, text="Run XGBoost Discovery",
        bg=BLUE, fg=WHITE,
        font=("Segoe UI", 11, "bold"),
        bd=0, pady=8, cursor="hand2",
        command=_on_run,
    )
    _run_btn.pack(fill="x")

    # WHY (Phase A.25): second button to run bot entry discovery — same
    #      panel, same display, different discovery target.
    # CHANGED: April 2026 — Phase A.25
    def _run_bot_entry():
        import threading
        def _worker():
            try:
                from project1_reverse_engineering.bot_entry_discovery import (
                    discover_bot_entry_rules, BOT_RULES_PATH,
                )

                def _cb(msg):
                    _status_lbl.config(text=msg[:80])

                result = discover_bot_entry_rules(
                    max_rules    = int(_var_max_rules.get()),
                    max_depth    = int(_var_max_depth.get()),
                    n_estimators = int(_var_estimators.get()),
                    min_coverage = int(_var_min_cov.get()),
                    min_win_rate = float(_var_min_wr.get()),
                    progress_callback = _cb,
                )
                _display_xgb_results(result)
                _status_lbl.config(
                    text=f"Bot entry: {len(result.get('rules', []))} rules → {BOT_RULES_PATH}"
                )
            except Exception as e:
                _status_lbl.config(text=f"Bot entry error: {e}")
                from tkinter import messagebox
                messagebox.showerror("Bot Entry Discovery Error", str(e))
            finally:
                _bot_entry_btn.config(state="normal")
                _progress_bar.stop()

        _bot_entry_btn.config(state="disabled")
        _progress_bar.start(10)
        threading.Thread(target=_worker, daemon=True).start()

    global _bot_entry_btn
    _bot_entry_btn = tk.Button(
        frame, text="Discover Bot Entry Rules (all TFs)",
        command=_run_bot_entry,
        bg="#6c5ce7", fg="white", font=("Segoe UI", 11, "bold"),
        bd=0, pady=8, cursor="hand2",
    )
    _bot_entry_btn.pack(pady=(5, 0), fill="x")

    _progress_bar = ttk.Progressbar(frame, mode="indeterminate")
    _progress_bar.pack(fill="x", pady=(8, 0))

    _status_lbl = tk.Label(frame, text="Ready", bg=CARD, fg=GREY,
                            font=("Segoe UI", 9))
    _status_lbl.pack(anchor="w", pady=(4, 0))


# ── Results notebook ──────────────────────────────────────────────────────────

def _build_results_notebook(parent):
    global _notebook, _xgb_tab_text, _dt_tab_text, _compare_frame

    outer = tk.Frame(parent, bg=CARD, padx=15, pady=12)
    outer.pack(fill="both", expand=True, padx=20, pady=(0, 10))

    tk.Label(outer, text="Results", bg=CARD, fg=DARK,
             font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 6))

    _notebook = ttk.Notebook(outer)
    _notebook.pack(fill="both", expand=True)

    # Tab 1 — XGBoost
    tab_xgb = tk.Frame(_notebook, bg="#1a1a2a")
    _notebook.add(tab_xgb, text="XGBoost Results")
    _xgb_tab_text = _scrolled_text(tab_xgb)
    _xgb_tab_text.pack(fill="both", expand=True)

    # Tab 2 — Original DT
    tab_dt = tk.Frame(_notebook, bg="#1a1a2a")
    _notebook.add(tab_dt, text="Original DT Results")
    _dt_tab_text = _scrolled_text(tab_dt)
    _dt_tab_text.pack(fill="both", expand=True)

    # Tab 3 — Compare
    tab_cmp = tk.Frame(_notebook, bg=BG)
    _notebook.add(tab_cmp, text="Compare")
    _compare_frame = tab_cmp


# ── Action buttons ────────────────────────────────────────────────────────────

def _build_action_buttons(parent):
    frame = tk.Frame(parent, bg=CARD, padx=15, pady=12)
    frame.pack(fill="x", padx=20, pady=(0, 10))

    tk.Label(frame, text="Pipeline Integration", bg=CARD, fg=DARK,
             font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 8))

    btn_row = tk.Frame(frame, bg=CARD)
    btn_row.pack(fill="x")

    btn_activate = tk.Button(
        btn_row, text="Use XGBoost Rules in Pipeline",
        bg=GREEN, fg=WHITE, font=("Segoe UI", 9, "bold"),
        bd=0, pady=6, cursor="hand2",
        command=_on_activate,
    )
    btn_activate.pack(side="left", padx=(0, 8), fill="x", expand=True)

    btn_restore = tk.Button(
        btn_row, text="Restore Original Rules",
        bg=AMBER, fg=WHITE, font=("Segoe UI", 9, "bold"),
        bd=0, pady=6, cursor="hand2",
        command=_on_restore,
    )
    btn_restore.pack(side="left", padx=(0, 8), fill="x", expand=True)

    btn_export = tk.Button(
        btn_row, text="Export JSON",
        bg=DARK, fg=WHITE, font=("Segoe UI", 9, "bold"),
        bd=0, pady=6, cursor="hand2",
        command=_on_export,
    )
    btn_export.pack(side="left", fill="x", expand=True)

    _action_btns.clear()
    _action_btns.extend([btn_activate, btn_restore, btn_export])


# ── Smart Features preview ────────────────────────────────────────────────────

def _build_smart_features_preview(parent):
    global _smart_frame

    outer = tk.Frame(parent, bg=CARD, padx=15, pady=10)
    outer.pack(fill="x", padx=20, pady=(0, 16))

    toggle_btn = tk.Button(
        outer, text="▶  Smart Features Preview (50 columns)",
        bg=CARD, fg=DARK, font=("Segoe UI", 10, "bold"),
        bd=0, cursor="hand2",
        command=lambda: _toggle_smart(toggle_btn, _smart_frame),
        anchor="w",
    )
    toggle_btn.pack(fill="x")

    _smart_frame = tk.Frame(outer, bg=BG)
    # Not packed — revealed by toggle


def _toggle_smart(btn, frame):
    _smart_open[0] = not _smart_open[0]
    if _smart_open[0]:
        btn.config(text="▼  Smart Features Preview (50 columns)")
        _populate_smart_frame(frame)
        frame.pack(fill="x", pady=(8, 0))
    else:
        btn.config(text="▶  Smart Features Preview (50 columns)")
        frame.pack_forget()


def _populate_smart_frame(frame):
    for w in frame.winfo_children():
        w.destroy()

    try:
        p1_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        sys.path.insert(0, p1_path)
        from smart_features import SMART_FEATURE_CATEGORIES
    except Exception as e:
        tk.Label(frame, text=f"Could not load smart_features: {e}",
                 bg=BG, fg=RED, font=("Segoe UI", 9)).pack(anchor="w")
        return

    for category, entries in SMART_FEATURE_CATEGORIES.items():
        cat_lbl = tk.Label(frame, text=f"  {category}",
                           bg=BG, fg=DARK, font=("Segoe UI", 9, "bold"))
        cat_lbl.pack(anchor="w", pady=(6, 2))
        for feat_name, description in entries:
            row = tk.Frame(frame, bg=BG)
            row.pack(fill="x", padx=16)
            tk.Label(row, text=feat_name, bg=BG, fg=BLUE,
                     font=("Consolas", 8), width=32, anchor="w").pack(side="left")
            tk.Label(row, text=description, bg=BG, fg=GREY,
                     font=("Segoe UI", 8), anchor="w").pack(side="left")


# ── Display functions ─────────────────────────────────────────────────────────

def _display_xgb_results(result):
    _clear_text(_xgb_tab_text)
    w = _xgb_tab_text

    _append_text(w, "─" * 60 + "\n", "grey")
    _append_text(w, "  XGBOOST DISCOVERY RESULTS\n", "header")
    _append_text(w, "─" * 60 + "\n\n", "grey")

    m = result.get('xgb_metrics', {})
    _append_text(w, f"  Accuracy:  {m.get('accuracy', 0):.4f}\n", "green")
    _append_text(w, f"  Precision: {m.get('precision', 0):.4f}\n")
    _append_text(w, f"  Recall:    {m.get('recall', 0):.4f}\n")
    _append_text(w, f"  F1:        {m.get('f1', 0):.4f}\n")
    _append_text(w, f"  ROC-AUC:   {m.get('roc_auc', 0):.4f}\n\n")

    _append_text(w, "  TOP 20 FEATURE IMPORTANCE\n", "header")
    _append_text(w, "─" * 60 + "\n", "grey")
    for feat, imp in result.get('feature_importance', [])[:20]:
        bar = "█" * int(imp * 200)
        _append_text(w, f"  {feat:<38s} {imp:.5f}  {bar}\n")

    _append_text(w, "\n  EXTRACTED RULES\n", "header")
    _append_text(w, "─" * 60 + "\n", "grey")
    rules = result.get('rules', [])
    if not rules:
        _append_text(w, "  No rules extracted.\n", "amber")
    else:
        for i, rule in enumerate(rules, 1):
            _append_text(w, f"\n  RULE #{i}  ", "header")
            _append_text(w, f"WR={rule['win_rate']:.1%}  cov={rule['coverage']}\n", "green")
            for cond in rule.get('conditions', []):
                op  = cond.get('operator', '<=')
                val = cond.get('value', 0)
                feat = cond.get('feature', '')
                _append_text(w, f"    IF {feat} {op} {val}\n", "blue")
            _append_text(w, f"    THEN WIN (confidence={rule['confidence']:.3f})\n")


def _display_dt_results(result):
    _clear_text(_dt_tab_text)
    w = _dt_tab_text

    _append_text(w, "─" * 60 + "\n", "grey")
    _append_text(w, "  BASELINE DECISION TREE RESULTS\n", "header")
    _append_text(w, "─" * 60 + "\n\n", "grey")

    m = result.get('dt_metrics', {})
    _append_text(w, f"  Accuracy:  {m.get('accuracy', 0):.4f}\n")
    _append_text(w, f"  Precision: {m.get('precision', 0):.4f}\n")
    _append_text(w, f"  Recall:    {m.get('recall', 0):.4f}\n")
    _append_text(w, f"  F1:        {m.get('f1', 0):.4f}\n\n")

    _append_text(w, "  (DT rules are extracted using the DT baseline model)\n", "grey")
    _append_text(w, "  (Use Robot Analysis panel to view full DT rule set)\n", "grey")


def _display_compare(result):
    for w in _compare_frame.winfo_children():
        w.destroy()

    xgb = result.get('xgb_metrics', {})
    dt  = result.get('dt_metrics', {})

    tk.Label(_compare_frame, text="Model Comparison", bg=BG, fg=DARK,
             font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=15, pady=(10, 6))

    metrics = [("Accuracy", "accuracy"), ("Precision", "precision"),
               ("Recall", "recall"), ("F1 Score", "f1")]

    for label, key in metrics:
        xv = xgb.get(key, 0)
        dv = dt.get(key, 0)
        row = tk.Frame(_compare_frame, bg=BG)
        row.pack(fill="x", padx=15, pady=2)
        tk.Label(row, text=f"{label}:", bg=BG, fg=GREY,
                 font=("Segoe UI", 9), width=12, anchor="w").pack(side="left")
        # WHY (Phase 48 Fix 6): Old code colored each metric GREEN/RED
        #      independently. Higher precision at the cost of recall
        #      isn't strictly better. Use a tolerance band so small
        #      improvements aren't trumpeted as wins, and yellow for
        #      ambiguous cases.
        # CHANGED: April 2026 — Phase 48 Fix 6 — softer metric coloring
        #          (audit Part D HIGH #77)
        if xv >= dv * 1.05:    # >=5% improvement
            xgb_col = GREEN
        elif xv <= dv * 0.95:  # >=5% regression
            xgb_col = RED
        else:                  # within ±5%
            xgb_col = "#f39c12"  # amber
        tk.Label(row, text=f"XGBoost {xv:.4f}", bg=BG, fg=xgb_col,
                 font=("Segoe UI", 9, "bold"), width=20).pack(side="left")
        dt_col = GREEN if dv >= xv else GREY
        tk.Label(row, text=f"DT {dv:.4f}", bg=BG, fg=dt_col,
                 font=("Segoe UI", 9)).pack(side="left")

    n_rules = len(result.get('rules', []))
    tk.Label(_compare_frame, text=f"\nXGBoost extracted {n_rules} rules",
             bg=BG, fg=BLUE, font=("Segoe UI", 9)).pack(anchor="w", padx=15)

    if result.get('use_smart'):
        tk.Label(_compare_frame, text="Smart features: ON",
                 bg=BG, fg=GREEN, font=("Segoe UI", 9)).pack(anchor="w", padx=15)


# ── Button handlers ───────────────────────────────────────────────────────────

def _on_run():
    # Phase 48 Fix 7c: atomic check-and-set
    if not _set_running(True):
        return  # Already running

    def _work():
        _progress_bar.start(12)
        _status_lbl.config(text="Running...")

        try:
            p1_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            sys.path.insert(0, p1_path)
            from xgboost_discovery import run_xgboost_discovery

            def _cb(msg):
                _status_lbl.config(text=msg[:80])

            result = run_xgboost_discovery(
                max_rules       = int(_var_max_rules.get()),
                max_depth       = int(_var_max_depth.get()),
                n_estimators    = int(_var_estimators.get()),
                min_coverage    = int(_var_min_cov.get()),
                min_win_rate    = float(_var_min_wr.get()),
                use_smart_features = _use_smart_var.get(),
                # WHY (Phase 62 Fix 8): Old code passed the user's raw
                #      input directly. Values like 1.1 or -0.5 produced
                #      cryptic sklearn errors. Clamp to [0.50, 0.95].
                # CHANGED: April 2026 — Phase 62 Fix 8 — bounds validation
                #          (audit Part D MED #80)
                train_test_split = max(0.50, min(0.95, float(_var_train_split.get()))),
                progress_callback  = _cb,
            )

            _display_xgb_results(result)
            _display_dt_results(result)
            _display_compare(result)
            _status_lbl.config(text=f"Done. {len(result.get('rules', []))} rules extracted.")

        except Exception as e:
            _status_lbl.config(text=f"Error: {e}")
            messagebox.showerror("XGBoost Discovery Error", str(e))
        finally:
            _progress_bar.stop()
            _set_running(False)

    threading.Thread(target=_work, daemon=True).start()


def _on_activate():
    # WHY (Phase 58 Fix 6): Old code overwrote analysis_report.json
    #      (and the DT rules inside it) with one accidental click.
    #      "Restore Original Rules" only works if the backup exists —
    #      and there was no warning before the overwrite happened.
    #      Add a confirmation dialog so the user understands what is
    #      about to change before it does.
    # CHANGED: April 2026 — Phase 58 Fix 6 — confirm before overwriting rules
    #          (audit Part D HIGH #76)
    confirmed = messagebox.askyesno(
        "Confirm: Use XGBoost Rules",
        "This will REPLACE the current Decision Tree rules in\n"
        "analysis_report.json with the XGBoost rules.\n\n"
        "The original rules will be backed up and can be restored\n"
        "with 'Restore Original Rules'.\n\n"
        "Continue?",
    )
    if not confirmed:
        return
    p1_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    sys.path.insert(0, p1_path)
    from xgboost_discovery import activate_xgboost_rules
    ok, msg = activate_xgboost_rules()
    if ok:
        messagebox.showinfo("Pipeline Updated", msg)
    else:
        messagebox.showwarning("Cannot Activate", msg)


def _on_restore():
    p1_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    sys.path.insert(0, p1_path)
    from xgboost_discovery import restore_original_rules
    ok, msg = restore_original_rules()
    if ok:
        messagebox.showinfo("Rules Restored", msg)
    else:
        messagebox.showwarning("Cannot Restore", msg)


def _on_export():
    result = _load_result()
    if not result:
        messagebox.showwarning("No Result", "Run XGBoost Discovery first.")
        return
    from tkinter import filedialog
    path = filedialog.asksaveasfilename(
        defaultextension=".json",
        filetypes=[("JSON files", "*.json")],
        initialfile="xgboost_result.json",
    )
    if path:
        import shutil
        shutil.copy2(_result_path(), path)
        messagebox.showinfo("Exported", f"Saved to {path}")


# ── Public API ────────────────────────────────────────────────────────────────

def build_panel(parent):
    global _panel

    _panel = tk.Frame(parent, bg=BG)

    # Header
    header = tk.Frame(_panel, bg=WHITE, pady=18)
    header.pack(fill="x", padx=20, pady=(20, 10))
    tk.Label(header, text="XGBoost Discovery",
             bg=WHITE, fg=DARK, font=("Segoe UI", 18, "bold")).pack()
    tk.Label(header,
             text="Train gradient boosting on smart features and extract IF/THEN trading rules",
             bg=WHITE, fg=GREY, font=("Segoe UI", 10)).pack(pady=(4, 0))

    # Scrollable inner area
    canvas_outer = tk.Canvas(_panel, bg=BG, highlightthickness=0)
    scrollbar    = ttk.Scrollbar(_panel, orient="vertical", command=canvas_outer.yview)
    canvas_outer.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas_outer.pack(side="left", fill="both", expand=True)

    inner = tk.Frame(canvas_outer, bg=BG)
    window_id = canvas_outer.create_window((0, 0), window=inner, anchor="nw")

    def _on_inner_resize(event):
        canvas_outer.configure(scrollregion=canvas_outer.bbox("all"))

    def _on_canvas_resize(event):
        canvas_outer.itemconfig(window_id, width=event.width)

    inner.bind("<Configure>", _on_inner_resize)
    canvas_outer.bind("<Configure>", _on_canvas_resize)

    def _on_wheel(event):
        canvas_outer.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas_outer.bind("<MouseWheel>", _on_wheel)
    inner.bind("<MouseWheel>", _on_wheel)

    # Build sections
    _build_data_status(inner)
    _build_settings(inner)
    _build_run_controls(inner)
    _build_results_notebook(inner)
    _build_action_buttons(inner)
    _build_smart_features_preview(inner)

    # Load existing result if any
    result = _load_result()
    if result:
        _display_xgb_results(result)
        _display_dt_results(result)
        _display_compare(result)

    _panel.pack_forget()
    return _panel


def refresh():
    if _panel is None:
        return
    # Refresh data status dot colors
    result = _load_result()
    if result and _xgb_tab_text:
        _display_xgb_results(result)
        _display_dt_results(result)
        _display_compare(result)
