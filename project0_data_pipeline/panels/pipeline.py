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

        # auto-detect separator (comma for CSV, tab for exported TXT)
        sep = '\t' if '\t' in raw_text.split('\n')[0] else ','
        import pandas as _pd
        data = _pd.read_csv(io.StringIO(raw_text), skipinitialspace=True, sep=sep)

        if len(data) == 0:
            root.after(0, pipeline_done, None,
                       "File was read but 0 rows were found.\n\n"
                       "This usually means the row format did not match what was expected.\n"
                       f"First 200 characters of file:\n{raw_text[:200]}")
            return

        col0 = data.columns[0]
        col1 = data.columns[1]

        if not fmt_b:
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

    bad_close_count = 0
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
    date_cols = [df.columns[0], df.columns[1]]
    for c in date_cols:
        df = df[df[c].astype(str).str.strip() != "NaT"]
    df = df.dropna(subset=date_cols)
    df = df.drop_duplicates()
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


def save_clean_data():
    if state.loaded_data is None:
        messagebox.showwarning("No data", "Please run the pipeline first.")
        return
    # WHY: Hardcoded absolute path breaks on every other developer machine.
    # CHANGED: April 2026 — use _default_dialog_dir() (audit LOW)
    path = filedialog.asksaveasfilename(
        title="Save clean data",
        defaultextension=".csv",
        initialfile="trades_clean.csv",
        initialdir=_default_dialog_dir(),
        filetypes=[("CSV files", "*.csv")]
    )
    if path:
        state.loaded_data.to_csv(path, index=False)
        messagebox.showinfo("Saved",
                            f"Clean data saved to:\n{path}\n\n"
                            "Analysis panels will read from this file.")
