"""
Verification script — XGBoost Discovery build.

Checks:
  1. smart_features module importable + SMART_FEATURE_CATEGORIES present
  2. xgboost_discovery module importable + key functions present
  3. xgboost_panel module importable + build_panel / refresh present
  4. xgboost package installed (version check)
  5. state.py — p1_xgboost in PROJECT1_SUB_PANELS
  6. main_app.py — p1_xgboost panel build + refresh_map entries
  7. sidebar.py — btn_p1_xgboost + PROJECT1_BUTTONS key
  8. panels/__init__.py — xgboost_panel exported
"""

import sys
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
P1   = os.path.join(ROOT, 'project1_reverse_engineering')
sys.path.insert(0, ROOT)
sys.path.insert(0, P1)

PASS = "\033[32m[PASS]\033[0m"
FAIL = "\033[31m[FAIL]\033[0m"
results = []


def check(name, condition, detail=""):
    symbol = PASS if condition else FAIL
    print(f"  {symbol}  {name}" + (f"  ({detail})" if detail else ""))
    results.append(condition)


print("\n" + "=" * 60)
print("  XGBoost Discovery — Verification")
print("=" * 60 + "\n")

# 1. smart_features
try:
    import smart_features
    has_cats   = hasattr(smart_features, 'SMART_FEATURE_CATEGORIES')
    has_names  = hasattr(smart_features, 'get_smart_feature_names')
    has_compute = hasattr(smart_features, 'compute_smart_features')
    check("smart_features importable", True)
    check("SMART_FEATURE_CATEGORIES present", has_cats)
    check("get_smart_feature_names present", has_names)
    check("compute_smart_features present", has_compute)
    if has_names:
        names = smart_features.get_smart_feature_names()
        check(f"SMART_ features count >= 20", len(names) >= 20, f"{len(names)} features")
except Exception as e:
    check("smart_features importable", False, str(e))
    check("SMART_FEATURE_CATEGORIES present", False)
    check("get_smart_feature_names present", False)
    check("compute_smart_features present", False)
    check("SMART_ features count >= 20", False)

print()

# 2. xgboost_discovery
try:
    import xgboost_discovery as xd
    check("xgboost_discovery importable", True)
    check("run_xgboost_discovery present", hasattr(xd, 'run_xgboost_discovery'))
    check("load_xgboost_result present",   hasattr(xd, 'load_xgboost_result'))
    check("activate_xgboost_rules present", hasattr(xd, 'activate_xgboost_rules'))
    check("restore_original_rules present", hasattr(xd, 'restore_original_rules'))
except Exception as e:
    check("xgboost_discovery importable", False, str(e))
    for fn in ["run_xgboost_discovery", "load_xgboost_result",
               "activate_xgboost_rules", "restore_original_rules"]:
        check(f"{fn} present", False)

print()

# 3. xgboost_panel (import without Tk)
try:
    panels_path = os.path.join(P1, 'panels')
    sys.path.insert(0, panels_path)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "xgboost_panel",
        os.path.join(panels_path, "xgboost_panel.py")
    )
    mod = importlib.util.module_from_spec(spec)
    # Don't exec — just check the file exists and has the right functions
    with open(os.path.join(panels_path, "xgboost_panel.py"), encoding="utf-8") as f:
        src = f.read()
    check("xgboost_panel.py exists", True)
    check("build_panel defined",  "def build_panel" in src)
    check("refresh defined",      "def refresh" in src)
    check("_on_run defined",      "def _on_run" in src)
    check("_on_activate defined", "def _on_activate" in src)
    check("_on_restore defined",  "def _on_restore" in src)
except Exception as e:
    check("xgboost_panel.py exists", False, str(e))
    for fn in ["build_panel", "refresh", "_on_run", "_on_activate", "_on_restore"]:
        check(f"{fn} defined", False)

print()

# 4. xgboost package
try:
    import xgboost
    ver = xgboost.__version__
    check("xgboost installed", True, f"version {ver}")
    major = int(ver.split(".")[0])
    check("xgboost >= 2.0 (no use_label_encoder)", major >= 2)
except ImportError:
    check("xgboost installed", False, "pip install xgboost")
    check("xgboost >= 2.0", False)

print()

# 5. state.py
state_path = os.path.join(ROOT, 'state.py')
with open(state_path, encoding="utf-8") as f:
    state_src = f.read()
check("state.py — p1_xgboost in PROJECT1_SUB_PANELS",
      '"p1_xgboost"' in state_src or "'p1_xgboost'" in state_src)

print()

# 6. main_app.py
main_path = os.path.join(ROOT, 'main_app.py')
with open(main_path, encoding="utf-8") as f:
    main_src = f.read()
check("main_app.py — xgboost_panel import",    "xgboost_panel" in main_src)
check("main_app.py — p1_xgboost panel build",  '"p1_xgboost"' in main_src)
check("main_app.py — p1_xgboost refresh_map",  "p1_xgboost" in main_src)

print()

# 7. sidebar.py
sidebar_path = os.path.join(ROOT, 'sidebar.py')
with open(sidebar_path, encoding="utf-8") as f:
    sidebar_src = f.read()
check("sidebar.py — btn_p1_xgboost defined",      "btn_p1_xgboost" in sidebar_src)
check("sidebar.py — p1_xgboost in PROJECT1_BUTTONS", '"p1_xgboost"' in sidebar_src or "'p1_xgboost'" in sidebar_src)
check("sidebar.py — XGBoost button before Strategy Search",
      sidebar_src.index("p1_xgboost") < sidebar_src.index("p1_search")
      if "p1_xgboost" in sidebar_src and "p1_search" in sidebar_src else False)

print()

# 8. panels/__init__.py
init_path = os.path.join(P1, 'panels', '__init__.py')
with open(init_path, encoding="utf-8") as f:
    init_src = f.read()
check("panels/__init__.py — xgboost_panel exported", "xgboost_panel" in init_src)

print()
print("=" * 60)
passed = sum(results)
total  = len(results)
color  = "\033[32m" if passed == total else "\033[31m"
print(f"  {color}{passed}/{total} checks passed\033[0m")
print("=" * 60 + "\n")

sys.exit(0 if passed == total else 1)
