"""
Project 2 - Run Backtest Panel
Execute backtest and monitor progress
"""

# WHY (Phase 33 Fix 7): Progress display shows approximate P&L using a
#      hardcoded 6.67 dollar-per-pip (XAUUSD). Load from config at module
#      import so non-XAUUSD users see correct dollar estimates.
# CHANGED: April 2026 — Phase 33 Fix 7 — config-driven $/pip calculation
#          (Ref: trade_bot_audit_round2_partC.pdf HIGH item #87 pg.31)
_rb_dollar_per_pip = 6.67
try:
    from project2_backtesting.panels.configuration import load_config as _rb_load_config
    _rb_cfg = _rb_load_config()
    _rb_account = float(_rb_cfg.get('account_size', 100000))
    _rb_risk_pct = float(_rb_cfg.get('risk_per_trade', 1.0))
    _rb_sl_pips = float(_rb_cfg.get('default_sl_pips', 150))
    _rb_pip_value = float(_rb_cfg.get('pip_value', 10.0))
    _rb_dollar_per_pip = (_rb_account * _rb_risk_pct / 100) / (_rb_sl_pips * _rb_pip_value)
except Exception:
    _rb_dollar_per_pip = 6.67  # fallback to XAUUSD default

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import os
import sys
import threading
import json

# Module-level variables
_output_text   = None
_progress_label = None
_progress_bar  = None
_step_label    = None
_run_button    = None
_best_label    = None
_running       = False
_best_result      = [None]  # Track best result
import threading as _rb_threading
_best_result_lock = _rb_threading.Lock()  # Phase 69 Fix 38: guard concurrent writes
_rule_vars     = []  # list of (BooleanVar, rule_dict) tuples
_current_rules = []  # loaded rules
_current_source_path = [None]
_source_var    = None
_rule_canvas   = None
_rule_inner    = None
_use_safety_var  = None  # BooleanVar for safety stops toggle
_multi_tf_var    = None  # BooleanVar for multi-TF entry testing

# WHY (Phase A.21): exceptions during Run Backtest were being formatted
#      into output_text via format_exc(), but the user reported they
#      could not see the traceback — either due to scroll position,
#      panel clearing, or the exception firing in a UI-thread callback
#      that doesn't reach the worker's outer catch. Write a copy to
#      disk so it survives everything.
# CHANGED: April 2026 — Phase A.21 — disk-resident error log
import datetime as _a21_datetime
import traceback as _a21_traceback
import os as _a21_os

# WHY (Phase A.23): A.21's single path with bare except:pass was
#      silently swallowing write failures. Try multiple locations
#      and record every attempt result so we can see what's wrong
#      even if every write fails.
# CHANGED: April 2026 — Phase A.23
import tempfile as _a23_tempfile
import sys as _a23_sys

# Primary: under panels/../outputs (where A.21 originally pointed)
_A21_ERROR_LOG_PATH = _a21_os.path.normpath(_a21_os.path.join(
    _a21_os.path.dirname(_a21_os.path.abspath(__file__)),
    '..', 'outputs', 'last_backtest_error.txt'
))

# Backup 1: cwd at process start
_A23_ERROR_LOG_CWD = _a21_os.path.abspath('last_backtest_error.txt')

# Backup 2: system temp dir (always writable)
_A23_ERROR_LOG_TEMP = _a21_os.path.join(
    _a23_tempfile.gettempdir(), 'trade_bot_last_backtest_error.txt'
)

# Track every write attempt so the panel can show what happened
_a23_last_write_attempts = []


def _a21_write_error(label, exc):
    """Write a full traceback to disk in multiple locations.

    Phase A.23: writes to three locations (panel outputs, cwd, temp dir)
    and records the result of each attempt to _a23_last_write_attempts.
    Prints a banner to stderr that lists where the file landed so the
    user can find it without guessing.
    """
    global _a23_last_write_attempts
    _a23_last_write_attempts = []

    # Build the content once
    try:
        body_lines = [
            f"=== {label} ===",
            f"Time:  {_a21_datetime.datetime.now().isoformat()}",
            f"Type:  {type(exc).__name__}",
            f"Error: {exc!r}",
            "",
            "--- Full traceback ---",
            _a21_traceback.format_exc(),
            "",
        ]
        body = "\n".join(body_lines)
    except Exception as _build_err:
        body = f"=== {label} ===\nFailed to build error body: {_build_err!r}\n"

    targets = [
        ("primary",  _A21_ERROR_LOG_PATH),
        ("cwd",      _A23_ERROR_LOG_CWD),
        ("temp",     _A23_ERROR_LOG_TEMP),
    ]

    for name, path in targets:
        try:
            _a21_os.makedirs(_a21_os.path.dirname(path) or '.', exist_ok=True)
            # Use errors='replace' so surrogates in the traceback don't crash
            with open(path, 'w', encoding='utf-8', errors='replace') as f:
                f.write(body)
            _a23_last_write_attempts.append((name, path, "OK"))
        except Exception as _write_err:
            _a23_last_write_attempts.append((name, path, f"FAIL: {_write_err!r}"))

    # Print banner to stderr regardless
    try:
        _a23_sys.stderr.write("\n" + "=" * 70 + "\n")
        _a23_sys.stderr.write(f"[A.23] _a21_write_error called for: {label}\n")
        for name, path, result in _a23_last_write_attempts:
            _a23_sys.stderr.write(f"  [{name:8}] {path}\n             {result}\n")
        _a23_sys.stderr.write("=" * 70 + "\n")
        _a23_sys.stderr.flush()
    except Exception:
        pass


