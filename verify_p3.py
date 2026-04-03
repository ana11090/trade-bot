import sys, os
sys.path.insert(0, '.')

from project3_live_trading.indicator_mapper import parse_feature_name, get_mql_code, get_custom_indicator_list
feat = parse_feature_name('H4_adx_14')
print(f'1. Indicator mapper OK -- {feat}')

from project3_live_trading.ea_generator import generate_ea
print('2. EA generator OK')

from project3_live_trading.test_script_generator import generate_test_script
print('2b. Test script generator OK')

from project3_live_trading.news_calendar import download_news_calendar
print('3. News calendar OK')

from project3_live_trading.ea_verifier import verify_ea_trades
print('4. EA verifier OK')

import ast
for pname, fname in [('ea_generator_panel','ea_generator_panel.py'),('live_monitor_panel','live_monitor_panel.py')]:
    path = os.path.join('project3_live_trading','panels',fname)
    with open(path, encoding='utf-8') as f: src = f.read()
    ast.parse(src)
    assert 'def build_panel' in src and 'def refresh' in src
    print(f'5/6. {pname} OK')

with open('main_app.py', encoding='utf-8') as f: c = f.read()
assert 'p3_generator' in c and 'p3_monitor' in c
print('7. main_app.py OK')

with open('sidebar.py', encoding='utf-8') as f: c = f.read()
assert 'p3_generator' in c
print('8. sidebar.py OK')

with open('state.py', encoding='utf-8') as f: c = f.read()
assert 'PROJECT3_SUB_PANELS' in c
print('9. state.py OK')

import json
report_path = os.path.join('project1_reverse_engineering','outputs','analysis_report.json')
if os.path.exists(report_path):
    with open(report_path) as f: report = json.load(f)
    custom = get_custom_indicator_list(report['rules'])
    if custom:
        print(f'10. Custom indicators needed ({len(custom)}): {[c.split(" on ")[0] for c in custom]}')
    else:
        print('10. No custom indicators needed')
else:
    print('10. analysis_report.json not found (run analysis first)')

print()
print('ALL CHECKS PASSED')
print('Sidebar: 3 - Live Trading -> EA Generator, Live Monitor')
