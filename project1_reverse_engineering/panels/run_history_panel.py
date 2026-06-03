"""Run History — lists Project-1 discovery runs and the criteria used."""
import json
import tkinter as tk
from tkinter import ttk

BG="#f0f2f5"; WHITE="white"; DARK="#1a1a2a"; MIDGREY="#555566"; GREEN="#2d8a4e"

_tree=None; _detail=None; _runs=[]

def _load():
    global _runs
    try:
        from shared.run_history import load_runs
        _runs = load_runs()
    except Exception:
        _runs = []

def _on_select(_e=None):
    if not _tree: return
    sel=_tree.selection()
    if not sel: return
    rec=_runs[int(sel[0])]
    lines=[]
    lines.append("Run #%s   %s" % (rec.get("run_number"), rec.get("timestamp")))
    lines.append("Dataset    : %s" % rec.get("dataset"))
    lines.append("Timeframes : %s" % rec.get("timeframes"))
    lines.append("Scenarios  : %s" % ", ".join(rec.get("scenarios", []) or []))
    lines.append("Firm/Symbol: %s" % rec.get("symbol_or_firm"))
    lines.append("Broker TZ  : %s" % rec.get("broker_timezone"))
    lines.append("")
    lines.append("--- Criteria that influenced this run ---")
    for k, v in (rec.get("criteria") or {}).items():
        lines.append("  %-28s %s" % (k, v))
    if rec.get("results"):
        lines.append("")
        lines.append("--- Per-scenario results ---")
        for sc, r in rec["results"].items():
            lines.append("  %s: %s" % (sc, r))
    _detail.configure(state="normal"); _detail.delete("1.0","end")
    _detail.insert("end", "\n".join(lines)); _detail.configure(state="disabled")

def _populate():
    _load()
    if not _tree: return
    for it in _tree.get_children(): _tree.delete(it)
    for i, r in enumerate(_runs):
        _tree.insert("", "end", iid=str(i), values=(
            r.get("run_number"),
            (r.get("timestamp") or "")[:19],
            r.get("dataset") or "",
            r.get("timeframes") or "",
            r.get("symbol_or_firm") or "",
            ", ".join(r.get("scenarios", []) or []),
        ))

def build_panel(parent):
    global _tree, _detail
    panel=tk.Frame(parent, bg=BG)
    hdr=tk.Frame(panel, bg=DARK); hdr.pack(fill="x")
    tk.Label(hdr, text="Run History — discovery criteria per run", bg=DARK, fg="white",
             font=("Segoe UI", 13, "bold"), padx=12, pady=8).pack(side=tk.LEFT)
    tk.Button(hdr, text="↻ Reload", command=_populate, bg=MIDGREY, fg="white",
              relief=tk.FLAT, cursor="hand2", padx=10, pady=3,
              font=("Segoe UI", 9, "bold")).pack(side=tk.RIGHT, padx=10, pady=6)

    body=tk.PanedWindow(panel, orient=tk.HORIZONTAL, bg=BG, sashwidth=6)
    body.pack(fill="both", expand=True, padx=6, pady=6)

    left=tk.Frame(body, bg=WHITE)
    cols=("run","date","dataset","tf","firm","scen")
    _tree=ttk.Treeview(left, columns=cols, show="headings", height=22)
    for c,t,w in [("run","Run #",55),("date","Date",150),("dataset","Dataset",170),
                  ("tf","Timeframes",130),("firm","Firm/Symbol",130),("scen","Scenarios",160)]:
        _tree.heading(c, text=t); _tree.column(c, width=w, anchor="w")
    _tree.bind("<<TreeviewSelect>>", _on_select)
    _tree.pack(side="left", fill="both", expand=True)
    sb=ttk.Scrollbar(left, orient="vertical", command=_tree.yview)
    _tree.configure(yscrollcommand=sb.set); sb.pack(side="right", fill="y")
    body.add(left, minsize=560)

    right=tk.Frame(body, bg=WHITE)
    tk.Label(right, text="Run detail", bg=WHITE, fg=DARK,
             font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=8, pady=(8,2))
    _detail=tk.Text(right, font=("Consolas", 9), bg="#0f1020", fg="#d8e0ff", wrap="none")
    _detail.pack(fill="both", expand=True, padx=8, pady=(0,8)); _detail.configure(state="disabled")
    body.add(right, minsize=360)

    _populate()
    return panel

def refresh():
    _populate()