# WHY (Phase A.22): UI-thread exceptions raised inside Tk after(0, fn)
#      callbacks bypass every Python try/except in the worker thread
#      because they run on the main loop. Tk routes them through
#      tk.Tk.report_callback_exception which defaults to printing to
#      stderr. A.21's catches never see them, so the disk error log
#      stays empty and the user sees only a one-line label.
#
#      Install a Tk-level handler that writes the same disk log A.21
#      uses. This works regardless of which thread raised the error.
# CHANGED: April 2026 — Phase A.22
_a22_installed = False


def _a22_install_tk_handler(any_widget):
    """Install a custom report_callback_exception on the toplevel.

    Idempotent — safe to call from multiple panel constructions.
    """
    global _a22_installed
    try:
        win = any_widget.winfo_toplevel()
    except Exception:
        return
    if _a22_installed:
        return

    def _handler(exc_type, exc_value, exc_tb):
        try:
            # Reuse A.21's helper so all errors land in one place
            _a21_write_error("TK CALLBACK EXCEPTION", exc_value)
        except Exception:
            pass
        # Also print to stderr so the terminal still shows it
        try:
            import sys as _sys
            _a21_traceback.print_exception(
                exc_type, exc_value, exc_tb, file=_sys.stderr
            )
        except Exception:
            pass

    try:
        win.report_callback_exception = _handler
        _a22_installed = True
    except Exception:
        pass


# Step weights: loading data, running matrix, completion
_STEP_MILESTONES = [0, 10, 85, 100]   # % at start of each step boundary
_STEP_NAMES = [
    "Loading data and rules...",
    "Running comparison matrix...",
    "Done!",
]


def _set_progress(bar, label, pct, text):
    bar['value'] = pct
    label.config(text=text)


def _animate_to(bar, current, target, step_ms=60):
    """Smoothly animate the progress bar from current to target %.

    WHY (Phase 69 Fix 39): Old code called bar.after() unconditionally.
         If the panel is closed mid-animation (e.g. user switches tabs),
         'bar' widget is destroyed and subsequent callbacks raise TclError
         in an infinite loop. Guard with winfo_exists().
    CHANGED: April 2026 — Phase 69 Fix 39 — guard destroyed widget
             (audit Part E MEDIUM #39)
    """
    try:
        if not bar.winfo_exists():
            return
    except Exception:
        return
    if current >= target:
        bar['value'] = target
        return
    nxt = min(current + 1, target)
    bar['value'] = nxt
    bar.after(step_ms, lambda: _animate_to(bar, nxt, target, step_ms))


