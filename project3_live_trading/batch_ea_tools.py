"""
batch_ea_tools.py — generate many EAs at once, build MT5 tester .ini files to run
them headlessly, and (separately) compare the resulting MT5 reports to Python.

WHY: round-tripping one EA at a time through the MT5 GUI is slow. This batches:
  1) GENERATE : turn every saved/my_rules rule into a .mq5 (reuses the panel's exact
                arg-building so EAs are identical to what the app produces).
  2) RUN      : emit one MT5 Strategy-Tester .ini per EA + a .bat that runs each pass
                headlessly via terminal64.exe /config:<ini>. (You set the terminal
                path + dates once.)
  3) COMPARE  : see batch_compare_reports.py (separate file) — reads the MT5 .xlsx
                reports and lines them up against Python backtest trades.

CHANGED: June 2026 — batch EA generation + tester .ini emit
Run on Windows (where MT5 lives). Python 3.9+ compatible.
"""

import os
import sys
import json

# repo root = two levels up from this file
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ----------------------------------------------------------------------------
# 1) BATCH GENERATE
# ----------------------------------------------------------------------------
def _safe_name(s):
    keep = []
    for ch in str(s):
        keep.append(ch if (ch.isalnum() or ch in ('_', '-')) else '_')
    return ''.join(keep)[:80] or 'ea'


def batch_generate(out_dir, source='my_rules', symbol='XAUUSD', magic_start=12345,
                   limit=None, entries=None):
    """Generate a .mq5 for every rule in the chosen store.

    source: 'my_rules' or 'saved_rules'.
    entries: optional explicit list of rule entry dicts (from the panel's checkbox grid).
             When None, falls back to loading the whole store (CLI / back-compat).
    Returns list of dicts: {name, path, rule_combo, exit_name, entry_tf, ok, error}.
    """
    # CHANGED: June 2026 — accept an explicit list of rule entries (from the panel's
    #   checkbox grid). When None, fall back to loading the whole store (CLI/back-compat).
    from project3_live_trading.panels.my_rules_eas_panel import _gen_ea_for
    if entries is not None:
        rules = list(entries)
    else:
        if source == 'saved_rules':
            from shared.saved_rules import load_all
        else:
            from shared.my_rules import load_all
        rules = load_all()
    if limit:
        rules = rules[:int(limit)]
    os.makedirs(out_dir, exist_ok=True)

    results = []
    magic = int(magic_start)
    for i, entry in enumerate(rules):
        r = entry.get('rule', {}) if isinstance(entry, dict) else {}
        combo = r.get('rule_combo') or entry.get('rule_id') or ('rule_%d' % i)
        name = _safe_name(combo)
        path = os.path.join(out_dir, name + '.mq5')
        rec = {
            'name': name, 'path': path,
            'rule_combo': combo,
            'exit_name': r.get('exit_name'),
            'entry_tf': r.get('entry_tf') or r.get('entry_timeframe') or 'H1',
            'entry_bar_offset': r.get('entry_bar_offset', 0),
            'magic': magic,
            'ok': False, 'error': '',
        }
        try:
            code = _gen_ea_for(entry)
            if not code or code.startswith('// EA generation failed'):
                rec['error'] = (code or 'empty').splitlines()[0]
            else:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(code)
                rec['ok'] = True
        except Exception as e:  # noqa
            rec['error'] = repr(e)
        results.append(rec)
        magic += 1
        print('[GEN] %s -> %s' % (name, 'ok' if rec['ok'] else 'FAILED: ' + rec['error']))

    # manifest for the runner + comparer
    man = os.path.join(out_dir, 'batch_manifest.json')
    with open(man, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)
    print('\n[GEN] %d/%d generated. Manifest: %s'
          % (sum(1 for x in results if x['ok']), len(results), man))
    return results


# ----------------------------------------------------------------------------
# 2) EMIT MT5 TESTER .ini FILES + a .bat to run them headlessly
# ----------------------------------------------------------------------------
_INI_TEMPLATE = """; auto-generated MT5 Strategy Tester config
[Tester]
Expert={expert_rel}
Symbol={symbol}
Period={period}
Model=1
FromDate={from_date}
ToDate={to_date}
Deposit={deposit}
Leverage=1:{leverage}
Optimization=0
ShutdownTerminal=1
Report={report_path}
ReplaceReport=1
"""


