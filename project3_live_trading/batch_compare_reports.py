"""
batch_compare_reports.py — read a folder of MT5 Strategy-Tester reports (.xlsx) and
line each one up against the matching Python backtest trades, producing one summary
table: per EA, MT5 entries vs Python entries, exact-bar matches, and one-bar shifts.

WHY: doing this by hand (open each report, count, eyeball against Python) doesn't
scale. This consumes the reports the batch runner produced and tells you which EAs
actually match Python.

Matching MT5 report -> Python trades:
  - MT5 report file name is the EA name (from the batch manifest's 'name').
  - Python trades for that rule come from a per-rule trades CSV/JSON you point at,
    keyed by the same name or rule_combo. Adjust _python_trades_for() to your export.

CHANGED: June 2026 — batch report comparison
Python 3.9+; needs openpyxl (already used elsewhere in the project).
"""

import os
import sys
import csv
import glob
import json
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


def _read_mt5_entries(xlsx_path):
    """Return sorted list of MT5 entry datetimes (deals with Direction == 'in')."""
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    rows = list(wb.worksheets[0].iter_rows(values_only=True))
    hdr_i = None
    for i, row in enumerate(rows):
        if row and row[0] == 'Time' and 'Deal' in [str(c) for c in row]:
            hdr_i = i
            break
    if hdr_i is None:
        return []
    out = []
    for row in rows[hdr_i + 1:]:
        if not row or row[0] is None:
            continue
        if 'Balance:' in str(row[0]):
            break
        # Direction column is index 4 in these reports
        if len(row) > 4 and str(row[4]).lower() == 'in':
            try:
                out.append(datetime.strptime(str(row[0])[:16], '%Y.%m.%d %H:%M'))
            except Exception:
                pass
    return sorted(out)


def _read_python_entries(csv_path):
    """Return sorted list of Python entry datetimes (minute-truncated)."""
    out = []
    with open(csv_path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            try:
                out.append(datetime.strptime(r['entry_time'], '%Y-%m-%d %H:%M:%S')
                           .replace(second=0))
            except Exception:
                pass
    return sorted(out)


def _python_trades_for(name, python_dir):
    """Locate the Python trades CSV for an EA name. Adjust to your export layout.

    Tries, in order:
      <python_dir>/<name>.csv
      <python_dir>/<name>_trades.csv
      <python_dir>/trades_<name>.csv
    Falls back to a single shared <python_dir>/trades.csv if present (one-rule case).
    """
    cands = [
        os.path.join(python_dir, name + '.csv'),
        os.path.join(python_dir, name + '_trades.csv'),
        os.path.join(python_dir, 'trades_' + name + '.csv'),
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    shared = os.path.join(python_dir, 'trades.csv')
    return shared if os.path.exists(shared) else None


def _compare(mt5, py, bar_minutes=5):
    """Return dict of comparison metrics between two sorted datetime lists."""
    mset, pset = set(mt5), set(py)
    if not mt5:
        return dict(mt5=0, py=len(py), py_in_range=0, exact=0,
                    one_bar_early=0, one_bar_late=0, py_after_end=0)
    cutoff = max(mt5)
    py_in = set(p for p in pset if p <= cutoff)
    delta = timedelta(minutes=bar_minutes)
    early = sum(1 for p in pset if (p + delta) in mset)   # python earlier than MT5
    late = sum(1 for p in pset if (p - delta) in mset)    # python later than MT5
    return dict(
        mt5=len(mset), py=len(pset), py_in_range=len(py_in),
        exact=len(mset & pset),
        one_bar_early=early, one_bar_late=late,
        py_after_end=len(pset - py_in),
    )


def compare_folder(reports_dir, python_dir, manifest_path=None, bar_minutes=5):
    """Compare every MT5 .xlsx report in reports_dir to its Python trades.

    Prints a table and returns the list of row dicts.
    """
    # name -> rule_combo from manifest (optional, for nicer labels)
    label = {}
    if manifest_path and os.path.exists(manifest_path):
        try:
            for rec in json.load(open(manifest_path, encoding='utf-8')):
                label[rec['name']] = rec.get('rule_combo', rec['name'])
        except Exception:
            pass

    reports = sorted(glob.glob(os.path.join(reports_dir, '*.xlsx')))
    if not reports:
        print('[CMP] no .xlsx reports in %s' % reports_dir)
        return []

    rows = []
    print('\n%-32s %5s %5s %6s %7s %6s %6s' %
          ('EA', 'MT5', 'PY', 'exact', 'early', 'late', 'after'))
    print('-' * 78)
    for rp in reports:
        name = os.path.splitext(os.path.basename(rp))[0]
        mt5 = _read_mt5_entries(rp)
        py_csv = _python_trades_for(name, python_dir)
        if not py_csv:
            print('%-32s %5d   no python trades found' % (name[:32], len(mt5)))
            rows.append(dict(name=name, mt5=len(mt5), error='no_python'))
            continue
        py = _read_python_entries(py_csv)
        m = _compare(mt5, py, bar_minutes=bar_minutes)
        m['name'] = name
        rows.append(m)
        print('%-32s %5d %5d %6d %7d %6d %6d' %
              (name[:32], m['mt5'], m['py'], m['exact'],
               m['one_bar_early'], m['one_bar_late'], m['py_after_end']))

    # write CSV summary
    out_csv = os.path.join(reports_dir, 'comparison_summary.csv')
    keys = ['name', 'mt5', 'py', 'py_in_range', 'exact',
            'one_bar_early', 'one_bar_late', 'py_after_end']
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print('\n[CMP] summary -> %s' % out_csv)
    print('Columns: exact=same-bar entries; early/late=Python off by one bar;')
    print('         after=Python trades past MT5 end (expected if Python ran longer).')
    return rows


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='Compare MT5 reports folder to Python trades')
    p.add_argument('--reports', required=True, help='folder of MT5 .xlsx reports')
    p.add_argument('--python', required=True,
                   help='folder of Python trade CSVs (per-rule or a shared trades.csv)')
    p.add_argument('--manifest', default='', help='batch_manifest.json (optional labels)')
    p.add_argument('--bar-minutes', type=int, default=5, help='bar size for shift check')
    args = p.parse_args()
    compare_folder(args.reports, args.python,
                   manifest_path=args.manifest or None,
                   bar_minutes=args.bar_minutes)
