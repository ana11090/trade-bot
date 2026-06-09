# WHY: expose batch EA generation + MT5 run-file emit + report compare in one panel.
# CHANGED: June 2026 — new panel; reuses batch_ea_tools + batch_compare_reports.
# CHANGED: June 2026 — added checkbox rule grid (same interaction as Strategy Refiner)
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog

from shared.my_rules import load_all as _load_my_rules
try:
    from shared.saved_rules import load_all as _load_saved_rules
except Exception:
    _load_saved_rules = None

BG      = "#f0f2f5"
DARK    = "#1a1a2a"
MIDGREY = "#555566"
WHITE   = "#ffffff"

# CHANGED: June 2026 — checkbox grid state
_log            = None
_grid_tree      = None      # the Treeview
_grid_entries   = []        # rule entry dicts; index == iid
_batch_sel_iids = set()     # iids (str) currently ticked


# ── grid helpers ──────────────────────────────────────────────────────────────

def _populate_grid(source):
    # WHY: reload the grid whenever Source changes; reset selections.
    global _grid_entries, _batch_sel_iids
    if _grid_tree is None:
        return
    for _i in _grid_tree.get_children():
        _grid_tree.delete(_i)
    _batch_sel_iids = set()
    if source == 'saved_rules' and _load_saved_rules:
        _grid_entries = _load_saved_rules() or []
    else:
        _grid_entries = _load_my_rules() or []
    for idx, entry in enumerate(_grid_entries):
        r = entry.get('rule', {}) if isinstance(entry, dict) else {}
        combo = r.get('rule_combo') or entry.get('rule_id') or ('rule_%d' % idx)
        tf    = r.get('entry_tf') or r.get('entry_timeframe') or '?'
        exit_ = r.get('exit_name') or '?'
        off   = r.get('entry_bar_offset', 0)
        _grid_tree.insert('', 'end', iid=str(idx),
                          values=("☐", str(entry.get('id', idx)), str(combo)[:46],
                                  tf, exit_, ("N" if int(off or 0) == 0 else "N+1")))
    _grid_tree.heading("sel", text="☐", command=_toggle_all)


def _toggle_row(event):
    # WHY: column-#1 click toggles one row's tick, matching the Refiner pattern.
    if _grid_tree is None:
        return
    if _grid_tree.identify_region(event.x, event.y) != 'cell':
        return
    if _grid_tree.identify_column(event.x) != '#1':
        return
    iid = _grid_tree.identify_row(event.y)
    if not iid:
        return
    if iid in _batch_sel_iids:
        _batch_sel_iids.discard(iid)
        glyph = "☐"
    else:
        _batch_sel_iids.add(iid)
        glyph = "☑"
    vals = list(_grid_tree.item(iid, 'values'))
    if vals:
        vals[0] = glyph
        _grid_tree.item(iid, values=vals)
    return "break"


def _toggle_all():
    # WHY: header click selects or deselects every visible row at once.
    if _grid_tree is None:
        return
    kids = list(_grid_tree.get_children())
    all_on = bool(kids) and all(k in _batch_sel_iids for k in kids)
    glyph = "☐" if all_on else "☑"
    for k in kids:
        if all_on:
            _batch_sel_iids.discard(k)
        else:
            _batch_sel_iids.add(k)
        vals = list(_grid_tree.item(k, 'values'))
        if vals:
            vals[0] = glyph
            _grid_tree.item(k, values=vals)
    _grid_tree.heading("sel", text=("☑" if not all_on else "☐"), command=_toggle_all)


# ── log helper ────────────────────────────────────────────────────────────────

def _append(msg):
    # WHY: thread-safe append; marshal via after() since Tk is single-threaded.
    if _log is None:
        return
    def _do():
        _log.configure(state="normal")
        _log.insert("end", msg + "\n")
        _log.see("end")
        _log.configure(state="disabled")
    _log.after(0, _do)


def _run_bg(fn):
    # WHY: keep the UI responsive during slow operations.
    threading.Thread(target=fn, daemon=True).start()


# ── button callbacks ──────────────────────────────────────────────────────────