def run_backtest_threaded(output_text, progress_label, progress_bar, step_label, run_button):
    """Run backtest in a separate thread"""
    # WHY (Phase A.22): defense-in-depth — also install from the worker
    #      entry point so the handler is in place even if build_panel
    #      was bypassed by a different code path.
    # CHANGED: April 2026 — Phase A.22
    _a22_install_tk_handler(output_text)

    global _running

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
    backtest_dir = os.path.join(project_root, 'project2_backtesting')

    def ui(fn):
        """Schedule fn on the main thread."""
        progress_bar.after(0, fn)

    def run_in_thread():
        global _running
        _running = True

        run_button.config(state=tk.DISABLED, text="Running...")
        output_text.delete(1.0, tk.END)
        output_text.insert(tk.END, "=== BACKTEST STARTED ===\n\n")
        output_text.insert(tk.END, "Entry: next candle open (no look-ahead bias)\n\n")
        output_text.see(tk.END)

        ui(lambda: _set_progress(progress_bar, step_label, 5, "Loading data and rules..."))

        try:
            import io
            import contextlib

            # Read entry timeframe from config
            from project2_backtesting.panels.configuration import load_config
            cfg = load_config()
            entry_tf = cfg.get('winning_scenario', 'H1')
            symbol = cfg.get('symbol', 'XAUUSD').lower()
            multi_tf = _multi_tf_var.get() if _multi_tf_var is not None else False

            output_text.insert(tk.END, f"Entry timeframe: {entry_tf}\n")

            # Find candle data for the selected (base) timeframe
            candle_path = None
            candidates = [
                os.path.join(project_root, 'data', f'{symbol}_{entry_tf}.csv'),
                os.path.join(project_root, 'data', symbol, f'{entry_tf}.csv'),
            ]
            for p in candidates:
                if os.path.exists(p):
                    candle_path = p
                    break

            if candle_path is None:
                output_text.insert(tk.END, f"ERROR: {entry_tf} candle data not found!\n")
                output_text.insert(tk.END, f"Looked in:\n")
                for p in candidates:
                    output_text.insert(tk.END, f"  {p}\n")
                progress_label.config(text="Error: candle data not found", fg="#dc3545")
                return

            output_text.insert(tk.END, f"Candle data: {candle_path}\n\n")
            output_text.see(tk.END)

            # Progress callback for the backtester - shows real-time results
            def _progress(cur, tot, name, result_dict=None):
                """Called after each rule × exit combination completes."""
                pct = 10 + int(cur / max(tot, 1) * 75)

                def _update():
                    # WHY (Phase A.21): UI-thread callbacks can raise
                    #      exceptions that never reach the worker's
                    #      outer catch. Capture to disk for diagnosis.
                    # CHANGED: April 2026 — Phase A.21
                    try:
                        progress_bar['value'] = pct
                        step_label.config(text=f"{cur}/{tot}: {name}")

                        if result_dict:
                            trades = result_dict.get('total_trades', 0)
                            wr = result_dict.get('win_rate', 0)
                            net = result_dict.get('net_total_pips', 0)
                            pf = result_dict.get('net_profit_factor', 0)

                            # Fix: handle win_rate that might be decimal (0.82) or already percent (82.3)
                            if wr > 1:
                                wr_display = f"{wr:.1f}%"  # already in percent
                            else:
                                wr_display = f"{wr*100:.1f}%"  # convert decimal to percent

                            # Calculate approximate P&L in % using config-loaded dollar-per-pip
                            approx_pnl_pct = (net * _rb_dollar_per_pip) / 1000

                            # Color code: green if profitable, red if not, gray if 0 trades
                            if trades == 0:
                                color = "#888888"
                                line = f"  [{cur}/{tot}] {name}: 0 trades\n"
                            elif net > 0:
                                color = "#28a745"
                                line = f"  [{cur}/{tot}] {name}: {trades} trades, WR {wr_display}, PF {pf:.2f}, {net:+,.0f} pips (~{approx_pnl_pct:+.1f}%) ✅\n"
                            else:
                                color = "#dc3545"
                                line = f"  [{cur}/{tot}] {name}: {trades} trades, WR {wr_display}, PF {pf:.2f}, {net:+,.0f} pips (~{approx_pnl_pct:+.1f}%) ❌\n"

                            output_text.insert(tk.END, line)
                            # Apply color to the last line
                            line_count = int(output_text.index('end-1c').split('.')[0])
                            output_text.tag_add(f"line_{cur}", f"{line_count}.0", f"{line_count}.end")
                            output_text.tag_config(f"line_{cur}", foreground=color)
                            output_text.see(tk.END)

                            # WHY (Phase 69 Fix 38): _best_result[0] is written from
                            #      the background worker and read by _update_best() in
                            #      the UI thread — no synchronisation. Add a lock.
                            # CHANGED: April 2026 — Phase 69 Fix 38 — lock _best_result
                            #          (audit Part E MEDIUM #38)
                            global _best_result
                            if trades > 0:
                                with _best_result_lock:
                                    if _best_result[0] is None or net > _best_result[0].get('net_total_pips', 0):
                                        _best_result[0] = result_dict
                                _update_best()
                    except Exception as _e:
                        _a21_write_error("_update CALLBACK EXCEPTION", _e)
                        try:
                            progress_label.config(
                                text=f"Error: {str(_e)[:60]}", fg="#dc3545"
                            )
                        except Exception:
                            pass

                try:
                    progress_bar.after(0, _update)
                except Exception:
                    pass

            def _update_best():
                """Update the best result display."""
                global _best_result, _best_label
                # WHY (Phase A.21): UI callback exception capture.
                # CHANGED: April 2026 — Phase A.21
                try:
                    if _best_result[0] and _best_label:
                        b = _best_result[0]
                        # Fix win rate display
                        wr = b['win_rate']
                        if wr > 1:
                            wr_display = f"{wr:.1f}%"
                        else:
                            wr_display = f"{wr*100:.1f}%"
                        # Add P&L %
                        net = b['net_total_pips']
                        approx_pnl_pct = (net * _rb_dollar_per_pip) / 1000
                        _best_label.config(
                            text=f"🏆 {b['rule_combo']} × {b['exit_name']}\n"
                                 f"   {b['total_trades']} trades | WR {wr_display} | "
                                 f"PF {b['net_profit_factor']:.2f} | {b['net_total_pips']:+,.0f} pips (~{approx_pnl_pct:+.1f}%)",
                            fg="#28a745"
                        )
                except Exception as _e:
                    _a21_write_error("_update_best CALLBACK EXCEPTION", _e)

            # Get only the checked rules
            global _rule_vars
            selected_rules = [rule for var, rule in _rule_vars if var.get()]

            if not selected_rules:
                output_text.insert(tk.END, "ERROR: No rules selected! Check at least one rule.\n")
                progress_label.config(text="Error: no rules selected", fg="#dc3545")
                return

            output_text.insert(tk.END, f"Testing {len(selected_rules)} selected rules x exit strategies\n\n")
            output_text.see(tk.END)

            # Write selected rules to a temporary file for the backtester
            temp_rules = {
                'rules': selected_rules,
                'discovery_method': 'selected_subset',
            }
            temp_path = os.path.join(project_root, 'project2_backtesting', 'outputs', '_temp_selected_rules.json')
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(temp_rules, f, indent=2, default=str)

            # Run the backtest with captured output
            ui(lambda: _set_progress(progress_bar, step_label, 10, "Running comparison matrix..."))

            use_safety = _use_safety_var.get() if _use_safety_var is not None else True
            output_text.insert(tk.END, f"Safety stops: {'ON' if use_safety else 'OFF'}\n")
            output_text.insert(tk.END, f"Multi-TF test: {'ON' if multi_tf else 'OFF'}\n\n")

            # Determine which TFs to test
            # WHY (Phase 33 Fix 9): Old list missed D1 timeframe. Some users
            #      backtest on daily candles, so include it in multi-TF test.
            # CHANGED: April 2026 — Phase 33 Fix 9 — added D1 to TF list
            #          (Ref: trade_bot_audit_round2_partC.pdf HIGH item #89 pg.31)
            if multi_tf:
                tfs_to_test = ['M5', 'M15', 'H1', 'H4', 'D1']
            else:
                tfs_to_test = [entry_tf]

            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                sys.path.insert(0, project_root)
                from project2_backtesting.strategy_backtester import run_comparison_matrix

                # Run matrix for each TF and combine results
                # WHY: Testing all TFs in one run finds the best entry frequency
                #      without the user having to switch config and re-run manually.
                # CHANGED: April 2026 — multi-TF entry testing
                all_matrix   = []
                total_elapsed = 0

                for tf_idx, tf in enumerate(tfs_to_test):
                    if multi_tf:
                        output_text.insert(tk.END,
                            f"\n>>> Testing entry TF: {tf} ({tf_idx+1}/{len(tfs_to_test)})\n")
                        output_text.see(tk.END)

                    # Resolve candle file for this TF
                    tf_candle_path = None
                    for cand in [
                        os.path.join(project_root, 'data', f'{symbol}_{tf}.csv'),
                        os.path.join(project_root, 'data', symbol, f'{tf}.csv'),
                    ]:
                        if os.path.exists(cand):
                            tf_candle_path = cand
                            break

                    if tf_candle_path is None:
                        if multi_tf:
                            output_text.insert(tk.END, f"    Skipping {tf} — no candle file found\n")
                        continue

                    tf_results = run_comparison_matrix(
                        candles_path=tf_candle_path,
                        timeframe=tf,
                        report_path=temp_path,
                        progress_callback=_progress,
                        use_safety_stops=use_safety,
                    )

                    # Tag each result row with entry TF when running multi-TF
                    # WHY: Put entry_tf at both top-level and inside stats so every
                    #      downstream tool can find it regardless of how it reads the row.
                    # CHANGED: April 2026 — multi-TF support
                    if multi_tf:
                        for row in tf_results.get('matrix', []):
                            row['entry_tf'] = tf
                            if isinstance(row.get('stats'), dict):
                                row['stats']['entry_tf'] = tf

                    all_matrix.extend(tf_results.get('matrix', []))
                    total_elapsed += tf_results.get('elapsed', 0)

                # Sort combined results by net pips descending
                # WHY: Same as view_results.py fix — backtest matrix flattens stats
                #      to top level, so r.get('stats', {}) returns empty dict.
                # CHANGED: April 2026 — read flattened stats from r top level
                all_matrix.sort(
                    key=lambda r: (r.get('stats') or r).get('net_total_pips', 0),
                    reverse=True,
                )

                results = {
                    'matrix':  all_matrix,
                    'elapsed': total_elapsed,
                }

            # Clean up temp file
            try:
                os.remove(temp_path)
            except Exception:
                pass

            output_text.insert(tk.END, capture.getvalue())
            output_text.see(tk.END)

            # Show completion
            ui(lambda: _animate_to(progress_bar, int(progress_bar['value']), 100, 30))
            ui(lambda: progress_bar.config(style="green.Horizontal.TProgressbar"))
            progress_label.config(text="Backtest completed successfully!", fg="#28a745")
            step_label.config(text="Done!")
            output_text.insert(tk.END, "\n=== BACKTEST COMPLETED SUCCESSFULLY ===\n")
            output_text.insert(tk.END, "\nGo to 'View Results' panel to see the comparison matrix!\n")
            output_text.see(tk.END)

            output_text.after(0, lambda: messagebox.showinfo(
                "Backtest Complete",
                "Backtest completed successfully!\n\n"
                "Go to the 'View Results' panel to review results."
            ))

        except Exception as e:
            import traceback
            err = traceback.format_exc()
            # WHY (Phase A.21+A.23): persist to disk in multiple locations
            #      and surface the write results in the panel so the user
            #      can see where the file actually landed without guessing.
            # CHANGED: April 2026 — Phase A.23
            _a21_write_error("WORKER THREAD EXCEPTION", e)
            output_text.insert(tk.END, f"\n[ERROR] {e}\n{err}\n")
            output_text.insert(tk.END, "\n[A.23] Disk error log write attempts:\n")
            try:
                for _n, _p, _r in _a23_last_write_attempts:
                    output_text.insert(tk.END, f"  [{_n}] {_p}\n         {_r}\n")
            except Exception:
                pass
            output_text.see(tk.END)
            progress_label.config(text=f"Error: {str(e)[:60]}", fg="#dc3545")

        finally:
            _running = False
            run_button.config(state=tk.NORMAL, text="Run Backtest")

    threading.Thread(target=run_in_thread, daemon=True).start()


