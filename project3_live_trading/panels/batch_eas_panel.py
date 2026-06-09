# WHY: expose batch EA generation + MT5 run-file emit + report compare in one panel.
# CHANGED: June 2026 — new panel; reuses batch_ea_tools + batch_compare_reports.
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog

# Mirror the color constants used by the other p3 panels.
BG      = "#f0f2f5"
DARK    = "#1a1a2a"
MIDGREY = "#555566"
WHITE   = "#ffffff"

_log = None


def _append(msg):
    # WHY: thread-safe-ish append; Tk is single-threaded so marshal via after().
    if _log is None:
        return
    def _do():
        _log.configure(state="normal")
        _log.insert("end", msg + "\n")
        _log.see("end")
        _log.configure(state="disabled")
    _log.after(0, _do)


def _run_bg(fn):
    # WHY: keep the UI responsive; batch generate over many rules can take seconds.
    threading.Thread(target=fn, daemon=True).start()


def _do_generate(source_var):
    def work():
        try:
            from project3_live_trading.batch_ea_tools import batch_generate
            out_dir = filedialog.askdirectory(title="Choose output folder for .mq5 + manifest")
            if not out_dir:
                _append("Generate cancelled (no folder).")
                return
            src = source_var.get()
            _append("Generating EAs from %s into %s ..." % (src, out_dir))
            results = batch_generate(out_dir, source=src)
            ok = sum(1 for r in results if r.get("ok"))
            _append("Done: %d/%d EAs written." % (ok, len(results)))
            for r in results:
                if not r.get("ok"):
                    _append("  FAILED %s: %s" % (r.get("name"), r.get("error")))
            _append("Manifest: %s" % os.path.join(out_dir, "batch_manifest.json"))
        except Exception as e:
            _append("ERROR during generate: %s" % e)
    _run_bg(work)


def _do_build_run_files():
    def work():
        try:
            from project3_live_trading.batch_ea_tools import emit_tester_inis
            manifest = filedialog.askopenfilename(
                title="Pick batch_manifest.json",
                filetypes=[("JSON", "*.json")])
            if not manifest:
                _append("Build run files cancelled.")
                return
            data_dir = filedialog.askdirectory(
                title="Pick MT5 DATA folder (contains MQL5\\Experts)")
            if not data_dir:
                _append("Build run files cancelled (no data dir).")
                return
            reports_dir = filedialog.askdirectory(title="Pick folder for MT5 reports output")
            if not reports_dir:
                _append("Build run files cancelled (no reports dir).")
                return
            emit_tester_inis(manifest, data_dir, "Experts\\batch", reports_dir)
            _append("Wrote tester .ini files + run_all.bat next to the manifest.")
            # WHY: the panel cannot compile or launch MT5; say so plainly.
            _append("MANUAL STEPS (no code does these for you):")
            _append("  1. Copy the .mq5 files into MQL5\\Experts\\batch")
            _append("  2. Compile each .mq5 -> .ex5 in MetaEditor (or /compile)")
            _append("  3. Set terminal64.exe path in run_all.bat, then run it.")
        except Exception as e:
            _append("ERROR during build run files: %s" % e)
    _run_bg(work)


def _do_compare():
    def work():
        try:
            from project3_live_trading.batch_compare_reports import compare_reports
            reports = filedialog.askdirectory(title="Pick folder of MT5 .xlsx reports")
            if not reports:
                _append("Compare cancelled.")
                return
            python_dir = filedialog.askdirectory(title="Pick folder of Python trade CSVs")
            if not python_dir:
                _append("Compare cancelled (no python dir).")
                return
            manifest = filedialog.askopenfilename(
                title="Pick batch_manifest.json (recommended)",
                filetypes=[("JSON", "*.json")])
            _append("Comparing reports in %s ..." % reports)
            rows = compare_reports(reports, python_dir, manifest or "")
            for line in rows:
                _append(line)
            _append("comparison_summary.csv written in the reports folder.")
        except Exception as e:
            _append("ERROR during compare: %s" % e)
    _run_bg(work)


def build_panel(parent):
    global _log
    panel = tk.Frame(parent, bg=BG)

    hdr = tk.Frame(panel, bg=DARK)
    hdr.pack(fill="x")
    tk.Label(hdr, text="Batch EAs", bg=DARK, fg="white",
             font=("Segoe UI", 13, "bold"), padx=12, pady=8).pack(side=tk.LEFT)

    src_var = tk.StringVar(value="my_rules")
    bar = tk.Frame(panel, bg=BG)
    bar.pack(fill="x", padx=10, pady=8)
    tk.Label(bar, text="Source:", bg=BG, fg=DARK,
             font=("Segoe UI", 9)).pack(side=tk.LEFT)
    ttk.Combobox(bar, textvariable=src_var, width=12, state="readonly",
                 values=["my_rules", "saved_rules"]).pack(side=tk.LEFT, padx=6)

    def _btn(text, cmd):
        return tk.Button(bar, text=text, command=cmd, bg=MIDGREY, fg="white",
                         relief=tk.FLAT, cursor="hand2", padx=12, pady=4,
                         font=("Segoe UI", 9, "bold"))

    _btn("1. Generate EAs",    lambda: _do_generate(src_var)).pack(side=tk.LEFT, padx=4)
    _btn("2. Build Run Files", _do_build_run_files).pack(side=tk.LEFT, padx=4)
    _btn("3. Compare Reports", _do_compare).pack(side=tk.LEFT, padx=4)

    _log = tk.Text(panel, font=("Consolas", 9), bg="#ffffff", fg=DARK, wrap="word")
    _log.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    _log.insert("end",
                "Batch flow: 1. Generate EAs  ->  (compile .mq5→.ex5 in MetaEditor)"
                "  ->  2. Build Run Files  ->  run .bat in MT5  ->  3. Compare Reports.\n\n")
    _log.configure(state="disabled")

    return panel


def refresh():
    pass
