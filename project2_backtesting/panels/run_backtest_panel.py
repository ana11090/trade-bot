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

# WHY (Phase A.40b): Module-level dict that stores widget/fn references
#      set during build() so the public apply_pending_rule_selection()
#      can reach them. Using a dict instead of raw globals keeps the
#      refs grouped and avoids polluting the module namespace.
# CHANGED: April 2026 — Phase A.40b
_a40b_refs = {}


def apply_pending_rule_selection():
    """Consume state.pending_backtest_rule_id and set up the backtest
    panel to test that single rule.

    Steps:
        1. Read the pending rule ID from state.
        2. Load saved_rules.json and find the matching entry.
        3. Set the Rule Source dropdown to the saved-rules label.
        4. Load rules from that source (populates the checkbox list).
        5. Iterate the checkbox list; check only the rule matching the
           target rule's condition-hash, uncheck all others.
        6. If state.pending_backtest_auto_run[0] is True, click the
           Run Backtest button.
        7. Clear both pending flags.

    Called by saved_rules_panel after navigating to p2_run. Safe to
    call when no rule is pending (returns quickly). Never raises —
    any error is logged and the function returns.
    """
    import logging as _a40b_log_mod
    _a40b_log = _a40b_log_mod.getLogger(__name__)
    try:
        import state as _a40b_state
        rule_id = _a40b_state.pending_backtest_rule_id[0]
        auto_run = bool(_a40b_state.pending_backtest_auto_run[0])
        if rule_id is None:
            return
        # Clear immediately so a duplicate invocation doesn't re-fire.
        _a40b_state.pending_backtest_rule_id[0] = None
        _a40b_state.pending_backtest_auto_run[0] = False
    except Exception as e:
        _a40b_log.warning(f"[A.40b] could not read pending state: {e}")
        return

    refs = _a40b_refs
    if not refs:
        _a40b_log.warning(
            "[A.40b] backtest panel not yet built — cannot apply pending "
            "selection. Rule navigation will fall back to user manually "
            "selecting the source."
        )
        return

    try:
        # Step 1 — find the rule by id in saved_rules.json.
        from shared.saved_rules import load_all
        all_entries = load_all() or []
        target_entry = next(
            (e for e in all_entries if e.get('id') == rule_id), None
        )
        if target_entry is None:
            _a40b_log.warning(
                f"[A.40b] rule id {rule_id} not found in saved_rules.json"
            )
            return
        target_rule = target_entry.get('rule', {}) or {}
        # Normalise conditions the same way the panel's _load_rules_from_source does.
        try:
            from helpers import normalize_conditions
            target_rule_norm = normalize_conditions(dict(target_rule))
        except Exception:
            target_rule_norm = target_rule
        target_hash = refs['rule_hash_fn'](target_rule_norm)

        # Step 2 — find the saved-rules label among available sources.
        saved_label = None
        for label in refs.get('source_labels', []):
            if label.startswith("Saved/Bookmarked Rules"):
                saved_label = label
                break
        if saved_label is None:
            # Source list may be stale. Refresh once and retry.
            try:
                import os as _a40b_os
                import json as _a40b_json
                project_root = _a40b_os.path.abspath(
                    _a40b_os.path.join(_a40b_os.path.dirname(__file__), '../..')
                )
                saved_path = _a40b_os.path.join(project_root, 'saved_rules.json')
                if _a40b_os.path.exists(saved_path):
                    with open(saved_path, encoding='utf-8') as f:
                        d = _a40b_json.load(f)
                    if d:
                        saved_label = f"Saved/Bookmarked Rules ({len(d)} rules)"
                        # Patch the refs dicts so the selector has the entry
                        refs['source_labels'].append(saved_label)
                        refs['source_paths'][saved_label] = saved_path
                        refs['source_combo']['values'] = list(refs['source_labels'])
            except Exception:
                pass
        if saved_label is None:
            _a40b_log.warning(
                "[A.40b] 'Saved/Bookmarked Rules' source not available in "
                "backtest panel — cannot pre-select."
            )
            return

        # Step 3 — set the dropdown to the saved-rules label and load.
        refs['source_var'].set(saved_label)
        refs['source_combo'].set(saved_label)
        refs['load_fn']()   # populates _rule_vars

        # Step 4 — iterate checkboxes, check only the target.
        _rule_vars_list = _rule_vars   # module global populated by _load_rules_from_source
        found = False
        for var, rule_dict in _rule_vars_list:
            try:
                h = refs['rule_hash_fn'](rule_dict)
            except Exception:
                h = None
            if h == target_hash:
                var.set(True)
                found = True
            else:
                var.set(False)

        if not found:
            _a40b_log.warning(
                f"[A.40b] rule id {rule_id} was found in saved_rules.json but "
                f"didn't match any checkbox entry after load — the rule may "
                f"have been filtered (e.g. a non-WIN/LOSS prediction). "
                f"Falling back to leaving all rules unchecked."
            )
            return

        # Step 5 — optional auto-run (not used under A.40b default wiring).
        if auto_run and refs.get('run_button') is not None:
            try:
                refs['run_button'].invoke()
            except Exception as e:
                _a40b_log.warning(f"[A.40b] auto-run invoke failed: {e}")
    except Exception as e:
        _a40b_log.warning(f"[A.40b] apply_pending_rule_selection failed: {e}")
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


