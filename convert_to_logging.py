"""
Automated print() → logging conversion script for Phase 19d
Converts print statements to logging calls in project2 files.
"""
import os
import re
import sys

# Files to convert
BASE_DIR = r'D:\traiding data\trade-bot'
FILES_TO_CONVERT = [
    # Project 4
    ('project4_strategy_creation', 'scratch_discovery.py'),
]

LOGGER_IMPORT = """# CHANGED: April 2026 — UI-safe logging (Phase 19d)
from shared.logging_setup import get_logger
log = get_logger(__name__)"""


def has_logger_import(content):
    """Check if file already has logger import."""
    return 'from shared.logging_setup import get_logger' in content


def add_logger_import(content):
    """Add logger import after existing imports."""
    lines = content.split('\n')

    # Find the last import statement
    last_import_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('import ') or stripped.startswith('from '):
            last_import_idx = i

    # Insert logger import after last import, with blank line before
    if last_import_idx >= 0:
        lines.insert(last_import_idx + 1, '')
        lines.insert(last_import_idx + 2, LOGGER_IMPORT)

    return '\n'.join(lines)


def convert_print_to_log(line, indent):
    """Convert a single print() call to log.info/warning/error."""

    # Skip prints with end= or flush= parameters (for Fix 3)
    if 'end=' in line or 'flush=' in line:
        return None  # Return None to signal skip

    # Extract the print argument
    match = re.match(r'^(\s*)print\((.*)\)\s*$', line)
    if not match:
        return line  # Not a simple print statement, leave as-is

    indent_str = match.group(1)
    arg = match.group(2)

    # Determine log level based on content
    # Check if argument starts with f-string or regular string with ERROR/WARNING

    # Handle ERROR prefix
    if re.match(r'''['"]ERROR:\s*''', arg) or re.match(r'''f['"]ERROR:\s*''', arg):
        # Strip ERROR: prefix
        arg = re.sub(r'''(f?['"])ERROR:\s*''', r'\1', arg)
        return f'{indent_str}log.error({arg})'

    # Handle WARNING prefix
    if re.match(r'''['"]WARNING:\s*''', arg) or re.match(r'''f['"]WARNING:\s*''', arg):
        # Strip WARNING: prefix
        arg = re.sub(r'''(f?['"])WARNING:\s*''', r'\1', arg)
        return f'{indent_str}log.warning({arg})'

    # Check for ⚠ symbol (keep as warning)
    if '⚠' in arg or 'WARNING' in arg.upper():
        return f'{indent_str}log.warning({arg})'

    # Default to log.info
    return f'{indent_str}log.info({arg})'


def convert_file(filepath):
    """Convert all print() calls in a file to logging."""

    print(f"\n{'='*60}")
    print(f"Converting: {os.path.basename(filepath)}")
    print(f"{'='*60}")

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Add logger import if needed
    if not has_logger_import(content):
        content = add_logger_import(content)
        print("  [OK] Added logger import")
    else:
        print("  [OK] Logger import already present")

    # Convert print statements line by line
    lines = content.split('\n')
    new_lines = []

    stats = {'info': 0, 'warning': 0, 'error': 0, 'skipped': 0, 'total': 0}

    for line in lines:
        stripped = line.strip()

        # Check if this is a print statement
        if stripped.startswith('print('):
            stats['total'] += 1
            converted = convert_print_to_log(line, line[:len(line) - len(line.lstrip())])

            if converted is None:
                # Skipped (has end= or flush=)
                stats['skipped'] += 1
                new_lines.append(line)
            elif converted != line:
                # Successfully converted
                if '.error(' in converted:
                    stats['error'] += 1
                elif '.warning(' in converted:
                    stats['warning'] += 1
                else:
                    stats['info'] += 1
                new_lines.append(converted)
            else:
                # Not converted (complex print)
                new_lines.append(line)
        else:
            new_lines.append(line)

    # Write back
    new_content = '\n'.join(new_lines)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print(f"  [OK] Converted {stats['total']} prints:")
    print(f"      log.info:    {stats['info']}")
    print(f"      log.warning: {stats['warning']}")
    print(f"      log.error:   {stats['error']}")
    if stats['skipped'] > 0:
        print(f"      skipped:     {stats['skipped']} (for Fix 3)")

    return stats


def main():
    """Convert all specified files."""

    print("\n" + "="*60)
    print("Phase 19d - Automated Print to Logging Conversion")
    print(f"Processing {len(FILES_TO_CONVERT)} files")
    print("="*60)

    total_stats = {'info': 0, 'warning': 0, 'error': 0, 'skipped': 0, 'total': 0}

    for subdir, filename in FILES_TO_CONVERT:
        filepath = os.path.join(BASE_DIR, subdir, filename)

        if not os.path.exists(filepath):
            print(f"\n[SKIP] {filename} (file not found)")
            continue

        try:
            stats = convert_file(filepath)
            for key in total_stats:
                total_stats[key] += stats[key]
        except Exception as e:
            print(f"\n[ERROR] converting {filename}: {e}")
            import traceback
            traceback.print_exc()

    # Print summary
    print("\n" + "="*60)
    print("CONVERSION COMPLETE")
    print("="*60)
    print(f"Total prints converted: {total_stats['total']}")
    print(f"  log.info:    {total_stats['info']}")
    print(f"  log.warning: {total_stats['warning']}")
    print(f"  log.error:   {total_stats['error']}")
    if total_stats['skipped'] > 0:
        print(f"  skipped:     {total_stats['skipped']} (for Fix 3)")
    print()


if __name__ == '__main__':
    main()
