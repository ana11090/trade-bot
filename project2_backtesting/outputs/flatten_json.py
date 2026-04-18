"""
Hotfix script to flatten stats in backtest_matrix.json

Run this ONCE to fix the existing backtest results.
After running, View Results will show all 480 results.
"""
import json
import os

json_path = os.path.join(os.path.dirname(__file__), 'backtest_matrix.json')

print(f"Reading: {json_path}")
with open(json_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

print(f"Found {len(data.get('results', []))} results")

# Flatten stats from nested dict to top level
flattened = []
for r in data.get('results', []):
    flat = dict(r)  # shallow copy
    stats = flat.pop('stats', {})
    if isinstance(stats, dict):
        flat.update(stats)  # merge stats to top level
    flattened.append(flat)

data['results'] = flattened

# Backup original
backup_path = json_path.replace('.json', '_backup.json')
print(f"Creating backup: {backup_path}")
with open(json_path, 'r', encoding='utf-8') as f:
    with open(backup_path, 'w', encoding='utf-8') as bf:
        bf.write(f.read())

# Write flattened
print(f"Writing flattened data...")
with open(json_path, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2, default=str)

print("✓ Done! Restart the app and go to View Results.")
print(f"  If something goes wrong, restore from: {backup_path}")