def emit_tester_inis(manifest_path, terminal_data_dir, experts_subdir,
                     reports_dir, symbol='XAUUSD', period='M5',
                     from_date='2026.01.01', to_date='2026.04.08',
                     deposit=10000, leverage=10, terminal_exe=None):
    """Write one .ini per generated EA and a run_all.bat.

    terminal_data_dir : the MT5 *data* folder (where MQL5\\Experts lives), e.g.
        C:\\Users\\you\\AppData\\Roaming\\MetaQuotes\\Terminal\\<HASH>
    experts_subdir : path UNDER MQL5\\Experts where you copied the .mq5 files,
        relative like 'Experts\\batch' -> Expert= value becomes 'batch\\name.ex5'
        NOTE: you must COMPILE the .mq5 to .ex5 first (MetaEditor or /compile).
    terminal_exe : full path to terminal64.exe (for the .bat). If None, the .bat
        uses a placeholder you edit once.
    """
    with open(manifest_path, encoding='utf-8') as f:
        man = json.load(f)
    ini_dir = os.path.join(os.path.dirname(manifest_path), 'tester_inis')
    os.makedirs(ini_dir, exist_ok=True)
    os.makedirs(reports_dir, exist_ok=True)

    bat_lines = ['@echo off', 'REM auto-generated — runs each tester pass in sequence']
    exe = terminal_exe or 'C:\\Program Files\\MetaTrader 5\\terminal64.exe'
    made = 0
    for rec in man:
        if not rec.get('ok'):
            continue
        name = rec['name']
        expert_rel = experts_subdir.replace('/', '\\') + '\\' + name + '.ex5'
        # per-EA report path (MT5 writes relative to terminal dir if not absolute)
        report_path = os.path.join(reports_dir, name).replace('/', '\\')
        ini = _INI_TEMPLATE.format(
            expert_rel=expert_rel, symbol=symbol, period=period,
            from_date=from_date, to_date=to_date, deposit=deposit,
            leverage=leverage, report_path=report_path,
        )
        ini_path = os.path.join(ini_dir, name + '.ini')
        with open(ini_path, 'w', encoding='utf-8') as f:
            f.write(ini)
        bat_lines.append('echo === %s ===' % name)
        bat_lines.append('"%s" /config:"%s"' % (exe, ini_path))
        made += 1

    bat_path = os.path.join(os.path.dirname(manifest_path), 'run_all_tests.bat')
    with open(bat_path, 'w', encoding='utf-8') as f:
        f.write('\r\n'.join(bat_lines) + '\r\n')
    print('[RUN] wrote %d .ini files to %s' % (made, ini_dir))
    print('[RUN] wrote %s' % bat_path)
    print('\nNEXT STEPS (one-time setup):')
    print('  1) Copy the .mq5 files into <data_dir>\\MQL5\\Experts\\%s' % experts_subdir)
    print('  2) Compile them to .ex5 (MetaEditor: open folder, Compile; or')
    print('     run metaeditor64.exe /compile for each — see MT5 docs).')
    print('  3) Edit run_all_tests.bat if the terminal path differs.')
    print('  4) Double-click run_all_tests.bat — each EA runs headless, writes a report.')
    print('  5) Then run batch_compare_reports.py against the reports folder.')
    return bat_path


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='Batch generate EAs + emit MT5 tester inis')
    p.add_argument('--out', default=os.path.join(_ROOT, 'project3_live_trading',
                                                 'outputs', 'batch_eas'))
    p.add_argument('--source', default='my_rules', choices=['my_rules', 'saved_rules'])
    p.add_argument('--symbol', default='XAUUSD')
    p.add_argument('--limit', type=int, default=None)
    p.add_argument('--emit-inis', action='store_true',
                   help='also emit MT5 tester .ini files + run_all.bat')
    p.add_argument('--terminal-data-dir', default='')
    p.add_argument('--experts-subdir', default='batch')
    p.add_argument('--reports-dir', default='')
    p.add_argument('--period', default='M5')
    p.add_argument('--from-date', default='2026.01.01')
    p.add_argument('--to-date', default='2026.04.08')
    p.add_argument('--terminal-exe', default='')
    args = p.parse_args()

    res = batch_generate(args.out, source=args.source, symbol=args.symbol,
                         limit=args.limit)
    if args.emit_inis:
        emit_tester_inis(
            os.path.join(args.out, 'batch_manifest.json'),
            terminal_data_dir=args.terminal_data_dir,
            experts_subdir=args.experts_subdir,
            reports_dir=args.reports_dir or os.path.join(args.out, 'reports'),
            symbol=args.symbol, period=args.period,
            from_date=args.from_date, to_date=args.to_date,
            terminal_exe=args.terminal_exe or None,
        )
