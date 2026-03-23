import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import os
import pandas as pd
import re
import io
import threading
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# ─────────────────────────────────────────────────────────────────────────────
# MAIN WINDOW
# ─────────────────────────────────────────────────────────────────────────────
window = tk.Tk()
window.title("Trade Bot")
window.geometry("900x680")

# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
sidebar = tk.Frame(window, bg="#16213e", width=200)
sidebar.pack(side="left", fill="y")
sidebar.pack_propagate(False)

tk.Label(sidebar, text="Trade Bot", bg="#16213e", fg="white",
         font=("Segoe UI", 14, "bold"), pady=20).pack()

COL_ACTIVE    = "#e94560"
COL_INACTIVE  = "#16213e"
COL_PARENT    = "#1e2d4e"   # btn0 color when a sub-panel is active
COL_SUB       = "#0f1628"   # submenu background
FG_ACTIVE     = "white"
FG_INACTIVE   = "#445577"
FG_SUB        = "#5a7a99"   # sub-button inactive text

# all_panels is populated after frames are created
all_panels   = {}
active_panel = [None]
SUB_PANELS   = {"panel4", "panel5", "panel6", "panel7", "panel8"}
submenu_open = [False]

def show_panel(name):
    for pframe in all_panels.values():
        pframe.pack_forget()
    if name in all_panels:
        all_panels[name].pack(fill="both", expand=True)

    is_sub = name in SUB_PANELS

    # btn0 color: red if pipeline active, lighter if a sub-panel is open, dark if neither
    if name == "pipeline":
        btn0.configure(bg=COL_ACTIVE,  fg=FG_ACTIVE,
                       activebackground=COL_ACTIVE,  activeforeground=FG_ACTIVE)
    elif is_sub:
        btn0.configure(bg=COL_PARENT,  fg="white",
                       activebackground=COL_PARENT,  activeforeground="white")
    else:
        btn0.configure(bg=COL_INACTIVE, fg=FG_INACTIVE,
                       activebackground=COL_INACTIVE, activeforeground=FG_INACTIVE)

    # sub-button colors
    for pname, btn in SUB_BUTTONS.items():
        if pname == name:
            btn.configure(bg=COL_ACTIVE, fg=FG_ACTIVE,
                          activebackground=COL_ACTIVE, activeforeground=FG_ACTIVE)
        else:
            btn.configure(bg=COL_SUB, fg=FG_SUB,
                          activebackground=COL_SUB, activeforeground=FG_SUB)

    # if navigating to a sub-panel, make sure the submenu is visible
    if is_sub and not submenu_open[0]:
        submenu_frame.pack(fill="x", after=btn0)
        submenu_open[0] = True

    canvas.yview_moveto(0)
    active_panel[0] = name
    if name == "panel4":
        refresh_panel4()
    elif name == "panel5":
        refresh_panel5()

def _sidebar_btn(text, cmd):
    return tk.Button(sidebar, text=text,
                     bg=COL_INACTIVE, fg=FG_INACTIVE,
                     activebackground=COL_INACTIVE, activeforeground=FG_INACTIVE,
                     font=("Segoe UI", 11), bd=0, pady=12, anchor="w", padx=16,
                     command=cmd)

def _toggle_pipeline():
    """Click on btn0: always show pipeline panel; toggle submenu open/closed."""
    show_panel("pipeline")
    if submenu_open[0]:
        submenu_frame.pack_forget()
        submenu_open[0] = False
    else:
        submenu_frame.pack(fill="x", after=btn0)
        submenu_open[0] = True

btn0 = _sidebar_btn("0 - Data Pipeline", _toggle_pipeline)
btn0.pack(fill="x")

# ── submenu (hidden until btn0 is clicked) ──────────────────────────────────
submenu_frame = tk.Frame(sidebar, bg=COL_SUB)

def _sub_btn(text, cmd):
    return tk.Button(submenu_frame, text=text,
                     bg=COL_SUB, fg=FG_SUB,
                     activebackground=COL_SUB, activeforeground=FG_SUB,
                     font=("Segoe UI", 10), bd=0, pady=9, anchor="w", padx=30,
                     command=cmd)

btn_p4 = _sub_btn("Performance",     lambda: show_panel("panel4"))
btn_p4.pack(fill="x")
btn_p5 = _sub_btn("Statistics",      lambda: show_panel("panel5"))
btn_p5.pack(fill="x")
btn_p6 = _sub_btn("Risk & Flags",    lambda: show_panel("panel6"))
btn_p6.pack(fill="x")
btn_p7 = _sub_btn("Prop Compliance", lambda: show_panel("panel7"))
btn_p7.pack(fill="x")
btn_p8 = _sub_btn("Cost & Spread",   lambda: show_panel("panel8"))
btn_p8.pack(fill="x")

SUB_BUTTONS = {
    "panel4": btn_p4, "panel5": btn_p5, "panel6": btn_p6,
    "panel7": btn_p7, "panel8": btn_p8,
}

# ── remaining project buttons ────────────────────────────────────────────────
btn1 = _sidebar_btn("1 - Reverse Engineer", lambda: None)
btn1.pack(fill="x")
btn2 = _sidebar_btn("2 - Backtesting",      lambda: None)
btn2.pack(fill="x")
btn3 = _sidebar_btn("3 - Forward Bot",      lambda: None)
btn3.pack(fill="x")

PANEL_BUTTONS = {
    "pipeline": btn0,
    "panel4":   btn_p4,
    "panel5":   btn_p5,
    "panel6":   btn_p6,
    "panel7":   btn_p7,
    "panel8":   btn_p8,
}

