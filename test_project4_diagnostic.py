"""
Diagnostic script for Project 4 issues.
Run this and share the output to help identify the problem.
"""
import sys
sys.path.insert(0, '.')

import os
import pandas as pd

print("="*70)
print("PROJECT 4 DIAGNOSTIC TEST")
print("="*70)

# Test 1: Check CSV structure
print("\n[TEST 1] CSV Structure")
print("-"*70)
candles_path = os.path.join('data', 'xauusd_H1.csv')
if os.path.exists(candles_path):
    df = pd.read_csv(candles_path, nrows=5)
    print(f"File exists: {candles_path}")
    print(f"Columns: {list(df.columns)}")
    print(f"First column name: '{df.columns[0]}'")
    print(f"First row:\n{df.iloc[0]}")
else:
    print(f"ERROR: File not found: {candles_path}")

# Test 2: Test timestamp auto-detection
print("\n[TEST 2] Timestamp Auto-Detection")
print("-"*70)
try:
    candles = pd.read_csv(candles_path, nrows=10)

    # Run the detection logic
    ts_col = None
    for col in candles.columns:
        cl = col.lower().strip()
        if cl in ('timestamp', 'time', 'date', 'datetime', 'open_time', 'open time', 'opentime'):
            ts_col = col
            break
    if ts_col is None:
        ts_col = candles.columns[0]

    print(f"Detected timestamp column: '{ts_col}'")

    candles['timestamp'] = pd.to_datetime(candles[ts_col], errors='coerce')
    candles = candles.dropna(subset=['timestamp'])
    print(f"Successfully converted {len(candles)} rows")
    print(f"Sample timestamps: {candles['timestamp'].head(3).tolist()}")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

# Test 3: Test candle_labeler import and basic function
print("\n[TEST 3] Candle Labeler Function")
print("-"*70)
try:
    from project4_strategy_creation.candle_labeler import label_candles
    print("Import successful")

    # Try labeling a small subset
    temp_csv = 'temp_test.csv'
    pd.read_csv(candles_path, nrows=50).to_csv(temp_csv, index=False)

    result = label_candles(
        candles_path=temp_csv,
        sl_pips=150,
        tp_pips=300,
        direction='BUY',
        max_hold_candles=5,
        cache=False
    )

    print(f"Labeled {len(result)} candles successfully")
    print(f"Result columns: {list(result.columns)}")
    print(f"Win rate: {result['label'].mean():.1%}")

    os.remove(temp_csv)
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

# Test 4: Test scratch_discovery import
print("\n[TEST 4] Scratch Discovery Import")
print("-"*70)
try:
    from project4_strategy_creation.scratch_discovery import run_scratch_discovery
    print("Import successful")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

# Test 5: Test panel building
print("\n[TEST 5] Panel Building")
print("-"*70)
try:
    import tkinter as tk
    from project4_strategy_creation.panels.scratch_panel import build_panel

    root = tk.Tk()
    root.withdraw()  # Don't show window

    panel = build_panel(root)

    print(f"Panel created successfully")
    print(f"Panel type: {type(panel).__name__}")
    print(f"Panel children: {len(panel.winfo_children())}")

    # Check for canvas
    children = list(panel.winfo_children())
    canvas_found = any(isinstance(w, tk.Canvas) for w in children)
    scrollbar_found = any(isinstance(w, (tk.Scrollbar, __import__('tkinter.ttk', fromlist=['Scrollbar']).Scrollbar)) for w in children)

    print(f"Has Canvas: {canvas_found}")
    print(f"Has Scrollbar: {scrollbar_found}")

    if canvas_found:
        canvas = [w for w in children if isinstance(w, tk.Canvas)][0]
        print(f"Canvas pack info: {canvas.pack_info()}")

    root.destroy()
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

# Test 6: Test backtester integration
print("\n[TEST 6] Backtester Integration")
print("-"*70)
try:
    from project2_backtesting.strategy_backtester import build_multi_tf_indicators
    print("Backtester import successful")

    # This is what scratch_discovery calls
    data_dir = 'data'
    h1_df = pd.read_csv(candles_path, nrows=100)

    # Test that timestamp can be accessed
    if 'timestamp' in h1_df.columns:
        print(f"H1 CSV has 'timestamp' column: OK")
    else:
        print(f"WARNING: H1 CSV missing 'timestamp' column")
        print(f"Available columns: {list(h1_df.columns)}")

except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*70)
print("DIAGNOSTIC COMPLETE")
print("="*70)
print("\nIf you see errors above, please share this full output.")
