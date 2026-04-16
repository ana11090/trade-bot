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
panel_builders   = {}          # lazy builders: name -> callable, populated in main_app.py
SUB_PANELS      = {"panel4", "panel5", "panel6", "panel7", "panel8"}
PROB_SUB_PANELS = {"account_survival", "expected_value", "breakeven", "kelly", "streaks", "drawdown_recovery"}
PROJECT1_SUB_PANELS = {"p1_config", "p1_run", "p1_results", "p1_analysis", "p1_xgboost", "p1_search"}
PROJECT2_SUB_PANELS = {"p2_config", "p2_run", "p2_results", "p2_refiner", "p2_validator", "p2_prop_test", "p2_saved", "p2_playground"}

# WHY (Phase A.40b): Cross-panel coordination for the "▶ Backtest this
#      rule" button on the Saved Rules panel. The button sets these
#      globals, navigates to p2_run, then schedules a callback that
#      calls the run_backtest_panel helper to consume them. Using
#      list-of-one so we can mutate the reference without reassigning
#      a module attribute (avoids import-order surprises).
# CHANGED: April 2026 — Phase A.40b
pending_backtest_rule_id   = [None]   # int | None — rule id from saved_rules.json
pending_backtest_auto_run  = [False]  # bool — if True, click Run Backtest after selecting
PROJECT3_SUB_PANELS = {"p3_generator", "p3_monitor"}
PROJECT4_SUB_PANELS = {"p4_scratch"}
PROJECT0_EXTRA_PANELS = {"prop_explorer", "compare_histories", "lifecycle_sim"}
submenu_open = [False]

# Trade history management
active_history_id     = None    # ID of currently selected trade history
active_history_config = None    # Dict with trade history metadata

# -------- Phase A: active dataset mirror ------------------------------
# WHY: UI panels (Phase B) need a writable global to track the active
#      dataset without round-tripping through disk on every read. This
#      mirrors config.ACTIVE_DATASET_ID at startup; Phase B's Dataset
#      Manager panel will write to it and persist via dataset_registry.
# CHANGED: April 2026 — Phase A
active_dataset_id = None

def _init_active_dataset():
    global active_dataset_id
    try:
        from shared import dataset_registry
        active_dataset_id = dataset_registry.get_active_dataset_id()
    except Exception:
        active_dataset_id = None

_init_active_dataset()
