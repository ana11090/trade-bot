"""
Project 4 — Build Strategy from Scratch panel.

Scans ALL historical candles, labels each WIN/LOSS based on what happened
after entry, then trains XGBoost with 670 features to find profitable
entry conditions.  No robot trade history needed.

No bind_all calls here — scroll routing is owned by main_app.py.
All heavy computation runs in a daemon thread; UI updates via .after().
"""

import os
import sys
import json
import time
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, '..', '..')))

# ── module-level state (panel is built only once, lazily) ─────────────────────
_panel          = None
_result         = None
_run_thread     = None
_start_time     = [0.0]
_elapsed_var    = [None]   # tk.StringVar — filled after Tk exists
_status_var     = [None]
_progress_var   = [None]
_base_wr_var    = [None]

# widget refs set during build_panel()
_widgets        = {}

# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def build_panel(parent):
    global _panel
    if _panel is not None:
        return _panel
    _panel = _build(parent)
    return _panel


def refresh():
    """Called every time the panel becomes visible."""
    _load_existing_result()
    _render_results()
    _update_comparison()


# ─────────────────────────────────────────────────────────────────────────────
# BUILD
# ─────────────────────────────────────────────────────────────────────────────

def _build(parent):
    root = tk.Frame(parent, bg="#f0f2f5")

    # Scrollable canvas
    canvas = tk.Canvas(root, bg="#f0f2f5", highlightthickness=0)
    vsb    = ttk.Scrollbar(root, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    _widgets['canvas'] = canvas

    inner = tk.Frame(canvas, bg="#f0f2f5")
    cw    = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _on_inner_resize(e):
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _on_canvas_resize(e):
        canvas.itemconfig(cw, width=e.width)

    inner.bind("<Configure>", _on_inner_resize)
    canvas.bind("<Configure>", _on_canvas_resize)

    _build_inner(inner)
    return root


def _build_inner(inner):
    pad = dict(padx=20, pady=6)

    # ── Header ────────────────────────────────────────────────────────────────
    hdr = tk.Frame(inner, bg="#1e2d4e", pady=18)
    hdr.pack(fill="x", **dict(padx=0, pady=0))
    tk.Label(hdr, text="Build Strategy from Scratch",
             bg="#1e2d4e", fg="white",
             font=("Segoe UI", 18, "bold")).pack()
    tk.Label(hdr,
             text="Find trading edges directly from price data — no robot needed",
             bg="#1e2d4e", fg="#5a7a99",
             font=("Segoe UI", 10)).pack()

    # ── Explanation card ──────────────────────────────────────────────────────
    card = tk.Frame(inner, bg="#e8f4fd", relief="flat", bd=0)
    card.pack(fill="x", padx=20, pady=(14, 4))
    tk.Label(card,
             text=(
                 "This scans every candle in history and asks:\n"
                 "\"If I entered a trade HERE, would it have been profitable?\"\n\n"
                 "Then uses XGBoost + 670 features to find WHAT CONDITIONS\n"
                 "predict profitable entries. No robot trade history needed.\n\n"
                 "130,000+ candles analyzed  vs  ~1,100 trades from a robot.\n"
                 "More data = more reliable patterns."
             ),
             bg="#e8f4fd", fg="#1a3a5c",
             font=("Segoe UI", 10), justify="left",
             padx=14, pady=12).pack(anchor="w")

    # ── Candles file picker ───────────────────────────────────────────────────
    _section(inner, "Candles File (H1 CSV)")
    file_row = tk.Frame(inner, bg="#f0f2f5")
    file_row.pack(fill="x", **pad)

    candles_var = tk.StringVar(value="")
    _widgets['candles_var'] = candles_var

    tk.Entry(file_row, textvariable=candles_var,
             font=("Segoe UI", 9), bg="white", fg="#333",
             relief="solid", bd=1).pack(side="left", fill="x", expand=True)

    def _browse():
        p = filedialog.askopenfilename(
            title="Select H1 candles CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if p:
            candles_var.set(p)

    tk.Button(file_row, text="Browse...",
              bg="#1e2d4e", fg="white",
              font=("Segoe UI", 9), bd=0, padx=10,
              command=_browse).pack(side="left", padx=(6, 0))

    # ── Trade definition ──────────────────────────────────────────────────────
    _section(inner, "Trade Definition — what counts as WIN / LOSS")

    trade_frame = tk.Frame(inner, bg="#f0f2f5")
    trade_frame.pack(fill="x", **pad)

    direction_var = tk.StringVar(value="BUY")
    sl_var        = tk.StringVar(value="150")
    tp_var        = tk.StringVar(value="300")
    hold_var      = tk.StringVar(value="50")
    spread_var    = tk.StringVar(value="2.5")
    rr_var        = tk.StringVar(value="R:R = 2.0:1")

    _widgets.update(dict(direction_var=direction_var, sl_var=sl_var,
                         tp_var=tp_var, hold_var=hold_var,
                         spread_var=spread_var, rr_var=rr_var))

    def _update_rr(*_):
        try:
            tp = float(tp_var.get())
            sl = float(sl_var.get())
            rr_var.set(f"R:R = {tp/sl:.2f}:1" if sl > 0 else "R:R = ?")
        except (ValueError, ZeroDivisionError):
            rr_var.set("R:R = ?")

    sl_var.trace_add("write", _update_rr)
    tp_var.trace_add("write", _update_rr)

    rows = [
        ("Direction:",          direction_var, "combo", ["BUY", "SELL", "BOTH"]),
        ("Stop Loss (pips):",   sl_var,        "entry", None),
        ("Take Profit (pips):", tp_var,        "entry", None),
        ("Max hold (candles):", hold_var,      "entry", None),
        ("Spread (pips):",      spread_var,    "entry", None),
    ]
    for lbl, var, kind, opts in rows:
        row = tk.Frame(trade_frame, bg="#f0f2f5")
        row.pack(fill="x", pady=2)
        tk.Label(row, text=lbl, bg="#f0f2f5", fg="#333",
                 font=("Segoe UI", 10), width=22, anchor="w").pack(side="left")
        if kind == "combo":
            cb = ttk.Combobox(row, textvariable=var, values=opts,
                              state="readonly", width=12,
                              font=("Segoe UI", 10))
            cb.pack(side="left")
        else:
            tk.Entry(row, textvariable=var, width=12,
                     font=("Segoe UI", 10), bg="white", fg="#333",
                     relief="solid", bd=1).pack(side="left")

    rr_lbl = tk.Label(trade_frame, textvariable=rr_var,
                      bg="#f0f2f5", fg="#2ecc71",
                      font=("Segoe UI", 10, "bold"))
    rr_lbl.pack(anchor="w", pady=(4, 0))

    # ── ML Settings ───────────────────────────────────────────────────────────
    _section(inner, "ML Settings")

    ml_frame = tk.Frame(inner, bg="#f0f2f5")
    ml_frame.pack(fill="x", **pad)

    smart_var   = tk.BooleanVar(value=True)
    rules_var   = tk.StringVar(value="25")
    est_var     = tk.StringVar(value="300")
    depth_var   = tk.StringVar(value="4")
    cov_var     = tk.StringVar(value="1.0")
    minwr_var   = tk.StringVar(value="55")
    split_var   = tk.StringVar(value="70")

    _widgets.update(dict(smart_var=smart_var, rules_var=rules_var,
                         est_var=est_var, depth_var=depth_var,
                         cov_var=cov_var, minwr_var=minwr_var,
                         split_var=split_var))

    ml_rows = [
        ("Smart features:",         smart_var, "check", None),
        ("Max rules:",              rules_var,  "entry", None),
        ("XGBoost estimators:",     est_var,    "entry", None),
        ("Max depth:",              depth_var,  "entry", None),
        ("Min coverage (% candles):", cov_var,  "entry", None),
        ("Min win rate (%):",       minwr_var,  "entry", None),
        ("Train/test split (%):",   split_var,  "entry", None),
    ]
    for lbl, var, kind, _ in ml_rows:
        row = tk.Frame(ml_frame, bg="#f0f2f5")
        row.pack(fill="x", pady=2)
        tk.Label(row, text=lbl, bg="#f0f2f5", fg="#333",
                 font=("Segoe UI", 10), width=26, anchor="w").pack(side="left")
        if kind == "check":
            tk.Checkbutton(row, variable=var, bg="#f0f2f5",
                           activebackground="#f0f2f5").pack(side="left")
        else:
            tk.Entry(row, textvariable=var, width=10,
                     font=("Segoe UI", 10), bg="white", fg="#333",
                     relief="solid", bd=1).pack(side="left")

    # ── Presets ───────────────────────────────────────────────────────────────
    _section(inner, "Quick Presets")

    preset_row = tk.Frame(inner, bg="#f0f2f5")
    preset_row.pack(fill="x", **pad)

    def _apply_preset(sl, tp, hold, minwr):
        sl_var.set(str(sl))
        tp_var.set(str(tp))
        hold_var.set(str(hold))
        minwr_var.set(str(minwr))

    for label, sl, tp, hold, minwr, color in [
        ("[Conservative]", 200, 400, 100, 60, "#2980b9"),
        ("[Balanced]",     150, 300,  50, 55, "#27ae60"),
        ("[Aggressive]",   100, 150,  20, 55, "#e67e22"),
    ]:
        tk.Button(preset_row, text=label,
                  bg=color, fg="white",
                  font=("Segoe UI", 9, "bold"), bd=0, padx=10, pady=5,
                  command=lambda s=sl, t=tp, h=hold, m=minwr:
                      _apply_preset(s, t, h, m)
                  ).pack(side="left", padx=(0, 8))

    # ── Run button + progress ─────────────────────────────────────────────────
    _section(inner, "Run")

    run_btn = tk.Button(inner,
                        text="Build Strategy from Scratch",
                        bg="#27ae60", fg="white",
                        font=("Segoe UI", 13, "bold"), bd=0, pady=12,
                        command=_on_run)
    run_btn.pack(fill="x", padx=20, pady=(4, 0))
    _widgets['run_btn'] = run_btn

    progress_var = tk.DoubleVar(value=0)
    status_var   = tk.StringVar(value="Ready.")
    elapsed_var  = tk.StringVar(value="")
    base_wr_var  = tk.StringVar(value="")

    _elapsed_var[0] = elapsed_var
    _status_var[0]  = status_var
    _progress_var[0] = progress_var
    _base_wr_var[0] = base_wr_var

    prog_bar = ttk.Progressbar(inner, variable=progress_var,
                               maximum=100, length=400)
    prog_bar.pack(fill="x", padx=20, pady=(8, 0))
    _widgets['prog_bar'] = prog_bar

    tk.Label(inner, textvariable=status_var,
             bg="#f0f2f5", fg="#2c3e50",
             font=("Segoe UI", 10), anchor="w").pack(fill="x", padx=20, pady=2)

    tk.Label(inner, textvariable=elapsed_var,
             bg="#f0f2f5", fg="#7f8c8d",
             font=("Segoe UI", 9)).pack(anchor="w", padx=20)

    info_card = tk.Frame(inner, bg="#eafaf1", relief="flat")
    info_card.pack(fill="x", padx=20, pady=(4, 0))
    tk.Label(info_card, textvariable=base_wr_var,
             bg="#eafaf1", fg="#1e8449",
             font=("Segoe UI", 9, "italic"),
             padx=10, pady=6, justify="left").pack(anchor="w")
    _widgets['info_card'] = info_card

    # ── Results ───────────────────────────────────────────────────────────────
    _section(inner, "Results")
    results_frame = tk.Frame(inner, bg="#f0f2f5")
    results_frame.pack(fill="x", **pad)
    _widgets['results_frame'] = results_frame

    # ── Action buttons ────────────────────────────────────────────────────────
    _section(inner, "Actions")
    action_row = tk.Frame(inner, bg="#f0f2f5")
    action_row.pack(fill="x", **pad)

    use_btn = tk.Button(action_row,
                        text="Use These Rules in Pipeline →",
                        bg="#8e44ad", fg="white",
                        font=("Segoe UI", 10, "bold"), bd=0, padx=12, pady=7,
                        command=_on_activate)
    use_btn.pack(side="left", padx=(0, 8))
    _widgets['use_btn'] = use_btn

    restore_btn = tk.Button(action_row,
                            text="Restore Previous Rules",
                            bg="#7f8c8d", fg="white",
                            font=("Segoe UI", 10), bd=0, padx=12, pady=7,
                            command=_on_restore)
    restore_btn.pack(side="left", padx=(0, 8))

    export_btn = tk.Button(action_row,
                           text="Export Rules JSON",
                           bg="#2980b9", fg="white",
                           font=("Segoe UI", 10), bd=0, padx=12, pady=7,
                           command=_on_export)
    export_btn.pack(side="left")

    # ── Comparison table ──────────────────────────────────────────────────────
    _section(inner, "Method Comparison")
    cmp_frame = tk.Frame(inner, bg="#f0f2f5")
    cmp_frame.pack(fill="x", **pad)
    _widgets['cmp_frame'] = cmp_frame

    # Populate if existing result already exists
    _load_existing_result()
    _render_results()
    _update_comparison()


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _section(parent, title):
    f = tk.Frame(parent, bg="#f0f2f5")
    f.pack(fill="x", padx=20, pady=(12, 0))
    tk.Label(f, text=title.upper(),
             bg="#f0f2f5", fg="#8e44ad",
             font=("Segoe UI", 8, "bold")).pack(anchor="w")
    tk.Frame(f, bg="#8e44ad", height=1).pack(fill="x")


def _card(parent, **kw):
    f = tk.Frame(parent, bg=kw.get('bg', '#fff'),
                 relief="flat", bd=0)
    f.pack(fill="x", pady=3)
    return f


def _load_existing_result():
    global _result
    try:
        from project4_strategy_creation.scratch_discovery import load_scratch_result
        _result = load_scratch_result()
    except Exception:
        _result = None


def _render_results():
    frame = _widgets.get('results_frame')
    if frame is None:
        return
    for w in frame.winfo_children():
        w.destroy()

    if _result is None:
        tk.Label(frame,
                 text="No results yet. Configure settings above and click Run.",
                 bg="#f0f2f5", fg="#95a5a6",
                 font=("Segoe UI", 10, "italic")).pack(anchor="w")
        return

    r = _result
    metrics = r.get('model_metrics', {})

    # Summary card
    sc = tk.Frame(frame, bg="#2c3e50", pady=10)
    sc.pack(fill="x", pady=(0, 8))
    summary_text = (
        f"Candles analyzed:  {r.get('candles_analyzed', 0):,}\n"
        f"Base win rate:     {r.get('base_win_rate', 0):.1%}  (random entry)\n"
        f"Rules found:       {len(r.get('rules', []))}\n"
        f"Test accuracy:     {metrics.get('test_accuracy', 0):.1%}"
        f"  (train: {metrics.get('train_accuracy', 0):.1%})\n"
        f"Time:              {r.get('computation_time_s', 0):.0f}s\n"
        f"Features used:     {r.get('features_used', 0)}"
        f"  ({r.get('original_features', 0)} standard"
        f" + {r.get('smart_features', 0)} smart)"
    )
    tk.Label(sc, text=summary_text,
             bg="#2c3e50", fg="white",
             font=("Courier", 10), justify="left",
             padx=14).pack(anchor="w")

    # Top features
    top_feats = metrics.get('feature_importance_top_20', [])
    if top_feats:
        tf_frame = tk.Frame(frame, bg="#f0f2f5")
        tf_frame.pack(fill="x", pady=(4, 0))
        tk.Label(tf_frame, text="Top Features:",
                 bg="#f0f2f5", fg="#2c3e50",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        for name, imp in top_feats[:15]:
            bar_len  = max(2, int(imp * 300))
            is_smart = name.startswith("SMART_")
            row      = tk.Frame(tf_frame, bg="#f0f2f5")
            row.pack(fill="x")
            color = "#9b59b6" if is_smart else "#2980b9"
            tk.Label(row, text=f"  {'*' if is_smart else ' '} {name[:40]:<42}",
                     bg="#f0f2f5", fg=color,
                     font=("Courier", 8)).pack(side="left")
            tk.Frame(row, bg=color, height=10, width=bar_len).pack(side="left")
            tk.Label(row, text=f" {imp:.3f}",
                     bg="#f0f2f5", fg="#555",
                     font=("Courier", 8)).pack(side="left")

    # Rules
    rules = r.get('rules', [])
    if rules:
        tk.Label(frame, text=f"\nRules ({len(rules)}):",
                 bg="#f0f2f5", fg="#2c3e50",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        best_wr = max(rl['win_rate'] for rl in rules)
        for i, rule in enumerate(rules, 1):
            wr     = rule['win_rate']
            cov    = rule['coverage']
            pips_r = rule['avg_pips']
            pred   = rule['prediction']
            bg     = "#eafaf1" if pred == "WIN" else "#fdf2f2"
            fg     = "#1e8449" if pred == "WIN" else "#922b21"

            rc = tk.Frame(frame, bg=bg, pady=4)
            rc.pack(fill="x", pady=1)

            star = " ★" if wr == best_wr else ""
            tk.Label(rc,
                     text=f"  Rule {i}{star}: WR {wr:.1%}  |  "
                          f"{cov:,} candles ({rule.get('coverage_pct', 0):.1f}%)  |  "
                          f"avg {pips_r:+.0f} pips  |  {pred}",
                     bg=bg, fg=fg,
                     font=("Segoe UI", 9, "bold")).pack(anchor="w")

            for cond in rule.get('conditions', []):
                tk.Label(rc,
                         text=f"    {cond['feature']} {cond['operator']} {cond['value']}",
                         bg=bg, fg="#444",
                         font=("Courier", 8)).pack(anchor="w")


def _update_comparison():
    frame = _widgets.get('cmp_frame')
    if frame is None:
        return
    for w in frame.winfo_children():
        w.destroy()

    # Gather available results from other projects
    rows = []

    # Project 1 — DT results
    p1_dt_path = os.path.join(
        os.path.dirname(_HERE), '..', '..', '..', '..', 'project1_reverse_engineering',
        'outputs', 'analysis_report.json')
    p1_dt_path = os.path.normpath(p1_dt_path)
    if os.path.exists(p1_dt_path):
        try:
            with open(p1_dt_path, encoding='utf-8') as f:
                p1 = json.load(f)
            p1_rules = p1.get('rules', [])
            if p1_rules:
                best_p1 = max(r.get('win_rate', 0) for r in p1_rules)
                rows.append(("Decision Tree (P1)", len(p1_rules),
                             f"{best_p1:.1%}", "—", "~1,100 trades"))
        except Exception:
            pass

    # Project 1 — XGBoost results
    p1_xgb_path = os.path.join(
        os.path.dirname(_HERE), '..', '..', '..', '..', 'project1_reverse_engineering',
        'outputs', 'xgboost_result.json')
    p1_xgb_path = os.path.normpath(p1_xgb_path)
    if os.path.exists(p1_xgb_path):
        try:
            with open(p1_xgb_path, encoding='utf-8') as f:
                p1x = json.load(f)
            p1x_rules = p1x.get('rules', [])
            if p1x_rules:
                best_p1x = max(r.get('win_rate', 0) for r in p1x_rules)
                ta = p1x.get('model_metrics', {}).get('test_accuracy', 0)
                rows.append(("XGBoost (robot, P1)", len(p1x_rules),
                             f"{best_p1x:.1%}", f"{ta:.1%}", "~1,100 trades"))
        except Exception:
            pass

    # Project 4 — Scratch
    if _result:
        srules = _result.get('rules', [])
        best_s = max((r.get('win_rate', 0) for r in srules), default=0)
        ta_s   = _result.get('model_metrics', {}).get('test_accuracy', 0)
        nc     = _result.get('candles_analyzed', 0)
        rows.append(("Scratch Discovery (P4)", len(srules),
                     f"{best_s:.1%}", f"{ta_s:.1%}",
                     f"{nc:,} candles"))

    if not rows:
        tk.Label(frame,
                 text="Run any discovery method to see comparison.",
                 bg="#f0f2f5", fg="#95a5a6",
                 font=("Segoe UI", 9, "italic")).pack(anchor="w")
        return

    headers = ["Method", "Rules", "Best WR", "Test Acc", "Data Points"]
    widths  = [25, 7, 9, 10, 18]

    # Header row
    hrow = tk.Frame(frame, bg="#2c3e50")
    hrow.pack(fill="x")
    for h, w in zip(headers, widths):
        tk.Label(hrow, text=h, bg="#2c3e50", fg="white",
                 font=("Courier", 9, "bold"), width=w, anchor="w").pack(side="left")

    for i, (method, n_rules, best_wr, test_acc, data_pts) in enumerate(rows):
        bg   = "#eafaf1" if "Scratch" in method else ("#f8f9fa" if i % 2 == 0 else "white")
        brow = tk.Frame(frame, bg=bg)
        brow.pack(fill="x")
        for val, w in zip([method, n_rules, best_wr, test_acc, data_pts], widths):
            tk.Label(brow, text=str(val), bg=bg, fg="#2c3e50",
                     font=("Courier", 9), width=w, anchor="w").pack(side="left")

    if any("Scratch" in r[0] for r in rows):
        tk.Label(frame,
                 text="* Scratch Discovery uses 100x more data — patterns are more reliable",
                 bg="#f0f2f5", fg="#8e44ad",
                 font=("Segoe UI", 8, "italic")).pack(anchor="w", pady=(2, 0))


# ─────────────────────────────────────────────────────────────────────────────
# RUN LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def _on_run():
    global _run_thread, _result

    if _run_thread and _run_thread.is_alive():
        messagebox.showinfo("Running",
                            "Discovery is already running. Please wait.")
        return

    candles_path = _widgets['candles_var'].get().strip()
    if not candles_path or not os.path.exists(candles_path):
        messagebox.showerror("Missing File",
                             "Please select a valid H1 candles CSV file.")
        return

    try:
        sl         = float(_widgets['sl_var'].get())
        tp         = float(_widgets['tp_var'].get())
        hold       = int(_widgets['hold_var'].get())
        spread     = float(_widgets['spread_var'].get())
        max_rules  = int(_widgets['rules_var'].get())
        n_est      = int(_widgets['est_var'].get())
        depth      = int(_widgets['depth_var'].get())
        cov_pct    = float(_widgets['cov_var'].get())
        min_wr     = float(_widgets['minwr_var'].get()) / 100.0
        split      = float(_widgets['split_var'].get()) / 100.0
        direction  = _widgets['direction_var'].get()
        use_smart  = _widgets['smart_var'].get()
    except ValueError as exc:
        messagebox.showerror("Invalid Input", f"Check your settings:\n{exc}")
        return

    # Disable run button
    _widgets['run_btn'].configure(state="disabled", bg="#95a5a6",
                                  text="Running...")
    _progress_var[0].set(0)
    _status_var[0].set("Starting...")
    _start_time[0] = time.time()

    # Elapsed timer
    def _tick():
        if _run_thread and _run_thread.is_alive():
            elapsed = time.time() - _start_time[0]
            m, s = divmod(int(elapsed), 60)
            _elapsed_var[0].set(f"Elapsed: {m}m {s:02d}s")
            _panel.after(1000, _tick)

    _panel.after(1000, _tick)

    def _progress(step, total, msg):
        pct = (step / total) * 100
        if _progress_var[0]:
            _panel.after(0, lambda: _progress_var[0].set(pct))
        if _status_var[0]:
            _panel.after(0, lambda m=msg: _status_var[0].set(m))
        # Extract base win rate from labeling step message
        if "WR:" in msg and _base_wr_var[0]:
            try:
                wr_part = msg.split("WR:")[1].split(",")[0].strip()
                _panel.after(0, lambda s=wr_part: _base_wr_var[0].set(
                    f"Base win rate: {s}  (what random entries give you)\n"
                    f"Any rule above this has a real edge."))
            except Exception:
                pass

    def _worker():
        global _result
        try:
            from project4_strategy_creation.scratch_discovery import run_scratch_discovery
            _result = run_scratch_discovery(
                candles_path=candles_path,
                sl_pips=sl,
                tp_pips=tp,
                direction=direction,
                max_hold_candles=hold,
                pip_size=0.01,
                spread_pips=spread,
                use_smart_features=use_smart,
                max_rules=max_rules,
                max_depth=depth,
                n_estimators=n_est,
                min_coverage_pct=cov_pct,
                min_win_rate=min_wr,
                train_test_split=split,
                progress_callback=_progress,
            )
            _panel.after(0, _on_done)
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            _panel.after(0, lambda e=str(exc), t=tb: _on_error(e, t))

    _run_thread = threading.Thread(target=_worker, daemon=True)
    _run_thread.start()


def _on_done():
    _widgets['run_btn'].configure(state="normal", bg="#27ae60",
                                  text="Build Strategy from Scratch")
    _progress_var[0].set(100)
    elapsed = time.time() - _start_time[0]
    m, s = divmod(int(elapsed), 60)
    _elapsed_var[0].set(f"Completed in {m}m {s:02d}s")
    n_rules = len(_result.get('rules', [])) if _result else 0
    _status_var[0].set(f"Done! {n_rules} rules found.")
    _render_results()
    _update_comparison()


def _on_error(msg, tb):
    _widgets['run_btn'].configure(state="normal", bg="#27ae60",
                                  text="Build Strategy from Scratch")
    _progress_var[0].set(0)
    _status_var[0].set(f"Error: {msg}")
    messagebox.showerror("Discovery Failed",
                         f"An error occurred:\n{msg}\n\n{tb[:600]}")


# ─────────────────────────────────────────────────────────────────────────────
# ACTION BUTTONS
# ─────────────────────────────────────────────────────────────────────────────

def _on_activate():
    if _result is None:
        messagebox.showinfo("No Results", "Run discovery first.")
        return
    n = len(_result.get('rules', []))
    confirm = messagebox.askyesno(
        "Use Scratch Rules",
        f"Replace the current analysis_report.json with {n} scratch-discovered rules?\n\n"
        "The original file will be backed up automatically.\n"
        "You can restore it with 'Restore Previous Rules'.")
    if not confirm:
        return
    try:
        from project4_strategy_creation.scratch_discovery import activate_scratch_rules
        count = activate_scratch_rules()
        messagebox.showinfo("Done",
                            f"{count} rules saved to analysis_report.json.\n"
                            "Switch to Backtesting to test them.")
    except Exception as exc:
        messagebox.showerror("Error", str(exc))


def _on_restore():
    confirm = messagebox.askyesno(
        "Restore Previous Rules",
        "Restore analysis_report.json from the backup?\n"
        "This will undo the scratch rules.")
    if not confirm:
        return
    try:
        from project4_strategy_creation.scratch_discovery import restore_previous_rules
        restore_previous_rules()
        messagebox.showinfo("Restored", "Original rules restored.")
    except Exception as exc:
        messagebox.showerror("Error", str(exc))


def _on_export():
    if _result is None:
        messagebox.showinfo("No Results", "Run discovery first.")
        return
    path = filedialog.asksaveasfilename(
        title="Export Rules JSON",
        defaultextension=".json",
        filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        initialfile="scratch_rules.json")
    if not path:
        return
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(_result, f, indent=2, default=str)
        messagebox.showinfo("Exported", f"Rules saved to:\n{path}")
    except Exception as exc:
        messagebox.showerror("Error", str(exc))