def start_backtest(output_text, progress_label, progress_bar, step_label, run_button):
    global _running, _best_result, _best_label

    if _running:
        messagebox.showwarning("Already Running", "Backtest is already running!")
        return

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
    # New backtester uses analysis_report.json
    from project2_backtesting.panels.configuration import load_config
    cfg = load_config()
    symbol = cfg.get('symbol', 'XAUUSD').lower()
    entry_tf = cfg.get('winning_scenario', 'H1')
    rules_file = os.path.join(project_root, 'project1_reverse_engineering/outputs/analysis_report.json')
    price_file = os.path.join(project_root, f'data/{symbol}_{entry_tf}.csv')

    if not os.path.exists(rules_file):
        messagebox.showerror("Rules File Missing",
                             "Rules file not found!\n\n"
                             "Expected: project1_reverse_engineering/outputs/analysis_report.json\n\n"
                             "Please run Project 1 first to generate the rules.")
        return

    if not os.path.exists(price_file):
        # WHY (Phase 33 Fix 8): Old error hardcoded "XAUUSD data". Non-XAUUSD
        #      users saw a confusing message. Use symbol from config instead.
        # CHANGED: April 2026 — Phase 33 Fix 8 — symbol-aware error message
        #          (Ref: trade_bot_audit_round2_partC.pdf HIGH item #88 pg.31)
        messagebox.showerror("Price Data Missing",
                             f"Price data file not found!\n\nPlease download {symbol.upper()} data first.")
        return

    # ── Check for stale rules ─────────────────────────────────────────────
    # WHY: If analysis_report.json is missing entry_timeframe or direction,
    #      the backtest will use wrong defaults (H1, BUY) silently.
    #      Better to warn the user NOW than produce wrong results.
    # CHANGED: April 2026 — stale rules detection
    try:
        from shared.stale_check import check_analysis_report, format_warning
        stale = check_analysis_report()
        if stale['is_stale']:
            warning = format_warning(stale)
            proceed = messagebox.askyesno(
                "Stale Rules Warning",
                f"{warning}\n\nProceed anyway with defaults?",
                icon='warning',
            )
            if not proceed:
                return
    except ImportError:
        pass

    if messagebox.askyesno("Run Backtest",
                           "Start backtesting?\n\n"
                           "  1. Simulate trades on discovered rules\n"
                           "  2. Calculate performance statistics\n"
                           "  3. Generate HTML report\n\n"
                           "Estimated time: 2–5 minutes."):
        # Reset bar and best result
        progress_bar['value'] = 0
        progress_bar.config(style="Horizontal.TProgressbar")
        step_label.config(text="")
        _best_result[0] = None
        if _best_label:
            _best_label.config(text="Waiting for results...", fg="#666666")
        run_backtest_threaded(output_text, progress_label, progress_bar, step_label, run_button)


