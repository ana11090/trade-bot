# ─────────────────────────────────────────────────────────────────────────────
# STATE — shared mutable globals
# ─────────────────────────────────────────────────────────────────────────────

# Tk root (set in main_app.py after window is created)
window = None

# Data
loaded_data             = None   # holds the pandas DataFrame after loading
all_rows                = []     # holds all rows as lists for the grid
current_page            = [0]
rows_per_page           = 50
selected_file_full_path = ""

# StringVars (assigned in panels/pipeline.py after Tk() exists)
account_type     = None   # tk.StringVar — "Standard" / "Cent" / "Micro"
starting_balance = None   # tk.StringVar — initial deposit amount

# Sidebar / panel colour constants
COL_ACTIVE   = "#e94560"
COL_INACTIVE = "#16213e"
COL_PARENT   = "#1e2d4e"   # btn0 color when a sub-panel is active
COL_SUB      = "#0f1628"   # submenu background
FG_ACTIVE    = "white"
FG_INACTIVE  = "#445577"
FG_SUB       = "#5a7a99"   # sub-button inactive text

# Panel registry
all_panels   = {}
active_panel = [None]
SUB_PANELS      = {"panel4", "panel5", "panel6", "panel7", "panel8"}
PROB_SUB_PANELS = {"account_survival", "expected_value", "breakeven", "kelly", "streaks", "drawdown_recovery"}
PROJECT1_SUB_PANELS = {"p1_config", "p1_run", "p1_results", "p1_analysis"}
PROJECT2_SUB_PANELS = {"p2_config", "p2_run", "p2_results"}
PROJECT0_EXTRA_PANELS = {"prop_explorer", "compare_histories", "lifecycle_sim"}
submenu_open = [False]

# Trade history management
active_history_id     = None    # ID of currently selected trade history
active_history_config = None    # Dict with trade history metadata