# ─────────────────────────────────────────────────────────────────────────────
# MAIN AREA — scrollable canvas
# ─────────────────────────────────────────────────────────────────────────────
main_area = tk.Frame(window, bg="#f0f2f5")
main_area.pack(side="right", fill="both", expand=True)

canvas    = tk.Canvas(main_area, bg="#f0f2f5", highlightthickness=0)
scrollbar = ttk.Scrollbar(main_area, orient="vertical", command=canvas.yview)
canvas.configure(yscrollcommand=scrollbar.set)
scrollbar.pack(side="right", fill="y")
canvas.pack(side="left", fill="both", expand=True)

content        = tk.Frame(canvas, bg="#f0f2f5")
content_window = canvas.create_window((0, 0), window=content, anchor="nw")

def on_content_resize(event):
    canvas.configure(scrollregion=canvas.bbox("all"))

def on_canvas_resize(event):
    canvas.itemconfig(content_window, width=event.width)

content.bind("<Configure>", on_content_resize)
canvas.bind("<Configure>",  on_canvas_resize)

def scroll_canvas(event):
    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

canvas.bind("<MouseWheel>", scroll_canvas)
content.bind("<MouseWheel>", scroll_canvas)

# ─────────────────────────────────────────────────────────────────────────────
# DATA PIPELINE PANEL — Step 1, 2, 3
# ─────────────────────────────────────────────────────────────────────────────
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

selected_file           = tk.StringVar()
selected_file_full_path = ""
selected_file.set("No file selected")

def browse_file():
    global selected_file_full_path
    path = filedialog.askopenfilename(
        title="Select your trade file",
        initialdir=r"C:\Users\anani\my git delete\trade-bot",
        filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
    )
    if path:
        selected_file.set(os.path.basename(path))
        selected_file_full_path = path

