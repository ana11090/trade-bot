"""Fix remaining Phase 19d issues: multi-line prints and WARNING: prefixes"""
import os
import re

BASE_DIR = r'D:\traiding data\trade-bot'

# Files with remaining issues
FIXES = {
    'project2_backtesting/backtest_engine.py': {
        'prints': [
            (463, 'print(f"[BACKTEST ENGINE]   Trade #{len(trades)}: {trade[\'direction\']} "',
                  'log.info(f"[BACKTEST ENGINE]   Trade #{len(trades)}: {trade[\'direction\']} "'),
            (488, 'print(f"[BACKTEST ENGINE] {period_name} complete: {len(trades)} trades. "',
                  'log.info(f"[BACKTEST ENGINE] {period_name} complete: {len(trades)} trades. "'),
            (517, 'print(f"[BACKTEST ENGINE] Loaded {len(candles_df)} {WINNING_SCENARIO} candles "',
                  'log.info(f"[BACKTEST ENGINE] Loaded {len(candles_df)} {WINNING_SCENARIO} candles "'),
        ],
        'warnings': [(411, 'WARNING: ')],
    },
    'project2_backtesting/strategy_backtester.py': {
        'prints': [
            (171, 'print(f"  {tf}: computing {len(needed_indicators)} indicators "',
                  'log.info(f"  {tf}: computing {len(needed_indicators)} indicators "'),
            (813, 'print(f"  [WARN] Computed lot size {lot_size:.1f} exceeds 100',
                  'log.warning(f"  [WARN] Computed lot size {lot_size:.1f} exceeds 100'),
            (1063, 'print(f"  [SKIP] Absurd pips: {pips:.0f} "',
                   'log.warning(f"  [SKIP] Absurd pips: {pips:.0f} "'),
            (1103, 'print(f"  [fast_backtest] Skipped {_skipped_count} trade(s) with absurd pips "',
                   'log.warning(f"  [fast_backtest] Skipped {_skipped_count} trade(s) with absurd pips "'),
            (1303, 'print(f"  {len(candles_df)} candles "',
                   'log.info(f"  {len(candles_df)} candles "'),
        ],
        'warnings': [(143, 'WARNING: '), (553, 'WARNING: '), (607, 'WARNING: '),
                     (609, 'WARNING: '), (619, 'WARNING: '), (621, 'WARNING: '),
                     (1368, 'WARNING: '), (1370, 'WARNING: '), (1380, 'WARNING: ')],
    },
}


def fix_file(rel_path, fixes):
    """Apply manual fixes to a file."""
    filepath = os.path.join(BASE_DIR, rel_path)

    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    print(f"\nFixing {rel_path}:")

    # Fix multi-line prints
    if 'prints' in fixes:
        for line_num, old_pattern, new_pattern in fixes['prints']:
            idx = line_num - 1  # Convert to 0-based
            if idx < len(lines):
                old_line = lines[idx].rstrip()
                # Replace print with log.info/log.warning
                if 'print(f"' in old_line:
                    lines[idx] = old_line.replace('print(f"', new_pattern.split('(')[0] + '(f"') + '\n'
                    print(f"  Line {line_num}: Converted print -> {new_pattern.split('(')[0]}")

    # Fix WARNING: prefixes
    if 'warnings' in fixes:
        for line_num, prefix in fixes['warnings']:
            idx = line_num - 1
            if idx < len(lines) and 'log.warning' in lines[idx] and prefix in lines[idx]:
                lines[idx] = lines[idx].replace(prefix, '', 1)
                print(f"  Line {line_num}: Removed '{prefix}' prefix")

    # Write back
    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(lines)

    print(f"  [OK] Fixed {rel_path}")


def main():
    print("="*60)
    print("Fixing Remaining Phase 19d Issues")
    print("="*60)

    for rel_path, fixes in FIXES.items():
        fix_file(rel_path, fixes)

    print("\n" + "="*60)
    print("[DONE] All fixes applied")
    print("="*60)


if __name__ == '__main__':
    main()
