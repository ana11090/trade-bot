"""
Robot Analysis Panel — Displays complete reverse engineering results
Shows all 20 rules, profile, clusters, regimes, and improvement suggestions
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox
import os
import sys
import json
import threading

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)

import state
from helpers import make_copyable

# Design tokens
BG = "#f0f2f5"
WHITE = "white"
GREEN = "#2d8a4e"
RED = "#e94560"
AMBER = "#996600"
DARK = "#1a1a2a"
GREY = "#666666"
MIDGREY = "#555566"

# Module-level widgets
_content_frame = None
_status_label = None
_run_btn = None
_output_text = None


def _load_report():
    """Load analysis_report.json"""
    report_path = os.path.join(
        os.path.dirname(__file__), '..', 'outputs', 'analysis_report.json'
    )
    if not os.path.exists(report_path):
        return None
    try:
        with open(report_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading report: {e}")
        return None


def _load_and_display():
    """Load report and display all sections"""
    global _content_frame, _status_label, _output_text

    # Clear existing content
    if _content_frame:
        for widget in _content_frame.winfo_children():
            widget.destroy()

    report = _load_report()

    if report is None:
        # No report found
        no_data_frame = tk.Frame(_content_frame, bg=WHITE, padx=20, pady=30)
        no_data_frame.pack(fill="both", expand=True)

        tk.Label(
            no_data_frame,
            text="No analysis report found",
            font=("Segoe UI", 14, "bold"),
            bg=WHITE, fg=GREY
        ).pack(pady=(0, 10))

        tk.Label(
            no_data_frame,
            text="Click 'Run Full Analysis' to generate the report,\n"
                 "or run Project 1 scenarios first.",
            font=("Segoe UI", 10),
            bg=WHITE, fg=MIDGREY,
            justify=tk.CENTER
        ).pack()

        if _status_label:
            _status_label.configure(text="No report found", fg=AMBER)
        return

    # Display all sections
    _display_profile(report.get('profile', {}), report.get('trade_count', 0))
    _display_feature_importance(report.get('feature_importance', {}))
    _display_rules(report.get('rules', []))
    _display_clusters(report.get('clusters', []))
    _display_regimes(report.get('regimes', {}))
    _display_evolution(report.get('evolution', []))
    _display_anomalies(report.get('anomalies', {}))
    _display_suggestions(report.get('suggestions', []))

    if _status_label:
        _status_label.configure(
            text=f"Loaded report: {report.get('trade_count', 0)} trades analyzed",
            fg=GREEN
        )


def _display_profile(profile, trade_count):
    """Section 1: Robot Profile"""
    frame = tk.Frame(_content_frame, bg=WHITE, padx=20, pady=15)
    frame.pack(fill="x", padx=10, pady=5)

    tk.Label(
        frame, text="1️⃣ Robot Profile",
        font=("Segoe UI", 13, "bold"), bg=WHITE, fg=DARK
    ).pack(anchor="w", pady=(0, 10))

    # Grid of profile metrics
    grid = tk.Frame(frame, bg=WHITE)
    grid.pack(fill="x")

    metrics = [
        ("Direction", profile.get('direction', '?').replace('_', ' ').title()),
        ("Type", profile.get('duration_category', '?').title()),
        ("Trades", str(trade_count)),
        ("Win Rate", f"{profile.get('win_rate', 0)*100:.1f}%"),
        ("Reward:Risk", f"{profile.get('reward_risk_ratio', 0):.2f}:1"),
        ("Avg Win", f"+{profile.get('avg_win_pips', 0):.0f} pips"),
        ("Avg Loss", f"{profile.get('avg_loss_pips', 0):.0f} pips"),
    ]

    sl = profile.get('sl_pattern', {})
    if sl.get('fixed'):
        metrics.append(("Stop Loss", f"Fixed {sl.get('fixed_value_pips', 0):.0f} pips"))
    else:
        metrics.append(("Stop Loss", "Dynamic"))

    tp = profile.get('tp_pattern', {})
    if tp.get('fixed'):
        metrics.append(("Take Profit", f"Fixed {tp.get('fixed_value_pips', 0):.0f} pips"))
    else:
        metrics.append(("Take Profit", "Dynamic/Trailing"))

    sessions = ', '.join(profile.get('sessions', []))
    if sessions:
        metrics.append(("Sessions", sessions))

    freq = profile.get('frequency_trend', 'stable')
    metrics.append(("Frequency", freq.title()))

    for i, (label, value) in enumerate(metrics):
        row = i // 3
        col = i % 3

        item = tk.Frame(grid, bg=WHITE)
        item.grid(row=row, column=col, sticky="w", padx=15, pady=3)

        tk.Label(item, text=f"{label}:", font=("Segoe UI", 9),
                bg=WHITE, fg=GREY).pack(side=tk.LEFT)
        tk.Label(item, text=value, font=("Segoe UI", 9, "bold"),
                bg=WHITE, fg=DARK).pack(side=tk.LEFT, padx=(5, 0))


def _display_feature_importance(fi):
    """Section 2: Feature Importance"""
    frame = tk.Frame(_content_frame, bg=WHITE, padx=20, pady=15)
    frame.pack(fill="x", padx=10, pady=5)

    tk.Label(
        frame, text="2️⃣ Feature Importance (Top 20)",
        font=("Segoe UI", 13, "bold"), bg=WHITE, fg=DARK
    ).pack(anchor="w", pady=(0, 5))

    train_acc = fi.get('train_accuracy', 0)
    test_acc = fi.get('test_accuracy', 0)
    tk.Label(
        frame,
        text=f"Model accuracy: train={train_acc*100:.1f}%, test={test_acc*100:.1f}%",
        font=("Segoe UI", 9), bg=WHITE, fg=MIDGREY
    ).pack(anchor="w", pady=(0, 10))

    # Top 20 features
    top_20 = fi.get('top_20', [])
    for i, (feat, imp) in enumerate(top_20[:20], 1):
        row = tk.Frame(frame, bg=WHITE)
        row.pack(fill="x", pady=1)

        tk.Label(row, text=f"{i:2d}.", font=("Segoe UI", 8),
                bg=WHITE, fg=GREY, width=3).pack(side=tk.LEFT)
        tk.Label(row, text=feat, font=("Segoe UI", 8),
                bg=WHITE, fg=DARK, anchor="w", width=50).pack(side=tk.LEFT)
        tk.Label(row, text=f"{imp*100:.2f}%", font=("Segoe UI", 8, "bold"),
                bg=WHITE, fg=GREEN).pack(side=tk.LEFT, padx=(10, 0))


def _display_rules(rules):
    """Section 3: Trading Rules - ALL 20 with full details"""
    frame = tk.Frame(_content_frame, bg=WHITE, padx=20, pady=15)
    frame.pack(fill="x", padx=10, pady=5)

    tk.Label(
        frame, text=f"3️⃣ Trading Rules ({len(rules)} discovered)",
        font=("Segoe UI", 13, "bold"), bg=WHITE, fg=DARK
    ).pack(anchor="w", pady=(0, 10))

    # Display ALL rules
    for i, rule in enumerate(rules, 1):
        pred = rule.get('prediction', '?')
        conf = rule.get('confidence', 0)
        cov = rule.get('coverage', 0)
        wr = rule.get('win_rate', 0)
        pips = rule.get('avg_pips', 0)
        conditions = rule.get('conditions', [])

        # Determine color
        if pred == 'WIN' and conf >= 0.65:
            header_color = GREEN
        elif pred == 'LOSS':
            header_color = RED
        else:
            header_color = AMBER

        # Rule card
        card = tk.Frame(frame, bg=WHITE, highlightbackground="#e0e0e0",
                       highlightthickness=1, padx=12, pady=8)
        card.pack(fill="x", pady=3)

        # Header
        header_text = (f"Rule {i}: {pred} — "
                      f"confidence {conf*100:.1f}% | "
                      f"{cov} trades | "
                      f"WR {wr*100:.1f}% | "
                      f"avg {pips:+.0f} pips")
        tk.Label(
            card, text=header_text,
            font=("Segoe UI", 9, "bold"),
            bg=WHITE, fg=header_color
        ).pack(anchor="w")

        # Conditions (indented)
        for cond in conditions:
            feat = cond.get('feature', '?')
            op = cond.get('operator', '?')
            val = cond.get('value', 0)
            cond_text = f"  {feat} {op} {val:.4f}"

            tk.Label(
                card, text=cond_text,
                font=("Segoe UI", 8, "normal"),
                bg=WHITE, fg=MIDGREY
            ).pack(anchor="w", padx=(10, 0))


def _display_clusters(clusters):
    """Section 4: Trade Clusters"""
    frame = tk.Frame(_content_frame, bg=WHITE, padx=20, pady=15)
    frame.pack(fill="x", padx=10, pady=5)

    tk.Label(
        frame, text=f"4️⃣ Trade Clusters ({len(clusters)} groups)",
        font=("Segoe UI", 13, "bold"), bg=WHITE, fg=DARK
    ).pack(anchor="w", pady=(0, 10))

    for cluster in clusters:
        name = cluster.get('name', '?')
        count = cluster.get('count', 0)
        pct = cluster.get('pct', 0)
        avg_pips = cluster.get('avg_pips', 0)
        wr = cluster.get('win_rate', 0)
        dur = cluster.get('avg_duration_min', 0)

        row = tk.Frame(frame, bg=WHITE)
        row.pack(fill="x", pady=2)

        color = GREEN if avg_pips > 0 else RED

        tk.Label(row, text=f"'{name}':", font=("Segoe UI", 9, "bold"),
                bg=WHITE, fg=DARK, width=20, anchor="w").pack(side=tk.LEFT)
        tk.Label(row, text=f"{count} trades ({pct}%)", font=("Segoe UI", 8),
                bg=WHITE, fg=GREY).pack(side=tk.LEFT, padx=5)
        tk.Label(row, text=f"WR {wr*100:.0f}%", font=("Segoe UI", 8),
                bg=WHITE, fg=GREY).pack(side=tk.LEFT, padx=5)
        tk.Label(row, text=f"avg {avg_pips:+.0f} pips", font=("Segoe UI", 8, "bold"),
                bg=WHITE, fg=color).pack(side=tk.LEFT, padx=5)
        tk.Label(row, text=f"({dur:.0f}min)", font=("Segoe UI", 8),
                bg=WHITE, fg=GREY).pack(side=tk.LEFT)


def _display_regimes(regimes):
    """Section 5: Market Regimes"""
    frame = tk.Frame(_content_frame, bg=WHITE, padx=20, pady=15)
    frame.pack(fill="x", padx=10, pady=5)

    tk.Label(
        frame, text="5️⃣ Market Regime Performance",
        font=("Segoe UI", 13, "bold"), bg=WHITE, fg=DARK
    ).pack(anchor="w", pady=(0, 10))

    # Display each regime category
    for regime_name, regime_data in regimes.items():
        if not isinstance(regime_data, dict):
            continue

        tk.Label(
            frame, text=f"{regime_name.replace('_', ' ').title()}:",
            font=("Segoe UI", 10, "bold"), bg=WHITE, fg=MIDGREY
        ).pack(anchor="w", pady=(5, 3))

        for sub_name, sub_data in regime_data.items():
            if isinstance(sub_data, dict) and 'win_rate' in sub_data:
                count = sub_data.get('count', 0)
                wr = sub_data.get('win_rate', 0)
                pips = sub_data.get('avg_pips', 0)

                row = tk.Frame(frame, bg=WHITE)
                row.pack(fill="x", pady=1, padx=(15, 0))

                tk.Label(row, text=f"{sub_name}:", font=("Segoe UI", 8),
                        bg=WHITE, fg=GREY, width=25, anchor="w").pack(side=tk.LEFT)
                tk.Label(row, text=f"WR {wr*100:.0f}%", font=("Segoe UI", 8),
                        bg=WHITE, fg=GREY).pack(side=tk.LEFT, padx=5)
                color = GREEN if pips > 0 else RED
                tk.Label(row, text=f"avg {pips:+.0f} pips", font=("Segoe UI", 8),
                        bg=WHITE, fg=color).pack(side=tk.LEFT, padx=5)
                tk.Label(row, text=f"({count} trades)", font=("Segoe UI", 8),
                        bg=WHITE, fg=GREY).pack(side=tk.LEFT)


def _display_evolution(evolution):
    """Section 6: Time Period Evolution"""
    frame = tk.Frame(_content_frame, bg=WHITE, padx=20, pady=15)
    frame.pack(fill="x", padx=10, pady=5)

    tk.Label(
        frame, text="6️⃣ Time Period Evolution",
        font=("Segoe UI", 13, "bold"), bg=WHITE, fg=DARK
    ).pack(anchor="w", pady=(0, 10))

    for period in evolution:
        year = period.get('period', '?')
        trades = period.get('trades', 0)
        wr = period.get('win_rate', 0)
        pips = period.get('avg_pips', 0)
        per_month = period.get('trades_per_month', 0)

        row = tk.Frame(frame, bg=WHITE)
        row.pack(fill="x", pady=2)

        tk.Label(row, text=f"{year}:", font=("Segoe UI", 9, "bold"),
                bg=WHITE, fg=DARK, width=8).pack(side=tk.LEFT)
        tk.Label(row, text=f"{trades} trades", font=("Segoe UI", 8),
                bg=WHITE, fg=GREY).pack(side=tk.LEFT, padx=5)
        tk.Label(row, text=f"WR {wr*100:.0f}%", font=("Segoe UI", 8),
                bg=WHITE, fg=GREY).pack(side=tk.LEFT, padx=5)
        color = GREEN if pips > 0 else RED
        tk.Label(row, text=f"avg {pips:+.0f} pips", font=("Segoe UI", 8),
                bg=WHITE, fg=color).pack(side=tk.LEFT, padx=5)
        tk.Label(row, text=f"({per_month:.1f}/month)", font=("Segoe UI", 8),
                bg=WHITE, fg=GREY).pack(side=tk.LEFT)


def _display_anomalies(anomalies):
    """Section 7: Anomalies"""
    frame = tk.Frame(_content_frame, bg=WHITE, padx=20, pady=15)
    frame.pack(fill="x", padx=10, pady=5)

    count = anomalies.get('count', 0)
    pct = anomalies.get('pct', 0)
    anom_wr = anomalies.get('anomaly_win_rate', 0)
    norm_wr = anomalies.get('normal_win_rate', 0)

    tk.Label(
        frame, text=f"7️⃣ Anomalies ({count} outliers, {pct}%)",
        font=("Segoe UI", 13, "bold"), bg=WHITE, fg=DARK
    ).pack(anchor="w", pady=(0, 5))

    tk.Label(
        frame,
        text=f"Anomaly WR: {anom_wr*100:.0f}%  vs  Normal WR: {norm_wr*100:.0f}%",
        font=("Segoe UI", 9), bg=WHITE, fg=MIDGREY
    ).pack(anchor="w")


def _display_suggestions(suggestions):
    """Section 8: Improvement Suggestions"""
    frame = tk.Frame(_content_frame, bg=WHITE, padx=20, pady=15)
    frame.pack(fill="x", padx=10, pady=5)

    tk.Label(
        frame, text=f"8️⃣ Improvement Suggestions ({len(suggestions)} found)",
        font=("Segoe UI", 13, "bold"), bg=WHITE, fg=DARK
    ).pack(anchor="w", pady=(0, 10))

    if not suggestions:
        tk.Label(
            frame, text="No significant improvements detected.",
            font=("Segoe UI", 9, "italic"), bg=WHITE, fg=GREY
        ).pack(anchor="w")
        return

    for i, sugg in enumerate(suggestions, 1):
        desc = sugg.get('description', '?')
        impact = sugg.get('impact', '?')
        trades_filtered = sugg.get('trades_filtered', 0)
        pct_filtered = sugg.get('pct_filtered', 0)

        card = tk.Frame(frame, bg="#f0f8ff", padx=10, pady=8)
        card.pack(fill="x", pady=3)

        tk.Label(
            card, text=f"{i}. {desc}",
            font=("Segoe UI", 9, "bold"), bg="#f0f8ff", fg=DARK
        ).pack(anchor="w")

        tk.Label(
            card, text=impact,
            font=("Segoe UI", 8), bg="#f0f8ff", fg=MIDGREY
        ).pack(anchor="w", padx=(10, 0))

        tk.Label(
            card, text=f"Filters out {trades_filtered} trades ({pct_filtered}%)",
            font=("Segoe UI", 8, "italic"), bg="#f0f8ff", fg=GREY
        ).pack(anchor="w", padx=(10, 0))


def _run_full_analysis():
    """Run complete pipeline in background thread"""
    global _run_btn, _status_label

    def _worker():
        try:
            p1_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
            if p1_dir not in sys.path:
                sys.path.insert(0, p1_dir)

            # Step 1: alignment
            state.window.after(0, lambda: _status_label.configure(
                text="[1/3] Aligning trades to candles...", fg=GREY))
            from step1_align_price import align_all_timeframes
            align_all_timeframes()

            # Step 2: feature matrix
            state.window.after(0, lambda: _status_label.configure(
                text="[2/3] Computing indicators (may take ~5 min)...", fg=GREY))
            from step2_compute_indicators import compute_features
            compute_features()

            # Step 3: analysis
            state.window.after(0, lambda: _status_label.configure(
                text="[3/3] Running analysis...", fg=GREY))
            from analyze import run_analysis
            run_analysis()

            state.window.after(0, _load_and_display)
            state.window.after(0, lambda: _status_label.configure(
                text="Analysis complete!", fg=GREEN))

        except Exception as e:
            import traceback
            err = traceback.format_exc()
            state.window.after(0, lambda: _status_label.configure(
                text=f"Error: {e}", fg=RED))
            print(f"[robot_analysis] Pipeline error:\n{err}")
        finally:
            state.window.after(0, lambda: _run_btn.configure(
                state="normal", text="Run Full Analysis"))

    _run_btn.configure(state="disabled", text="Running...")
    _status_label.configure(text="Running full pipeline...", fg=GREY)
    threading.Thread(target=_worker, daemon=True).start()


def build_panel(parent):
    """Build the robot analysis panel"""
    global _content_frame, _status_label, _run_btn, _output_text

    panel = tk.Frame(parent, bg=BG)

    # Header
    header = tk.Frame(panel, bg=WHITE, pady=20)
    header.pack(fill="x", padx=20, pady=(20, 10))

    tk.Label(
        header, text="🤖 Robot Analysis",
        bg=WHITE, fg=DARK, font=("Segoe UI", 18, "bold")
    ).pack()

    tk.Label(
        header,
        text="Complete reverse engineering — profile, rules, clusters, regimes, suggestions",
        bg=WHITE, fg=GREY, font=("Segoe UI", 11)
    ).pack(pady=(5, 0))

    # Action buttons
    btn_frame = tk.Frame(panel, bg=BG)
    btn_frame.pack(pady=10)

    _run_btn = tk.Button(
        btn_frame, text="Run Full Analysis",
        command=_run_full_analysis,
        bg="#667eea", fg="white", font=("Segoe UI", 10, "bold"),
        relief=tk.FLAT, cursor="hand2", padx=20, pady=8
    )
    _run_btn.pack(side=tk.LEFT, padx=5)

    refresh_btn = tk.Button(
        btn_frame, text="Refresh from file",
        command=_load_and_display,
        bg=GREEN, fg="white", font=("Segoe UI", 10, "bold"),
        relief=tk.FLAT, cursor="hand2", padx=20, pady=8
    )
    refresh_btn.pack(side=tk.LEFT, padx=5)

    # Status label
    _status_label = tk.Label(
        panel, text="Ready", font=("Segoe UI", 9, "italic"),
        bg=BG, fg=GREY
    )
    _status_label.pack(pady=(0, 10))

    # Scrollable content area
    canvas = tk.Canvas(panel, bg=BG, highlightthickness=0)
    scrollbar = tk.Scrollbar(panel, orient="vertical", command=canvas.yview)
    _content_frame = tk.Frame(canvas, bg=BG)

    _content_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )

    canvas.create_window((0, 0), window=_content_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True, padx=(20, 0))
    scrollbar.pack(side="right", fill="y", padx=(0, 20))

    # Initial load
    _load_and_display()

    return panel


def refresh():
    """Refresh the panel (called when panel becomes active)"""
    _load_and_display()
