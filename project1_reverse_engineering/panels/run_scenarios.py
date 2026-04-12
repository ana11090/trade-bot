"""
Run Scenarios Panel for Project 1 - Reverse Engineering
Execute individual steps or run all scenarios
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import os
import sys
import threading

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

import state
from helpers import make_copyable

# Module-level variable to store data status frame
_data_status_frame = None

# WHY (Phase 56 Fix 3): run_btn.configure(state="disabled") handles most
#      double-clicks, but if run_btn=None (caller didn't pass it) two
#      concurrent background threads could start, interleaving output.
#      Add a module-level flag so the guard works regardless of whether
#      the button reference was passed in.
# CHANGED: April 2026 — Phase 56 Fix 3 — module-level running flag
#          (audit Part D HIGH #90)
import threading as _threading
_running = False
_running_lock = _threading.Lock()

# WHY (Phase 49 Fix 4b): Module-level step1 run cache for persistent
#      run tracking across button clicks. Keyed by output_dir so
#      re-clicks within the same session reuse existing aligned_trades.csv.
# CHANGED: April 2026 — Phase 49 Fix 4b — persistent run flags
#          (audit Part D HIGH #90)
_step1_run_cache = {}


def build_panel(parent):
    global _data_status_frame
    """Build the run scenarios panel"""
    panel = tk.Frame(parent, bg="#f0f2f5")

    # Title
    title_frame = tk.Frame(panel, bg="white", pady=20)
    title_frame.pack(fill="x", padx=20, pady=(20, 10))

    tk.Label(title_frame, text="🚀 Run Scenarios",
             bg="white", fg="#16213e",
             font=("Segoe UI", 18, "bold")).pack()

    tk.Label(title_frame, text="Execute reverse engineering pipeline for different timeframes",
             bg="white", fg="#666",
             font=("Segoe UI", 11)).pack(pady=(5, 0))

    # Main content
    content_frame = tk.Frame(panel, bg="#f0f2f5")
    content_frame.pack(fill="both", expand=True, padx=20, pady=10)

    # Left column - Scenario selection
    left_frame = tk.Frame(content_frame, bg="white", padx=20, pady=20)
    left_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))

    tk.Label(left_frame, text="📊 Select Scenarios to Run",
             bg="white", fg="#16213e",
             font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 15))

    # WHY (Phase 56 Fix 1): Old scenarios dict was hardcoded. A user
    #      whose config sets align_timeframes=M5,H1,D1 saw the wrong
    #      five options. Now: read align_timeframes from config_loader,
    #      union with any existing outputs/scenario_*/ dirs, and fall
    #      back to the hardcoded list only when config cannot be read.
    # CHANGED: April 2026 — Phase 56 Fix 1 — dynamic scenarios from config
    #          (audit Part D HIGH #86)
    _FALLBACK_SCENARIOS = {
        'M5':    ('M5 - 5 Minute',    'Fastest timeframe, best for scalping bots'),
        'M15':   ('M15 - 15 Minute',  'Medium-fast timeframe'),
        'H1':    ('H1 - 1 Hour',      'Most common timeframe for day trading'),
        'H4':    ('H4 - 4 Hour',      'Swing trading timeframe'),
        'H1_M15':('H1+M15 Combined',  'Multi-timeframe analysis'),
    }
    _TF_LABELS = {
        'M1': 'M1 - 1 Minute', 'M5': 'M5 - 5 Minute', 'M15': 'M15 - 15 Minute',
        'M30': 'M30 - 30 Minute', 'H1': 'H1 - 1 Hour', 'H4': 'H4 - 4 Hour',
        'H8': 'H8 - 8 Hour', 'D1': 'D1 - Daily', 'W1': 'W1 - Weekly',
    }
    def _build_scenarios():
        keys = []
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
            import config_loader as _cl
            _cfg = _cl.load()
            _tfs = [t.strip() for t in _cfg.get('align_timeframes', '').split(',') if t.strip()]
            keys.extend(_tfs)
        except Exception:
            pass
        # Also include any already-run scenario dirs
        try:
            import glob as _g
            _out = os.path.join(os.path.dirname(__file__), '..', 'outputs')
            for _d in sorted(_g.glob(os.path.join(_out, 'scenario_*'))):
                _k = os.path.basename(_d).replace('scenario_', '')
                if _k and _k not in keys:
                    keys.append(_k)
        except Exception:
            pass
        if not keys:
            return dict(_FALLBACK_SCENARIOS)
        result = {}
        for k in keys:
            label = _TF_LABELS.get(k, f'{k} - {k}')
            result[k] = (label, f'{k} timeframe scenario')
        return result

    scenarios = _build_scenarios()

    scenario_vars = {}

    for scenario_key, (label, desc) in scenarios.items():
        frame = tk.Frame(left_frame, bg="white", pady=5)
        frame.pack(fill="x")

        var = tk.BooleanVar(value=False)
        scenario_vars[scenario_key] = var

        cb = tk.Checkbutton(frame, text=label,
                          variable=var,
                          bg="white", fg="#333",
                          font=("Segoe UI", 11, "bold"),
                          activebackground="white")
        cb.pack(anchor="w")

        tk.Label(frame, text=f"   {desc}",
                bg="white", fg="#666",
                font=("Segoe UI", 9)).pack(anchor="w")

    # Select/Deselect all
    btn_frame = tk.Frame(left_frame, bg="white", pady=15)
    btn_frame.pack(fill="x")

    def select_all():
        for var in scenario_vars.values():
            var.set(True)

    def deselect_all():
        for var in scenario_vars.values():
            var.set(False)

    tk.Button(btn_frame, text="Select All",
             bg="#3498db", fg="white",
             font=("Segoe UI", 9), bd=0, pady=5, padx=15,
             cursor="hand2",
             command=select_all).pack(side="left", padx=(0, 5))

    tk.Button(btn_frame, text="Deselect All",
             bg="#95a5a6", fg="white",
             font=("Segoe UI", 9), bd=0, pady=5, padx=15,
             cursor="hand2",
             command=deselect_all).pack(side="left")

    # Steps info
    steps_frame = tk.Frame(left_frame, bg="#e8f4f8", padx=15, pady=15)
    steps_frame.pack(fill="x", pady=(15, 0))

    tk.Label(steps_frame, text="7 Steps per Scenario:",
             bg="#e8f4f8", fg="#16213e",
             font=("Segoe UI", 10, "bold")).pack(anchor="w")

    steps = [
        "1. Align price data",
        "2. Compute indicators",
        "3. Label trades",
        "4. Train ML model",
        "5. SHAP analysis",
        "6. Extract rules",
        "7. Validate results"
    ]

    for step in steps:
        tk.Label(steps_frame, text=f"  {step}",
                bg="#e8f4f8", fg="#333",
                font=("Segoe UI", 9)).pack(anchor="w", pady=1)

    # Right column - Execution controls
    right_frame = tk.Frame(content_frame, bg="white", padx=20, pady=20)
    right_frame.pack(side="left", fill="both", expand=True, padx=(10, 0))

    tk.Label(right_frame, text="▶️ Execute",
             bg="white", fg="#16213e",
             font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 15))

    # Trade data status indicator
    _data_status_frame = tk.Frame(right_frame, bg="#e8f4f8", padx=10, pady=10)
    _data_status_frame.pack(fill="x", pady=(0, 15))

    update_data_status_display()

    # Run button
    run_btn = tk.Button(right_frame, text="🚀 Run Selected Scenarios",
                       bg="#27ae60", fg="white",
                       font=("Segoe UI", 12, "bold"),
                       bd=0, pady=15, cursor="hand2",
                       command=lambda: run_scenarios(scenario_vars, output_text,
                                                     progress_label, progress_bar, pct_label, run_btn))
    run_btn.pack(fill="x", pady=(0, 10))

    # Progress indicator
    progress_label = tk.Label(right_frame, text="Ready to run",
                            bg="white", fg="#666",
                            font=("Segoe UI", 10))
    progress_label.pack(anchor="w", pady=(0, 5))
    make_copyable(progress_label)

    # Progress bar
    style = ttk.Style()
    style.theme_use("default")
    style.configure("scenarios.Horizontal.TProgressbar",
                    troughcolor="#e0e0e0", background="#27ae60", thickness=16)
    style.configure("scenarios.error.Horizontal.TProgressbar",
                    troughcolor="#e0e0e0", background="#e74c3c", thickness=16)

    progress_bar = ttk.Progressbar(right_frame, orient="horizontal",
                                   mode="determinate", length=300,
                                   style="scenarios.Horizontal.TProgressbar")
    progress_bar.pack(fill="x", pady=(0, 4))

    pct_label = tk.Label(right_frame, text="",
                         bg="white", fg="#888", font=("Segoe UI", 8))
    pct_label.pack(anchor="e", pady=(0, 10))

    # Output console
    tk.Label(right_frame, text="Console Output:",
             bg="white", fg="#333",
             font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 5))

    output_text = scrolledtext.ScrolledText(right_frame,
                                           height=20,
                                           font=("Consolas", 9),
                                           bg="#2c3e50", fg="#ecf0f1",
                                           insertbackground="white")
    output_text.pack(fill="both", expand=True)

    output_text.insert(tk.END, "Ready to run scenarios.\n")
    output_text.insert(tk.END, "Select scenarios from the left and click Run.\n\n")

    return panel


def run_scenarios(scenario_vars, output_text, progress_label, progress_bar, pct_label, run_btn=None):
    """Run selected scenarios"""
    # Check if trade data is loaded from Project 0
    if state.loaded_data is None:
        messagebox.showerror(
            "No Trade Data",
            "No trade data loaded!\n\n"
            "Please go to Project 0 → Data Pipeline and load your trade data first.\n\n"
            "Steps:\n"
            "1. Click '0 - Data Pipeline' in sidebar\n"
            "2. Select your trade file\n"
            "3. Click 'Run' to load the data\n"
            "4. Return to Project 1 and try again"
        )
        return

    selected = [key for key, var in scenario_vars.items() if var.get()]

    if not selected:
        messagebox.showwarning("No Selection", "Please select at least one scenario to run.")
        return

    # Phase 56 Fix 3: atomic guard — refuse second concurrent run
    global _running
    with _running_lock:
        if _running:
            messagebox.showwarning("Already Running",
                                   "A scenario run is already in progress.\n"
                                   "Please wait for it to complete.")
            return
        _running = True

    if run_btn:
        run_btn.configure(state="disabled", text="⏳ Running...", bg="#95a5a6")

    output_text.delete('1.0', tk.END)
    output_text.insert(tk.END, f"Starting execution of {len(selected)} scenario(s)...\n")
    output_text.insert(tk.END, f"Selected: {', '.join(selected)}\n")
    output_text.insert(tk.END, "=" * 60 + "\n\n")

    # Reset progress bar
    progress_bar.after(0, lambda: progress_bar.config(
        value=0, style="scenarios.Horizontal.TProgressbar"))
    pct_label.after(0, lambda: pct_label.config(text="0%"))

    # WHY (Phase 55 Fix 7a): STEPS_PER_SCENARIO was hardcoded to 7
    #      above the steps list definition. If a step is ever added or
    #      removed, the progress bar percentages are wrong. Compute
    #      total_steps after building the steps list instead.
    # CHANGED: April 2026 — Phase 55 Fix 7a — dynamic step count
    #          (audit Part D HIGH #89)
    # total_steps computed after steps list is built — see below
    completed_steps = [0]   # mutable counter accessible in closure
    # Phase 49 Fix 5: track failures to choose the right completion dialog
    _scenario_failures = []

    def _update_bar(extra_label=""):
        pct = int(completed_steps[0] / total_steps * 100)
        progress_bar.config(value=pct)
        pct_label.config(text=f"{pct}%  {extra_label}".strip())

    def run_in_background():
        # WHY (Phase 56 Fix 3): module-level guard checked again inside
        #      the thread to handle the None-run_btn edge case.
        global _running
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

            # WHY (Phase 56 Fix 2): Old code imported and ran legacy steps
            #      3-7 (label_trades, train_model, shap, extract_rules,
            #      validate). These write rules_report.txt — a format the
            #      rest of the app has moved past. The results panel now
            #      reads analysis_report.json produced by analyze.run_analysis.
            #      Replace the 7-step pipeline with the modern 3-step path
            #      so Run Scenarios → View Results actually works.
            # CHANGED: April 2026 — Phase 56 Fix 2 — modern 3-step pipeline
            #          (audit Part D HIGH #88)
            import step1_align_price
            import step2_compute_indicators

            # WHY: align_all_timeframes runs once for ALL TFs together —
            #      it doesn't need to run per scenario. Only run it on the
            #      first iteration.
            # WHY (Phase 49 Fix 4b): Old code used a closure-local
            #      `step1_already_run = [False]` flag that reset on
            #      every Run click. Clicking Run twice re-ran step1
            #      from scratch even though the first run's output
            #      was on disk. Use a module-level dict keyed on the
            #      output_dir so re-clicks within the same session
            #      reuse the existing aligned_trades.csv if present.
            # CHANGED: April 2026 — fix step1 function name + run-once logic
            # CHANGED: April 2026 — Phase 49 Fix 4b — persistent run flags
            #          (audit Part D HIGH #90)
            global _step1_run_cache
            _outputs_dir = os.path.normpath(
                os.path.join(os.path.dirname(__file__), '..', 'outputs')
            )
            _cache_key = _outputs_dir
            step1_already_run = [_cache_key in _step1_run_cache]

            def _step1_wrapper(scenario):
                if step1_already_run[0]:
                    print(f"  (Step 1 already run for previous scenario — skipping)")
                    return True
                result = step1_align_price.align_all_timeframes()
                step1_already_run[0] = (result is not None)
                if step1_already_run[0]:
                    _step1_run_cache[_cache_key] = True
                return step1_already_run[0]

            # WHY: compute_features() processes ALL timeframes at once —
            #      same pattern as step1. Only run it on the first iteration.
            # CHANGED: April 2026 — fix step2 function name + run-once logic
            step2_already_run = [False]

            def _step2_wrapper(scenario):
                # WHY: step2 saves feature_matrix.csv to outputs/, but step3+ look
                #      for it inside outputs/scenario_{name}/. Copy it to every
                #      selected scenario folder so the per-scenario steps find it.
                # CHANGED: April 2026 — copy feature matrix to scenario folders
                import shutil

                if not step2_already_run[0]:
                    result = step2_compute_indicators.compute_features()
                    step2_already_run[0] = (result is not None)
                    if not step2_already_run[0]:
                        return False

                # Always copy to the current scenario folder (even if step2 was already run)
                outputs_dir = os.path.normpath(
                    os.path.join(os.path.dirname(__file__), '..', 'outputs')
                )
                master_file = os.path.join(outputs_dir, 'feature_matrix.csv')

                if not os.path.exists(master_file):
                    print(f"  ERROR: master feature_matrix.csv not found at {master_file}")
                    return False

                scenario_dir = os.path.join(outputs_dir, f'scenario_{scenario}')
                os.makedirs(scenario_dir, exist_ok=True)

                target_file = os.path.join(scenario_dir, 'feature_matrix.csv')
                try:
                    shutil.copy2(master_file, target_file)
                    print(f"  Copied feature_matrix.csv -> scenario_{scenario}/")
                except Exception as e:
                    print(f"  ERROR copying to scenario_{scenario}: {e}")
                    return False

                # Also copy aligned_trades.csv if step3 needs it
                master_aligned = os.path.join(outputs_dir, 'aligned_trades.csv')
                if os.path.exists(master_aligned):
                    try:
                        shutil.copy2(master_aligned, os.path.join(scenario_dir, 'aligned_trades.csv'))
                    except Exception:
                        pass

                return True

            # _analyze_wrapper: run analyze.run_analysis once (all scenarios
            # share the same feature matrix), then copy analysis_report.json
            # into each scenario's outputs/ subdirectory so the results panel
            # can retrieve it by scenario name.
            analyze_already_run = [False]

            def _analyze_wrapper(scenario):
                import shutil
                import analyze as _analyze_mod
                _out = os.path.normpath(
                    os.path.join(os.path.dirname(__file__), '..', 'outputs')
                )
                if not analyze_already_run[0]:
                    _fm = os.path.join(_out, 'feature_matrix.csv')
                    if os.path.exists(_fm):
                        _analyze_mod.run_analysis(feature_matrix_path=_fm)
                    else:
                        _analyze_mod.run_analysis()
                    analyze_already_run[0] = True

                # Copy analysis_report.json into the scenario subfolder
                _src = os.path.join(_out, 'analysis_report.json')
                _scenario_dir = os.path.join(_out, f'scenario_{scenario}')
                os.makedirs(_scenario_dir, exist_ok=True)
                if os.path.exists(_src):
                    try:
                        shutil.copy2(_src, os.path.join(_scenario_dir, 'analysis_report.json'))
                    except Exception as _ce:
                        print(f"  WARNING: could not copy analysis_report.json to "
                              f"scenario_{scenario}: {_ce}")
                return True

            steps = [
                ("Step 1: Align Price",              _step1_wrapper),
                ("Step 2: Compute Indicators",       _step2_wrapper),
                ("Step 3: Analyze & Extract Rules",  _analyze_wrapper),
            ]
            # total_steps derived from actual list (Phase 55 Fix 7a)
            total_steps = len(selected) * len(steps)

            results = {}

            for scenario in selected:
                def log(msg):
                    output_text.after(0, lambda m=msg: output_text.insert(tk.END, m + "\n"))
                    output_text.after(0, lambda: output_text.see(tk.END))

                def update_progress(msg):
                    progress_label.after(0, lambda m=msg: progress_label.config(text=m))

                log(f"\n{'#' * 60}")
                log(f"# SCENARIO: {scenario}")
                log(f"{'#' * 60}\n")
                update_progress(f"Running {scenario}...")

                scenario_success = True

                for step_name, step_func in steps:
                    log(f">>> {step_name} — {scenario}")
                    update_progress(f"{scenario}: {step_name}")
                    extra = f"({scenario} — {step_name})"
                    progress_bar.after(0, lambda e=extra: _update_bar(e))

                    try:
                        import io
                        old_stdout = sys.stdout
                        # WHY (Phase 54 Fix 6): Old code used StringIO
                        #      which (a) grows unbounded and (b) doesn't
                        #      restore stdout if the step raises mid-run,
                        #      so subsequent runs lose all output to the
                        #      orphaned StringIO. Use a bounded buffer
                        #      (same as Phase 53 Fix 5 in p1 config
                        #      panel) and a try/finally guard around
                        #      the redirect block.
                        # CHANGED: April 2026 — Phase 54 Fix 6 — safe stdout redirect
                        #          (audit Part D MED #91)
                        class _BoundedBuf:
                            def __init__(self, max_lines=2000):
                                self._lines = []
                                self._max = max_lines
                            def write(self, s):
                                if not s:
                                    return
                                for line in str(s).splitlines():
                                    self._lines.append(line)
                                    if len(self._lines) > self._max:
                                        self._lines.pop(0)
                            def flush(self):
                                pass
                            def getvalue(self):
                                return '\n'.join(self._lines)
                        _saved_stdout = sys.stdout
                        sys.stdout = buffer = _BoundedBuf(max_lines=2000)
                        try:
                            success = step_func(scenario)
                        finally:
                            sys.stdout = old_stdout
                            captured = buffer.getvalue()

                        if captured:
                            for line in captured.split('\n'):
                                if line.strip():
                                    log(f"  {line}")

                        completed_steps[0] += 1
                        progress_bar.after(0, lambda: _update_bar())

                        if not success:
                            log(f"✗ FAILED: {step_name}")
                            scenario_success = False
                            break

                        log(f"✓ COMPLETED: {step_name}\n")

                    except Exception as e:
                        completed_steps[0] += 1
                        progress_bar.after(0, lambda: _update_bar())
                        log(f"✗ ERROR: {str(e)}")
                        import traceback
                        log(traceback.format_exc())
                        scenario_success = False
                        break

                results[scenario] = scenario_success

                if scenario_success:
                    log(f"\n✓ SCENARIO {scenario} COMPLETED SUCCESSFULLY\n")
                else:
                    log(f"\n✗ SCENARIO {scenario} FAILED\n")
                    _scenario_failures.append(f"{scenario}: pipeline failed")
                    progress_bar.after(0, lambda: progress_bar.config(
                        style="scenarios.error.Horizontal.TProgressbar"))

            # Summary
            log("\n" + "=" * 60)
            log("EXECUTION SUMMARY")
            log("=" * 60 + "\n")

            for scenario, success in results.items():
                status = "✓ SUCCESS" if success else "✗ FAILED"
                log(f"  {scenario:10s} {status}")

            successful = sum(1 for s in results.values() if s)
            log(f"\nCompleted: {successful}/{len(selected)} scenarios successful")

            update_progress(f"Done: {successful}/{len(selected)} successful")
            progress_bar.after(0, lambda: pct_label.config(
                text=f"100%  — {successful}/{len(selected)} scenarios OK"))
            progress_bar.after(0, lambda: progress_bar.config(value=100))

            # WHY (Phase 55 Fix 7b): Title said "Execution Complete"
            #      regardless of outcome. A user who saw 0/3 successful
            #      still got a green-sounding "Complete". Now the title
            #      and icon reflect the true outcome.
            # CHANGED: April 2026 — Phase 55 Fix 7b — outcome-aware title
            #          (audit Part D HIGH #92)
            _all_ok  = (successful == len(selected))
            _none_ok = (successful == 0)
            _title   = ("All Scenarios Complete" if _all_ok
                        else "Scenarios Failed" if _none_ok
                        else "Partial Success")
            _show    = messagebox.showinfo if not _none_ok else messagebox.showwarning
            if _scenario_failures:
                _fail_msg = f"Completed {len(selected)} scenario(s).\n" \
                            f"{successful} successful, {len(selected)-successful} failed.\n\n" \
                            f"Failures:\n" + "\n".join(f"  • {f}" for f in _scenario_failures)
                output_text.after(0, lambda: _show(
                    _title, _fail_msg))
            else:
                output_text.after(0, lambda: _show(
                    _title,
                    f"Completed {len(selected)} scenario(s).\n"
                    f"{successful} successful, {len(selected)-successful} failed.\n\n"
                    f"Check the console output for details."))

        except Exception as e:
            def show_error():
                output_text.insert(tk.END, f"\n\nFATAL ERROR: {str(e)}\n")
                import traceback
                output_text.insert(tk.END, traceback.format_exc())
                messagebox.showerror("Error", f"Execution failed:\n{str(e)}")
            output_text.after(0, show_error)

        finally:
            global _running
            _running = False
            if run_btn:
                run_btn.after(0, lambda: run_btn.configure(
                    state="normal", text="🚀 Run Selected Scenarios", bg="#27ae60"))

    # Run in background thread
    thread = threading.Thread(target=run_in_background, daemon=True)
    thread.start()


def update_data_status_display():
    """Update the data status indicator"""
    global _data_status_frame

    if _data_status_frame is None:
        return

    # Clear existing widgets
    for widget in _data_status_frame.winfo_children():
        widget.destroy()

    # Update with current status
    if state.loaded_data is not None:
        num_trades = len(state.loaded_data)
        status_text = f"✓ {num_trades} trades loaded from Project 0"
        status_color = "#27ae60"
    else:
        status_text = "⚠️ No trade data loaded - Load data in Project 0 first"
        status_color = "#e74c3c"

    tk.Label(_data_status_frame, text=status_text,
            bg="#e8f4f8", fg=status_color,
            font=("Segoe UI", 9, "bold")).pack()


def refresh():
    """Refresh the panel - update data status when panel is shown"""
    update_data_status_display()
