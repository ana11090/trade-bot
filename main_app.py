# ==============================================================================
# main_app.py
# ------------------------------------------------------------------------------
# PROJECT:  Trading Strategy Reverse Engineer
# PURPOSE:  This is the main window of the application.
#           It provides a graphical user interface (GUI) so that anyone can run
#           the pipeline without typing commands in a terminal.
#
# INPUT:    Nothing — just run the file and a window opens on screen.
# OUTPUT:   Launches the GUI; each project panel manages its own processing.
#
# HOW TO RUN:
#   python main_app.py
#   A window will appear. Use the sidebar to navigate between projects.
#
# PROJECTS:
#   Project 0 — Data Pipeline       (active — use this first)
#   Project 1 — Reverse Engineer    (coming soon)
#   Project 2 — Backtesting         (coming soon)
#   Project 3 — Forward Bot         (coming soon)
# ==============================================================================

import tkinter as tk                      # tkinter is the built-in Python GUI library
from tkinter import ttk                   # ttk provides newer-looking widgets (progress bar, scrollbar)
from tkinter import filedialog            # filedialog opens the Windows file browser popup
from tkinter import messagebox            # messagebox shows popup alerts and warnings
from tkinter import scrolledtext          # scrolledtext is a text box with a built-in scrollbar
import threading                          # threading lets us run the pipeline without freezing the window
import sys                                # sys lets us redirect print() output to the log area
import os                                 # os is used for file paths and opening files

# add the folder that contains this file to the Python search path
# this allows "from project0_data_pipeline import load_trades" to work correctly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ==============================================================================
# COLOUR AND FONT CONSTANTS
# Define all colours and fonts in one place so they are easy to change later
# ==============================================================================

COLOUR_SIDEBAR_BG      = "#16213e"    # dark navy — background of the left sidebar
COLOUR_SIDEBAR_HEADER  = "#0f3460"    # darker blue — header area at the top of the sidebar
COLOUR_SIDEBAR_ACTIVE  = "#e94560"    # red/pink — highlights the currently selected project
COLOUR_SIDEBAR_TEXT    = "#a8b2d8"    # soft blue-white — text in the sidebar
COLOUR_SIDEBAR_HOVER   = "#1a2a5e"    # slightly lighter than sidebar — hover highlight

COLOUR_CONTENT_BG      = "#f0f2f5"    # very light grey — background of the main content area
COLOUR_CARD_BG         = "#ffffff"    # white — background of summary cards and panels
COLOUR_HEADER_BG       = "#e8ecf3"    # pale blue-grey — top header strip of each project panel
COLOUR_ACCENT          = "#0066cc"    # blue — primary button colour
COLOUR_ACCENT_DARK     = "#004fa3"    # darker blue — button hover / pressed
COLOUR_SUCCESS         = "#2e7d32"    # dark green — run button
COLOUR_SUCCESS_DARK    = "#1b5e20"    # darker green — run button hover
COLOUR_TEXT_DARK       = "#1a1a2a"    # near-black — main body text
COLOUR_TEXT_MID        = "#555577"    # medium grey-purple — secondary text
COLOUR_TEXT_LIGHT      = "#ffffff"    # white — text on dark backgrounds
COLOUR_BORDER          = "#d0d4de"    # light grey — borders around cards

FONT_SIDEBAR_TITLE     = ("Segoe UI", 13, "bold")    # app name in the sidebar
FONT_SIDEBAR_ITEM      = ("Segoe UI", 11)            # project nav items in the sidebar
FONT_PAGE_TITLE        = ("Segoe UI", 22, "bold")    # big title at top of each project panel
FONT_SECTION_HEADING   = ("Segoe UI", 12, "bold")    # step headings inside a project panel
FONT_BODY              = ("Segoe UI", 11)            # normal body text
FONT_SMALL             = ("Segoe UI", 9)             # small helper text
FONT_BUTTON            = ("Segoe UI", 11, "bold")    # text on buttons
FONT_BUTTON_BIG        = ("Segoe UI", 13, "bold")    # text on the big Run button
FONT_LOG               = ("Consolas", 10)            # monospace font for the log output area
FONT_CARD_VALUE        = ("Segoe UI", 18, "bold")    # large number in a summary card
FONT_CARD_LABEL        = ("Segoe UI", 9)             # small label under a summary card number


# ==============================================================================
# LogRedirector
# A helper class that captures print() output and sends it to the GUI log area
# ==============================================================================

