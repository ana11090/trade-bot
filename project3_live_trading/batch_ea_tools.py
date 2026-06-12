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
import shutil
import subprocess
import glob
import socket

# repo root = two levels up from this file
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ----------------------------------------------------------------------------
# 0) BATCH COMPILE  (headless via metaeditor64.exe /compile)
# ----------------------------------------------------------------------------

# WHY: eliminate the manual "open MetaEditor and compile each EA" step. metaeditor64.exe has a
#   headless /compile mode. We copy each .mq5 into MQL5\Experts\batch, compile headlessly, and
#   confirm the .ex5 appeared + scan the per-file log for hard errors. metaeditor64 returns
#   non-zero on warnings too, so success is (.ex5 exists AND no "error" line in the log).
# CHANGED: June 2026 — new function; automated batch compile via metaeditor64 CLI

def _find_metaeditor(terminal_dir=None, explicit=None):
    """Locate metaeditor64.exe. Priority: explicit arg → alongside terminal dir → common paths."""
    cands = []
    if explicit:
        cands.append(explicit)
    if terminal_dir:
        cands.append(os.path.join(terminal_dir, 'metaeditor64.exe'))
    cands += [
        r"C:\Program Files\MetaTrader 5\metaeditor64.exe",
        r"C:\Program Files\MetaTrader 5 EXNESS\metaeditor64.exe",
        r"C:\Program Files\MetaTrader 5 IC Markets\metaeditor64.exe",
        r"C:\Program Files\MetaTrader 5 Pepperstone\metaeditor64.exe",
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


# WHY: metaeditor64.exe lives at a different path on each of the two machines. Persist it in
#   the gitignored p1_config.json keyed by hostname so each PC keeps its own entry and it
#   is never shared via git. config_loader.save() only handles keys in DEFAULTS and str()s
#   values, so we read/write the raw JSON file directly for this dict-valued key. Unknown
#   keys survive config_loader.save() calls untouched (verified in config_loader.load()).
# CHANGED: June 2026 — per-machine metaeditor path memory (hostname-keyed in p1_config.json)

_ME_CFG_KEY = "metaeditor_path_by_host"


def _p1_config_path():
    """Absolute path to the gitignored p1_config.json (project1_reverse_engineering/)."""
    # Mirror config_loader._CONFIG_FILE location: <repo>/project1_reverse_engineering/p1_config.json
    return os.path.join(_ROOT, "project1_reverse_engineering", "p1_config.json")


def get_saved_metaeditor_path():
    """Return the saved metaeditor64.exe path for THIS machine, or None.

    Reads p1_config.json directly (not via config_loader.load()) because the
    value is a dict — config_loader.load() only accepts keys in DEFAULTS and
    str()-coerces values, which would corrupt a hostname→path mapping.
    """
    cfg_path = _p1_config_path()
    if not os.path.isfile(cfg_path):
        return None
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        by_host = raw.get(_ME_CFG_KEY) or {}
        p = by_host.get(socket.gethostname())
        return p if (p and os.path.isfile(p)) else None
    except Exception:
        return None


def save_metaeditor_path(path):
    """Persist metaeditor64.exe path for THIS machine in p1_config.json (gitignored).

    Merges into the existing file so all other keys (data_source_id, etc.) are
    preserved. Atomic write via .tmp → rename.
    Returns True on success.
    """
    if not path or not os.path.isfile(path):
        return False
    cfg_path = _p1_config_path()
    try:
        raw = {}
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        by_host = raw.get(_ME_CFG_KEY) or {}
        by_host[socket.gethostname()] = path
        raw[_ME_CFG_KEY] = by_host
        tmp = cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2)
        if os.path.exists(cfg_path):
            os.replace(tmp, cfg_path)
        else:
            os.rename(tmp, cfg_path)
        return True
    except Exception as e:
        print("[COMPILE] could not save metaeditor path:", e)
        try:
            if os.path.exists(cfg_path + ".tmp"):
                os.remove(cfg_path + ".tmp")
        except Exception:
            pass
        return False


def batch_compile(out_dir, data_dir, experts_subdir=r"Experts\batch",
                  metaeditor_path=None, terminal_dir=None, timeout_sec=120):
    """Copy generated .mq5 from out_dir into <data_dir>\\MQL5\\<experts_subdir>, compile each
    to .ex5 with metaeditor64, and verify. Returns list of {name, ok, ex5, error}.

    out_dir         : folder where batch_generate wrote the .mq5 files
    data_dir        : MT5 DATA folder (contains MQL5\\Experts)
    experts_subdir  : subfolder under MQL5\\ (default Experts\\batch)
    metaeditor_path : full path to metaeditor64.exe (auto-detected if omitted)
    terminal_dir    : MT5 install dir to help auto-detect metaeditor64.exe
    timeout_sec     : per-file compile timeout
    """
    # CHANGED: June 2026 — prefer per-machine saved path, then auto-detect
    if metaeditor_path is None:
        metaeditor_path = get_saved_metaeditor_path()
    me = _find_metaeditor(terminal_dir, metaeditor_path)
    if not me:
        return [{"name": "(all)", "ok": False,
                 "error": "metaeditor64.exe not found — pass metaeditor_path or terminal_dir"}]

    dest_dir = os.path.join(data_dir, "MQL5", experts_subdir)
    os.makedirs(dest_dir, exist_ok=True)

    mq5_files = sorted(glob.glob(os.path.join(out_dir, "*.mq5")))
    if not mq5_files:
        return [{"name": "(none)", "ok": False, "error": "no .mq5 files in " + out_dir}]

    results = []
    for src in mq5_files:
        name = os.path.splitext(os.path.basename(src))[0]
        dest = os.path.join(dest_dir, name + ".mq5")
        ex5  = os.path.join(dest_dir, name + ".ex5")
        log  = os.path.join(dest_dir, name + ".compile.log")
        try:
            shutil.copy2(src, dest)
            # remove stale .ex5 so the existence check after compile is meaningful
            if os.path.isfile(ex5):
                os.remove(ex5)
            cmd = [me, "/compile:" + dest, "/log:" + log]
            try:
                subprocess.run(cmd, timeout=timeout_sec,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                results.append({"name": name, "ok": False, "error": "compile timed out"})
                continue

            # MetaEditor writes logs as UTF-16; try common encodings tolerantly
            err_text = ""
            if os.path.isfile(log):
                for enc in ("utf-16", "utf-8", "cp1252"):
                    try:
                        with open(log, "r", encoding=enc, errors="ignore") as f:
                            err_text = f.read()
                        break
                    except Exception:
                        continue

            has_ex5  = os.path.isfile(ex5)
            hard_err = any(" error " in ln.lower() for ln in err_text.splitlines())

            if has_ex5 and not hard_err:
                results.append({"name": name, "ok": True, "ex5": ex5})
            else:
                first_err = next(
                    (ln.strip() for ln in err_text.splitlines() if " error " in ln.lower()),
                    "no .ex5 produced")
                results.append({"name": name, "ok": False, "error": first_err})

        except Exception as e:
            results.append({"name": name, "ok": False, "error": str(e)})

    return results


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
