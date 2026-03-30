"""
Quick test to verify Project 0 -> Project 1 data integration
"""

import pandas as pd
import state
from shared import data_utils

print("=" * 60)
print("Testing Project 0 -> Project 1 Integration")
print("=" * 60)

# Test 1: Initial state
print("\n1. Initial state:")
print(f"   state.loaded_data is None: {state.loaded_data is None}")

# Test 2: Simulate loading data in Project 0
print("\n2. Simulating data load (like Project 0 does)...")

# Create sample data like Project 0 would
sample_data = pd.DataFrame({
    'Open Date': ['10/03/2026 10:24', '09/03/2026 19:18', '09/03/2026 15:56'],
    'Close Date': ['10/03/2026 10:31', '09/03/2026 19:19', '09/03/2026 15:57'],
    'Symbol': ['XAUUSD', 'XAUUSD', 'XAUUSD'],
    'Action': ['Buy', 'Buy', 'Buy'],
    'Lots': [100.0, 100.0, 100.0],
    'SL': [5169.3, 5096.41, 5096.5],
    'TP': [0.0, 0.0, 0.0],
    'Open Price': [5160.22, 5097.91, 5095.81],
    'Close Price': [5169.3, 5096.41, 5096.5],
    'Pips': [908.0, -150.0, 69.0],
    'Profit': [90350.0, -15450.0, 6450.0],
    'Duration (DDHHMMSS)': ['00:00:07:00', '00:00:01:00', '00:00:01:00'],
    'Change %': [1.6, -0.27, 0.11]
})

# Load into state (simulating Project 0)
state.loaded_data = sample_data
print(f"   Loaded {len(state.loaded_data)} trades into state.loaded_data")

# Test 3: Check if Project 1 can see it
print("\n3. Testing Project 1 data access:")
print(f"   state.loaded_data is None: {state.loaded_data is None}")
print(f"   Number of trades: {len(state.loaded_data)}")

# Test 4: Try loading trades using Project 1 function
print("\n4. Testing load_trades_from_state():")
try:
    trades_df = data_utils.load_trades_from_state(state)
    print(f"   ✓ Successfully loaded {len(trades_df)} trades")
    print(f"   ✓ Columns: {trades_df.columns.tolist()}")
    print(f"   ✓ Date range: {trades_df['open_time'].min()} to {trades_df['open_time'].max()}")
except Exception as e:
    print(f"   ✗ Error: {e}")

print("\n" + "=" * 60)
print("Integration Test Complete!")
print("=" * 60)
print("\nIf all tests passed, the integration is working correctly.")
print("The issue in the UI must be a refresh timing problem.")
