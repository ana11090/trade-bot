import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import os
import pandas as pd
import re
import io
import threading

import state

# WHY: Default file dialog directory — falls back to project root if running
#      from inside the repo, otherwise the user's home folder.
# CHANGED: April 2026 — remove hardcoded user-specific path
def _default_dialog_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    cur  = here
    for _ in range(5):
        if os.path.isdir(os.path.join(cur, 'data')):
            return cur
        cur = os.path.dirname(cur)
    return os.path.expanduser('~')


# Module-level StringVar references — created in build_panel() once Tk root exists
account_type    = None
starting_balance = None

# Internal widget references needed by callbacks
_selected_file   = None   # StringVar
_run_btn         = None
_progress_bar    = None
_tree            = None
_page_label      = None
_check_results   = None


def build_panel(content):
    global account_type, starting_balance
    global _selected_file, _run_btn, _progress_bar, _tree, _page_label, _check_results

    pipeline_panel = tk.Frame(content, bg="#f0f2f5")

    tk.Label(pipeline_panel, text="Data Pipeline", bg="#f0f2f5", fg="#1a1a2a",
             font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(24, 2))
    tk.Label(pipeline_panel, text="Select your Myfxbook trade history file to begin.",
             bg="#f0f2f5", fg="#666666", font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(0, 16))

    # ---------- STEP 1 CARD ----------
    card1 = tk.Frame(pipeline_panel, bg="white", bd=1, relief="solid")
    card1.pack(fill="x", padx=20, pady=(0, 10))
    tk.Label(card1, text="Step 1 - Select the trade transactions", bg="white", fg="#1a1a2a",
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 10))

    _selected_file = tk.StringVar()
    _selected_file.set("No file selected")

    def browse_file():
        path = filedialog.askopenfilename(
            title="Select your trade file",
            initialdir=_default_dialog_dir(),
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if path:
            _selected_file.set(os.path.basename(path))
            state.selected_file_full_path = path

    file_row = tk.Frame(card1, bg="white")
    file_row.pack(anchor="w", padx=16, pady=(0, 14))
    tk.Entry(file_row, textvariable=_selected_file, width=40, font=("Segoe UI", 10),
             bd=1, relief="solid").pack(side="left")
    tk.Button(file_row, text="Browse", font=("Segoe UI", 10), bd=1, relief="solid",
              activebackground="white", activeforeground="black",
              command=browse_file).pack(side="left", padx=(6, 0))

    # ---------- STEP 2 CARD ----------
    card2 = tk.Frame(pipeline_panel, bg="white", bd=1, relief="solid")
    card2.pack(fill="x", padx=20, pady=(0, 10))
    tk.Label(card2, text="Step 2 - Load the data", bg="white", fg="#1a1a2a",
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 10))

    # account settings row
    settings_row = tk.Frame(card2, bg="white")
    settings_row.pack(fill="x", padx=16, pady=(0, 12))

    tk.Label(settings_row, text="Account type:", bg="white", font=("Segoe UI", 10)).pack(side="left")
    account_type = tk.StringVar(value="Standard")
    state.account_type = account_type
    for label, value in [("Standard", "Standard"), ("Cent", "Cent"), ("Micro", "Micro")]:
        tk.Radiobutton(settings_row, text=label, variable=account_type, value=value,
                       bg="white", font=("Segoe UI", 10),
                       activebackground="white").pack(side="left", padx=(6, 0))

    tk.Frame(settings_row, bg="#dddddd", width=1).pack(side="left", fill="y", padx=14)

    tk.Label(settings_row, text="Initial deposit:", bg="white", font=("Segoe UI", 10)).pack(side="left")
    starting_balance = tk.StringVar(value="10000")
    state.starting_balance = starting_balance
    tk.Entry(settings_row, textvariable=starting_balance, width=10, font=("Segoe UI", 10),
             bd=1, relief="solid").pack(side="left", padx=(6, 0))
    tk.Label(settings_row, text="USD", bg="white", font=("Segoe UI", 10),
             fg="#666666").pack(side="left", padx=(4, 0))

    # treeview grid
    tree_frame = tk.Frame(card2, bg="white")
    tree_frame.pack(fill="x", padx=16, pady=(0, 0))

    _tree = ttk.Treeview(tree_frame, show="headings", height=8)
    _tree.pack(side="left", fill="x", expand=True)

    tree_yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=_tree.yview)
    tree_yscroll.pack(side="right", fill="y")
    _tree.configure(yscrollcommand=tree_yscroll.set)

    tree_xscroll = ttk.Scrollbar(card2, orient="horizontal", command=_tree.xview)
    tree_xscroll.pack(fill="x", padx=16)
    _tree.configure(xscrollcommand=tree_xscroll.set)

    # pagination row
    pagination_row = tk.Frame(card2, bg="white")
    pagination_row.pack(fill="x", padx=16, pady=(6, 14))

    _page_label = tk.Label(pagination_row, text="", bg="white", font=("Segoe UI", 10))
    _page_label.pack(side="left")

    tk.Button(pagination_row, text="< Prev", font=("Segoe UI", 10), bd=1, relief="solid",
              activebackground="white", activeforeground="black",
              command=prev_page).pack(side="right", padx=(4, 0))
    tk.Button(pagination_row, text="Next >", font=("Segoe UI", 10), bd=1, relief="solid",
              activebackground="white", activeforeground="black",
              command=next_page).pack(side="right")

    # run button row
    run_row = tk.Frame(card2, bg="white")
    run_row.pack(fill="x", padx=16, pady=(0, 10))

    _run_btn = tk.Button(run_row, text="Run", font=("Segoe UI", 10, "bold"),
                         bg="#e94560", fg="white",
                         activebackground="#e94560", activeforeground="white",
                         bd=0, padx=20, pady=8,
                         command=lambda: start_pipeline())
    _run_btn.pack(side="left")

    _progress_bar = ttk.Progressbar(run_row, mode="indeterminate", length=200)
    # not packed until Run is clicked

    def export_csv():
        if state.loaded_data is None:
            messagebox.showwarning("No data", "Please run the pipeline first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save as CSV",
            defaultextension=".csv",
            initialdir=_default_dialog_dir(),
            filetypes=[("CSV files", "*.csv")]
        )
        if path:
            state.loaded_data.to_csv(path, index=False)
            messagebox.showinfo("Exported", f"Saved to:\n{path}")

    def export_txt():
        if state.loaded_data is None:
            messagebox.showwarning("No data", "Please run the pipeline first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save as TXT",
            defaultextension=".txt",
            initialdir=_default_dialog_dir(),
            filetypes=[("Text files", "*.txt")]
        )
        if path:
            state.loaded_data.to_csv(path, index=False, sep="\t")
            messagebox.showinfo("Exported", f"Saved to:\n{path}")

    export_row = tk.Frame(card2, bg="white")
    export_row.pack(fill="x", padx=16, pady=(0, 14))
    tk.Button(export_row, text="Export CSV", font=("Segoe UI", 10), bd=1, relief="solid",
              padx=14, pady=6, activebackground="white", activeforeground="black",
              command=export_csv).pack(side="left")
    tk.Button(export_row, text="Export TXT", font=("Segoe UI", 10), bd=1, relief="solid",
              padx=14, pady=6, activebackground="white", activeforeground="black",
              command=export_txt).pack(side="left", padx=(8, 0))

    # ---------- STEP 3 CARD ----------
    card3 = tk.Frame(pipeline_panel, bg="white", bd=1, relief="solid")
    card3.pack(fill="x", padx=20, pady=(0, 20))
    tk.Label(card3, text="Step 3 - Clean the data", bg="white", fg="#1a1a2a",
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 10))

    _check_results = tk.Text(card3, bg="#f8f8f8", fg="#1a1a2a", font=("Segoe UI", 10),
                             height=6, bd=1, relief="solid", state="disabled", padx=10, pady=8)
    _check_results.pack(fill="x", padx=16, pady=(0, 10))

    btn_row3 = tk.Frame(card3, bg="white")
    btn_row3.pack(anchor="w", padx=16, pady=(0, 14))
    tk.Button(btn_row3, text="Check", font=("Segoe UI", 10, "bold"),
              bd=1, relief="solid", padx=16, pady=7,
              activebackground="white", activeforeground="black",
              command=check_data).pack(side="left")
    tk.Button(btn_row3, text="Clean", font=("Segoe UI", 10, "bold"),
              bg="#e94560", fg="white", bd=0, padx=16, pady=8,
              activebackground="#e94560", activeforeground="white",
              command=clean_data).pack(side="left", padx=(8, 0))
    tk.Button(btn_row3, text="Save Clean Data", font=("Segoe UI", 10),
              bd=1, relief="solid", padx=14, pady=7,
              activebackground="white", activeforeground="black",
              command=save_clean_data).pack(side="left", padx=(8, 0))

    return pipeline_panel


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE CALLBACKS
# ─────────────────────────────────────────────────────────────────────────────

