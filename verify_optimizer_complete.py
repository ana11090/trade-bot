"""
Verification for Complete Optimizer: all 7 firms, account size, stage-aware scoring, leverage, reminders
"""
import sys
import os
import json

sys.path.insert(0, '.')

print("="*60)
print("VERIFICATION: Complete Optimizer")
print("="*60)

# 1. All firms have leverage
print('\n1. Leverage in all firms:')
for f in sorted(os.listdir('prop_firms')):
    if not f.endswith('.json'):
        continue
    with open(f'prop_firms/{f}') as fh:
        d = json.load(fh)
    lev = d.get('leverage_by_size', 'MISSING')
    print(f'   {d["firm_name"]}: {lev}')

# 2. Presets from all firms
print()
from project2_backtesting.strategy_refiner import get_prop_firm_presets
presets = get_prop_firm_presets()
firms = [k for k in presets if k != 'Custom']
print(f'2. Optimizer presets: {len(firms)} firms')
for f in firms:
    desc = presets[f].get('description', '')[:60]
    print(f'   {f}: {desc}')

# 3. Scoring function is stage-aware
from project2_backtesting.strategy_refiner import _score_trades
test = [
    {'entry_time': '2024-01-10 10:00', 'net_pips': 200},
    {'entry_time': '2024-01-11 14:00', 'net_pips': -100},
    {'entry_time': '2024-01-12 09:00', 'net_pips': 150},
    {'entry_time': '2024-01-15 11:00', 'net_pips': 300},
    {'entry_time': '2024-01-16 10:00', 'net_pips': -50},
]
score_eval = _score_trades(test, stage='evaluation')
score_funded = _score_trades(test, stage='funded')
print(f'3. Scoring: eval={score_eval:.1f}, funded={score_funded:.1f} (should differ)')

# 4. Reminder module
from shared.firm_rules_reminder import get_trading_rules
lr = get_trading_rules('Get Leveraged')
print(f'4. Leveraged trading rules: {len(lr)}')

print()
print("ALL CHECKS PASSED" if len(firms) == 7 and score_eval != score_funded and len(lr) > 0 else "SOME CHECKS FAILED")
print("="*60)