# WHY (Phase A.26): Run Backtest finds ~5 trades when applying rules that
#      independently match 35% of trades in feature_matrix.csv. The
#      4-orders-of-magnitude gap could be (a) indicator value mismatch
#      between feature_matrix.csv and build_multi_tf_indicators output,
#      (b) entry-TF spine vs rule TF mismatch causing merge_asof to
#      update rule features only every N candles, or (c) something else
#      entirely. This diagnostic loads the SAME indicators_df the
#      backtester would build, applies each selected rule's mask
#      against it, and writes a per-rule, per-condition hit-count
#      report to disk. Read-only — does not call run_backtest or
#      modify any state.
# CHANGED: April 2026 — Phase A.26 — diagnose rules button
def _a26_diagnose_rules(output_text):
    """Run a read-only diagnostic on currently-selected rules.

    Loads the selected rules, builds the same indicators_df the backtester
    would build for the configured entry TF, applies each rule's condition
    mask, and writes a per-rule report to outputs/diagnose_rules.txt.
    """
    import io as _a26_io
    import os as _a26_os
    import sys as _a26_sys
    import time as _a26_time
    import json as _a26_json
    import datetime as _a26_dt

    output_text.insert(tk.END, "\n" + ("=" * 70) + "\n")
    output_text.insert(tk.END, "PHASE A.26 — RULE DIAGNOSTIC\n")
    output_text.insert(tk.END, ("=" * 70) + "\n")
    output_text.see(tk.END)

    project_root = _a26_os.path.normpath(_a26_os.path.join(
        _a26_os.path.dirname(_a26_os.path.abspath(__file__)), '..', '..'
    ))
    if project_root not in _a26_sys.path:
        _a26_sys.path.insert(0, project_root)

    # ── Step 1: gather selected rules from the panel ─────────────────────
    global _rule_vars
    selected_rules = [rule for var, rule in _rule_vars if var.get()]
    if not selected_rules:
        output_text.insert(tk.END,
            "ERROR: No rules selected. Check at least one rule in the list above.\n")
        output_text.see(tk.END)
        return

    output_text.insert(tk.END, f"Selected rules: {len(selected_rules)}\n")
    output_text.see(tk.END)

    # ── Step 2: read entry TF and resolve candle path the same way the
    #            backtest does, so the diagnostic uses the same data ─────
    try:
        from project2_backtesting.panels.configuration import load_config
        cfg = load_config()
    except Exception as _e:
        output_text.insert(tk.END, f"ERROR reading config: {_e}\n")
        output_text.see(tk.END)
        return

    entry_tf = cfg.get('winning_scenario', 'H1')
    symbol = str(cfg.get('symbol', 'XAUUSD')).lower()
    output_text.insert(tk.END, f"Entry TF (from config): {entry_tf}\n")
    output_text.insert(tk.END, f"Symbol:                 {symbol}\n")
    output_text.see(tk.END)

    candle_path = None
    for cand in [
        _a26_os.path.join(project_root, 'data', f'{symbol}_{entry_tf}.csv'),
        _a26_os.path.join(project_root, 'data', symbol, f'{entry_tf}.csv'),
    ]:
        if _a26_os.path.exists(cand):
            candle_path = cand
            break
    if candle_path is None:
        output_text.insert(tk.END,
            f"ERROR: no candle file for {symbol} {entry_tf}.\n")
        output_text.see(tk.END)
        return
    output_text.insert(tk.END, f"Candle file:            {candle_path}\n")
    output_text.see(tk.END)

    # ── Step 3: load candles + build the same indicators_df ─────────────
    try:
        import pandas as _a26_pd
        import numpy as _a26_np
        from project2_backtesting.strategy_backtester import (
            build_multi_tf_indicators,
            _extract_required_indicators,
        )
        from shared.data_utils import normalize_timestamp
    except Exception as _e:
        output_text.insert(tk.END, f"ERROR importing backtester: {_e}\n")
        output_text.see(tk.END)
        return

    output_text.insert(tk.END, "\nLoading candles...\n")
    output_text.see(tk.END)
    try:
        candles_df = _a26_pd.read_csv(candle_path, encoding='utf-8-sig')
    except Exception as _e:
        output_text.insert(tk.END, f"ERROR reading candle CSV: {_e}\n")
        output_text.see(tk.END)
        return

    # Auto-detect timestamp column (mirror backtester logic)
    if 'timestamp' not in candles_df.columns:
        ts_col = None
        for col in candles_df.columns:
            if col.lower().strip() in ('time', 'date', 'datetime', 'open_time', 'opentime'):
                ts_col = col
                break
        if ts_col is None:
            ts_col = candles_df.columns[0]
        candles_df = candles_df.rename(columns={ts_col: 'timestamp'})
    candles_df['timestamp'] = normalize_timestamp(candles_df['timestamp'])
    candles_df = candles_df.sort_values('timestamp').reset_index(drop=True)
    output_text.insert(tk.END,
        f"Loaded {len(candles_df):,} candles "
        f"({candles_df['timestamp'].min()} to {candles_df['timestamp'].max()})\n")
    output_text.see(tk.END)

    # Required indicators come from the same helper the backtester uses,
    # over the same selected rules.
    try:
        required = _extract_required_indicators(selected_rules)
    except Exception as _e:
        output_text.insert(tk.END,
            f"ERROR in _extract_required_indicators: {_e}\n")
        output_text.see(tk.END)
        return

    output_text.insert(tk.END, "\nRequired indicators per TF (used by selected rules):\n")
    for tf, inds in (required or {}).items():
        preview = ', '.join(list(inds)[:5]) + ('...' if len(inds) > 5 else '')
        output_text.insert(tk.END, f"  {tf}: {len(inds)} — {preview}\n")
    output_text.see(tk.END)

    output_text.insert(tk.END, "\nBuilding indicators_df (this can take a minute)...\n")
    output_text.see(tk.END)
    data_dir = _a26_os.path.dirname(candle_path)
    try:
        indicators_df = build_multi_tf_indicators(
            data_dir, candles_df['timestamp'], required_indicators=required)
    except Exception as _e:
        import traceback as _tb
        output_text.insert(tk.END,
            f"ERROR in build_multi_tf_indicators: {_e}\n{_tb.format_exc()}\n")
        output_text.see(tk.END)
        return
    output_text.insert(tk.END,
        f"indicators_df: {len(indicators_df):,} rows × "
        f"{len(indicators_df.columns):,} cols\n")
    output_text.see(tk.END)

    # WHY (Phase A.26.2): The real backtest path does NOT stop at
    #      build_multi_tf_indicators. After it, run_comparison_matrix
    #      computes SMART_/REGIME_ features on the merged multi-TF
    #      frame — that is the ONLY place those features can be
    #      computed correctly because they need cross-TF lookups
    #      like H1_candle_range / D1_atr_14. The diagnostic must
    #      mirror this step or it tests a frame the real backtester
    #      never sees. Mirror exactly the block in
    #      strategy_backtester.run_comparison_matrix lines ~1539-1583.
    # CHANGED: April 2026 — Phase A.26.2
    _a262_smart_needed = {
        c.get('feature', '') for r in selected_rules
        for c in r.get('conditions', [])
        if isinstance(c, dict) and c.get('feature', '').startswith('SMART_')
    }
    _a262_regime_needed = {
        c.get('feature', '') for r in selected_rules
        for c in r.get('conditions', [])
        if isinstance(c, dict) and c.get('feature', '').startswith('REGIME_')
    }
    if _a262_smart_needed:
        output_text.insert(tk.END,
            f"\nRules need {len(_a262_smart_needed)} SMART feature(s) — "
            f"computing on merged frame...\n")
        output_text.see(tk.END)
        try:
            from project1_reverse_engineering.smart_features import (
                _add_tf_divergences, _add_indicator_dynamics,
                _add_alignment_scores, _add_session_intelligence,
                _add_volatility_regimes, _add_price_action,
                _add_momentum_quality,
            )
            if 'hour_of_day' not in indicators_df.columns:
                indicators_df['hour_of_day'] = candles_df['timestamp'].dt.hour
            if 'open_time' not in indicators_df.columns:
                indicators_df['open_time'] = candles_df['timestamp'].astype(str)
            indicators_df = _add_tf_divergences(indicators_df)
            indicators_df = _add_indicator_dynamics(indicators_df)
            indicators_df = _add_alignment_scores(indicators_df)
            indicators_df = _add_session_intelligence(indicators_df)
            indicators_df = _add_volatility_regimes(indicators_df)
            indicators_df = _add_price_action(indicators_df)
            indicators_df = _add_momentum_quality(indicators_df)
            _smart_cols = [c for c in indicators_df.columns if c.startswith('SMART_')]
            output_text.insert(tk.END,
                f"  Added {len(_smart_cols)} SMART columns to indicators_df\n")
            # Surface any column smart_features faked with zeros so the
            # report makes the upstream gap visible. _missing_columns is
            # cumulative across calls — snapshot it now.
            try:
                from project1_reverse_engineering.smart_features import (
                    get_missing_columns as _a262_get_missing,
                )
                _a262_missing_upstream = sorted(_a262_get_missing())
                if _a262_missing_upstream:
                    output_text.insert(tk.END,
                        f"  WARNING: smart_features._safe_col faked these upstream "
                        f"columns with zeros: {_a262_missing_upstream[:10]}"
                        + ("..." if len(_a262_missing_upstream) > 10 else "") + "\n")
            except Exception:
                _a262_missing_upstream = []
        except Exception as _e:
            import traceback as _tb
            output_text.insert(tk.END,
                f"  ERROR computing SMART features: {_e}\n{_tb.format_exc()}\n")
            _a262_missing_upstream = []
    else:
        _a262_missing_upstream = []

    if _a262_regime_needed:
        output_text.insert(tk.END,
            f"\nRules need {len(_a262_regime_needed)} REGIME feature(s) — "
            f"computing on merged frame...\n")
        output_text.see(tk.END)
        try:
            from project1_reverse_engineering.smart_features import _add_regime_features
            indicators_df = _add_regime_features(indicators_df)
            _regime_cols = [c for c in indicators_df.columns if c.startswith('REGIME_')]
            output_text.insert(tk.END,
                f"  Added {len(_regime_cols)} REGIME columns to indicators_df\n")
        except Exception as _e:
            output_text.insert(tk.END,
                f"  ERROR computing REGIME features: {_e}\n")

    output_text.insert(tk.END,
        f"\nFinal indicators_df: {len(indicators_df):,} rows × "
        f"{len(indicators_df.columns):,} cols\n")
    output_text.see(tk.END)

    # WHY (Phase A.26.1): build_multi_tf_indicators can emit duplicate
    #      column names when two timeframes derive an indicator with
    #      the same prefixed name, or when a SMART feature collides
    #      with a base indicator. fast_backtest does `ind[col]` which
    #      returns a DataFrame (not a Series) on duplicates, and every
    #      comparison against that DataFrame produces a broken mask
    #      that silently fires zero times. Surface the duplicates so
    #      the report identifies this directly.
    # CHANGED: April 2026 — Phase A.26.1
    _a26_dupes = indicators_df.columns[indicators_df.columns.duplicated(keep=False)]
    _a26_dupe_set = sorted(set(_a26_dupes.tolist()))
    if _a26_dupe_set:
        output_text.insert(tk.END,
            f"WARNING: {len(_a26_dupe_set)} duplicate column name(s) in indicators_df:\n")
        for _dn in _a26_dupe_set[:20]:
            _count = int((indicators_df.columns == _dn).sum())
            output_text.insert(tk.END, f"  {_dn} (x{_count})\n")
        if len(_a26_dupe_set) > 20:
            output_text.insert(tk.END, f"  ... and {len(_a26_dupe_set) - 20} more\n")
    else:
        output_text.insert(tk.END, "No duplicate columns in indicators_df.\n")
    output_text.see(tk.END)

    # Apply the same warmup trim the backtester does (.iloc[200:])
    n_total = len(indicators_df)
    if n_total > 200:
        ind_trimmed = indicators_df.iloc[200:].reset_index(drop=True)
    else:
        ind_trimmed = indicators_df.reset_index(drop=True)
    output_text.insert(tk.END,
        f"After warmup trim (.iloc[200:]): {len(ind_trimmed):,} rows\n")
    output_text.see(tk.END)

    # ── Step 4: compare to feature_matrix.csv as the cross-reference ────
    fm_path = _a26_os.path.join(
        project_root, 'project1_reverse_engineering', 'outputs', 'feature_matrix.csv'
    )
    fm_df = None
    if _a26_os.path.exists(fm_path):
        try:
            fm_df = _a26_pd.read_csv(fm_path)
            output_text.insert(tk.END,
                f"\nCross-ref feature_matrix.csv: {len(fm_df):,} rows\n")
        except Exception as _e:
            output_text.insert(tk.END,
                f"WARNING could not read feature_matrix.csv: {_e}\n")
    else:
        output_text.insert(tk.END,
            f"\nNOTE: feature_matrix.csv not found — cross-ref skipped\n")
    output_text.see(tk.END)

    # ── Step 5: per-rule diagnostic ─────────────────────────────────────
    report_lines = []
    report_lines.append("=" * 78)
    report_lines.append("PHASE A.26 RULE DIAGNOSTIC")
    report_lines.append(f"Generated: {_a26_dt.datetime.now().isoformat()}")
    report_lines.append(f"Entry TF: {entry_tf}    Symbol: {symbol}")
    report_lines.append(f"Candle file: {candle_path}")
    report_lines.append(
        f"indicators_df: {n_total:,} rows × {len(indicators_df.columns):,} cols")
    report_lines.append(
        f"After warmup trim: {len(ind_trimmed):,} rows")
    if fm_df is not None:
        report_lines.append(f"Cross-ref feature_matrix.csv: {len(fm_df):,} rows")
    report_lines.append(f"Selected rules: {len(selected_rules)}")
    # WHY (Phase A.26.2): Surface upstream columns that smart_features
    #      had to fake with zeros, right at the top of the report so
    #      the user sees the upstream gap before drilling into rules.
    # CHANGED: April 2026 — Phase A.26.2
    if _a262_missing_upstream:
        report_lines.append(
            f"WARNING — smart_features._safe_col faked "
            f"{len(_a262_missing_upstream)} upstream column(s) with zeros: "
            f"{_a262_missing_upstream}")
    report_lines.append("=" * 78)
    report_lines.append("")

    union_mask_bt = _a26_pd.Series(False, index=ind_trimmed.index)
    union_mask_fm = (
        _a26_pd.Series(False, index=fm_df.index) if fm_df is not None else None
    )

    for r_idx, rule in enumerate(selected_rules):
        report_lines.append(f"--- Rule {r_idx} ---")
        report_lines.append(
            f"  prediction={rule.get('prediction')!r}  "
            f"action={rule.get('action')!r}  "
            f"reported_coverage={rule.get('coverage')}  "
            f"confidence={rule.get('confidence')}")

        rule_mask_bt = _a26_pd.Series(True, index=ind_trimmed.index)
        rule_mask_fm = (
            _a26_pd.Series(True, index=fm_df.index) if fm_df is not None else None
        )
        valid = True

        for c_idx, cond in enumerate(rule.get('conditions', [])):
            # WHY (Phase A.26.1): Pre-initialize so the per-condition
            #      report line can reference _fm_dup_count even when
            #      fm_df is None (no cross-ref available).
            # CHANGED: April 2026 — Phase A.26.1
            _fm_dup_count = 0
            # The selected_rules list is already normalized by the panel
            # (normalize_conditions runs at load time) so cond should be
            # a dict here. Defend anyway.
            if not isinstance(cond, dict):
                report_lines.append(
                    f"    cond[{c_idx}] NOT-A-DICT: {cond!r}")
                valid = False
                break

            feat = cond.get('feature', '')
            op   = cond.get('operator', '')
            val  = cond.get('value', None)

            in_ind = feat in ind_trimmed.columns
            in_fm  = (fm_df is not None and feat in fm_df.columns)

            # WHY (Phase A.26.1): If `feat` is duplicated in ind_trimmed,
            #      ind_trimmed[feat] returns a DataFrame, not a Series,
            #      and downstream .isna().sum() returns a Series →
            #      int() raises TypeError. Take the first column when
            #      duplicates exist and record how many duplicates there
            #      were. This mirrors what fast_backtest implicitly does
            #      (its mask-building code crashes silently in the same
            #      situation), so the diagnostic measures what the
            #      backtester actually sees.
            # CHANGED: April 2026 — Phase A.26.1
            _ind_dup_count = int((ind_trimmed.columns == feat).sum()) if in_ind else 0

            # Hit count in indicators_df
            if not in_ind:
                hits_bt   = 0
                nans_bt   = -1
                col_min   = None
                col_max   = None
                col_mean  = None
                cond_mask_bt = _a26_pd.Series(False, index=ind_trimmed.index)
                rule_mask_bt &= cond_mask_bt
                valid = False
            else:
                _raw_bt = ind_trimmed[feat]
                if isinstance(_raw_bt, _a26_pd.DataFrame):
                    # Duplicate column name — take first occurrence
                    col_bt = _raw_bt.iloc[:, 0]
                else:
                    col_bt = _raw_bt
                nans_bt = int(col_bt.isna().sum())
                try:
                    col_min  = float(col_bt.min())
                    col_max  = float(col_bt.max())
                    col_mean = float(col_bt.mean())
                except Exception:
                    col_min = col_max = col_mean = float('nan')
                if op == '<=':
                    cond_mask_bt = col_bt <= val
                elif op == '>':
                    cond_mask_bt = col_bt > val
                elif op == '<':
                    cond_mask_bt = col_bt < val
                elif op == '>=':
                    cond_mask_bt = col_bt >= val
                elif op == '==':
                    cond_mask_bt = col_bt == val
                elif op == '!=':
                    cond_mask_bt = col_bt != val
                else:
                    report_lines.append(
                        f"    cond[{c_idx}] UNKNOWN OP {op!r}")
                    cond_mask_bt = _a26_pd.Series(False, index=ind_trimmed.index)
                    valid = False
                cond_mask_bt = cond_mask_bt.fillna(False)
                hits_bt = int(cond_mask_bt.sum())
                rule_mask_bt &= cond_mask_bt

            # Hit count in feature_matrix.csv (cross-ref)
            if rule_mask_fm is not None:
                # WHY (Phase A.26.1): Same dedup as ind_trimmed above.
                # CHANGED: April 2026 — Phase A.26.1
                _fm_dup_count = (
                    int((fm_df.columns == feat).sum()) if in_fm else 0
                )
                if not in_fm:
                    cond_mask_fm = _a26_pd.Series(False, index=fm_df.index)
                    rule_mask_fm &= cond_mask_fm
                    hits_fm = 0
                    fm_min = fm_max = fm_mean = None
                else:
                    _raw_fm = fm_df[feat]
                    if isinstance(_raw_fm, _a26_pd.DataFrame):
                        col_fm = _raw_fm.iloc[:, 0]
                    else:
                        col_fm = _raw_fm
                    try:
                        fm_min  = float(col_fm.min())
                        fm_max  = float(col_fm.max())
                        fm_mean = float(col_fm.mean())
                    except Exception:
                        fm_min = fm_max = fm_mean = float('nan')
                    if op == '<=':
                        cond_mask_fm = col_fm <= val
                    elif op == '>':
                        cond_mask_fm = col_fm > val
                    elif op == '<':
                        cond_mask_fm = col_fm < val
                    elif op == '>=':
                        cond_mask_fm = col_fm >= val
                    elif op == '==':
                        cond_mask_fm = col_fm == val
                    elif op == '!=':
                        cond_mask_fm = col_fm != val
                    else:
                        cond_mask_fm = _a26_pd.Series(False, index=fm_df.index)
                    cond_mask_fm = cond_mask_fm.fillna(False)
                    hits_fm = int(cond_mask_fm.sum())
                    rule_mask_fm &= cond_mask_fm
            else:
                hits_fm = None
                fm_min = fm_max = fm_mean = None

            # Per-condition line
            # WHY (Phase A.26.1): Include duplicate count so the report
            #      flags the duplicate-column bug right next to the
            #      hit-count number, not only in the panel preamble.
            # CHANGED: April 2026 — Phase A.26.1
            report_lines.append(
                f"  cond[{c_idx}] {feat} {op} {val}")
            report_lines.append(
                f"    in_ind={in_ind} in_fm={in_fm} "
                f"NaNs_in_ind={nans_bt} "
                f"ind_dup_count={_ind_dup_count} "
                f"fm_dup_count={_fm_dup_count if rule_mask_fm is not None else 'n/a'}")
            if in_ind:
                report_lines.append(
                    f"    ind_col stats: "
                    f"min={col_min:.6f} max={col_max:.6f} mean={col_mean:.6f}")
            if in_fm and fm_min is not None:
                report_lines.append(
                    f"    fm_col stats:  "
                    f"min={fm_min:.6f} max={fm_max:.6f} mean={fm_mean:.6f}")
            report_lines.append(
                f"    hits_in_indicators_df = {hits_bt:>10,} "
                f"({(hits_bt / max(1, len(ind_trimmed)) * 100):.2f}%)")
            if hits_fm is not None:
                report_lines.append(
                    f"    hits_in_feature_matrix = {hits_fm:>10,} "
                    f"({(hits_fm / max(1, len(fm_df)) * 100):.2f}%)")

        rule_mask_bt = rule_mask_bt.fillna(False)
        rule_hits_bt = int(rule_mask_bt.sum())
        report_lines.append(
            f"  >>> RULE TOTAL hits in indicators_df: {rule_hits_bt:>10,} "
            f"({(rule_hits_bt / max(1, len(ind_trimmed)) * 100):.2f}%) "
            f"valid={valid}")
        if rule_mask_fm is not None:
            rule_hits_fm = int(rule_mask_fm.fillna(False).sum())
            report_lines.append(
                f"  >>> RULE TOTAL hits in feature_matrix:  {rule_hits_fm:>10,} "
                f"({(rule_hits_fm / max(1, len(fm_df)) * 100):.2f}%)")
        report_lines.append("")

        if valid:
            union_mask_bt |= rule_mask_bt
            if union_mask_fm is not None:
                union_mask_fm |= rule_mask_fm.fillna(False)

    # Union summary
    report_lines.append("=" * 78)
    union_hits_bt = int(union_mask_bt.sum())
    report_lines.append(
        f"UNION (any rule fires) in indicators_df: {union_hits_bt:>10,} "
        f"of {len(ind_trimmed):,} = "
        f"{(union_hits_bt / max(1, len(ind_trimmed)) * 100):.2f}%")
    if union_mask_fm is not None:
        union_hits_fm = int(union_mask_fm.sum())
        report_lines.append(
            f"UNION (any rule fires) in feature_matrix: {union_hits_fm:>10,} "
            f"of {len(fm_df):,} = "
            f"{(union_hits_fm / max(1, len(fm_df)) * 100):.2f}%")
    report_lines.append("=" * 78)

    # ── Step 6: write to disk ───────────────────────────────────────────
    out_dir = _a26_os.path.normpath(_a26_os.path.join(
        _a26_os.path.dirname(_a26_os.path.abspath(__file__)), '..', 'outputs'
    ))
    try:
        _a26_os.makedirs(out_dir, exist_ok=True)
    except Exception:
        pass
    out_path = _a26_os.path.join(out_dir, 'diagnose_rules.txt')
    try:
        with open(out_path, 'w', encoding='utf-8') as _f:
            _f.write("\n".join(report_lines))
        output_text.insert(tk.END,
            "\n" + ("=" * 70) + "\n"
            f"Diagnostic written to:\n  {out_path}\n"
            + ("=" * 70) + "\n\n")
    except Exception as _e:
        output_text.insert(tk.END,
            f"\nERROR writing diagnostic file: {_e}\n")

    # Echo the union summary inline for quick visibility
    for line in report_lines[-6:]:
        output_text.insert(tk.END, line + "\n")
    output_text.see(tk.END)