file_row = tk.Frame(card1, bg="white")
file_row.pack(anchor="w", padx=16, pady=(0, 14))
tk.Entry(file_row, textvariable=selected_file, width=40, font=("Segoe UI", 10),
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
for label, value in [("Standard", "Standard"), ("Cent", "Cent"), ("Micro", "Micro")]:
    tk.Radiobutton(settings_row, text=label, variable=account_type, value=value,
                   bg="white", font=("Segoe UI", 10),
                   activebackground="white").pack(side="left", padx=(6, 0))

tk.Frame(settings_row, bg="#dddddd", width=1).pack(side="left", fill="y", padx=14)

tk.Label(settings_row, text="Initial deposit:", bg="white", font=("Segoe UI", 10)).pack(side="left")
starting_balance = tk.StringVar(value="10000")
tk.Entry(settings_row, textvariable=starting_balance, width=10, font=("Segoe UI", 10),
         bd=1, relief="solid").pack(side="left", padx=(6, 0))
tk.Label(settings_row, text="USD", bg="white", font=("Segoe UI", 10),
         fg="#666666").pack(side="left", padx=(4, 0))

loaded_data   = None    # holds the pandas DataFrame after loading
all_rows      = []      # holds all rows as lists for the grid
current_page  = [0]
rows_per_page = 50

# treeview grid
tree_frame = tk.Frame(card2, bg="white")
tree_frame.pack(fill="x", padx=16, pady=(0, 0))

tree = ttk.Treeview(tree_frame, show="headings", height=8)
tree.pack(side="left", fill="x", expand=True)

tree_yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
tree_yscroll.pack(side="right", fill="y")
tree.configure(yscrollcommand=tree_yscroll.set)

tree_xscroll = ttk.Scrollbar(card2, orient="horizontal", command=tree.xview)
tree_xscroll.pack(fill="x", padx=16)
tree.configure(xscrollcommand=tree_xscroll.set)

# pagination row
pagination_row = tk.Frame(card2, bg="white")
pagination_row.pack(fill="x", padx=16, pady=(6, 14))

page_label = tk.Label(pagination_row, text="", bg="white", font=("Segoe UI", 10))
page_label.pack(side="left")

def show_page(page_number):
    for item in tree.get_children():
        tree.delete(item)
    start = page_number * rows_per_page
    end   = start + rows_per_page
    for row in all_rows[start:end]:
        tree.insert("", "end", values=row)
    total_pages = max(1, -(-len(all_rows) // rows_per_page))
    page_label.configure(text=f"Page {page_number + 1} of {total_pages}  ({len(all_rows)} rows total)")
    current_page[0] = page_number

def prev_page():
    if current_page[0] > 0:
        show_page(current_page[0] - 1)

def next_page():
    total_pages = -(-len(all_rows) // rows_per_page)
    if current_page[0] < total_pages - 1:
        show_page(current_page[0] + 1)

tk.Button(pagination_row, text="< Prev", font=("Segoe UI", 10), bd=1, relief="solid",
          activebackground="white", activeforeground="black",
          command=prev_page).pack(side="right", padx=(4, 0))
tk.Button(pagination_row, text="Next >", font=("Segoe UI", 10), bd=1, relief="solid",
          activebackground="white", activeforeground="black",
          command=next_page).pack(side="right")

# run button row
run_row = tk.Frame(card2, bg="white")
run_row.pack(fill="x", padx=16, pady=(0, 10))

run_btn = tk.Button(run_row, text="Run", font=("Segoe UI", 10, "bold"),
                    bg="#e94560", fg="white",
                    activebackground="#e94560", activeforeground="white",
                    bd=0, padx=20, pady=8,
                    command=lambda: start_pipeline())
run_btn.pack(side="left")

progress_bar = ttk.Progressbar(run_row, mode="indeterminate", length=200)
# not packed until Run is clicked

def export_csv():
    if loaded_data is None:
        messagebox.showwarning("No data", "Please run the pipeline first.")
        return
    path = filedialog.asksaveasfilename(
        title="Save as CSV",
        defaultextension=".csv",
        initialdir=r"C:\Users\anani\my git delete\trade-bot",
        filetypes=[("CSV files", "*.csv")]
    )
    if path:
        loaded_data.to_csv(path, index=False)
        messagebox.showinfo("Exported", f"Saved to:\n{path}")

def export_txt():
    if loaded_data is None:
        messagebox.showwarning("No data", "Please run the pipeline first.")
        return
    path = filedialog.asksaveasfilename(
        title="Save as TXT",
        defaultextension=".txt",
        initialdir=r"C:\Users\anani\my git delete\trade-bot",
        filetypes=[("Text files", "*.txt")]
    )
    if path:
        loaded_data.to_csv(path, index=False, sep="\t")
        messagebox.showinfo("Exported", f"Saved to:\n{path}")

export_row = tk.Frame(card2, bg="white")
export_row.pack(fill="x", padx=16, pady=(0, 14))
tk.Button(export_row, text="Export CSV", font=("Segoe UI", 10), bd=1, relief="solid",
          padx=14, pady=6, activebackground="white", activeforeground="black",
          command=export_csv).pack(side="left")
tk.Button(export_row, text="Export TXT", font=("Segoe UI", 10), bd=1, relief="solid",
          padx=14, pady=6, activebackground="white", activeforeground="black",
          command=export_txt).pack(side="left", padx=(8, 0))

def start_pipeline():
    if not selected_file_full_path:
        messagebox.showwarning("No file", "Please select a file first.")
        return
    run_btn.configure(state="disabled")
    progress_bar.pack(side="left", padx=(10, 0))
    progress_bar.start(10)
    t = threading.Thread(target=pipeline_worker, daemon=True)
    t.start()

def pipeline_worker():
    try:
        with open(selected_file_full_path, 'r') as f:
            raw_text = f.read()

        raw_text = re.sub(r'(Change %)\s+(\d{8})',         r'\1\n\2', raw_text)
        raw_text = re.sub(r'(-?\d+\.\d+) (\d{8} \d{4},)', r'\1\n\2', raw_text)

        data = pd.read_csv(io.StringIO(raw_text), skipinitialspace=True)

        col0 = data.columns[0]
        col1 = data.columns[1]
        data[col0] = pd.to_datetime(data[col0].astype(str).str.strip(),
                                    format="%m%d%Y %H%M", errors="coerce").dt.strftime("%d/%m/%Y %H:%M")
        data[col1] = pd.to_datetime(data[col1].astype(str).str.strip(),
                                    format="%m%d%Y %H%M", errors="coerce").dt.strftime("%d/%m/%Y %H:%M")

        if "Duration (DDHHMMSS)" in data.columns:
            def fmt_dur(v):
                v = str(v).strip().zfill(8)
                return f"{v[0:2]}:{v[2:4]}:{v[4:6]}:{v[6:8]}"
            data["Duration (DDHHMMSS)"] = data["Duration (DDHHMMSS)"].apply(fmt_dur)

        window.after(0, pipeline_done, data, None)

    except Exception as e:
        window.after(0, pipeline_done, None, str(e))

def pipeline_done(data, error):
    global loaded_data

    progress_bar.stop()
    progress_bar.pack_forget()
    run_btn.configure(state="normal")

    if error:
        messagebox.showerror("Error", f"Could not load file:\n{error}")
        return

    loaded_data = data

    tree["columns"] = ["ID"] + list(data.columns)
    tree.heading("ID", text="ID")
    tree.column("ID", width=50, anchor="center")
    for col in data.columns:
        tree.heading(col, text=col)
        tree.column(col, width=110, anchor="w")

    all_rows.clear()
    for index, row in enumerate(data.itertuples(index=False), start=1):
        all_rows.append([index] + list(row))

    show_page(0)

# ---------- STEP 3 CARD ----------
card3 = tk.Frame(pipeline_panel, bg="white", bd=1, relief="solid")
card3.pack(fill="x", padx=20, pady=(0, 20))
tk.Label(card3, text="Step 3 - Clean the data", bg="white", fg="#1a1a2a",
         font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 10))

check_results = tk.Text(card3, bg="#f8f8f8", fg="#1a1a2a", font=("Segoe UI", 10),
                        height=6, bd=1, relief="solid", state="disabled", padx=10, pady=8)
check_results.pack(fill="x", padx=16, pady=(0, 10))

def write_check_result(text):
    check_results.configure(state="normal")
    check_results.delete("1.0", tk.END)
    check_results.insert(tk.END, text)
    check_results.configure(state="disabled")

def check_data():
    if loaded_data is None:
        write_check_result("No data loaded. Please run Step 2 first.")
        return

    df = loaded_data
    problem_indices = set()

    bad_open_count = sum(1 for v in df.iloc[:, 0] if str(v).strip() == "NaT")
    for i, v in enumerate(df.iloc[:, 0]):
        if str(v).strip() == "NaT":
            problem_indices.add(i)

    bad_close_count = sum(1 for v in df.iloc[:, 1] if str(v).strip() == "NaT")
    for i, v in enumerate(df.iloc[:, 1]):
        if str(v).strip() == "NaT":
            problem_indices.add(i)

    dup_mask  = df.duplicated(keep=False)
    dup_count = int(dup_mask.sum()) // 2
    for i, is_dup in enumerate(dup_mask):
        if is_dup:
            problem_indices.add(i)

    missing_profit_count = 0
    if "Profit" in df.columns:
        for i, v in enumerate(df["Profit"]):
            if pd.isna(v):
                problem_indices.add(i)
                missing_profit_count += 1

    for item in tree.get_children():
        tree.delete(item)
    for i in sorted(problem_indices):
        if i < len(all_rows):
            tree.insert("", "end", values=all_rows[i])

    total_issues = len(problem_indices)
    if total_issues == 0:
        page_label.configure(text="No issues found — data looks clean.")
        show_page(current_page[0])
    else:
        page_label.configure(text=f"Showing {total_issues} problem row(s) — click Clean to remove them")

    write_check_result("\n".join([
        f"Invalid Open Date:    {bad_open_count}",
        f"Invalid Close Date:   {bad_close_count}",
        f"Duplicate pairs:      {dup_count}  (both copies shown so you can compare)",
        f"Missing Profit:       {missing_profit_count}",
        "",
        "No issues found — data is clean." if total_issues == 0
        else f"{total_issues} row(s) highlighted in the grid above."
    ]))

def clean_data():
    global loaded_data
    if loaded_data is None:
        write_check_result("No data loaded. Please run Step 2 first.")
        return

    df     = loaded_data.copy()
    before = len(df)

    df = df[df.iloc[:, 0] != "NaT"]
    df = df[df.iloc[:, 1] != "NaT"]
    df = df.drop_duplicates()
    if "Profit" in df.columns:
        df = df.dropna(subset=["Profit"])

    after   = len(df)
    removed = before - after

    loaded_data = df.reset_index(drop=True)

    all_rows.clear()
    for index, row in enumerate(loaded_data.itertuples(index=False), start=1):
        all_rows.append([index] + list(row))

    show_page(0)
    write_check_result(
        f"Cleaning done.\n\nRows before:  {before}\nRows after:   {after}\n"
        f"Rows removed: {removed}\n\nGrid updated — showing clean data."
    )

def save_clean_data():
    if loaded_data is None:
        messagebox.showwarning("No data", "Please run the pipeline first.")
        return
    path = filedialog.asksaveasfilename(
        title="Save clean data",
        defaultextension=".csv",
        initialfile="trades_clean.csv",
        initialdir=r"C:\Users\anani\my git delete\trade-bot",
        filetypes=[("CSV files", "*.csv")]
    )
    if path:
        loaded_data.to_csv(path, index=False)
        messagebox.showinfo("Saved", f"Clean data saved to:\n{path}\n\nAnalysis panels will read from this file.")

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

# ─────────────────────────────────────────────────────────────────────────────
# PANEL 4 — PERFORMANCE
# ─────────────────────────────────────────────────────────────────────────────
panel4_frame = tk.Frame(content, bg="#f0f2f5")

tk.Label(panel4_frame, text="Performance", bg="#f0f2f5", fg="#1a1a2a",
         font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(24, 2))
tk.Label(panel4_frame, text="Equity curve and profit breakdown by time period. "
         "Click bars to drill down: Year → Month → Day.",
         bg="#f0f2f5", fg="#666666", font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(0, 16))

# ── Equity Curve card ──
eq_card = tk.Frame(panel4_frame, bg="white", bd=1, relief="solid")
eq_card.pack(fill="x", padx=20, pady=(0, 10))
tk.Label(eq_card, text="Equity Curve", bg="white", fg="#1a1a2a",
         font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
tk.Label(eq_card,
         text="Shows how your account balance grew or shrank trade by trade. "
              "The line starts at your initial deposit. "
              "Green shading = above starting balance. Red shading = below starting balance. "
              "Dashed line = your initial deposit.",
         bg="white", fg="#888888", font=("Segoe UI", 9),
         wraplength=600, justify="left").pack(anchor="w", padx=16, pady=(0, 8))

eq_fig = Figure(figsize=(7, 2.8), dpi=90)
eq_ax  = eq_fig.add_subplot(111)
eq_fig.patch.set_facecolor("white")
eq_canvas_widget = FigureCanvasTkAgg(eq_fig, master=eq_card)
eq_canvas_widget.get_tk_widget().pack(fill="x", padx=16, pady=(0, 14))

# ── Drilldown card ──
dd_card = tk.Frame(panel4_frame, bg="white", bd=1, relief="solid")
dd_card.pack(fill="x", padx=20, pady=(0, 20))

dd_header = tk.Frame(dd_card, bg="white")
dd_header.pack(fill="x", padx=16, pady=(14, 0))
tk.Label(dd_header, text="Profit Breakdown", bg="white", fg="#1a1a2a",
         font=("Segoe UI", 11, "bold")).pack(side="left")
dd_breadcrumb = tk.Label(dd_header, text="Year", bg="white", fg="#888888",
                          font=("Segoe UI", 10))
dd_breadcrumb.pack(side="left", padx=(12, 0))
dd_back_btn = tk.Button(dd_header, text="← Back", font=("Segoe UI", 10),
                         bd=1, relief="solid", padx=10, pady=3,
                         activebackground="white", activeforeground="black")
# back button packed/forgotten dynamically

tk.Label(dd_card,
         text="Total profit per time period. Green = profit, red = loss. "
              "Click any bar to drill down: Year → Month → Day. "
              "Hover over a bar to see the value in USD and the return % for that period. "
              "The % is calculated as profit ÷ account balance at the start of that period — "
              "the same way prop firms and fund managers report it, not relative to the initial deposit.",
         bg="white", fg="#888888", font=("Segoe UI", 9),
         wraplength=600, justify="left").pack(anchor="w", padx=16, pady=(6, 0))

dd_fig = Figure(figsize=(7, 2.8), dpi=90)
dd_ax  = dd_fig.add_subplot(111)
dd_fig.patch.set_facecolor("white")
dd_canvas_widget = FigureCanvasTkAgg(dd_fig, master=dd_card)
dd_canvas_widget.get_tk_widget().pack(fill="x", padx=16, pady=(8, 14))

drilldown_state  = {"level": "year", "year": None, "month": None}
dd_annot_holder  = [None]   # holds the current tooltip annotation (recreated on each redraw)
dd_bar_data      = []        # list of (profit, start_balance) per bar, populated on each redraw

MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun",
               "Jul","Aug","Sep","Oct","Nov","Dec"]

def get_scaled_df():
    """Return loaded_data with profit_scaled and open_dt columns added. None if no data."""
    if loaded_data is None:
        return None
    df = loaded_data.copy()
    scale = {"Standard": 1.0, "Cent": 0.01, "Micro": 0.1}.get(account_type.get(), 1.0)
    if "Profit" in df.columns:
        df["profit_scaled"] = pd.to_numeric(df["Profit"], errors="coerce").fillna(0) * scale
    else:
        df["profit_scaled"] = 0.0
    col0 = df.columns[0]
    df["open_dt"] = pd.to_datetime(df[col0], format="%d/%m/%Y %H:%M", errors="coerce")
    return df

def build_equity_chart():
    df = get_scaled_df()
    eq_ax.clear()
    eq_ax.set_facecolor("#fafafa")
    if df is None:
        eq_ax.text(0.5, 0.5, "No data — run the pipeline first",
                   ha="center", va="center", transform=eq_ax.transAxes, color="#aaaaaa")
    else:
        try:
            balance = float(starting_balance.get())
        except ValueError:
            balance = 0.0
        cumulative = df["profit_scaled"].cumsum() + balance
        x          = list(range(len(cumulative)))
        eq_ax.plot(x, cumulative, color="#e94560", linewidth=1.4)
        eq_ax.fill_between(x, balance, cumulative,
                           where=(cumulative >= balance), alpha=0.08, color="#27ae60")
        eq_ax.fill_between(x, balance, cumulative,
                           where=(cumulative < balance),  alpha=0.08, color="#e94560")
        eq_ax.axhline(balance, color="#cccccc", linewidth=0.8, linestyle="--")
        eq_ax.set_ylabel("Balance (USD)", fontsize=9)
        eq_ax.set_title("Equity Curve", fontsize=10)
        eq_ax.grid(True, alpha=0.25)
        # label ticks with trade number + date
        n       = len(df)
        n_ticks = min(8, n)
        indices = [int(i * (n - 1) / (n_ticks - 1)) for i in range(n_ticks)] if n_ticks > 1 else [0]
        eq_ax.set_xticks(indices)
        eq_ax.set_xticklabels(
            [f"#{i}\n{df['open_dt'].iloc[i].strftime('%b %Y') if pd.notna(df['open_dt'].iloc[i]) else ''}"
             for i in indices],
            fontsize=7.5, ha="center"
        )
        eq_ax.tick_params(axis="y", labelsize=8)
    eq_fig.tight_layout(pad=1.2)
    eq_canvas_widget.draw()

def build_drilldown_chart():
    global dd_bar_data
    df = get_scaled_df()
    dd_ax.clear()
    dd_ax.set_facecolor("#fafafa")
    dd_bar_data = []

    if df is None:
        dd_ax.text(0.5, 0.5, "No data — run the pipeline first",
                   ha="center", va="center", transform=dd_ax.transAxes, color="#aaaaaa")
        dd_canvas_widget.draw()
        return

    try:
        deposit = float(starting_balance.get())
    except ValueError:
        deposit = 0.0

    level   = drilldown_state["level"]
    df_all  = df.dropna(subset=["open_dt"])   # used for cumulative balance lookups

    if level == "year":
        grouped = df_all.groupby(df_all["open_dt"].dt.year)["profit_scaled"].sum()
        labels  = [str(y) for y in grouped.index]
        values  = grouped.values
        title   = "Profit by Year — click a bar to see months"
        dd_breadcrumb.configure(text="Year")
        dd_back_btn.pack_forget()
        # start balance for each year = deposit + sum of all previous years
        running = deposit
        for v in values:
            dd_bar_data.append((v, running))
            running += v

    elif level == "month":
        year    = drilldown_state["year"]
        mask    = df["open_dt"].dt.year == year
        sub     = df[mask].dropna(subset=["open_dt"])
        grouped = sub.groupby(sub["open_dt"].dt.month)["profit_scaled"].sum()
        labels  = [MONTH_NAMES[m - 1] for m in grouped.index]
        values  = grouped.values
        title   = f"Profit by Month — {year} — click a bar to see days"
        dd_breadcrumb.configure(text=f"Year  ›  {year}")
        dd_back_btn.pack(side="right")
        # start balance for first month = deposit + all profits before this year
        all_years = df_all.groupby(df_all["open_dt"].dt.year)["profit_scaled"].sum()
        running   = deposit + sum(v for y, v in all_years.items() if y < year)
        for v in values:
            dd_bar_data.append((v, running))
            running += v

    elif level == "day":
        year    = drilldown_state["year"]
        month   = drilldown_state["month"]
        mask    = (df["open_dt"].dt.year == year) & (df["open_dt"].dt.month == month)
        sub     = df[mask].dropna(subset=["open_dt"])
        grouped = sub.groupby(sub["open_dt"].dt.day)["profit_scaled"].sum()
        labels  = [str(d) for d in grouped.index]
        values  = grouped.values
        title   = f"Profit by Day — {MONTH_NAMES[month - 1]} {year}"
        dd_breadcrumb.configure(text=f"Year  ›  {year}  ›  {MONTH_NAMES[month - 1]}")
        dd_back_btn.pack(side="right")
        # start balance for first day = deposit + all profits before this month
        all_years = df_all.groupby(df_all["open_dt"].dt.year)["profit_scaled"].sum()
        running   = deposit + sum(v for y, v in all_years.items() if y < year)
        yr_mask   = df_all["open_dt"].dt.year == year
        all_months = df_all[yr_mask].groupby(df_all[yr_mask]["open_dt"].dt.month)["profit_scaled"].sum()
        running  += sum(v for m, v in all_months.items() if m < month)
        for v in values:
            dd_bar_data.append((v, running))
            running += v

    colors = ["#27ae60" if v >= 0 else "#e94560" for v in values]
    dd_ax.bar(labels, values, color=colors, zorder=2)
    dd_ax.axhline(0, color="#cccccc", linewidth=0.8)
    dd_ax.set_ylabel("Profit (USD)", fontsize=9)
    dd_ax.set_title(title, fontsize=10)
    dd_ax.grid(axis="y", alpha=0.25, zorder=0)
    dd_ax.tick_params(labelsize=8)

    # recreate annotation — ax.clear() destroys the previous one
    annot = dd_ax.annotate(
        "", xy=(0, 0), xytext=(10, 12), textcoords="offset points",
        bbox=dict(boxstyle="round,pad=0.45", fc="white", ec="#bbbbbb", lw=0.8, alpha=0.95),
        fontsize=9, zorder=10
    )
    annot.set_visible(False)
    dd_annot_holder[0] = annot

    dd_fig.tight_layout(pad=1.2)
    dd_canvas_widget.draw()

# drilldown click — detect which bar was clicked and go one level deeper
_dd_bar_groups = []   # list of x-axis group keys at current level (for click lookup)

def _rebuild_drilldown_keys():
    """Return ordered list of keys (year/month/day) for the current drilldown level."""
    df = get_scaled_df()
    if df is None:
        return []
    level = drilldown_state["level"]
    if level == "year":
        grouped = df.dropna(subset=["open_dt"]).groupby(
            df["open_dt"].dt.year)["profit_scaled"].sum()
        return list(grouped.index)
    elif level == "month":
        year = drilldown_state["year"]
        mask = df["open_dt"].dt.year == year
        sub  = df[mask].dropna(subset=["open_dt"])
        grouped = sub.groupby(sub["open_dt"].dt.month)["profit_scaled"].sum()
        return list(grouped.index)
    elif level == "day":
        return []   # no further drill-in at day level
    return []

def on_dd_click(event):
    if event.inaxes != dd_ax:
        return
    level = drilldown_state["level"]
    if level == "day":
        return   # nothing deeper
    keys = _rebuild_drilldown_keys()
    # find which bar was clicked by comparing x-coordinate to bar positions
    for i, bar in enumerate(dd_ax.patches):
        if bar.contains(event)[0] and i < len(keys):
            if level == "year":
                drilldown_state["level"] = "month"
                drilldown_state["year"]  = keys[i]
            elif level == "month":
                drilldown_state["level"] = "day"
                drilldown_state["month"] = keys[i]
            build_drilldown_chart()
            return

def on_dd_back():
    level = drilldown_state["level"]
    if level == "day":
        drilldown_state["level"] = "month"
        drilldown_state["month"] = None
    elif level == "month":
        drilldown_state["level"] = "year"
        drilldown_state["year"]  = None
        drilldown_state["month"] = None
    build_drilldown_chart()

dd_back_btn.configure(command=on_dd_back)
dd_fig.canvas.mpl_connect("button_press_event", on_dd_click)

def on_dd_hover(event):
    annot = dd_annot_holder[0]
    if annot is None:
        return
    if event.inaxes != dd_ax:
        if annot.get_visible():
            annot.set_visible(False)
            dd_canvas_widget.draw_idle()
        return
    for i, bar in enumerate(dd_ax.patches):
        if bar.contains(event)[0]:
            val = bar.get_height()
            if i < len(dd_bar_data):
                _, start_bal = dd_bar_data[i]
                pct  = (val / start_bal * 100) if start_bal != 0 else 0
            else:
                pct = 0.0
            sign = "+" if val >= 0 else ""
            annot.xy = (bar.get_x() + bar.get_width() / 2, val)
            annot.set_text(f"{sign}{val:.2f} USD\n{sign}{pct:.2f}% of period start")
            annot.set_visible(True)
            dd_canvas_widget.draw_idle()
            return
    if annot.get_visible():
        annot.set_visible(False)
        dd_canvas_widget.draw_idle()

dd_fig.canvas.mpl_connect("motion_notify_event", on_dd_hover)

def refresh_panel4():
    drilldown_state["level"] = "year"
    drilldown_state["year"]  = None
    drilldown_state["month"] = None
    build_equity_chart()
    build_drilldown_chart()

# ─────────────────────────────────────────────────────────────────────────────
# PANEL 5 — TRADE STATISTICS
# ─────────────────────────────────────────────────────────────────────────────
panel5_frame = tk.Frame(content, bg="#f0f2f5")

tk.Label(panel5_frame, text="Trade Statistics", bg="#f0f2f5", fg="#1a1a2a",
         font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(24, 2))
tk.Label(panel5_frame, text="Key performance metrics across all trades.",
         bg="#f0f2f5", fg="#666666", font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(0, 16))

# ── Summary metrics card ──────────────────────────────────────────────────────
stats_card = tk.Frame(panel5_frame, bg="white", bd=1, relief="solid")
stats_card.pack(fill="x", padx=20, pady=(0, 10))
tk.Label(stats_card, text="Summary", bg="white", fg="#1a1a2a",
         font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 10))

stats_grid = tk.Frame(stats_card, bg="white")
stats_grid.pack(fill="x", padx=16, pady=(0, 16))

# we'll populate these labels when data is available
_stat_labels = {}

def _stat_cell(parent, row, col, title, value_text, title_color="#888888", value_color="#1a1a2a"):
    cell = tk.Frame(parent, bg="white")
    cell.grid(row=row, column=col, padx=16, pady=6, sticky="w")
    tk.Label(cell, text=title, bg="white", fg=title_color,
             font=("Segoe UI", 8)).pack(anchor="w")
    lbl = tk.Label(cell, text=value_text, bg="white", fg=value_color,
                   font=("Segoe UI", 13, "bold"))
    lbl.pack(anchor="w")
    return lbl

STAT_DEFS = [
    ("total_trades",   "Total Trades",    0, 0),
    ("winners",        "Winners",         0, 1),
    ("losers",         "Losers",          0, 2),
    ("breakeven",      "Break-even",      0, 3),
    ("win_rate",       "Win Rate",        1, 0),
    ("profit_factor",  "Profit Factor",   1, 1),
    ("avg_win",        "Avg Win",         1, 2),
    ("avg_loss",       "Avg Loss",        1, 3),
    ("largest_win",    "Largest Win",     2, 0),
    ("largest_loss",   "Largest Loss",    2, 1),
    ("net_profit",     "Net Profit",      2, 2),
    ("net_pct",        "Net Return",      2, 3),
]

for key, title, row, col in STAT_DEFS:
    _stat_labels[key] = _stat_cell(stats_grid, row, col, title, "—")

# ── Charts card ───────────────────────────────────────────────────────────────
charts_card = tk.Frame(panel5_frame, bg="white", bd=1, relief="solid")
charts_card.pack(fill="x", padx=20, pady=(0, 10))
tk.Label(charts_card, text="Win / Loss Breakdown", bg="white", fg="#1a1a2a",
         font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
tk.Label(charts_card,
         text="Left: share of winning, losing and break-even trades by count. "
              "Right: average profit of a winning trade vs average loss of a losing trade (in USD).",
         bg="white", fg="#888888", font=("Segoe UI", 9),
         wraplength=600, justify="left").pack(anchor="w", padx=16, pady=(0, 8))

s_fig1 = Figure(figsize=(7, 3), dpi=90)
s_fig1.patch.set_facecolor("white")
s_ax_pie = s_fig1.add_subplot(121)
s_ax_bar = s_fig1.add_subplot(122)
s_canvas1 = FigureCanvasTkAgg(s_fig1, master=charts_card)
s_canvas1.get_tk_widget().pack(fill="x", padx=16, pady=(0, 14))

# ── Distribution card ─────────────────────────────────────────────────────────
dist_card = tk.Frame(panel5_frame, bg="white", bd=1, relief="solid")
dist_card.pack(fill="x", padx=20, pady=(0, 20))
tk.Label(dist_card, text="Profit Distribution", bg="white", fg="#1a1a2a",
         font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
tk.Label(dist_card,
         text="How often each profit/loss range occurs. "
              "A well-distributed histogram with a rightward skew is a healthy sign. "
              "Bars to the right of zero are winning trades, bars to the left are losing trades.",
         bg="white", fg="#888888", font=("Segoe UI", 9),
         wraplength=600, justify="left").pack(anchor="w", padx=16, pady=(0, 8))

s_fig2 = Figure(figsize=(7, 2.8), dpi=90)
s_fig2.patch.set_facecolor("white")
s_ax_hist = s_fig2.add_subplot(111)
s_canvas2 = FigureCanvasTkAgg(s_fig2, master=dist_card)
s_canvas2.get_tk_widget().pack(fill="x", padx=16, pady=(0, 14))

def build_stats_charts():
    df = get_scaled_df()

    # clear charts
    s_ax_pie.clear()
    s_ax_bar.clear()
    s_ax_hist.clear()
    for ax in (s_ax_pie, s_ax_bar, s_ax_hist):
        ax.set_facecolor("#fafafa")

    if df is None or "profit_scaled" not in df.columns:
        for ax in (s_ax_pie, s_ax_bar, s_ax_hist):
            ax.text(0.5, 0.5, "No data — run the pipeline first",
                    ha="center", va="center", transform=ax.transAxes, color="#aaaaaa")
        s_canvas1.draw()
        s_canvas2.draw()
        for lbl in _stat_labels.values():
            lbl.configure(text="—", fg="#1a1a2a")
        return

    profits = df["profit_scaled"].dropna()

    winners   = profits[profits > 0]
    losers    = profits[profits < 0]
    breakeven = profits[profits == 0]

    n_total = len(profits)
    n_win   = len(winners)
    n_loss  = len(losers)
    n_be    = len(breakeven)
    win_rate    = (n_win / n_total * 100) if n_total > 0 else 0
    avg_win     = winners.mean()  if n_win  > 0 else 0
    avg_loss    = losers.mean()   if n_loss > 0 else 0
    largest_win = winners.max()   if n_win  > 0 else 0
    largest_loss= losers.min()    if n_loss > 0 else 0
    gross_profit= winners.sum()   if n_win  > 0 else 0
    gross_loss  = abs(losers.sum()) if n_loss > 0 else 0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    net_profit  = profits.sum()

    try:
        deposit = float(starting_balance.get())
    except ValueError:
        deposit = 0.0
    net_pct = (net_profit / deposit * 100) if deposit != 0 else 0

    # update summary labels
    pf_text = f"{profit_factor:.2f}" if profit_factor != float("inf") else "∞"
    net_color = "#27ae60" if net_profit >= 0 else "#e94560"
    _stat_labels["total_trades"].configure(text=str(n_total))
    _stat_labels["winners"].configure(text=str(n_win),   fg="#27ae60")
    _stat_labels["losers"].configure(text=str(n_loss),   fg="#e94560")
    _stat_labels["breakeven"].configure(text=str(n_be))
    _stat_labels["win_rate"].configure(text=f"{win_rate:.1f}%")
    _stat_labels["profit_factor"].configure(text=pf_text)
    _stat_labels["avg_win"].configure(text=f"+{avg_win:.2f}",  fg="#27ae60")
    _stat_labels["avg_loss"].configure(text=f"{avg_loss:.2f}", fg="#e94560")
    _stat_labels["largest_win"].configure(text=f"+{largest_win:.2f}",  fg="#27ae60")
    _stat_labels["largest_loss"].configure(text=f"{largest_loss:.2f}", fg="#e94560")
    _stat_labels["net_profit"].configure(text=f"{net_profit:+.2f}", fg=net_color)
    _stat_labels["net_pct"].configure(text=f"{net_pct:+.2f}%",      fg=net_color)

    # pie chart
    sizes  = [n_win, n_loss, n_be]
    colors = ["#27ae60", "#e94560", "#aaaaaa"]
    labels = ["Winners", "Losers", "Break-even"]
    non_zero = [(s, c, l) for s, c, l in zip(sizes, colors, labels) if s > 0]
    if non_zero:
        sz, co, la = zip(*non_zero)
        s_ax_pie.pie(sz, labels=la, colors=co, autopct="%1.1f%%",
                     textprops={"fontsize": 8}, startangle=90)
    s_ax_pie.set_title("Trade Outcome", fontsize=9)

    # avg win vs avg loss bar
    s_ax_bar.bar(["Avg Win"], [avg_win],  color="#27ae60", zorder=2)
    s_ax_bar.bar(["Avg Loss"], [avg_loss], color="#e94560", zorder=2)
    s_ax_bar.axhline(0, color="#cccccc", linewidth=0.8)
    s_ax_bar.set_title("Avg Win vs Avg Loss (USD)", fontsize=9)
    s_ax_bar.set_ylabel("USD", fontsize=8)
    s_ax_bar.grid(axis="y", alpha=0.25, zorder=0)
    s_ax_bar.tick_params(labelsize=8)

    s_fig1.tight_layout(pad=1.2)
    s_canvas1.draw()

    # profit distribution histogram
    bin_colors = ["#27ae60" if v >= 0 else "#e94560" for v in profits]
    n_bins = min(40, max(10, n_total // 20))
    counts, edges, patches = s_ax_hist.hist(profits, bins=n_bins, edgecolor="white", linewidth=0.4)
    for patch, left_edge in zip(patches, edges[:-1]):
        patch.set_facecolor("#27ae60" if left_edge >= 0 else "#e94560")
    s_ax_hist.axvline(0, color="#888888", linewidth=0.9, linestyle="--")
    s_ax_hist.set_xlabel("Profit per trade (USD)", fontsize=9)
    s_ax_hist.set_ylabel("Number of trades", fontsize=9)
    s_ax_hist.set_title("Profit Distribution", fontsize=10)
    s_ax_hist.grid(axis="y", alpha=0.25)
    s_ax_hist.tick_params(labelsize=8)

    s_fig2.tight_layout(pad=1.2)
    s_canvas2.draw()

def refresh_panel5():
    build_stats_charts()

# ─────────────────────────────────────────────────────────────────────────────
# PLACEHOLDER PANELS 6-8  (filled in later steps)
# ─────────────────────────────────────────────────────────────────────────────
def _placeholder_panel(title, subtitle):
    frame = tk.Frame(content, bg="#f0f2f5")
    tk.Label(frame, text=title, bg="#f0f2f5", fg="#1a1a2a",
             font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(24, 2))
    tk.Label(frame, text=subtitle, bg="#f0f2f5", fg="#666666",
             font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(0, 16))
    card = tk.Frame(frame, bg="white", bd=1, relief="solid")
    card.pack(fill="x", padx=20, pady=(0, 10))
    tk.Label(card, text="Coming soon — this panel will be built in the next step.",
             bg="white", fg="#aaaaaa", font=("Segoe UI", 10), pady=40).pack()
    return frame
panel6_frame = _placeholder_panel(
    "Risk & Red Flags",
    "Lot size over time, consecutive wins/losses, duration distribution.")
panel7_frame = _placeholder_panel(
    "Prop Compliance",
    "Configurable compliance rules and pass/fail scorecard.")
panel8_frame = _placeholder_panel(
    "Cost & Spread",
    "Commission check and break-even spread analysis.")

# ─────────────────────────────────────────────────────────────────────────────
# REGISTER PANELS AND START
# ─────────────────────────────────────────────────────────────────────────────
all_panels["pipeline"] = pipeline_panel
all_panels["panel4"]   = panel4_frame
all_panels["panel5"]   = panel5_frame
all_panels["panel6"]   = panel6_frame
all_panels["panel7"]   = panel7_frame
all_panels["panel8"]   = panel8_frame

show_panel("pipeline")

window.mainloop()