def show_page(page_number):
    for item in _tree.get_children():
        _tree.delete(item)
    start = page_number * state.rows_per_page
    end   = start + state.rows_per_page
    for row in state.all_rows[start:end]:
        _tree.insert("", "end", values=row)
    total_pages = max(1, -(-len(state.all_rows) // state.rows_per_page))
    _page_label.configure(
        text=f"Page {page_number + 1} of {total_pages}  ({len(state.all_rows)} rows total)")
    state.current_page[0] = page_number


def prev_page():
    if state.current_page[0] > 0:
        show_page(state.current_page[0] - 1)


def next_page():
    total_pages = -(-len(state.all_rows) // state.rows_per_page)
    if state.current_page[0] < total_pages - 1:
        show_page(state.current_page[0] + 1)


def start_pipeline():
    if not state.selected_file_full_path:
        messagebox.showwarning("No file", "Please select a file first.")
        return
    _run_btn.configure(state="disabled")
    _progress_bar.pack(side="left", padx=(10, 0))
    _progress_bar.start(10)
    t = threading.Thread(target=pipeline_worker, daemon=True)
    t.start()


def pipeline_worker():
    # We need the root window to schedule pipeline_done — grab it from the tree widget
    root = _tree.winfo_toplevel()
    try:
        # read with utf-8, fall back to cp1252 if needed
        try:
            with open(state.selected_file_full_path, 'r', encoding='utf-8') as f:
                raw_text = f.read()
        except UnicodeDecodeError:
            with open(state.selected_file_full_path, 'r', encoding='cp1252', errors='replace') as f:
                raw_text = f.read()

        # ── split rows if file is single-line (both source formats pack everything onto one line)
        data_lines = [l for l in raw_text.strip().splitlines() if l.strip()]
        already_multiline = len(data_lines) > 2   # header + at least 2 data rows

        if not already_multiline:
            if re.search(r'\d{2}/\d{2}/\d{4} \d{2}:\d{2}', raw_text):
                # Format B single-line: dates DD/MM/YYYY HH:MM
                raw_text = re.sub(r'(Change %)\s+(\d{2}/\d{2}/\d{4})',               r'\1\n\2', raw_text)
                raw_text = re.sub(r'(-?\d+\.\d+) (\d{2}/\d{2}/\d{4} \d{2}:\d{2},)', r'\1\n\2', raw_text)
                fmt_b = True
            else:
                # Format A single-line: dates MMDDYYYY HHMM
                raw_text = re.sub(r'(Change %)\s+(\d{8})',         r'\1\n\2', raw_text)
                raw_text = re.sub(r'(-?\d+\.\d+) (\d{8} \d{4},)', r'\1\n\2', raw_text)
                fmt_b = False
        else:
            # already multi-line (exported CSV/TXT) — just detect date format
            fmt_b = bool(re.search(r'\d{2}/\d{2}/\d{4} \d{2}:\d{2}', raw_text))

        # WHY: MT5 exports use 'YYYY.MM.DD HH:MM:SS' which is neither Format A
        #      (MMDDYYYY HHMM) nor Format B (DD/MM/YYYY HH:MM). Its dates are
        #      already readable, so flag it and skip BOTH date converters below.
        #      This is a NEW branch — Format A/B paths are untouched.
        # CHANGED: June 2026 — recognize MT5 'YYYY.MM.DD HH:MM:SS' format
        fmt_mt5 = bool(re.search(r'\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}', raw_text))

        # WHY: MT5 trade-history exports are semicolon-delimited and start with
        #      a UTF-8 BOM. The old detector only knew tab-or-comma, so a ';'
        #      file was read as a SINGLE column; the later data.columns[1]
        #      access then threw "index out of bounds". Strip the BOM and add
        #      ';' to separator detection. Tab and comma behavior is unchanged.
        # CHANGED: June 2026 — support MT5 ';'-delimited exports (additive)
        _first_line = raw_text.split('\n')[0].lstrip('\ufeff')
        if '\t' in _first_line:
            sep = '\t'
        elif ';' in _first_line and _first_line.count(';') >= _first_line.count(','):
            sep = ';'
        else:
            sep = ','
        import pandas as _pd
        # mangle_dupe_cols handles the duplicate Time/Volume/Price headers
        # (pandas renames the 2nd occurrence to 'Time.1' etc. — harmless here).
        data = _pd.read_csv(io.StringIO(raw_text), skipinitialspace=True, sep=sep)
        # Strip a BOM left on the first column name, if any.
        if len(data.columns) and isinstance(data.columns[0], str):
            data = data.rename(columns={data.columns[0]: data.columns[0].lstrip('\ufeff')})

        if len(data) == 0:
            root.after(0, pipeline_done, None,
                       "File was read but 0 rows were found.\n\n"
                       "This usually means the row format did not match what was expected.\n"
                       f"First 200 characters of file:\n{raw_text[:200]}")
            return

        col0 = data.columns[0]
        col1 = data.columns[1]

        if not fmt_b and not fmt_mt5:
            # Format A: convert dates MMDDYYYY HHMM → DD/MM/YYYY HH:MM
            data[col0] = _pd.to_datetime(data[col0].astype(str).str.strip(),
                                         format="%m%d%Y %H%M", errors="coerce").dt.strftime("%d/%m/%Y %H:%M")
            data[col1] = _pd.to_datetime(data[col1].astype(str).str.strip(),
                                         format="%m%d%Y %H%M", errors="coerce").dt.strftime("%d/%m/%Y %H:%M")
            if "Duration (DDHHMMSS)" in data.columns:
                def fmt_dur_a(v):
                    v = str(v).strip().zfill(8)
                    return f"{v[0:2]}:{v[2:4]}:{v[4:6]}:{v[6:8]}"
                data["Duration (DDHHMMSS)"] = data["Duration (DDHHMMSS)"].apply(fmt_dur_a)
        # Format B: dates and duration already correct — nothing to convert

        root.after(0, pipeline_done, data, None)

    except Exception as e:
        root.after(0, pipeline_done, None, str(e))


def pipeline_done(data, error):
    _progress_bar.stop()
    _progress_bar.pack_forget()
    _run_btn.configure(state="normal")

    if error:
        messagebox.showerror("Error", f"Could not load file:\n{error}")
        return

    state.loaded_data = data

    _tree["columns"] = ["ID"] + list(data.columns)
    _tree.heading("ID", text="ID")
    _tree.column("ID", width=50, anchor="center")
    for col in data.columns:
        _tree.heading(col, text=col)
        _tree.column(col, width=110, anchor="w")

    state.all_rows.clear()
    for index, row in enumerate(data.itertuples(index=False), start=1):
        state.all_rows.append([index] + list(row))

    show_page(0)


# ── Step 3 callbacks ──────────────────────────────────────────────────────────

def _write_check_result(text):
    _check_results.configure(state="normal")
    _check_results.delete("1.0", tk.END)
    _check_results.insert(tk.END, text)
    _check_results.configure(state="disabled")


def check_data():
    if state.loaded_data is None:
        _write_check_result("No data loaded. Please run Step 2 first.")
        return

    df = state.loaded_data
    problem_indices = set()

    # WHY: Invalid dates can be either the string "NaT" (legacy) OR
    #      pandas NaN (after to_datetime+strftime). Check both.
    # CHANGED: April 2026 — detect both representations
    def _is_bad_date(v):
        if pd.isna(v):
            return True
        return str(v).strip() in ("NaT", "nan", "NaN", "")

    bad_open_count = 0
    for i, v in enumerate(df.iloc[:, 0]):
        if _is_bad_date(v):
            bad_open_count += 1
            problem_indices.add(i)

    # WHY: Format A/B have a close-date in col 1. MT5 format has 'Type' in
    #      col 1 (not a date), so treating it as a date would flag every MT5
    #      row as "bad close date". Skip the col-1 date check for MT5; the
    #      MT5 close-time is in a different column (named 'Time.1' after the
    #      duplicate-header rename in pipeline_worker).
    # CHANGED: June 2026 — col-1 date check skipped when col 1 is 'Type'
    bad_close_count = 0
    if not ("Type" in df.columns and df.columns[1] == "Type"):
        for i, v in enumerate(df.iloc[:, 1]):
            if _is_bad_date(v):
                bad_close_count += 1
                problem_indices.add(i)

    # WHY: `duplicated(keep=False)` flags ALL copies; `keep='first'` flags
    #      only the EXTRAS — exactly the count of rows that will be removed.
    # CHANGED: April 2026 — count extras, not half of total
    dup_mask        = df.duplicated(keep=False)
    extras_mask     = df.duplicated(keep='first')
    dup_count       = int(extras_mask.sum())
    for i, is_dup in enumerate(dup_mask):
        if is_dup:
            problem_indices.add(i)

    missing_profit_count = 0
    if "Profit" in df.columns:
        for i, v in enumerate(df["Profit"]):
            if pd.isna(v):
                problem_indices.add(i)
                missing_profit_count += 1

    for item in _tree.get_children():
        _tree.delete(item)
    for i in sorted(problem_indices):
        if i < len(state.all_rows):
            _tree.insert("", "end", values=state.all_rows[i])

    total_issues = len(problem_indices)
    if total_issues == 0:
        _page_label.configure(text="No issues found — data looks clean.")
        show_page(state.current_page[0])
    else:
        _page_label.configure(
            text=f"Showing {total_issues} problem row(s) — click Clean to remove them")

    _write_check_result("\n".join([
        f"Invalid Open Date:    {bad_open_count}",
        f"Invalid Close Date:   {bad_close_count}",
        f"Duplicate rows:       {dup_count} extras  (all copies shown so you can compare)",
        f"Missing Profit:       {missing_profit_count}",
        "",
        "No issues found — data is clean." if total_issues == 0
        else f"{total_issues} row(s) highlighted in the grid above."
    ]))


def clean_data():
    if state.loaded_data is None:
        _write_check_result("No data loaded. Please run Step 2 first.")
        return

    df     = state.loaded_data.copy()
    before = len(df)

    # WHY: After to_datetime+strftime, invalid dates become NaN (float), not
    #      the string "NaT". The old filter did nothing for those rows.
    # CHANGED: April 2026 — actually drop rows with invalid dates
    # WHY: Format A/B have dates in cols 0 AND 1. MT5 format has a date in
    #      col 0 but 'Type' in col 1, so treating col 1 as a date would drop
    #      every MT5 row. Only include col 1 as a date column when it isn't
    #      'Type'. Format A/B (no Type column) take the original 2-col path.
    # CHANGED: June 2026 — col-1 date check skipped for MT5 (additive)
    if "Type" in df.columns and df.columns[1] == "Type":
        date_cols = [df.columns[0]]
    else:
        date_cols = [df.columns[0], df.columns[1]]
    for c in date_cols:
        df = df[df[c].astype(str).str.strip() != "NaT"]
    df = df.dropna(subset=date_cols)
    df = df.drop_duplicates()

    # WHY: MT5 history exports include non-trade ledger rows (Balance, Deposit,
    #      Withdrawal, etc.). They carry a non-null Profit, so the Profit
    #      dropna below does NOT remove them, and they are not trades. Drop any
    #      row whose Type is not Buy/Sell. Guarded by the Type column existing,
    #      so Format A/B files (no Type column) are unaffected.
    # CHANGED: June 2026 — drop MT5 non-trade ledger rows (additive)
    if "Type" in df.columns:
        _t = df["Type"].astype(str).str.strip().str.lower()
        df = df[_t.isin(["buy", "sell"])]

    if "Profit" in df.columns:
        df = df.dropna(subset=["Profit"])

    after   = len(df)
    removed = before - after

    state.loaded_data = df.reset_index(drop=True)

    state.all_rows.clear()
    for index, row in enumerate(state.loaded_data.itertuples(index=False), start=1):
        state.all_rows.append([index] + list(row))

    show_page(0)
    _write_check_result(
        f"Cleaning done.\n\nRows before:  {before}\nRows after:   {after}\n"
        f"Rows removed: {removed}\n\nGrid updated — showing clean data."
    )


def _looks_like_mt5(df):
    # WHY: MT5 history exports have duplicate Time/Price headers that pandas
    #      auto-renames to Time/Time.1, Price/Price.1, plus a 'Type' column
    #      carrying Buy/Sell. Canonical files don't.
    # CHANGED: June 2026 — MT5 format sniffer
    cols = list(df.columns)
    return ("Type" in cols and "Time" in cols
            and any(str(c).startswith("Time.") for c in cols)
            and any(str(c).startswith("Price.") for c in cols))


def _resolve_pip_size():
    # WHY: Use the canonical pip_size from p1_config.json; default 0.01
    #      (XAUUSD) so a config-load error never blocks Save. Use importlib
    #      to avoid adding project1_reverse_engineering to sys.path
    #      permanently — same pattern as step1_align_price.
    # CHANGED: June 2026 — config-driven pip_size for MT5 conversion
    try:
        import importlib.util
        _repo_root = os.path.abspath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '..', '..'))
        _cl_path = os.path.join(_repo_root, 'project1_reverse_engineering',
                                'config_loader.py')
        _spec = importlib.util.spec_from_file_location('_p0_p1_cl', _cl_path)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _cfg = _mod.load()
        return float(_cfg.get('pip_size', 0.01) or 0.01)
    except Exception:
        return 0.01


def _convert_mt5_to_canonical(df):
    """MT5 history → canonical schema Step 1 expects.

    MT5 columns (after pandas dup-rename):
      Time / Type / Volume / Symbol / Price / Volume.1 / Time.1 / Price.1 /
      Commission / Swap / Profit
    Canonical out: Open Date, Close Date, Symbol, Action, Lots,
                   Open Price, Close Price, Pips, Profit
    Pips sign-aware: BUY profits when price rises (close-open),
                     SELL profits when price falls (open-close).
    """
    # WHY: Step 1 (project1_reverse_engineering/step1_align_price.py) maps
    #      Open Date / Close Date / Open Price / Close Price / Action / Lots
    #      / Pips / Profit. The MT5 export carries the same INFORMATION under
    #      different column names and is missing a Pips column. Compute Pips
    #      here from prices using config pip_size so sells are profitable
    #      when price falls — without this every sell would look like a loss.
    # CHANGED: June 2026 — MT5 → canonical converter
    import pandas as _pd

    _pip_size = _resolve_pip_size()

    out = _pd.DataFrame()
    out["Open Date"]   = df["Time"]
    out["Close Date"]  = df["Time.1"]
    out["Symbol"]      = df["Symbol"]
    out["Action"]      = df["Type"].astype(str).str.strip().str.upper()  # BUY / SELL
    out["Lots"]        = _pd.to_numeric(df["Volume"], errors="coerce")
    out["Open Price"]  = _pd.to_numeric(df["Price"], errors="coerce")
    out["Close Price"] = _pd.to_numeric(df["Price.1"], errors="coerce")
    if "Profit" in df.columns:
        out["Profit"]  = _pd.to_numeric(df["Profit"], errors="coerce")

    _o = out["Open Price"]
    _c = out["Close Price"]
    _is_buy = out["Action"].eq("BUY")
    # where(cond, other): keep (close-open) where BUY, else use (open-close)
    out["Pips"] = (_c - _o).where(_is_buy, _o - _c) / _pip_size

    # Drop rows that failed numeric conversion (stray ledger rows etc.).
    out = out.dropna(subset=["Open Date", "Close Date", "Open Price", "Close Price"])
    return out.reset_index(drop=True)


def save_clean_data():
    # WHY: Previously this only wrote a CSV via a file picker and never
    #      registered it as the active trade history, so the Project-1
    #      reverse-engineering run (which reads
    #      trade_history_manager.get_active_history()) kept using the old
    #      original_bot/trades_clean.csv fallback — ignoring whatever was
    #      loaded into this page. It also could not handle MT5 exports
    #      (different columns, no Pips). Now: if the loaded data is an MT5
    #      export, convert it to canonical (sign-aware Pips from config
    #      pip_size), then register the result as the ACTIVE history via
    #      load_trades() so the very next run uses THIS file.
    #      SELL handling needs no extra discovery code — analyze.py detects
    #      direction from the data and runs BOTH sides whenever each has
    #      >= 40 trades.
    # CHANGED: June 2026 — convert MT5 + register as active history on save
    if state.loaded_data is None:
        messagebox.showwarning("No data", "Please run the pipeline first.")
        return

    df = state.loaded_data.copy()
    try:
        if _looks_like_mt5(df):
            df = _convert_mt5_to_canonical(df)
    except Exception as _conv_err:
        messagebox.showerror("Conversion error",
                             f"Could not convert MT5 history to the canonical "
                             f"trade format:\n{_conv_err}")
        return

    # Ask for a name for this history (used as the active-history label /
    # robot_name in trade_history_manager).
    import tkinter.simpledialog as _sd
    _name = _sd.askstring("Trade history name",
                          "Name this trade history (used by the run):",
                          initialvalue="Gold Reaper")
    if not _name:
        return

    # Write to a temp CSV, then register as the active history.
    try:
        import tempfile
        _tmp = os.path.join(tempfile.gettempdir(),
                            f"_clean_{_name.replace(' ', '_')}.csv")
        df.to_csv(_tmp, index=False)

        # WHY: load_trades(robot_name, trades_csv_path, symbol='XAUUSD',
        #      description='') registers the file in trade_histories/ AND
        #      sets active_history_id = its id (line ~276), so the next
        #      run picks it up via get_active_history(). Positional order
        #      confirmed against shared/trade_history_manager.py:185.
        # CHANGED: June 2026 — register the cleaned file as active history
        from shared.trade_history_manager import load_trades
        load_trades(_name, _tmp)
        messagebox.showinfo(
            "Saved & activated",
            f"Cleaned {len(df)} trades and set as the ACTIVE trade history "
            f"('{_name}').\n\nThe next 'Run Selected Scenarios' will use THIS "
            f"file.\n\n(BUY/SELL is auto-detected from the data.)"
        )
    except Exception as _reg_err:
        messagebox.showerror(
            "Save error",
            f"Converted OK but could not register as active history:\n{_reg_err}"
        )