class LogRedirector:
    """
    This class replaces sys.stdout temporarily while the pipeline runs.
    Any print() call goes into the scrolled text widget in the GUI instead
    of the terminal, so the user can see progress inside the app.
    """

    def __init__(self, text_widget):
        self.text_widget = text_widget   # the scrolled text box we will write into

    def write(self, output_text):
        # this method is called every time something is printed
        self.text_widget.configure(state="normal")         # allow writing (widget is normally read-only)
        self.text_widget.insert(tk.END, output_text)       # add the text at the bottom of the log
        self.text_widget.see(tk.END)                       # scroll down so the latest line is always visible
        self.text_widget.configure(state="disabled")       # lock the widget again so the user cannot edit it
        self.text_widget.update_idletasks()                # force the GUI to refresh immediately

    def flush(self):
        pass   # required by Python's stdout interface — we do not need to do anything here


# ==============================================================================
# TradingBotApp
# The main application class — builds and manages the entire GUI window
# ==============================================================================

class TradingBotApp:
    """
    This class creates and manages the whole application window.
    It is split into methods, one for each part of the interface:
      - _build_window()    — configures the window size and position
      - _build_layout()    — creates the sidebar and content area frames
      - _build_sidebar()   — populates the sidebar with navigation
      - show_project_0()   — builds the Project 0 panel inside the content area
    """

    def __init__(self, root_window):
        self.root_window       = root_window        # the main tkinter Tk() window
        self.selected_file     = tk.StringVar()     # holds the path of the file the user picks
        self.pipeline_running  = False              # True while the pipeline is processing
        self.summary_frame_ref = None               # reference to the summary area frame (updated after run)

        self._build_window()    # set up the window size, title, position
        self._build_layout()    # create sidebar + content area frames
        self._show_project(0)   # show Project 0 panel by default when the app opens

    # --------------------------------------------------------------------------

    def _build_window(self):
        """Configure the main window: title, size, minimum size, and screen position."""

        self.root_window.title("Trading Strategy Reverse Engineer")   # text shown in the Windows taskbar
        self.root_window.configure(bg=COLOUR_SIDEBAR_BG)              # fallback background colour

        window_width  = 1080    # width of the window in pixels
        window_height = 700     # height of the window in pixels

        # calculate the x and y coordinates that will centre the window on screen
        self.root_window.update_idletasks()                                         # force window to measure itself
        screen_width  = self.root_window.winfo_screenwidth()                        # total screen width
        screen_height = self.root_window.winfo_screenheight()                       # total screen height
        x_position    = (screen_width  // 2) - (window_width  // 2)                # horizontal centre
        y_position    = (screen_height // 2) - (window_height // 2) - 30           # slightly above centre

        # apply size and position in one call
        self.root_window.geometry(f"{window_width}x{window_height}+{x_position}+{y_position}")
        self.root_window.minsize(820, 560)    # prevent the window from being shrunk too small

    # --------------------------------------------------------------------------

    def _build_layout(self):
        """Create the two main panels: the sidebar on the left and the content area on the right."""

        # outer wrapper that fills the entire window
        outer_frame = tk.Frame(self.root_window, bg=COLOUR_SIDEBAR_BG)
        outer_frame.pack(fill="both", expand=True)   # stretch to fill the whole window

        # left sidebar — fixed width, dark background
        self.sidebar_frame = tk.Frame(outer_frame, bg=COLOUR_SIDEBAR_BG, width=220)
        self.sidebar_frame.pack(side="left", fill="y")     # stick to the left edge, fill top to bottom
        self.sidebar_frame.pack_propagate(False)           # prevent the sidebar shrinking to fit its children

        # right content area — fills all remaining space
        self.content_frame = tk.Frame(outer_frame, bg=COLOUR_CONTENT_BG)
        self.content_frame.pack(side="right", fill="both", expand=True)

        self._build_sidebar()   # populate the sidebar with the app title and project buttons

    # --------------------------------------------------------------------------

    def _build_sidebar(self):
        """Populate the sidebar: app logo/title at the top, project nav buttons below."""

        # --- App title block ---
        title_block = tk.Frame(self.sidebar_frame, bg=COLOUR_SIDEBAR_HEADER, pady=0)
        title_block.pack(fill="x")   # span the full width of the sidebar

        tk.Label(
            title_block,
            text="Trade Bot",
            font=("Segoe UI", 15, "bold"),
            fg=COLOUR_TEXT_LIGHT,
            bg=COLOUR_SIDEBAR_HEADER,
            pady=18
        ).pack()   # centred by default

        tk.Label(
            title_block,
            text="Reverse Engineer",
            font=("Segoe UI", 10),
            fg="#8899cc",
            bg=COLOUR_SIDEBAR_HEADER,
            pady=0
        ).pack()

        tk.Label(
            title_block,
            text="v1.0",
            font=FONT_SMALL,
            fg="#556688",
            bg=COLOUR_SIDEBAR_HEADER,
            pady=8
        ).pack()

        # thin divider line between title and navigation
        tk.Frame(self.sidebar_frame, bg="#2a3a6a", height=1).pack(fill="x")

        # --- "PROJECTS" section label ---
        tk.Label(
            self.sidebar_frame,
            text="  PROJECTS",
            font=("Segoe UI", 8, "bold"),
            fg="#556688",
            bg=COLOUR_SIDEBAR_BG,
            anchor="w",
            pady=14
        ).pack(fill="x")

        # --- Navigation items ---
        # each entry is: (project number, display name, is_clickable)
        nav_items = [
            (0, "Data Pipeline",      True),
            (1, "Reverse Engineer",   False),
            (2, "Backtesting",        False),
            (3, "Forward Bot",        False),
        ]

        self.nav_label_refs = {}   # store label references so we can change active styles later

        for project_number, project_name, is_active in nav_items:

            # each nav item is a frame (so we can catch hover and click events on the whole row)
            item_frame = tk.Frame(self.sidebar_frame, bg=COLOUR_SIDEBAR_BG, cursor="hand2")
            item_frame.pack(fill="x")

            # colour the label differently depending on whether the project is active
            initial_text_colour = COLOUR_TEXT_LIGHT if is_active else "#445577"

            item_label = tk.Label(
                item_frame,
                text=f"  {project_number}  {project_name}",
                font=FONT_SIDEBAR_ITEM,
                fg=initial_text_colour,
                bg=COLOUR_SIDEBAR_BG,
                anchor="w",
                padx=10,
                pady=12
            )
            item_label.pack(fill="x")

            # keep a reference to this label so we can update its style when selected
            self.nav_label_refs[project_number] = (item_frame, item_label)

            # bind click and hover events
            if is_active:
                # clicking an active project switches to its panel
                item_frame.bind("<Button-1>", lambda e, n=project_number: self._show_project(n))
                item_label.bind("<Button-1>", lambda e, n=project_number: self._show_project(n))
            else:
                # clicking an inactive project shows a "coming soon" popup
                item_frame.bind("<Button-1>", lambda e, name=project_name: self._show_coming_soon(name))
                item_label.bind("<Button-1>", lambda e, name=project_name: self._show_coming_soon(name))

            # hover highlight for inactive items (active item has a permanent highlight)
            if not is_active:
                item_frame.bind("<Enter>", lambda e, f=item_frame, l=item_label: [
                    f.configure(bg=COLOUR_SIDEBAR_HOVER),
                    l.configure(bg=COLOUR_SIDEBAR_HOVER)
                ])
                item_frame.bind("<Leave>", lambda e, f=item_frame, l=item_label: [
                    f.configure(bg=COLOUR_SIDEBAR_BG),
                    l.configure(bg=COLOUR_SIDEBAR_BG)
                ])
                item_label.bind("<Enter>", lambda e, f=item_frame, l=item_label: [
                    f.configure(bg=COLOUR_SIDEBAR_HOVER),
                    l.configure(bg=COLOUR_SIDEBAR_HOVER)
                ])
                item_label.bind("<Leave>", lambda e, f=item_frame, l=item_label: [
                    f.configure(bg=COLOUR_SIDEBAR_BG),
                    l.configure(bg=COLOUR_SIDEBAR_BG)
                ])

        # thin divider above the help text
        tk.Frame(self.sidebar_frame, bg="#2a3a6a", height=1).pack(fill="x", pady=(20, 0))

        # helper text at the bottom of the sidebar
        tk.Label(
            self.sidebar_frame,
            text="Run projects in order.\nEach builds on the last.",
            font=FONT_SMALL,
            fg="#334466",
            bg=COLOUR_SIDEBAR_BG,
            justify="center",
            pady=16
        ).pack()

    # --------------------------------------------------------------------------

    def _show_coming_soon(self, project_name):
        """Show a popup when the user clicks a project that is not yet available."""
        messagebox.showinfo(
            "Coming Soon",
            f"Project '{project_name}' is not yet available.\n\n"
            "Please complete Project 0 first — it produces the\n"
            "trades_clean.csv file that all later projects need."
        )

    # --------------------------------------------------------------------------

    def _clear_content(self):
        """Destroy all widgets in the content area before showing a new project panel."""
        for child_widget in self.content_frame.winfo_children():
            child_widget.destroy()   # remove each widget one by one

    # --------------------------------------------------------------------------

    def _show_project(self, project_number):
        """Switch the content area to show the panel for the given project number."""
        self._clear_content()          # wipe whatever is currently showing

        # highlight the selected nav item in the sidebar
        for number, (frame, label) in self.nav_label_refs.items():
            if number == project_number:
                frame.configure(bg=COLOUR_SIDEBAR_ACTIVE)    # active row background
                label.configure(bg=COLOUR_SIDEBAR_ACTIVE, fg=COLOUR_TEXT_LIGHT)
            else:
                frame.configure(bg=COLOUR_SIDEBAR_BG)
                label.configure(bg=COLOUR_SIDEBAR_BG, fg="#445577" if number != 0 else COLOUR_TEXT_LIGHT)

        # show the correct panel
        if project_number == 0:
            self._build_project0_panel()   # build and display the Project 0 interface

    # ==========================================================================
    # PROJECT 0 PANEL
    # ==========================================================================

    def _build_project0_panel(self):
        """Build the full Project 0 interface inside the content area."""

        # outer frame that fills the content area
        outer = tk.Frame(self.content_frame, bg=COLOUR_CONTENT_BG)
        outer.pack(fill="both", expand=True)

        # ---- Page Header ----
        header_frame = tk.Frame(outer, bg=COLOUR_HEADER_BG, pady=0)
        header_frame.pack(fill="x")

        header_inner = tk.Frame(header_frame, bg=COLOUR_HEADER_BG)
        header_inner.pack(fill="x", padx=30, pady=22)

        # small project badge label
        tk.Label(
            header_inner,
            text="  PROJECT 0  ",
            font=("Segoe UI", 8, "bold"),
            fg=COLOUR_ACCENT,
            bg="#d8e6f8",
            padx=4,
            pady=3
        ).pack(anchor="w")

        tk.Label(
            header_inner,
            text="Data Pipeline",
            font=FONT_PAGE_TITLE,
            fg=COLOUR_TEXT_DARK,
            bg=COLOUR_HEADER_BG
        ).pack(anchor="w", pady=(4, 1))

        tk.Label(
            header_inner,
            text="Load and clean your Myfxbook trade history — the foundation for all other projects",
            font=FONT_BODY,
            fg=COLOUR_TEXT_MID,
            bg=COLOUR_HEADER_BG
        ).pack(anchor="w")

        # thin divider below header
        tk.Frame(outer, bg=COLOUR_BORDER, height=1).pack(fill="x")

        # ---- Scrollable body ----
        # We use a Canvas + scrollbar so the content can scroll if the window is small
        body_canvas    = tk.Canvas(outer, bg=COLOUR_CONTENT_BG, highlightthickness=0)
        body_scrollbar = ttk.Scrollbar(outer, orient="vertical", command=body_canvas.yview)

        # the inner frame sits inside the canvas
        body_inner = tk.Frame(body_canvas, bg=COLOUR_CONTENT_BG)

        # update the scroll region whenever the inner frame changes size
        body_inner.bind(
            "<Configure>",
            lambda e: body_canvas.configure(scrollregion=body_canvas.bbox("all"))
        )

        # place the inner frame at the top-left of the canvas
        body_canvas.create_window((0, 0), window=body_inner, anchor="nw")
        body_canvas.configure(yscrollcommand=body_scrollbar.set)

        body_scrollbar.pack(side="right", fill="y")
        body_canvas.pack(side="left", fill="both", expand=True)

        # enable mouse wheel scrolling on Windows
        body_canvas.bind_all(
            "<MouseWheel>",
            lambda e: body_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        )

        # all content sections go inside body_inner
        self._build_step_1_file_picker(body_inner)   # Step 1: file selection
        self._build_step_2_run_button(body_inner)     # Step 2: run + progress
        self._build_step_3_log_area(body_inner)       # Step 3: live log output
        self._build_step_4_summary_area(body_inner)   # Step 4: results summary cards

    # --------------------------------------------------------------------------

    def _build_section_header(self, parent, step_number, title, description):
        """
        Helper: draws a consistent step header used by each section.
        Shows a numbered badge, a bold title, and a grey description line.
        """
        row = tk.Frame(parent, bg=COLOUR_CONTENT_BG)
        row.pack(fill="x", padx=28, pady=(24, 6))

        # numbered circle badge
        tk.Label(
            row,
            text=str(step_number),
            font=("Segoe UI", 10, "bold"),
            fg=COLOUR_TEXT_LIGHT,
            bg=COLOUR_ACCENT,
            width=2,
            padx=7,
            pady=4
        ).pack(side="left", anchor="n")

        # title and description to the right of the badge
        text_col = tk.Frame(row, bg=COLOUR_CONTENT_BG)
        text_col.pack(side="left", padx=(10, 0))

        tk.Label(
            text_col,
            text=title,
            font=FONT_SECTION_HEADING,
            fg=COLOUR_TEXT_DARK,
            bg=COLOUR_CONTENT_BG,
            anchor="w"
        ).pack(anchor="w")

        tk.Label(
            text_col,
            text=description,
            font=("Segoe UI", 10),
            fg=COLOUR_TEXT_MID,
            bg=COLOUR_CONTENT_BG,
            anchor="w"
        ).pack(anchor="w")

    # --------------------------------------------------------------------------

    def _build_step_1_file_picker(self, parent):
        """Build the file selection section: description, path display, and Browse button."""

        self._build_section_header(
            parent,
            step_number=1,
            title="Select Your Trade File",
            description="Choose the .txt or .csv file exported from your Myfxbook account"
        )

        section = tk.Frame(parent, bg=COLOUR_CONTENT_BG)
        section.pack(fill="x", padx=28, pady=(0, 4))

        # white card that contains the path display and browse button
        card = tk.Frame(section, bg=COLOUR_CARD_BG, bd=1, relief="flat",
                        highlightbackground=COLOUR_BORDER, highlightthickness=1)
        card.pack(fill="x")

        card_inner = tk.Frame(card, bg=COLOUR_CARD_BG)
        card_inner.pack(fill="x", padx=16, pady=14)

        # instruction label above the path display
        tk.Label(
            card_inner,
            text="Selected file:",
            font=FONT_SMALL,
            fg=COLOUR_TEXT_MID,
            bg=COLOUR_CARD_BG,
            anchor="w"
        ).pack(anchor="w")

        # grey box showing the currently selected file path
        path_display_frame = tk.Frame(card_inner, bg="#eef0f5", bd=0,
                                      highlightbackground=COLOUR_BORDER, highlightthickness=1)
        path_display_frame.pack(fill="x", pady=(4, 10))

        self.path_display_label = tk.Label(
            path_display_frame,
            textvariable=self.selected_file,   # automatically shows whatever path is stored in selected_file
            font=("Consolas", 10),
            fg=COLOUR_TEXT_MID,
            bg="#eef0f5",
            anchor="w",
            padx=10,
            pady=8,
            wraplength=680    # wrap long paths onto a second line instead of going off-screen
        )
        self.path_display_label.pack(fill="x")

        # set placeholder text if no file has been chosen yet
        if not self.selected_file.get():
            self.selected_file.set("No file selected — click Browse to choose your file")

        # Browse button — opens the Windows file picker
        browse_btn = tk.Button(
            card_inner,
            text="  Browse for File  ",
            font=FONT_BUTTON,
            fg=COLOUR_TEXT_LIGHT,
            bg=COLOUR_ACCENT,
            activebackground=COLOUR_ACCENT_DARK,
            activeforeground=COLOUR_TEXT_LIGHT,
            bd=0,
            padx=14,
            pady=8,
            cursor="hand2",
            command=self._browse_for_file    # calls the file picker method when clicked
        )
        browse_btn.pack(anchor="w")

    # --------------------------------------------------------------------------

    def _build_step_2_run_button(self, parent):
        """Build the Run section: the big green Run button and a status message label."""

        self._build_section_header(
            parent,
            step_number=2,
            title="Run the Pipeline",
            description="Clean the data, compute the summary, and save trades_clean.csv"
        )

        section = tk.Frame(parent, bg=COLOUR_CONTENT_BG)
        section.pack(fill="x", padx=28, pady=(0, 4))

        card = tk.Frame(section, bg=COLOUR_CARD_BG, bd=1, relief="flat",
                        highlightbackground=COLOUR_BORDER, highlightthickness=1)
        card.pack(fill="x")

        card_inner = tk.Frame(card, bg=COLOUR_CARD_BG)
        card_inner.pack(fill="x", padx=16, pady=14)

        # the big green Run button
        self.run_button = tk.Button(
            card_inner,
            text="  Run Data Pipeline  ",
            font=FONT_BUTTON_BIG,
            fg=COLOUR_TEXT_LIGHT,
            bg=COLOUR_SUCCESS,
            activebackground=COLOUR_SUCCESS_DARK,
            activeforeground=COLOUR_TEXT_LIGHT,
            bd=0,
            padx=22,
            pady=12,
            cursor="hand2",
            command=self._start_pipeline    # calls the pipeline start method when clicked
        )
        self.run_button.pack(anchor="w")

        # progress bar — hidden by default, shown while the pipeline is running
        self.progress_bar = ttk.Progressbar(
            card_inner,
            mode="indeterminate",   # indeterminate = animated spinner, no % completion shown
            length=350
        )
        # not packed here — packed/unpacked dynamically when pipeline starts/finishes

        # status text shown below the button (e.g. "Pipeline running..." or "Done")
        self.status_label = tk.Label(
            card_inner,
            text="Select a file above, then click Run.",
            font=("Segoe UI", 10),
            fg=COLOUR_TEXT_MID,
            bg=COLOUR_CARD_BG
        )
        self.status_label.pack(anchor="w", pady=(8, 0))

    # --------------------------------------------------------------------------

    def _build_step_3_log_area(self, parent):
        """Build the live log output area — a dark terminal-style scrolling text box."""

        self._build_section_header(
            parent,
            step_number=3,
            title="Output Log",
            description="Live progress appears here while the pipeline is running"
        )

        section = tk.Frame(parent, bg=COLOUR_CONTENT_BG)
        section.pack(fill="x", padx=28, pady=(0, 4))

        card = tk.Frame(section, bg=COLOUR_CARD_BG, bd=1, relief="flat",
                        highlightbackground=COLOUR_BORDER, highlightthickness=1)
        card.pack(fill="x")

        # dark terminal-style scrolled text area
        self.log_area = scrolledtext.ScrolledText(
            card,
            font=FONT_LOG,
            bg="#1e1e2e",         # dark navy — looks like a terminal
            fg="#cdd6f4",         # pale blue-white — readable on dark background
            insertbackground="white",
            state="disabled",     # read-only — user cannot type here
            height=14,            # height in lines of text
            bd=0,
            padx=10,
            pady=10,
            wrap="word"           # wrap long lines at word boundaries
        )
        self.log_area.pack(fill="x")

        # button row below the log area
        btn_row = tk.Frame(card, bg=COLOUR_CARD_BG)
        btn_row.pack(fill="x", padx=10, pady=(4, 8))

        # small Clear button to wipe the log
        tk.Button(
            btn_row,
            text="Clear Log",
            font=FONT_SMALL,
            fg=COLOUR_TEXT_MID,
            bg=COLOUR_CARD_BG,
            activebackground=COLOUR_CONTENT_BG,
            bd=0,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self._clear_log    # calls the clear log method when clicked
        ).pack(side="right")

    # --------------------------------------------------------------------------

    def _build_step_4_summary_area(self, parent):
        """Build the summary section — shows results cards after the pipeline finishes."""

        self._build_section_header(
            parent,
            step_number=4,
            title="Summary",
            description="Trade statistics appear here after the pipeline completes"
        )

        # this frame will be filled with cards by _display_summary() after the pipeline runs
        self.summary_frame_ref = tk.Frame(parent, bg=COLOUR_CONTENT_BG)
        self.summary_frame_ref.pack(fill="x", padx=28, pady=(0, 30))

        # placeholder text shown before the pipeline has been run
        self.summary_placeholder_label = tk.Label(
            self.summary_frame_ref,
            text="Run the pipeline first — your summary will appear here.",
            font=FONT_BODY,
            fg="#8899bb",
            bg=COLOUR_CONTENT_BG,
            pady=14
        )
        self.summary_placeholder_label.pack(anchor="w")

    # ==========================================================================
    # ACTIONS — methods that respond to button clicks
    # ==========================================================================

    def _browse_for_file(self):
        """Open the Windows file picker and store the selected path in selected_file."""

        chosen_path = filedialog.askopenfilename(
            title="Select your Myfxbook trade history file",
            filetypes=[
                ("Text files",  "*.txt"),
                ("CSV files",   "*.csv"),
                ("All files",   "*.*")
            ]
        )

        # only update if the user actually selected something (did not cancel)
        if chosen_path:
            self.selected_file.set(chosen_path)               # update the path display label
            self.path_display_label.configure(fg="#222244")   # change text to dark now that a file is set
            self._write_to_log(f"File selected:\n  {chosen_path}\n\n")

    # --------------------------------------------------------------------------

    def _write_to_log(self, message):
        """Write a line of text into the log area."""
        self.log_area.configure(state="normal")       # temporarily allow writing
        self.log_area.insert(tk.END, message)         # add text at the end
        self.log_area.see(tk.END)                     # scroll to the bottom
        self.log_area.configure(state="disabled")     # lock again

    # --------------------------------------------------------------------------

    def _clear_log(self):
        """Remove all text from the log area."""
        self.log_area.configure(state="normal")       # allow editing
        self.log_area.delete("1.0", tk.END)           # delete from line 1 character 0 to the very end
        self.log_area.configure(state="disabled")     # lock again

    # --------------------------------------------------------------------------

    def _start_pipeline(self):
        """
        Validate the selected file and then start the pipeline in a background thread.
        Running in a background thread keeps the GUI responsive during processing.
        """

        current_file_path = self.selected_file.get()   # read the currently displayed file path

        # check that the user has actually selected a file
        if not current_file_path or current_file_path.startswith("No file selected"):
            messagebox.showwarning(
                "No File Selected",
                "Please select your trade history file first.\n\n"
                "Click 'Browse for File' and navigate to your .txt or .csv export."
            )
            return   # stop — nothing to process

        # check the file physically exists on disk
        if not os.path.isfile(current_file_path):
            messagebox.showerror(
                "File Not Found",
                f"The file could not be found:\n\n{current_file_path}\n\n"
                "The file may have been moved or deleted. Please select it again."
            )
            return   # stop — file is gone

        # check the pipeline is not already running
        if self.pipeline_running:
            messagebox.showinfo(
                "Already Running",
                "The pipeline is already running. Please wait for it to finish."
            )
            return   # stop — do not start two pipelines at once

        # mark pipeline as running and update the UI
        self.pipeline_running = True

        self.run_button.configure(
            state="disabled",               # grey out the button so it cannot be clicked twice
            text="  Processing...  ",
            bg="#888888"
        )

        self.progress_bar.pack(anchor="w", pady=(10, 0))   # show the animated progress bar
        self.progress_bar.start(12)                        # start the animation (update every 12ms)

        self.status_label.configure(
            text="Pipeline is running — see the log below for progress...",
            fg=COLOUR_TEXT_MID
        )

        self._clear_log()   # wipe any previous log output before the new run

        # start the processing in a separate background thread so the window stays responsive
        worker_thread = threading.Thread(
            target=self._pipeline_worker,   # the function that does the actual work
            args=(current_file_path,),      # pass the file path to that function
            daemon=True                     # daemon = thread will stop automatically if the app closes
        )
        worker_thread.start()   # launch the background thread

    # --------------------------------------------------------------------------

    def _pipeline_worker(self, file_path):
        """
        This method runs in a background thread.
        It imports and calls the processing module, then signals the GUI when done.
        """

        original_stdout = sys.stdout   # save the real stdout so we can restore it after

        # redirect all print() output to the GUI log area
        sys.stdout = LogRedirector(self.log_area)

        try:
            # import the processing module from project0_data_pipeline folder
            from project0_data_pipeline.load_trades import run_pipeline

            # run the pipeline — this returns a summary dictionary when it finishes
            summary_results = run_pipeline(file_path)

            # schedule the summary display on the main GUI thread (thread-safe way to update GUI)
            self.root_window.after(0, self._display_summary, summary_results)

            # schedule the "finished successfully" state change
            self.root_window.after(0, self._on_pipeline_finished, True)

        except Exception as pipeline_error:
            # if anything crashed, print a clear error to the log area
            print(f"\n{'=' * 56}")
            print(f" PIPELINE FAILED")
            print(f"{'=' * 56}")
            print(f" Error: {pipeline_error}")
            print(f" Check the log above for the step where it failed.")
            print(f"{'=' * 56}")

            # schedule the "finished with error" state change
            self.root_window.after(0, self._on_pipeline_finished, False)

        finally:
            # always restore the real stdout — even if an error occurred
            sys.stdout = original_stdout

    # --------------------------------------------------------------------------

    def _on_pipeline_finished(self, success):
        """Called on the main GUI thread when the pipeline completes (success or failure)."""

        # stop and hide the progress bar animation
        self.progress_bar.stop()
        self.progress_bar.pack_forget()   # hide the progress bar widget

        # re-enable the run button so the user can run again
        self.run_button.configure(
            state="normal",
            text="  Run Data Pipeline  ",
            bg=COLOUR_SUCCESS
        )

        # update the status label to reflect whether it succeeded or failed
        if success:
            self.status_label.configure(
                text="Pipeline completed successfully. See the summary below.",
                fg=COLOUR_SUCCESS
            )
        else:
            self.status_label.configure(
                text="Pipeline failed. See the log above for details.",
                fg="#cc2222"
            )

        # mark as no longer running
        self.pipeline_running = False

    # --------------------------------------------------------------------------

    def _display_summary(self, summary_results):
        """
        Populate the summary section with cards showing the key trade statistics.
        Called after the pipeline finishes successfully.
        """

        # remove the placeholder label and any previous summary widgets
        for widget in self.summary_frame_ref.winfo_children():
            widget.destroy()

        # safety check — if we got no results back, show an error message
        if not summary_results:
            tk.Label(
                self.summary_frame_ref,
                text="No summary data was returned. Check the log for errors.",
                font=FONT_BODY,
                fg="#cc2222",
                bg=COLOUR_CONTENT_BG
            ).pack(anchor="w")
            return

        # ---- Row 1: four stat cards ----
        cards_row = tk.Frame(self.summary_frame_ref, bg=COLOUR_CONTENT_BG)
        cards_row.pack(fill="x", pady=(0, 10))

        # define the four cards: (label text, key in summary dict, suffix to add)
        card_definitions = [
            ("Total Trades",   "total_trades",   ""),
            ("Win Rate",       "win_rate",        "%"),
            ("Total Profit",   "total_profit",    ""),
            ("Winning Trades", "winning_trades",  ""),
        ]

        for column_index, (label_text, result_key, suffix) in enumerate(card_definitions):
            raw_value = summary_results.get(result_key, "N/A")   # get the value, or N/A if missing

            # format the value: floats get 2 decimal places and comma separators
            if isinstance(raw_value, float):
                display_value = f"{raw_value:,.2f}{suffix}"
            elif isinstance(raw_value, int):
                display_value = f"{raw_value:,}{suffix}"
            else:
                display_value = f"{raw_value}{suffix}"

            # each card is a small white bordered frame
            card = tk.Frame(
                cards_row,
                bg=COLOUR_CARD_BG,
                highlightbackground=COLOUR_BORDER,
                highlightthickness=1
            )
            card.grid(row=0, column=column_index, padx=(0, 8), sticky="ew")

            card_inner = tk.Frame(card, bg=COLOUR_CARD_BG)
            card_inner.pack(padx=14, pady=12)

            tk.Label(card_inner, text=label_text,    font=FONT_CARD_LABEL,  fg="#888899", bg=COLOUR_CARD_BG).pack(anchor="w")
            tk.Label(card_inner, text=display_value, font=FONT_CARD_VALUE,  fg=COLOUR_TEXT_DARK, bg=COLOUR_CARD_BG).pack(anchor="w")

        # make all 4 card columns equal width
        for col in range(len(card_definitions)):
            cards_row.columnconfigure(col, weight=1)

        # ---- Row 2: date range and symbols in a wider card ----
        detail_card = tk.Frame(
            self.summary_frame_ref,
            bg=COLOUR_CARD_BG,
            highlightbackground=COLOUR_BORDER,
            highlightthickness=1
        )
        detail_card.pack(fill="x", pady=(0, 10))

        detail_inner = tk.Frame(detail_card, bg=COLOUR_CARD_BG)
        detail_inner.pack(fill="x", padx=16, pady=12)

        # date range
        tk.Label(detail_inner, text="Date Range",                              font=FONT_CARD_LABEL,             fg="#888899",         bg=COLOUR_CARD_BG).pack(anchor="w")
        tk.Label(detail_inner, text=summary_results.get("date_range", "N/A"), font=("Segoe UI", 12, "bold"),    fg=COLOUR_TEXT_DARK,   bg=COLOUR_CARD_BG).pack(anchor="w", pady=(2, 10))

        # symbols traded
        tk.Label(detail_inner, text="Symbols Traded",                          font=FONT_CARD_LABEL,             fg="#888899",         bg=COLOUR_CARD_BG).pack(anchor="w")
        tk.Label(detail_inner, text=summary_results.get("symbols", "N/A"),    font=("Segoe UI", 12, "bold"),    fg=COLOUR_TEXT_DARK,   bg=COLOUR_CARD_BG).pack(anchor="w", pady=(2, 0))

        # ---- Open output file button ----
        output_file_path = summary_results.get("output_path", "")

        if output_file_path and os.path.isfile(output_file_path):   # only show button if file exists
            open_btn = tk.Button(
                self.summary_frame_ref,
                text=f"  Open trades_clean.csv  ",
                font=FONT_BUTTON,
                fg=COLOUR_ACCENT,
                bg=COLOUR_CARD_BG,
                activebackground=COLOUR_CONTENT_BG,
                activeforeground=COLOUR_ACCENT,
                bd=1,
                relief="solid",
                padx=12,
                pady=7,
                cursor="hand2",
                command=lambda p=output_file_path: os.startfile(p)   # open the file in Excel or default app
            )
            open_btn.pack(anchor="w", pady=(6, 0))

            # also show where the file was saved
            tk.Label(
                self.summary_frame_ref,
                text=f"Saved to: {output_file_path}",
                font=FONT_SMALL,
                fg=COLOUR_TEXT_MID,
                bg=COLOUR_CONTENT_BG
            ).pack(anchor="w", pady=(4, 0))


# ==============================================================================
# ENTRY POINT
# This block runs when you execute: python main_app.py
# ==============================================================================

if __name__ == "__main__":
    root_window = tk.Tk()              # create the main tkinter application window
    app = TradingBotApp(root_window)   # create the app and pass the window to it
    root_window.mainloop()             # start the GUI event loop — keeps the window open
