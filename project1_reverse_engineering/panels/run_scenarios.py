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

    # Scenario checkboxes
    scenarios = {
        'M5': ('M5 - 5 Minute', 'Fastest timeframe, best for scalping bots'),
        'M15': ('M15 - 15 Minute', 'Medium-fast timeframe'),
        'H1': ('H1 - 1 Hour', 'Most common timeframe for day trading'),
        'H4': ('H4 - 4 Hour', 'Swing trading timeframe'),
        'H1_M15': ('H1+M15 Combined', 'Multi-timeframe analysis')
    }

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
                                                     progress_label, progress_bar, pct_label))
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


def run_scenarios(scenario_vars, output_text, progress_label, progress_bar, pct_label):
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

    output_text.delete('1.0', tk.END)
    output_text.insert(tk.END, f"Starting execution of {len(selected)} scenario(s)...\n")
    output_text.insert(tk.END, f"Selected: {', '.join(selected)}\n")
    output_text.insert(tk.END, "=" * 60 + "\n\n")

    # Reset progress bar
    progress_bar.after(0, lambda: progress_bar.config(
        value=0, style="scenarios.Horizontal.TProgressbar"))
    pct_label.after(0, lambda: pct_label.config(text="0%"))

    STEPS_PER_SCENARIO = 7
    total_steps = len(selected) * STEPS_PER_SCENARIO
    completed_steps = [0]   # mutable counter accessible in closure

    def _update_bar(extra_label=""):
        pct = int(completed_steps[0] / total_steps * 100)
        progress_bar.config(value=pct)
        pct_label.config(text=f"{pct}%  {extra_label}".strip())

    def run_in_background():
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

            import step1_align_price
            import step2_compute_indicators
            import step3_label_trades
            import step4_train_model
            import step5_shap_analysis
            import step6_extract_rules
            import step7_validate

            steps = [
                ("Step 1: Align Price",        step1_align_price.align_price_for_scenario),
                ("Step 2: Compute Indicators", step2_compute_indicators.compute_indicators_for_scenario),
                ("Step 3: Label Trades",       step3_label_trades.label_trades_for_scenario),
                ("Step 4: Train Model",        step4_train_model.train_model_for_scenario),
                ("Step 5: SHAP Analysis",      step5_shap_analysis.shap_analysis_for_scenario),
                ("Step 6: Extract Rules",      step6_extract_rules.extract_rules_for_scenario),
                ("Step 7: Validate",           step7_validate.validate_rules_for_scenario),
            ]

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
                        sys.stdout = buffer = io.StringIO()
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

            output_text.after(0, lambda: messagebox.showinfo(
                "Execution Complete",
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