# WHY (Phase A.33): Read-only diagnostic that loads four files and
#      prints a formatted report answering the four "why only N trades"
#      questions. No side effects. Called by the Full Diagnostic
#      Summary button.
# CHANGED: April 2026 — Phase A.33
# WHY (Phase A.33): Read-only diagnostic that loads four files and
#      prints a formatted report answering the four "why only N trades"
#      questions. No side effects. Called by the Full Diagnostic
#      Summary button.
# WHY (Phase A.33.1): The A.33 version ran synchronously on the main
#      Tk thread. Windows Tk does not reliably redraw between
#      update_idletasks() calls from a button handler, so the UI
#      froze for the duration of the diagnostic and all output
#      appeared at once at the end — or not at all if anything
#      crashed inside Tk's after() callback (the exception went to
#      report_callback_exception which writes to a file silently).
# WHY (Phase A.33.2): A.33.1 was applied incorrectly. The button
#      ended up with a fake thread wrapper that still ran the
#      diagnostic synchronously on the main thread, and the
#      function signature never got the btn parameter it needed.
#      Full replacement: the function spawns its OWN background
#      thread, accepts a btn reference, flips the button to
#      "Running..." on click, bounces every log line through
#      widget.after(0, ...), and catches its own exceptions so
#      the user ACTUALLY SEES any crash inside the diagnostic
#      console instead of having it silently swallowed by the
#      report_callback_exception handler.
# CHANGED: April 2026 — Phase A.33.2 — full replacement
def _a33_run_full_diagnostic(output_widget, btn=None):
    """Run the full diagnostic in a background thread.

    Args:
        output_widget: the ScrolledText widget to print into.
        btn: optional tk.Button — flipped to a visible
            "Running..." state while the worker is active,
            restored when the worker finishes (even on error).
    """
    import os as _os
    import json as _json
    import tkinter as _tk
    import threading as _threading
    import traceback as _tb

    # Clear the widget and show an immediate "starting" line so the
    # user sees proof that the click registered. This runs on the
    # main thread because we are still inside the button's command
    # handler at this point.
    try:
        output_widget.delete("1.0", _tk.END)
        output_widget.insert(_tk.END, "Starting diagnostic...\n")
        output_widget.see(_tk.END)
    except Exception:
        pass

    if btn is not None:
        try:
            btn.configure(
                state="disabled",
                text="⏳ Running diagnostic...",
                bg="#7f8c8d",
            )
        except Exception:
            pass

    def _log(msg):
        """Thread-safe log — schedules the widget update on the main loop."""
        text = str(msg) + "\n"
        try:
            output_widget.after(
                0,
                lambda t=text: (
                    output_widget.insert(_tk.END, t),
                    output_widget.see(_tk.END),
                ),
            )
        except Exception:
            print(text, end="")

    def _hdr(title):
        _log("")
        _log("=" * 72)
        _log(f"  {title}")
        _log("=" * 72)

    def _restore_btn():
        if btn is None:
            return
        try:
            btn.after(
                0,
                lambda: btn.configure(
                    state="normal",
                    text="📊 Full Diagnostic Summary",
                    bg="#16a085",
                ),
            )
        except Exception:
            pass

    def _worker():
        try:
            _log("FULL DIAGNOSTIC SUMMARY — A.33.2")
            _log("Reads four files on disk. No re-running of discovery or backtest.")

            # Resolve paths
            _here = _os.path.dirname(_os.path.abspath(__file__))
            _project_root = _os.path.abspath(_os.path.join(_here, "..", ".."))
            p1_out = _os.path.join(_project_root, "project1_reverse_engineering", "outputs")
            p2_out = _os.path.join(_project_root, "project2_backtesting",       "outputs")
            analysis_path   = _os.path.join(p1_out, "analysis_report.json")
            bot_entry_path  = _os.path.join(p1_out, "bot_entry_rules.json")
            feature_matrix  = _os.path.join(p1_out, "feature_matrix.csv")
            backtest_matrix = _os.path.join(p2_out, "backtest_matrix.json")

            _summary = {
                "has_analysis":     False,
                "has_bot_entry":    False,
                "has_fm":           False,
                "has_matrix":       False,
                "n_analysis_rules": 0,
                "n_buy_rules":      0,
                "n_sell_rules":     0,
                "n_both_rules":     0,
                "n_bot_entry":      0,
                "bot_one_at_time":  None,
                "max_concurrent":   None,
                "n_trades_hist":    0,
                "days_span":        0,
                "max_single_rule":  0,
                "max_combined":     0,
                "best_combo_name":  None,
            }

            # ─── SECTION 1: analysis_report.json ──────────────────
            _hdr("1. analysis_report.json (analyze.py pipeline)")
            try:
                if not _os.path.exists(analysis_path):
                    _log(f"  NOT FOUND: {analysis_path}")
                    _log("  → Run Project 1 → Run Selected Scenarios first.")
                else:
                    with open(analysis_path, "r", encoding="utf-8") as f:
                        ar = _json.load(f)
                    _summary["has_analysis"] = True
                    rules = ar.get("rules", []) or []
                    _summary["n_analysis_rules"] = len(rules)

                    action_counts = {}
                    for r in rules:
                        a = str(r.get("action", "MISSING")).upper()
                        action_counts[a] = action_counts.get(a, 0) + 1
                    _summary["n_buy_rules"]  = action_counts.get("BUY", 0)
                    _summary["n_sell_rules"] = action_counts.get("SELL", 0)
                    _summary["n_both_rules"] = action_counts.get("BOTH", 0)

                    _log(f"  Path:             {analysis_path}")
                    _log(f"  Discovery method: {ar.get('discovery_method', '?')}")
                    _log(f"  Report direction: {ar.get('direction', '?')}")
                    _log(f"  Total rules:      {len(rules)}")
                    _log(f"  Action counts:    {action_counts}")

                    if rules:
                        sorted_rules = sorted(
                            rules,
                            key=lambda r: float(r.get("coverage_pct", 0) or 0),
                            reverse=True,
                        )
                        _log("")
                        _log("  Top 10 rules by coverage_pct:")
                        _log(f"    {'#':<4}{'action':<8}{'conds':<8}{'cov':<10}{'cov_pct':<10}{'conf':<10}{'win_rate':<10}{'avg_pips':<10}")
                        for i, r in enumerate(sorted_rules[:10]):
                            _log(
                                f"    {i+1:<4}"
                                f"{str(r.get('action','?'))[:6]:<8}"
                                f"{len(r.get('conditions',[])):<8}"
                                f"{int(r.get('coverage',0) or 0):<10}"
                                f"{float(r.get('coverage_pct',0) or 0):<10.2f}"
                                f"{float(r.get('confidence',0) or 0):<10.3f}"
                                f"{float(r.get('win_rate',0) or 0):<10.3f}"
                                f"{float(r.get('avg_pips',0) or 0):<10.1f}"
                            )
            except Exception as e:
                _log(f"  ERROR reading analysis_report.json: {type(e).__name__}: {e}")
                _log(_tb.format_exc())

            # ─── SECTION 2: bot_entry_rules.json ──────────────────
            _hdr("2. bot_entry_rules.json (bot_entry_discovery pipeline)")
            try:
                if not _os.path.exists(bot_entry_path):
                    _log(f"  NOT FOUND: {bot_entry_path}")
                    _log("  → Produced by Step 4 of Run Selected Scenarios (A.31/A.31.1).")
                else:
                    with open(bot_entry_path, "r", encoding="utf-8") as f:
                        be = _json.load(f)
                    _summary["has_bot_entry"] = True
                    rules = be.get("rules", []) or []
                    _summary["n_bot_entry"] = len(rules)

                    action_counts = {}
                    tf_counts = {}
                    for r in rules:
                        a = str(r.get("action", "MISSING")).upper()
                        action_counts[a] = action_counts.get(a, 0) + 1
                        tf = str(r.get("entry_timeframe", "?"))
                        tf_counts[tf] = tf_counts.get(tf, 0) + 1

                    _log(f"  Path:             {bot_entry_path}")
                    _log(f"  Discovery method: {be.get('discovery_method', '?')}")
                    _log(f"  Total rules:      {len(rules)}")
                    _log(f"  Action counts:    {action_counts}")
                    _log(f"  By timeframe:     {tf_counts}")
                    params = be.get("params", {})
                    if params:
                        _log(f"  Params used:      {params}")

                    if rules:
                        sorted_rules = sorted(
                            rules,
                            key=lambda r: float(r.get("coverage_pct", 0) or 0),
                            reverse=True,
                        )
                        _log("")
                        _log("  Top 10 rules by coverage_pct:")
                        _log(f"    {'#':<4}{'tf':<6}{'action':<8}{'conds':<8}{'cov':<10}{'cov_pct':<10}{'conf':<10}{'win_rate':<10}{'avg_pips':<10}")
                        for i, r in enumerate(sorted_rules[:10]):
                            _log(
                                f"    {i+1:<4}"
                                f"{str(r.get('entry_timeframe','?'))[:4]:<6}"
                                f"{str(r.get('action','?'))[:6]:<8}"
                                f"{len(r.get('conditions',[])):<8}"
                                f"{int(r.get('coverage',0) or 0):<10}"
                                f"{float(r.get('coverage_pct',0) or 0):<10.2f}"
                                f"{float(r.get('confidence',0) or 0):<10.3f}"
                                f"{float(r.get('win_rate',0) or 0):<10.3f}"
                                f"{float(r.get('avg_pips',0) or 0):<10.1f}"
                            )
            except Exception as e:
                _log(f"  ERROR reading bot_entry_rules.json: {type(e).__name__}: {e}")
                _log(_tb.format_exc())

            # ─── SECTION 3: feature_matrix.csv ───────────────────
            _hdr("3. Historical trade overlap + duration (feature_matrix.csv)")
            try:
                if not _os.path.exists(feature_matrix):
                    _log(f"  NOT FOUND: {feature_matrix}")
                else:
                    import pandas as _pd

                    _header = _pd.read_csv(feature_matrix, nrows=0).columns.tolist()
                    _want = [c for c in ("open_time", "close_time", "action", "pips") if c in _header]
                    if "open_time" not in _want or "close_time" not in _want:
                        _log("  ERROR: feature_matrix.csv is missing open_time or close_time.")
                        _log(f"  First 20 columns found: {_header[:20]}")
                    else:
                        tdf = _pd.read_csv(feature_matrix, usecols=_want)
                        tdf["open_time"]  = _pd.to_datetime(tdf["open_time"],  errors="coerce")
                        tdf["close_time"] = _pd.to_datetime(tdf["close_time"], errors="coerce")
                        tdf = tdf.dropna(subset=["open_time", "close_time"])
                        tdf = tdf.sort_values("open_time").reset_index(drop=True)

                        n_trades = len(tdf)
                        _summary["n_trades_hist"] = n_trades
                        _log(f"  Path:             {feature_matrix}")
                        _log(f"  Total trades:     {n_trades}")

                        if n_trades > 0:
                            # Overlap count
                            overlap_count = 0
                            prev_close_max = tdf["close_time"].iloc[0]
                            open_times  = tdf["open_time"].tolist()
                            close_times = tdf["close_time"].tolist()
                            for i in range(1, n_trades):
                                if open_times[i] < prev_close_max:
                                    overlap_count += 1
                                if close_times[i] > prev_close_max:
                                    prev_close_max = close_times[i]

                            # Sweep-line max concurrent
                            events = []
                            for ot, ct in zip(open_times, close_times):
                                events.append((ot, 1))
                                events.append((ct, -1))
                            events.sort(key=lambda e: (e[0], -e[1]))
                            running = 0
                            max_concurrent = 0
                            for _ts, delta in events:
                                running += delta
                                if running > max_concurrent:
                                    max_concurrent = running

                            _summary["max_concurrent"]  = max_concurrent
                            _summary["bot_one_at_time"] = (max_concurrent <= 1)

                            _log(f"  Overlapping trades (vs. prior open): {overlap_count}")
                            _log(f"  Max concurrent positions:            {max_concurrent}")

                            dur_min = (tdf["close_time"] - tdf["open_time"]).dt.total_seconds() / 60.0
                            dur_min = dur_min.dropna()
                            if len(dur_min) > 0:
                                _log("")
                                _log("  Trade duration (minutes):")
                                _log(f"    min:      {dur_min.min():>10.1f}")
                                _log(f"    25th:     {dur_min.quantile(0.25):>10.1f}")
                                _log(f"    median:   {dur_min.median():>10.1f}")
                                _log(f"    75th:     {dur_min.quantile(0.75):>10.1f}")
                                _log(f"    99th:     {dur_min.quantile(0.99):>10.1f}")
                                _log(f"    max:      {dur_min.max():>10.1f}")
                                _log(f"    mean:     {dur_min.mean():>10.1f}")

                            span_seconds = (
                                tdf["open_time"].iloc[-1] - tdf["open_time"].iloc[0]
                            ).total_seconds()
                            span_days = span_seconds / 86400.0 if span_seconds > 0 else 0
                            _summary["days_span"] = span_days
                            if span_days > 0:
                                _log("")
                                _log(f"  Calendar span:    {span_days:,.1f} days")
                                _log(f"  Trades / day:     {n_trades / span_days:.3f}")
                                _log(f"  Trades / week:    {n_trades / (span_days / 7.0):.2f}")
                                _log(f"  Trades / month:   {n_trades / (span_days / 30.0):.2f}")

                            if "action" in tdf.columns:
                                ac_norm = tdf["action"].astype(str).str.upper().str.strip()
                                n_buy  = int(ac_norm.str.contains("BUY").sum())
                                n_sell = int(ac_norm.str.contains("SELL").sum())
                                _log("")
                                _log(
                                    f"  Historical direction: {n_buy} BUY / {n_sell} SELL  "
                                    f"({n_buy / max(n_trades, 1):.0%} / {n_sell / max(n_trades, 1):.0%})"
                                )

                            _summary["has_fm"] = True
            except Exception as e:
                _log(f"  ERROR reading feature_matrix.csv: {type(e).__name__}: {e}")
                _log(_tb.format_exc())

            # ─── SECTION 4: backtest_matrix.json ──────────────────
            _hdr("4. Last backtest run (backtest_matrix.json)")
            try:
                if not _os.path.exists(backtest_matrix):
                    _log(f"  NOT FOUND: {backtest_matrix}")
                    _log("  → Run Project 2 → Run Backtest at least once.")
                else:
                    with open(backtest_matrix, "r", encoding="utf-8") as f:
                        first = f.readline()
                    if first.startswith("version https://git-lfs.github.com/spec/v1"):
                        _log("  SKIP: file is a Git LFS pointer, not a real JSON.")
                    else:
                        with open(backtest_matrix, "r", encoding="utf-8") as f:
                            mx = _json.load(f)
                        results = mx.get("results", []) or []
                        _summary["has_matrix"] = True
                        _log(f"  Path:          {backtest_matrix}")
                        _log(f"  Total combos:  {len(results)}")

                        if results:
                            def _st(r, k, default=0):
                                s = r.get("stats", r) or {}
                                v = s.get(k, r.get(k, default))
                                return v if v is not None else default

                            by_trades = sorted(
                                results,
                                key=lambda r: int(_st(r, "total_trades", 0) or 0),
                                reverse=True,
                            )[:10]
                            _log("")
                            _log("  Top 10 combos by trade count:")
                            _log(f"    {'#':<4}{'trades':<10}{'win%':<8}{'net_pips':<12}{'pf':<8}  name")
                            for i, r in enumerate(by_trades):
                                _log(
                                    f"    {i+1:<4}"
                                    f"{int(_st(r,'total_trades',0) or 0):<10}"
                                    f"{float(_st(r,'win_rate',0) or 0):<8.1f}"
                                    f"{float(_st(r,'net_total_pips',0) or 0):<12,.0f}"
                                    f"{float(_st(r,'net_profit_factor',0) or 0):<8.2f}  "
                                    f"{r.get('rule_combo','?')} × {r.get('exit_name','?')}"
                                )

                            by_pips = sorted(
                                results,
                                key=lambda r: float(_st(r, "net_total_pips", 0) or 0),
                                reverse=True,
                            )[:10]
                            _log("")
                            _log("  Top 10 combos by net pips:")
                            _log(f"    {'#':<4}{'trades':<10}{'win%':<8}{'net_pips':<12}{'pf':<8}  name")
                            for i, r in enumerate(by_pips):
                                _log(
                                    f"    {i+1:<4}"
                                    f"{int(_st(r,'total_trades',0) or 0):<10}"
                                    f"{float(_st(r,'win_rate',0) or 0):<8.1f}"
                                    f"{float(_st(r,'net_total_pips',0) or 0):<12,.0f}"
                                    f"{float(_st(r,'net_profit_factor',0) or 0):<8.2f}  "
                                    f"{r.get('rule_combo','?')} × {r.get('exit_name','?')}"
                                )

                            def _is_union(name):
                                n = str(name or "").lower()
                                return ("all rules" in n) or ("top 3" in n) or ("top 5" in n) or ("combined" in n)

                            singles = [r for r in results if not _is_union(r.get("rule_combo", ""))]
                            unions  = [r for r in results if     _is_union(r.get("rule_combo", ""))]

                            if singles:
                                max_single = max(int(_st(r, "total_trades", 0) or 0) for r in singles)
                                _summary["max_single_rule"] = max_single
                                _log("")
                                _log(f"  Best SINGLE-rule combo trade count: {max_single}")
                            if unions:
                                max_union = max(int(_st(r, "total_trades", 0) or 0) for r in unions)
                                _summary["max_combined"] = max_union
                                _log(f"  Best UNION combo trade count:       {max_union}")

                            if by_trades:
                                top = by_trades[0]
                                _summary["best_combo_name"] = (
                                    f"{top.get('rule_combo','?')} × {top.get('exit_name','?')}"
                                )
                                _log(
                                    f"  Highest-trade combo overall:        "
                                    f"{_summary['best_combo_name']} "
                                    f"({int(_st(top,'total_trades',0))} trades)"
                                )
            except Exception as e:
                _log(f"  ERROR reading backtest_matrix.json: {type(e).__name__}: {e}")
                _log(_tb.format_exc())

            # ─── SECTION 5: Recommendation ────────────────────
            _hdr("5. Recommendation — what to fix next")
            _recs = []

            if _summary["max_concurrent"] is not None:
                if _summary["max_concurrent"] > 1:
                    _recs.append(
                        f"[CRITICAL] Bot ran up to {_summary['max_concurrent']} concurrent "
                        "positions historically. The backtester forces max_open_trades=1. "
                        "No amount of rule tuning can match the bot's historical trade "
                        "count until a real max_open_trades > 1 implementation is added."
                    )
                else:
                    _recs.append(
                        f"[OK] Bot was one-at-a-time historically "
                        f"(max concurrent = {_summary['max_concurrent']})."
                    )

            n_dir_rules = _summary["n_buy_rules"] + _summary["n_sell_rules"]
            if _summary["has_analysis"]:
                if n_dir_rules < 10:
                    _recs.append(
                        f"[HIGH] Only {n_dir_rules} directional rules from analyze.py "
                        f"({_summary['n_buy_rules']} BUY + {_summary['n_sell_rules']} SELL). "
                        "Drop Discovery Settings further: confidence=0.50, leaf=3, "
                        "leaf_filter=3, depth=8. Re-run scenarios, re-run backtest."
                    )
                elif _summary["n_both_rules"] > 0:
                    _recs.append(
                        f"[MED] analyze.py still emitted {_summary['n_both_rules']} BOTH "
                        "rules — unexpected after A.32. Check A.32 was applied and "
                        "profile.direction == 'both'."
                    )
                else:
                    _recs.append(
                        f"[OK] analyze.py emitted {n_dir_rules} directional rules "
                        f"({_summary['n_buy_rules']} BUY + {_summary['n_sell_rules']} SELL)."
                    )

            if _summary["has_bot_entry"]:
                _recs.append(
                    f"[INFO] bot_entry_rules.json has {_summary['n_bot_entry']} rules — "
                    "run a backtest against this source too and compare trade counts."
                )
            else:
                _recs.append(
                    "[INFO] bot_entry_rules.json not found. Apply A.31 + A.31.1."
                )

            if _summary["max_single_rule"] and _summary["max_combined"]:
                if _summary["max_combined"] > _summary["max_single_rule"] * 1.3:
                    _recs.append(
                        f"[OK] Best union combo ({_summary['max_combined']} trades) "
                        f"beats best single-rule combo ({_summary['max_single_rule']} trades)."
                    )
                else:
                    _recs.append(
                        f"[HIGH] Best union ({_summary['max_combined']}) is only marginally "
                        f"better than best single rule ({_summary['max_single_rule']}). "
                        "Rules overlap on the same candles — loosen discovery and/or "
                        "use bot_entry_discovery for a more diverse rule set."
                    )

            if _summary["has_fm"] and _summary["days_span"] > 0:
                hist_rate = _summary["n_trades_hist"] / _summary["days_span"]
                _recs.append(
                    f"[TARGET] Historical bot rate = {hist_rate:.3f} trades/day "
                    f"({_summary['n_trades_hist']} trades / {_summary['days_span']:.0f} days). "
                    f"User goal = 0.5-1.0 trades/day."
                )

            for rec in _recs:
                _log("  " + rec)
                _log("")

            _log("=" * 72)
            _log("END OF DIAGNOSTIC")
            _log("=" * 72)

        except Exception as _worker_err:
            _log("")
            _log("=" * 72)
            _log(f"FATAL ERROR in diagnostic worker: {type(_worker_err).__name__}: {_worker_err}")
            _log("=" * 72)
            _log(_tb.format_exc())
        finally:
            _restore_btn()

    # Launch the worker thread and return immediately so the main
    # Tk thread stays responsive.
    _t = _threading.Thread(target=_worker, daemon=True)
    _t.start()


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

    # WHY (Phase A.40b): Cross-panel hook — expose a public function on
    #      this module that the Saved Rules panel's "▶ Backtest" button
    #      can call to pre-select a specific rule. The function reads
    #      state.pending_backtest_rule_id[0], finds the matching entry
    #      in saved_rules.json by ID, switches the source dropdown to
    #      the saved-rules source, loads the rules, then iterates the
    #      resulting checkboxes to check ONLY the target rule (matching
    #      by content hash so we're robust against the saved rule
    #      being normalised during load).
    #
    #      Exposed as a module-level function (not nested inside
    #      `build`) so other modules can import it. Binds to the
    #      widgets via the module-level globals _source_var,
    #      source_paths, source_combo, _rule_vars, _run_button.
    # CHANGED: April 2026 — Phase A.40b
    import hashlib as _a40b_hashlib

    def _a40b_rule_hash(rule):
        """Content hash of a rule's condition set. Matches the logic in
        shared.rule_library_bridge so saved rules hash identically
        whether read from saved_rules.json or from normalize_conditions
        output."""
        conds = rule.get('conditions', []) or []
        triples = []
        for c in conds:
            if not isinstance(c, dict):
                continue
            feat = c.get('feature')
            op = c.get('operator')
            val = c.get('value') if 'value' in c else c.get('threshold')
            if feat is None or op is None or val is None:
                continue
            try:
                val_f = float(val)
            except Exception:
                continue
            triples.append((str(feat), str(op), round(val_f, 10)))
        triples_sorted = sorted(triples)
        h = _a40b_hashlib.sha1()
        for f, o, v in triples_sorted:
            h.update(f.encode('utf-8'))
            h.update(b'|')
            h.update(o.encode('utf-8'))
            h.update(b'|')
            h.update(f"{v:.10f}".encode('utf-8'))
            h.update(b';')
        return h.hexdigest()

    # Attach refs the helper needs on the module-level dict so
    # apply_pending_rule_selection (defined at module level) can reach
    # them. `build` is called once per app start so this runs exactly
    # once. _run_button is patched in below after it's instantiated —
    # at this point in build() it doesn't exist yet.
    _a40b_refs['source_var']    = _source_var
    _a40b_refs['source_combo']  = source_combo
    _a40b_refs['source_paths']  = source_paths
    _a40b_refs['source_labels'] = source_labels
    _a40b_refs['load_fn']       = lambda: _load_rules_from_source(source_paths)
    _a40b_refs['rule_hash_fn']  = _a40b_rule_hash

    # WHY (Phase A.40a.1): Subscribe to library mutations so the source
    #      dropdown refreshes automatically when a rule is saved or
    #      deleted anywhere in the app. Without this, A.40a's auto-save
    #      adds entries that the dropdown doesn't show, and deletions
    #      leave the dropdown listing stale rules — both bugs the user
    #      hit immediately after A.40a landed.
    #
    #      Listener fires from whatever thread called save/delete (may
    #      be a worker thread during discovery auto-save). We marshal
    #      the widget refresh to the Tk main thread via panel.after(0,
    #      ...) so we never touch widgets off-thread.
    #
    #      Capture source_paths, source_combo, _get_available_sources,
    #      and panel by closure so the listener has everything it
    #      needs. The listener registration survives panel rebuild
    #      attempts because saved_rules module-level state is shared
    #      across the whole app process.
    # CHANGED: April 2026 — Phase A.40a.1
    try:
        from shared.saved_rules import (
            register_change_listener as _a40a1_register,
        )

        def _a40a1_on_library_change(event, payload, _pnl=panel,
                                     _combo=source_combo,
                                     _paths=source_paths,
                                     _get_fn=_get_available_sources):
            # Called from whatever thread fired save_rule/delete_rule.
            # Use after(0, ...) to hop back to the Tk main loop before
            # calling _refresh_sources, which manipulates widgets.
            try:
                _pnl.after(0, lambda: _refresh_sources(_combo, _paths, _get_fn))
            except Exception:
                # panel may have been destroyed during app shutdown —
                # silently ignore, nothing left to refresh.
                pass

        _a40a1_register(_a40a1_on_library_change)
    except Exception as _a40a1_reg_err:
        # Import failure or attribute missing — log once, don't crash
        # the panel build. The manual 🔄 button still works.
        print(
            f"[A.40a.1] could not register library-change listener: "
            f"{_a40a1_reg_err}. Run Backtest source dropdown will only "
            f"refresh on manual 🔄 click."
        )

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
    # WHY (Phase A.26): The Run Backtest button is wrapped in a row frame so
    #      the new Diagnose Rules button can sit beside it without disturbing
    #      any other panel layout. The Run button keeps the same callback,
    #      same padding, same colors — only the parent changes from `panel`
    #      to `_a26_run_row`.
    # CHANGED: April 2026 — Phase A.26
    _a26_run_row = tk.Frame(panel, bg="#ffffff")
    _a26_run_row.pack(pady=(0, 12))

    _run_button = tk.Button(
        _a26_run_row, text="Run Backtest",
        command=lambda: start_backtest(_output_text, _progress_label,
                                       _progress_bar, _step_label, _run_button),
        bg="#28a745", fg="white", font=("Arial", 12, "bold"),
        relief=tk.FLAT, cursor="hand2", padx=30, pady=12
    )
    _run_button.pack(side=tk.LEFT, padx=(0, 10))

    # WHY (Phase A.40b): Patch the run-button reference into the A.40b
    #      refs dict now that it exists. Used only by the optional
    #      auto-run path in apply_pending_rule_selection.
    # CHANGED: April 2026 — Phase A.40b
    _a40b_refs['run_button'] = _run_button

    # WHY (Phase A.26): Read-only diagnostic — does NOT call run_backtest.
    #      Loads the same indicators_df the backtester would build for the
    #      configured entry TF, applies each selected rule's mask, and
    #      writes a per-rule, per-condition hit-count report to
    #      outputs/diagnose_rules.txt. Used to investigate the discrepancy
    #      between rule coverage in feature_matrix.csv and signal count
    #      in Run Backtest.
    # CHANGED: April 2026 — Phase A.26
    _a26_diagnose_button = tk.Button(
        _a26_run_row, text="Diagnose Rules",
        command=lambda: threading.Thread(
            target=lambda: _output_text.after(0, lambda: _a26_diagnose_rules(_output_text)),
            daemon=True,
        ).start(),
        bg="#6c757d", fg="white", font=("Arial", 11),
        relief=tk.FLAT, cursor="hand2", padx=20, pady=12
    )
    _a26_diagnose_button.pack(side=tk.LEFT)

    # WHY (Phase A.33): Read-only summary that answers the four "why
    #      only 700 trades" questions by reading analysis_report.json,
    #      bot_entry_rules.json, feature_matrix.csv, and
    #      backtest_matrix.json. No re-running, no backtest
    #      invocation. Prints a formatted multi-section report to the
    #      diagnostic console so the user can see the current state
    #      of their pipeline and know what to fix next without
    #      guessing.
    # CHANGED: April 2026 — Phase A.33
    # WHY (Phase A.33.2): Old A.33.1 version wrapped the call in a
    #      daemon thread whose ONLY job was to schedule the diagnostic
    #      back onto the main Tk thread via after(0, ...). That
    #      defeated the whole purpose of threading — the diagnostic
    #      still ran synchronously and froze the UI. The new function
    #      signature accepts btn as a second argument and spawns its
    #      own internal worker thread. The command here just calls
    #      it directly.
    # CHANGED: April 2026 — Phase A.33.2 — direct call, no outer thread
    full_diag_btn = tk.Button(
        _a26_run_row, text="📊 Full Diagnostic Summary",
        bg="#16a085", fg="white", font=("Arial", 11),
        relief=tk.FLAT, cursor="hand2", padx=20, pady=12,
    )
    full_diag_btn.configure(
        command=lambda _b=full_diag_btn: _a33_run_full_diagnostic(_output_text, _b)
    )
    full_diag_btn.pack(side=tk.LEFT, padx=(5, 0))

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
