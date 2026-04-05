"""
Project 2 - View Results Panel
View backtest results and HTML report
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox
import os
import sys
import webbrowser
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from helpers import make_copyable

# Module-level variables
_output_text = None
_summary_frame = None
_sort_key = ['net_total_pips']  # default sort
_sort_reverse = [True]


def load_summary_stats():
    """Load backtest matrix results from strategy_backtester output"""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
    matrix_file = os.path.join(project_root, 'project2_backtesting/outputs/backtest_matrix.json')

    if not os.path.exists(matrix_file):
        return None

    try:
        import json
        with open(matrix_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data
    except Exception as e:
        print(f"Error loading backtest matrix: {e}")
        return None


def open_html_report():
    """Open the HTML report in browser"""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
    report_file = os.path.join(project_root, 'project2_backtesting/outputs/backtest_report.html')

    if not os.path.exists(report_file):
        messagebox.showerror(
            "Report Not Found",
            "HTML report not found!\n\n"
            "Please run the backtest first."
        )
        return

    # Open in browser
    try:
        webbrowser.open(f'file:///{report_file}')
    except Exception as e:
        messagebox.showerror(
            "Error Opening Report",
            f"Failed to open report:\n{str(e)}"
        )


def open_output_folder():
    """Open the outputs folder in file explorer"""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
    outputs_folder = os.path.join(project_root, 'project2_backtesting/outputs')

    if not os.path.exists(outputs_folder):
        messagebox.showwarning(
            "Folder Not Found",
            "Outputs folder not found!\n\n"
            "Please run the backtest first."
        )
        return

    # Open folder
    try:
        if sys.platform == 'win32':
            os.startfile(outputs_folder)
        elif sys.platform == 'darwin':  # macOS
            os.system(f'open "{outputs_folder}"')
        else:  # Linux
            os.system(f'xdg-open "{outputs_folder}"')
    except Exception as e:
        messagebox.showerror(
            "Error Opening Folder",
            f"Failed to open folder:\n{str(e)}"
        )


def display_summary(output_text, summary_frame):
    """Display ALL backtest results as sortable cards."""
    for widget in summary_frame.winfo_children():
        widget.destroy()

    data = load_summary_stats()
    if data is None:
        tk.Label(summary_frame, text="No backtest results found. Run the backtest first.",
                 font=("Arial", 10, "italic"), bg="#ffffff", fg="#999").pack(pady=20)
        output_text.delete(1.0, tk.END)
        output_text.insert(tk.END, "No backtest results available.\n\nRun the backtest first.")
        return

    results = data.get('results', [])
    if not results:
        output_text.delete(1.0, tk.END)
        output_text.insert(tk.END, "Backtest matrix is empty. Re-run the backtest.\n")
        return

    # ── Header info ──
    info_frame = tk.Frame(summary_frame, bg="#e8f5e9", padx=15, pady=10)
    info_frame.pack(fill="x", padx=10, pady=(0, 5))

    combos = data.get('combinations', len(results))
    elapsed = data.get('elapsed_seconds', 0)
    spread = data.get('spread_pips', 0)
    gen_at = data.get('generated_at', '?')
    with_trades = sum(1 for r in results if r.get('total_trades', 0) > 0)

    tk.Label(info_frame, text=f"Backtest Matrix — {combos} combinations ({with_trades} with trades)",
             bg="#e8f5e9", fg="#2e7d32", font=("Arial", 11, "bold")).pack(anchor="w")
    tk.Label(info_frame, text=f"Generated: {gen_at}  |  Spread: {spread} pips  |  Time: {elapsed:.0f}s",
             bg="#e8f5e9", fg="#555", font=("Arial", 9)).pack(anchor="w")

    # ── Sort buttons ──
    sort_frame = tk.Frame(summary_frame, bg="#ffffff")
    sort_frame.pack(fill="x", padx=10, pady=(5, 5))

    tk.Label(sort_frame, text="Sort by:", font=("Arial", 9, "bold"),
             bg="#ffffff", fg="#555").pack(side=tk.LEFT)

    def _resort(key, reverse=True):
        _sort_key[0] = key
        _sort_reverse[0] = reverse
        display_summary(output_text, summary_frame)

    for label, key, rev in [
        ("Net Pips ↓", "net_total_pips", True),
        ("Win Rate ↓", "win_rate", True),
        ("Profit Factor ↓", "net_profit_factor", True),
        ("Max DD ↑ (lowest first)", "max_dd_pips", False),
        ("Trades ↓", "total_trades", True),
        ("Avg Pips ↓", "net_avg_pips", True),
    ]:
        tk.Button(sort_frame, text=label, font=("Arial", 8),
                  bg="#667eea", fg="white", relief=tk.FLAT, padx=6, pady=2,
                  command=lambda k=key, r=rev: _resort(k, r)).pack(side=tk.LEFT, padx=2)

    # ── Filter: hide 0-trade results ──
    show_zero_var = tk.BooleanVar(value=False)
    tk.Checkbutton(sort_frame, text="Show 0-trade results", variable=show_zero_var,
                    bg="#ffffff", font=("Arial", 8),
                    command=lambda: display_summary(output_text, summary_frame)).pack(side=tk.RIGHT)

    # ── Sort results ──
    sorted_results = sorted(results, key=lambda r: r.get(_sort_key[0], 0), reverse=_sort_reverse[0])

    # Filter 0-trade if checkbox unchecked
    if not show_zero_var.get():
        sorted_results = [r for r in sorted_results if r.get('total_trades', 0) > 0]

    # ── Scrollable results area ──
    results_canvas = tk.Canvas(summary_frame, bg="#ffffff", highlightthickness=0)
    results_scroll = tk.Scrollbar(summary_frame, orient="vertical", command=results_canvas.yview)
    results_canvas.configure(yscrollcommand=results_scroll.set)
    results_scroll.pack(side=tk.RIGHT, fill="y")
    results_canvas.pack(fill="both", expand=True, padx=10)

    results_inner = tk.Frame(results_canvas, bg="#ffffff")
    results_wid = results_canvas.create_window((0, 0), window=results_inner, anchor="nw")
    results_inner.bind("<Configure>", lambda e: results_canvas.configure(scrollregion=results_canvas.bbox("all")))
    results_canvas.bind("<Configure>", lambda e: results_canvas.itemconfig(results_wid, width=e.width))

    # Mousewheel
    def _mw(event):
        results_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    results_canvas.bind("<Enter>", lambda e: results_canvas.bind_all("<MouseWheel>", _mw))
    results_canvas.bind("<Leave>", lambda e: results_canvas.unbind_all("<MouseWheel>"))

    # Count label
    count_text = f"Showing {len(sorted_results)} of {len(results)} results"
    if _sort_key[0] == 'max_dd_pips' and not _sort_reverse[0]:
        count_text += " (sorted by lowest drawdown — safer strategies first)"
    tk.Label(results_inner, text=count_text, font=("Arial", 9),
             bg="#ffffff", fg="#888").pack(anchor="w", pady=(0, 5))

    # ── Result cards ──
    for i, r in enumerate(sorted_results):
        net_pips = r.get('net_total_pips', 0)
        wr = r.get('win_rate', 0)
        pf = r.get('net_profit_factor', 0)
        trades = r.get('total_trades', 0)
        avg = r.get('net_avg_pips', 0)
        dd = r.get('max_dd_pips', 0)
        best = r.get('best_trade', 0)
        worst = r.get('worst_trade', 0)

        is_profitable = net_pips > 0 and trades > 0
        bg_color = "#f8fff8" if is_profitable else "#fff8f8" if trades > 0 else "#f5f5f5"
        border_color = "#28a745" if is_profitable else "#dc3545" if trades > 0 else "#ccc"

        card = tk.Frame(results_inner, bg=bg_color, highlightbackground=border_color,
                         highlightthickness=1, padx=12, pady=6)
        card.pack(fill="x", pady=2)

        # Row 1: rank + strategy name + save button
        header_row = tk.Frame(card, bg=bg_color)
        header_row.pack(fill="x")

        rank_label = f"#{i+1}"
        header_text = f"{rank_label}  {r.get('rule_combo', '?')}  ×  {r.get('exit_strategy', '?')}"
        tk.Label(header_row, text=header_text, bg=bg_color, fg="#333",
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT)

        # Save button
        try:
            from shared.saved_rules import build_save_button
            # Save the result metadata
            save_data = {
                'rule_combo': r.get('rule_combo', '?'),
                'exit_strategy': r.get('exit_strategy', '?'),
                'exit_name': r.get('exit_name', '?'),
                'prediction': 'WIN',
                'win_rate': wr,
                'net_total_pips': net_pips,
                'net_profit_factor': pf,
                'total_trades': trades,
                'max_dd_pips': dd,
            }
            sb = build_save_button(header_row, save_data, source="Backtest Result", bg=bg_color)
            sb.pack(side=tk.RIGHT, padx=3)
        except Exception:
            pass

        # Row 2: metrics
        if trades > 0:
            # Format win rate correctly
            wr_str = f"{wr:.1f}%" if wr > 1 else f"{wr*100:.1f}%"
            wr_color = "#28a745" if (wr if wr > 1 else wr*100) >= 55 else "#dc3545"
            pf_color = "#28a745" if pf >= 1.5 else "#dc3545" if pf < 1.0 else "#ff8f00"
            net_color = "#28a745" if net_pips > 0 else "#dc3545"

            metrics_row = tk.Frame(card, bg=bg_color)
            metrics_row.pack(fill="x", pady=(2, 0))

            for label, value, color in [
                ("Trades", str(trades), "#333"),
                ("WR", wr_str, wr_color),
                ("PF", f"{pf:.2f}", pf_color),
                ("Net", f"{net_pips:+,.0f} pips", net_color),
                ("Avg", f"{avg:+.1f} pips", net_color),
                ("MaxDD", f"{dd:,.0f} pips", "#dc3545"),
                ("Best", f"{best:+.0f}", "#28a745"),
                ("Worst", f"{worst:+.0f}", "#dc3545"),
            ]:
                tk.Label(metrics_row, text=f"{label}: ", bg=bg_color, fg="#888",
                         font=("Arial", 8)).pack(side=tk.LEFT)
                tk.Label(metrics_row, text=value, bg=bg_color, fg=color,
                         font=("Arial", 8, "bold")).pack(side=tk.LEFT, padx=(0, 10))

            # Breach counter (from precomputed data)
            breaches = r.get('breaches', {})
            if breaches:
                from shared.tooltip import add_tooltip

                blown = breaches.get('blown_count', 0)

                breach_row = tk.Frame(card, bg=bg_color)
                breach_row.pack(fill="x", pady=(2, 0))

                if blown == 0:
                    breach_lbl = tk.Label(breach_row, text="✅ 0 breaches — prop firm safe",
                                          bg=bg_color, fg="#28a745", font=("Arial", 8, "bold"))
                    breach_lbl.pack(side=tk.LEFT)
                    add_tooltip(breach_lbl,
                                "This strategy NEVER exceeded the prop firm's\n"
                                "daily or total drawdown limits across the\n"
                                "entire backtest period. Safe to trade.")
                else:
                    daily_b = breaches.get('daily_breaches', 0)
                    total_b = breaches.get('total_breaches', 0)
                    surv = breaches.get('survival_rate_per_month', 0)
                    wd = breaches.get('worst_daily_pct', 0)
                    wt = breaches.get('worst_total_pct', 0)

                    # Build date list for tooltip
                    import datetime
                    all_blow_dates = sorted(set(
                        breaches.get('daily_breach_dates', []) +
                        breaches.get('total_breach_dates', [])
                    ))
                    # Format as month/year: "2008-10-15" → "Oct 2008"
                    blow_months = []
                    for d in all_blow_dates:
                        try:
                            dt = datetime.datetime.strptime(d[:10], '%Y-%m-%d')
                            blow_months.append(dt.strftime('%b %Y'))
                        except Exception:
                            blow_months.append(d[:7])

                    dates_text = ""
                    if blow_months:
                        dates_text = "\n\nBlown in:\n"
                        for bm in blow_months:
                            dates_text += f"  • {bm}\n"

                    color = "#dc3545" if blown > 3 else "#e67e22"
                    breach_lbl = tk.Label(breach_row,
                                          text=f"💀 {blown} blows (daily:{daily_b} total:{total_b}) — "
                                               f"worst daily:{wd:.1f}%/5% total:{wt:.1f}%/10% — survival {surv}%/mo",
                                          bg=bg_color, fg=color, font=("Arial", 8, "bold"))
                    breach_lbl.pack(side=tk.LEFT)
                    add_tooltip(breach_lbl,
                                f"💀 {blown} blows = account blown {blown} times\n"
                                f"  Each blow = 1 failed challenge = 1 fee lost\n\n"
                                f"daily:{daily_b} = {daily_b} times lost ≥5% in a single day\n"
                                f"total:{total_b} = {total_b} times equity dropped ≥10% from peak\n\n"
                                f"worst daily: {wd:.1f}% (limit: 5%)\n"
                                f"worst total: {wt:.1f}% (limit: 10%)\n\n"
                                f"survival {surv}%/mo = {surv}% of months had no blowup"
                                f"{dates_text}")
        else:
            tk.Label(card, text="0 trades — rule conditions never triggered",
                     bg=bg_color, fg="#888", font=("Arial", 9, "italic")).pack(anchor="w")

    # ── Update detailed text output too ──
    output_text.delete(1.0, tk.END)
    output_text.insert(tk.END, f"{'Rank':<5} {'Rule Combo':<22} {'Exit Strategy':<28} "
                                f"{'Trades':>6} {'WR':>7} {'PF':>6} {'Net Pips':>10} {'MaxDD':>8} "
                                f"{'Blows':>6} {'DailyDD%':>8} {'TotalDD%':>8}\n")
    output_text.insert(tk.END, "-" * 130 + "\n")

    for i, r in enumerate(sorted_results):
        trades = r.get('total_trades', 0)
        wr = r.get('win_rate', 0)
        wr_str = f"{wr:.1f}%" if wr > 1 else f"{wr*100:.1f}%"
        pf = r.get('net_profit_factor', 0)
        net = r.get('net_total_pips', 0)
        avg = r.get('net_avg_pips', 0)
        dd = r.get('max_dd_pips', 0)
        rule = r.get('rule_combo', '?')[:20]
        exit_s = r.get('exit_strategy', '?')[:26]

        blown_str = "-"
        wd_str = "-"
        wt_str = "-"
        breaches = r.get('breaches', {})
        if breaches:
            blown_str = str(breaches.get('blown_count', 0))
            wd_str = f"{breaches.get('worst_daily_pct', 0):.1f}%"
            wt_str = f"{breaches.get('worst_total_pct', 0):.1f}%"

        output_text.insert(tk.END, f"#{i+1:<4} {rule:<22} {exit_s:<28} "
                                    f"{trades:>6} {wr_str:>7} {pf:>5.2f} {net:>+10,.0f} {dd:>8,.0f} "
                                    f"{blown_str:>6} {wd_str:>8} {wt_str:>8}\n")

    output_text.see("1.0")


def build_panel(parent):
    """Build the view results panel"""
    global _output_text, _summary_frame

    panel = tk.Frame(parent, bg="#ffffff")

    # Title
    title = tk.Label(
        panel,
        text="Backtest Results",
        font=("Arial", 16, "bold"),
        bg="#ffffff",
        fg="#333333"
    )
    title.pack(pady=(20, 10))

    subtitle = tk.Label(
        panel,
        text="View performance metrics and HTML report",
        font=("Arial", 10),
        bg="#ffffff",
        fg="#666666"
    )
    subtitle.pack(pady=(0, 20))

    # Action buttons
    button_frame = tk.Frame(panel, bg="#ffffff")
    button_frame.pack(pady=10)

    open_report_btn = tk.Button(
        button_frame,
        text="Open HTML Report",
        command=open_html_report,
        bg="#667eea",
        fg="white",
        font=("Arial", 10, "bold"),
        relief=tk.FLAT,
        cursor="hand2",
        padx=20,
        pady=8
    )
    open_report_btn.pack(side=tk.LEFT, padx=5)

    open_folder_btn = tk.Button(
        button_frame,
        text="Open Outputs Folder",
        command=open_output_folder,
        bg="#667eea",
        fg="white",
        font=("Arial", 10, "bold"),
        relief=tk.FLAT,
        cursor="hand2",
        padx=20,
        pady=8
    )
    open_folder_btn.pack(side=tk.LEFT, padx=5)

    refresh_btn = tk.Button(
        button_frame,
        text="Refresh Results",
        command=lambda: display_summary(_output_text, _summary_frame),
        bg="#28a745",
        fg="white",
        font=("Arial", 10, "bold"),
        relief=tk.FLAT,
        cursor="hand2",
        padx=20,
        pady=8
    )
    refresh_btn.pack(side=tk.LEFT, padx=5)

    # Summary frame (for key metrics)
    _summary_frame = tk.Frame(panel, bg="#ffffff")
    _summary_frame.pack(fill="both", expand=True, padx=20, pady=10)

    # Output text (for detailed stats)
    output_frame = tk.LabelFrame(
        panel,
        text="Detailed Statistics",
        font=("Arial", 11, "bold"),
        bg="#ffffff",
        fg="#333333",
        padx=10,
        pady=10
    )
    output_frame.pack(fill="both", expand=True, padx=20, pady=10)

    _output_text = scrolledtext.ScrolledText(
        output_frame,
        height=8,
        font=("Courier", 9),
        bg="#f8f9fa",
        fg="#333333",
        wrap=tk.WORD
    )
    _output_text.pack(fill="both", expand=True)

    # Initial load
    display_summary(_output_text, _summary_frame)

    return panel


def refresh():
    """Refresh the panel (called when panel becomes active)"""
    global _output_text, _summary_frame
    if _output_text is not None and _summary_frame is not None:
        display_summary(_output_text, _summary_frame)