def _do_generate(source_var):
    def work():
        try:
            from project3_live_trading.batch_ea_tools import batch_generate
            # CHANGED: June 2026 — generate only the rules ticked in the grid
            _selected = [_grid_entries[int(i)] for i in _batch_sel_iids
                         if i.isdigit() and int(i) < len(_grid_entries)]
            if not _selected:
                _append("Tick at least one rule in the grid (or the header ☐ to select all).")
                return
            out_dir = filedialog.askdirectory(title="Choose output folder for .mq5 + manifest")
            if not out_dir:
                _append("Generate cancelled (no folder).")
                return
            src = source_var.get()
            _append("Generating %d EA(s) into %s ..." % (len(_selected), out_dir))
            results = batch_generate(out_dir, source=src, entries=_selected)
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


# ── panel builder ─────────────────────────────────────────────────────────────

def build_panel(parent):
    global _log, _grid_tree
    panel = tk.Frame(parent, bg=BG)

    hdr = tk.Frame(panel, bg=DARK)
    hdr.pack(fill="x")
    tk.Label(hdr, text="Batch EAs", bg=DARK, fg="white",
             font=("Segoe UI", 13, "bold"), padx=12, pady=8).pack(side=tk.LEFT)

    # Source + action buttons row
    src_var = tk.StringVar(value="my_rules")
    bar = tk.Frame(panel, bg=BG)
    bar.pack(fill="x", padx=10, pady=8)
    tk.Label(bar, text="Source:", bg=BG, fg=DARK,
             font=("Segoe UI", 9)).pack(side=tk.LEFT)
    src_combo = ttk.Combobox(bar, textvariable=src_var, width=12, state="readonly",
                              values=["my_rules", "saved_rules"])
    src_combo.pack(side=tk.LEFT, padx=6)
    # CHANGED: June 2026 — repopulate grid when source changes
    src_combo.bind("<<ComboboxSelected>>", lambda e: _populate_grid(src_var.get()))

    def _btn(text, cmd):
        return tk.Button(bar, text=text, command=cmd, bg=MIDGREY, fg="white",
                         relief=tk.FLAT, cursor="hand2", padx=12, pady=4,
                         font=("Segoe UI", 9, "bold"))

    _btn("1. Generate EAs",    lambda: _do_generate(src_var)).pack(side=tk.LEFT, padx=4)
    _btn("2. Build Run Files", _do_build_run_files).pack(side=tk.LEFT, padx=4)
    _btn("3. Compare Reports", _do_compare).pack(side=tk.LEFT, padx=4)

    # CHANGED: June 2026 — rule selection grid (same interaction as Strategy Refiner)
    _grid_frame = tk.Frame(panel, bg=WHITE)
    _grid_frame.pack(fill="both", expand=False, padx=6, pady=(2, 4))
    cols = ("sel", "id", "combo", "tf", "exit", "off")
    _grid_tree = ttk.Treeview(_grid_frame, columns=cols, show="headings", height=10)
    for c, t, w in [("sel", "☐", 36), ("id", "ID", 50), ("combo", "Rule", 320),
                    ("tf", "TF", 50), ("exit", "Exit", 120), ("off", "N/N+1", 60)]:
        _grid_tree.heading(c, text=t)
        _grid_tree.column(c, width=w, anchor="w")
    _grid_tree.heading("sel", text="☐", command=_toggle_all)
    _grid_tree.bind("<Button-1>", _toggle_row, add="+")
    _gsb = ttk.Scrollbar(_grid_frame, orient="vertical", command=_grid_tree.yview)
    _grid_tree.configure(yscrollcommand=_gsb.set)
    _grid_tree.pack(side="left", fill="both", expand=True)
    _gsb.pack(side="right", fill="y")
    _populate_grid(src_var.get())

    # Log box
    _log = tk.Text(panel, font=("Consolas", 9), bg=WHITE, fg=DARK, wrap="word")
    _log.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    _log.insert("end",
                "Batch flow: 1. Tick rules above -> Generate EAs  ->  "
                "(compile .mq5->ex5 in MetaEditor)  ->  2. Build Run Files  "
                "->  run .bat in MT5  ->  3. Compare Reports.\n\n")
    _log.configure(state="disabled")

    return panel


def refresh():
    pass