def build_panel(parent):
    global _output_text, _progress_label, _progress_bar, _step_label, _run_button, _best_label

    panel = tk.Frame(parent, bg="#ffffff")

    # WHY (Phase A.22): install Tk-level exception handler so callback
    #      exceptions land in outputs/last_backtest_error.txt via A.21.
    # CHANGED: April 2026 — Phase A.22
    _a22_install_tk_handler(panel)

    tk.Label(panel, text="Run Backtest", font=("Arial", 16, "bold"),
             bg="#ffffff", fg="#333333").pack(pady=(20, 5))
    tk.Label(panel, text="Execute backtest and monitor progress",
             font=("Arial", 10), bg="#ffffff", fg="#666666").pack(pady=(0, 15))

    # ── Rule Source Selector ─────────────────────────────────────
    global _source_var, _rule_canvas, _rule_inner, _rule_vars, _current_rules, _current_source_path

    source_frame = tk.LabelFrame(panel, text="📋 Rules to Test",
                                  font=("Arial", 11, "bold"), bg="#ffffff", fg="#333",
                                  padx=15, pady=10)
    source_frame.pack(fill="x", padx=20, pady=(10, 5))

    # Dropdown
    source_row = tk.Frame(source_frame, bg="#ffffff")
    source_row.pack(fill="x")
    tk.Label(source_row, text="Rule Source:", font=("Arial", 10, "bold"),
             bg="#ffffff", fg="#333").pack(side=tk.LEFT)

    _source_var = tk.StringVar(value="auto")

    def _get_available_sources():
        """Scan for all available rule files and return list of (label, path) tuples."""
        sources = []
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
        p1_report = os.path.join(project_root, 'project1_reverse_engineering', 'outputs', 'analysis_report.json')
        p4_scratch = os.path.join(project_root, 'project4_strategy_creation', 'outputs', 'discovery_scratch.json')
        # WHY (Phase 67 Fix 36): Old scanner looked for discovery_xgboost.json
        #      but the XGBoost panel writes to xgboost_result.json.
        #      The XGBoost source never appeared in the dropdown.
        #      Check both filenames for backward compat.
        # CHANGED: April 2026 — Phase 67 Fix 36 — correct XGBoost filename
        #          (audit Part E MEDIUM #36)
        _xgb_new = os.path.join(project_root, 'project1_reverse_engineering', 'outputs', 'xgboost_result.json')
        _xgb_old = os.path.join(project_root, 'project1_reverse_engineering', 'outputs', 'discovery_xgboost.json')
        p1_xgboost = _xgb_new if os.path.exists(_xgb_new) else _xgb_old
        saved_path = os.path.join(project_root, 'saved_rules.json')

        if os.path.exists(p1_report):
            try:
                with open(p1_report, encoding='utf-8') as f:
                    d = json.load(f)
                win = [r for r in d.get('rules', []) if r.get('prediction') == 'WIN']
                method = d.get('discovery_method', 'Decision Tree')
                sources.append((f"Active Rules — {method} ({len(win)} WIN)", p1_report))
            except Exception:
                pass

        if os.path.exists(p4_scratch):
            try:
                with open(p4_scratch, encoding='utf-8') as f:
                    d = json.load(f)
                rules = d.get('rules', [])
                sources.append((f"Scratch Discovery ({len(rules)} rules)", p4_scratch))
            except Exception:
                pass

        if os.path.exists(p1_xgboost):
            try:
                with open(p1_xgboost, encoding='utf-8') as f:
                    d = json.load(f)
                rules = d.get('rules', [])
                sources.append((f"XGBoost Discovery ({len(rules)} rules)", p1_xgboost))
            except Exception:
                pass

        # WHY (Phase A.25): bot_entry_rules.json is a discovered set of
        #      bot entry conditions. Listed here so users can backtest
        #      what the bot ACTUALLY does (vs what they assumed).
        # CHANGED: April 2026 — Phase A.25
        p1_bot_entry = os.path.join(
            project_root, 'project1_reverse_engineering', 'outputs', 'bot_entry_rules.json'
        )
        if os.path.exists(p1_bot_entry):
            try:
                with open(p1_bot_entry, encoding='utf-8') as f:
                    d = json.load(f)
                rules = d.get('rules', [])
                sources.append((f"Bot Entry Rules ({len(rules)} rules, all TFs)", p1_bot_entry))
            except Exception:
                pass

        if os.path.exists(saved_path):
            try:
                with open(saved_path, encoding='utf-8') as f:
                    d = json.load(f)
                if d:
                    sources.append((f"Saved/Bookmarked Rules ({len(d)} rules)", saved_path))
            except Exception:
                pass

        return sources

    available_sources = _get_available_sources()
    source_labels = [s[0] for s in available_sources]
    source_paths = {s[0]: s[1] for s in available_sources}

    source_combo = ttk.Combobox(source_row, textvariable=_source_var,
                                 values=source_labels, width=45, state="readonly")
    source_combo.pack(side=tk.LEFT, padx=10)
    if source_labels:
        source_combo.set(source_labels[0])

    tk.Button(source_row, text="🔄", font=("Arial", 9),
              command=lambda: _refresh_sources(source_combo, source_paths, _get_available_sources),
              bg="#667eea", fg="white", relief=tk.FLAT, padx=6).pack(side=tk.LEFT)

    # ── Rule List with checkboxes ────────────────────────────────
    rule_list_frame = tk.Frame(source_frame, bg="#ffffff")
    rule_list_frame.pack(fill="x", pady=(10, 0))

    # Canvas for scrollable rule list
    _rule_canvas = tk.Canvas(rule_list_frame, bg="#ffffff", highlightthickness=0, height=200)
    rule_scrollbar = tk.Scrollbar(rule_list_frame, orient="vertical", command=_rule_canvas.yview)
    _rule_canvas.configure(yscrollcommand=rule_scrollbar.set)
    rule_scrollbar.pack(side=tk.RIGHT, fill="y")
    _rule_canvas.pack(side=tk.LEFT, fill="both", expand=True)

    _rule_inner = tk.Frame(_rule_canvas, bg="#ffffff")
    rule_window = _rule_canvas.create_window((0, 0), window=_rule_inner, anchor="nw")

    def _on_rule_canvas_config(event):
        _rule_canvas.configure(scrollregion=_rule_canvas.bbox("all"))
    _rule_inner.bind("<Configure>", _on_rule_canvas_config)

    def _on_rule_canvas_resize(event):
        _rule_canvas.itemconfig(rule_window, width=event.width)
    _rule_canvas.bind("<Configure>", _on_rule_canvas_resize)

    # Safe mousewheel binding for rule list — doesn't break other canvases
    def _on_enter(event):
        _rule_canvas.bind("<MouseWheel>",
            lambda e: _rule_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        # Linux
        _rule_canvas.bind("<Button-4>", lambda e: _rule_canvas.yview_scroll(-3, "units"))
        _rule_canvas.bind("<Button-5>", lambda e: _rule_canvas.yview_scroll(3, "units"))

    def _on_leave(event):
        _rule_canvas.unbind("<MouseWheel>")
        _rule_canvas.unbind("<Button-4>")
        _rule_canvas.unbind("<Button-5>")

    _rule_canvas.bind("<Enter>", _on_enter)
    _rule_canvas.bind("<Leave>", _on_leave)

    def _load_rules_from_source(source_paths):
        """Load rules from selected source and display with checkboxes."""
        global _rule_vars, _current_rules, _current_source_path, _rule_inner, _source_var

        # Clear existing
        for w in _rule_inner.winfo_children():
            w.destroy()
        _rule_vars.clear()
        _current_rules.clear()

        label = _source_var.get()
        path = source_paths.get(label)
        if not path or not os.path.exists(path):
            tk.Label(_rule_inner, text="No rules found for this source.",
                     font=("Arial", 9), bg="#ffffff", fg="#888").pack(pady=10)
            return

        _current_source_path[0] = path

        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return

        # Extract rules depending on file format
        if isinstance(data, list):
            # saved_rules.json format: list of {id, rule, source, ...}
            rules = [entry.get('rule', entry) for entry in data]
        else:
            rules = data.get('rules', [])

        # Normalize conditions to dict format (analysis_report stores them as strings)
        from helpers import normalize_conditions
        rules = [normalize_conditions(r) for r in rules]

        # WHY (Phase 67 Fix 37): Old code filtered to prediction=='WIN' only.
        #      A sell-only strategy extracted by analyze.py uses prediction='LOSS'
        #      (because it wins by being on the loss-of-going-long side).
        #      These rules were completely invisible in the backtest panel.
        #      Include both WIN and LOSS rules so sell strategies can be tested.
        # CHANGED: April 2026 — Phase 67 Fix 37 — include SELL/LOSS rules
        #          (audit Part E MEDIUM #37)
        win_rules = [r for r in rules
                     if r.get('prediction', 'WIN') in ('WIN', 'LOSS')]
        _current_rules = win_rules

        if not win_rules:
            tk.Label(_rule_inner, text="No WIN rules in this source.",
                     font=("Arial", 9), bg="#ffffff", fg="#888").pack(pady=10)
            return

        # Select all / deselect all buttons
        btn_row = tk.Frame(_rule_inner, bg="#ffffff")
        btn_row.pack(fill="x", pady=(0, 5))
        tk.Button(btn_row, text="Select All", font=("Arial", 8),
                  command=lambda: [v.set(True) for v, _ in _rule_vars],
                  bg="#28a745", fg="white", relief=tk.FLAT, padx=8).pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(btn_row, text="Deselect All", font=("Arial", 8),
                  command=lambda: [v.set(False) for v, _ in _rule_vars],
                  bg="#6c757d", fg="white", relief=tk.FLAT, padx=8).pack(side=tk.LEFT, padx=(0, 5))

        selected_count_label = tk.Label(btn_row, text=f"{len(win_rules)}/{len(win_rules)} selected",
                                         font=("Arial", 8), bg="#ffffff", fg="#666")
        selected_count_label.pack(side=tk.LEFT, padx=10)

        def _update_count(*_):
            sel = sum(1 for v, _ in _rule_vars if v.get())
            selected_count_label.config(text=f"{sel}/{len(win_rules)} selected")

        # Show each rule with checkbox
        for i, rule in enumerate(win_rules):
            var = tk.BooleanVar(value=True)
            var.trace_add("write", _update_count)
            _rule_vars.append((var, rule))

            row = tk.Frame(_rule_inner, bg="#f8f9fa" if i % 2 == 0 else "#ffffff",
                           padx=5, pady=3)
            row.pack(fill="x")

            # Checkbox
            tk.Checkbutton(row, variable=var,
                           bg=row['bg'], selectcolor=row['bg']).pack(side=tk.LEFT)

            # Rule info
            wr = rule.get('win_rate', 0)
            pips = rule.get('avg_pips', 0)
            conds = rule.get('conditions', [])
            # Conditions can be dicts {'feature':...} or plain strings depending on source
            def _feat_name(c):
                if isinstance(c, dict):
                    return c.get('feature', str(c))
                return str(c).split(' ')[0]  # "M15_roc_5 <= 0.19" → "M15_roc_5"
            features = [_feat_name(c) for c in conds]
            feat_str = ', '.join(features[:3])
            if len(features) > 3:
                feat_str += f" +{len(features)-3}"

            color = "#28a745" if wr >= 0.65 else "#e67e22" if wr >= 0.55 else "#dc3545"
            info = f"Rule {i+1}: WR {wr:.0%} | {pips:+.0f} pips | {feat_str}"
            tk.Label(row, text=info, font=("Courier", 8), bg=row['bg'],
                     fg=color, anchor="w").pack(side=tk.LEFT, fill="x", expand=True)

            # Delete button (permanently removes from source file)
            def _delete(idx=i, r=rule):
                feat_names = [_feat_name(c) for c in r.get('conditions', [])]
                if messagebox.askyesno("Delete Rule",
                    f"Permanently delete Rule {idx+1} from this source file?\n"
                    f"Features: {', '.join(feat_names)}"):
                    _delete_rule_from_source(idx, source_paths)
                    _load_rules_from_source(source_paths)  # refresh

            tk.Button(row, text="🗑️", font=("Arial", 7),
                      bg="#dc3545", fg="white", relief=tk.FLAT, padx=3,
                      command=_delete).pack(side=tk.RIGHT)

            # Save/bookmark button
            try:
                from shared.saved_rules import build_save_button
                sb = build_save_button(row, rule, source=label, bg=row['bg'])
                sb.pack(side=tk.RIGHT, padx=(0, 3))
            except Exception:
                pass

        _rule_canvas.configure(scrollregion=_rule_canvas.bbox("all"))

    def _delete_rule_from_source(rule_index, source_paths):
        """Delete a specific rule from the current source file."""
        # WHY (Phase 67 Fix 29+30): Old code used a stale win_indices built
        #      at load time. If another panel modified the file between load
        #      and delete, the wrong rule could be deleted silently. Re-read
        #      the file fresh at delete time and rebuild win_indices from
        #      current file contents.
        #      Also: old code wrote directly to the file — a crash mid-write
        #      corrupts analysis_report.json. Use atomic temp-file rename so
        #      the file is never in a partial state.
        # CHANGED: April 2026 — Phase 67 Fix 29+30 — fresh read + atomic write
        #          (audit Part E HIGH #29, #30)
        global _current_source_path, _source_var
        path = _current_source_path[0]
        if not path:
            return
        try:
            # Fresh read — not the stale data from load time
            with open(path, encoding='utf-8') as f:
                data = json.load(f)

            if isinstance(data, list):
                if rule_index < len(data):
                    data.pop(rule_index)
            else:
                rules = data.get('rules', [])
                # Rebuild win_indices fresh from current file contents
                win_indices = [i for i, r in enumerate(rules)
                               if r.get('prediction', 'WIN') == 'WIN']
                if rule_index < len(win_indices):
                    actual_idx = win_indices[rule_index]
                    rules.pop(actual_idx)
                    data['rules'] = rules

            # Atomic write: write to temp file then rename so the
            # target file is never in a partial/corrupted state
            import tempfile, os as _os
            _dir = _os.path.dirname(_os.path.abspath(path))
            with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=_dir,
                suffix='.tmp', delete=False
            ) as tf:
                json.dump(data, tf, indent=2, default=str)
                _tmp_path = tf.name
            _os.replace(_tmp_path, path)

        except Exception as e:
            messagebox.showerror("Error", f"Could not delete rule:\n{e}")

    def _refresh_sources(combo, paths_dict, get_fn):
        global _source_var
        new_sources = get_fn()
        new_labels = [s[0] for s in new_sources]
        new_paths = {s[0]: s[1] for s in new_sources}
        paths_dict.clear()
        paths_dict.update(new_paths)
        combo['values'] = new_labels
        if new_labels:
            combo.set(new_labels[0])
        _load_rules_from_source(new_paths)

    source_combo.bind("<<ComboboxSelected>>", lambda e: _load_rules_from_source(source_paths))

    # Load initial rules
    _load_rules_from_source(source_paths)

    # ── Feature Toggles ────────────────────────────────────────────────────────
    try:
        from shared import feature_toggles
        toggle_widget = feature_toggles.build_toggle_widget(panel, bg="#ffffff")
        toggle_widget.pack(fill="x", padx=20, pady=(15, 5))
    except ImportError:
        # WHY (Phase 67 Fix 34): Silent pass meant the backtest ran with
        #      unknown toggle defaults if the module was missing. Log a
        #      visible warning so users know toggles are not active.
        # CHANGED: April 2026 — Phase 67 Fix 34 — log toggle import failure
        #          (audit Part E HIGH #34)
        try:
            from shared.logging_setup import get_logger as _gl
            _gl(__name__).warning(
                "[run_backtest] shared.feature_toggles not importable — "
                "backtest will run with default feature toggle settings. "
                "SMART/REGIME feature filtering is inactive."
            )
        except Exception:
            pass
        tk.Label(panel,
                 text="⚠️  Feature toggles unavailable (shared.feature_toggles missing)",
                 font=("Segoe UI", 8, "italic"), bg="#ffffff", fg="#e67e22"
                 ).pack(fill="x", padx=20, pady=(5, 0))

    # ── Safety stops toggle ───────────────────────────────────────────────────
    # WHY: Lets user compare with/without safety stops enabled. Default ON
    #      because that matches the live EA behavior.
    # CHANGED: April 2026 — UI toggle for safety stops
    safety_frame = tk.Frame(panel, bg="white", pady=6)
    safety_frame.pack(fill="x", padx=20)

    global _use_safety_var
    use_safety_var = tk.BooleanVar(value=True)
    _use_safety_var = use_safety_var
    tk.Checkbutton(
        safety_frame,
        text="🛡️ Use safety stops (bot pauses before firm DD limits)",
        variable=use_safety_var,
        font=("Segoe UI", 10),
        bg="white",
    ).pack(anchor="w")

    tk.Label(
        safety_frame,
        text="    ON  = matches live EA behavior (recommended)\n"
             "    OFF = raw strategy test, no safety net (shows true risk)",
        font=("Segoe UI", 8),
        fg="#666",
        bg="white",
        justify="left",
    ).pack(anchor="w")

    # ── Multi-TF entry testing ────────────────────────────────────────────────
    # WHY: A rule that wins on H1 might be even better on M15 or H4. Without
    #      testing all entry TFs, you only see one slice of reality.
    # CHANGED: April 2026 — multi-TF entry testing
    global _multi_tf_var
    multi_tf_frame = tk.Frame(panel, bg="white", pady=6)
    multi_tf_frame.pack(fill="x", padx=20)

    multi_tf_var = tk.BooleanVar(value=False)
    _multi_tf_var = multi_tf_var
    tk.Checkbutton(
        multi_tf_frame,
        text="🔍 Test on all entry timeframes (M5, M15, H1, H4)",
        variable=multi_tf_var,
        font=("Segoe UI", 10),
        bg="white",
    ).pack(anchor="w")

    tk.Label(
        multi_tf_frame,
        text="    OFF = test only on the configured entry TF (faster)\n"
             "    ON  = run 4x — once per entry TF — to find the best entry frequency",
        font=("Segoe UI", 8),
        fg="#666",
        bg="white",
        justify="left",
    ).pack(anchor="w")

    # ── Run button ────────────────────────────────────────────────────────────
    _run_button = tk.Button(
        panel, text="Run Backtest",
        command=lambda: start_backtest(_output_text, _progress_label,
                                       _progress_bar, _step_label, _run_button),
        bg="#28a745", fg="white", font=("Arial", 12, "bold"),
        relief=tk.FLAT, cursor="hand2", padx=30, pady=12
    )
    _run_button.pack(pady=(0, 12))

    # ── Progress area ─────────────────────────────────────────────────────────
    prog_frame = tk.Frame(panel, bg="#ffffff")
    prog_frame.pack(fill="x", padx=40, pady=(0, 4))

    # green style
    style = ttk.Style()
    style.theme_use("default")
    style.configure("green.Horizontal.TProgressbar",
                    troughcolor="#e0e0e0", background="#28a745", thickness=18)
    style.configure("Horizontal.TProgressbar",
                    troughcolor="#e0e0e0", background="#667eea", thickness=18)

    _progress_bar = ttk.Progressbar(prog_frame, orient="horizontal",
                                    mode="determinate", length=500,
                                    style="Horizontal.TProgressbar")
    _progress_bar.pack(fill="x")

    pct_row = tk.Frame(prog_frame, bg="#ffffff")
    pct_row.pack(fill="x", pady=(3, 0))

    _progress_label = tk.Label(pct_row, text="Ready to run backtest",
                               font=("Arial", 10, "italic"),
                               bg="#ffffff", fg="#666666", anchor="w")
    _progress_label.pack(side=tk.LEFT)

    _step_label = tk.Label(pct_row, text="",
                           font=("Arial", 9), bg="#ffffff", fg="#999999", anchor="e")
    _step_label.pack(side=tk.RIGHT)

    # ── Best result so far ────────────────────────────────────────────────────
    best_frame = tk.LabelFrame(panel, text="Best Result So Far",
                                font=("Arial", 10, "bold"), bg="#ffffff", fg="#333333",
                                padx=10, pady=5)
    best_frame.pack(fill="x", padx=40, pady=(5, 0))

    _best_label = tk.Label(best_frame, text="Waiting for results...",
                           font=("Courier", 9), bg="#ffffff", fg="#666666",
                           anchor="w", justify=tk.LEFT)
    _best_label.pack(fill="x")

    # ── Output console ────────────────────────────────────────────────────────
    output_frame = tk.LabelFrame(panel, text="Backtest Output",
                                 font=("Arial", 11, "bold"), bg="#ffffff", fg="#333333",
                                 padx=10, pady=10)
    output_frame.pack(fill="both", expand=True, padx=20, pady=10)

    _output_text = scrolledtext.ScrolledText(output_frame, height=20,
                                             font=("Courier", 9), bg="#f8f9fa",
                                             fg="#333333", wrap=tk.WORD)
    _output_text.pack(fill="both", expand=True)

    _output_text.insert(tk.END, "Click 'Run Backtest' to start...\n\n")
    _output_text.insert(tk.END, "The backtest will:\n")
    _output_text.insert(tk.END, "  1. Simulate trades using discovered rules\n")
    _output_text.insert(tk.END, "  2. Test on in-sample and out-of-sample periods\n")
    _output_text.insert(tk.END, "  3. Calculate performance metrics\n")
    _output_text.insert(tk.END, "  4. Generate visual HTML report\n\n")
    _output_text.insert(tk.END, "Estimated time: 2-5 minutes\n")

    return panel


def refresh():
    global _output_text, _progress_label, _progress_bar, _step_label
    if _progress_label is not None and not _running:
        _progress_label.config(text="Ready to run backtest", fg="#666666")
        _progress_bar['value'] = 0
        _step_label.config(text="")
