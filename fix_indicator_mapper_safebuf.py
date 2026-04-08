"""
Script to replace CopyBuffer patterns in indicator_mapper.py with SafeCopyBuf.

This fixes the critical bug where indicators aren't checked for readiness,
causing the EA to trade on uninitialized memory when indicators are warming up.
"""

import re

PATH = 'project3_live_trading/indicator_mapper.py'

with open(PATH, encoding='utf-8') as f:
    content = f.read()

original_content = content
total_changes = 0

# Pattern 1: Simple single CopyBuffer (single line)
# Matches: double buf_XXX[1]; CopyBuffer(handle_XXX,N,0,1,buf_XXX); double val_{var} = buf_XXX[0];
pattern1 = re.compile(
    r'"mt5_buffer_read":\s*"double\s+([a-z_{}0-9]+)\[1\];\s*'
    r'CopyBuffer\(([a-z_{}0-9]+),(\d+),0,1,\1\);\s*'
    r'double\s+val_\{var\}\s*=\s*\1\[0\];"',
    re.MULTILINE
)

def repl1(m):
    handle_expr = m.group(2)
    bufnum = m.group(3)
    return (f'"mt5_buffer_read": "double val_{{var}} = '
            f'SafeCopyBuf({handle_expr}, {bufnum}); '
            f'if(val_{{var}} == EMPTY_VALUE) indicatorFailed = true;"')

content = pattern1.sub(repl1, content)
count1 = len(pattern1.findall(original_content))
total_changes += count1

# Pattern 2: Double CopyBuffer (single line, e.g., Bollinger Bands)
pattern2 = re.compile(
    r'"mt5_buffer_read":\s*"double\s+([a-z_{}0-9]+)\[1\],([a-z_{}0-9]+)\[1\];\s*'
    r'CopyBuffer\(([a-z_{}0-9]+),(\d+),0,1,\1\);\s*'
    r'CopyBuffer\(([a-z_{}0-9]+),(\d+),0,1,\2\);\s*'
    r'double\s+val_\{var\}\s*=\s*\1\[0\]\s*-\s*\2\[0\];"',
    re.MULTILINE
)

def repl2(m):
    handle1 = m.group(3)
    bufnum1 = m.group(4)
    handle2 = m.group(5)
    bufnum2 = m.group(6)
    return (f'"mt5_buffer_read": "double _tmp1 = SafeCopyBuf({handle1}, {bufnum1}); '
            f'double _tmp2 = SafeCopyBuf({handle2}, {bufnum2}); '
            f'if(_tmp1 == EMPTY_VALUE || _tmp2 == EMPTY_VALUE) {{ indicatorFailed = true; val_{{var}} = 0; }} '
            f'else {{ double val_{{var}} = _tmp1 - _tmp2; }}"')

content = pattern2.sub(repl2, content)
count2 = len(pattern2.findall(original_content))
total_changes += count2

# Pattern 3: Multiline mt5_buffer_read with parentheses (Momentum-style)
# "mt5_buffer_read": (
#     "double buf_mom_{tf}_{p}[1]; CopyBuffer(...); "
#     "double val_{var} = (buf_...[0] - 100.0);  "
pattern3 = re.compile(
    r'"mt5_buffer_read":\s*\(\s*\n'
    r'\s*"double\s+([a-z_{}0-9]+)\[1\];\s*CopyBuffer\(([a-z_{}0-9]+),(\d+),0,1,\1\);\s*"\s*\n'
    r'\s*"double\s+val_\{var\}\s*=\s*\(\1\[0\]\s*-\s*([0-9.]+)\);',
    re.MULTILINE
)

def repl3(m):
    buf_name = m.group(1)
    handle = m.group(2)
    bufnum = m.group(3)
    offset = m.group(4)
    return (f'"mt5_buffer_read": (\n'
            f'            "double _tmp = SafeCopyBuf({handle}, {bufnum}); "\n'
            f'            "if(_tmp == EMPTY_VALUE) {{ indicatorFailed = true; val_{{var}} = 0; }} "\n'
            f'            "else {{ double val_{{var}} = (_tmp - {offset});')

content = pattern3.sub(repl3, content)
count3 = len(pattern3.findall(original_content))
total_changes += count3

# Pattern 4: Multiline EMA distance style with calculations
pattern4 = re.compile(
    r'"mt5_buffer_read":\s*\(\s*\n'
    r'\s*"double\s+([a-z_{}0-9]+)\[1\];\s*CopyBuffer\(([a-z_{}0-9]+),(\d+),0,1,\1\);\s*"\s*\n'
    r'\s*"double\s+([a-z_{}0-9]+)\s*=\s*\1\[0\];\s*"\s*\n'
    r'\s*"double\s+([a-z_{}0-9]+)\s*=\s*([^;]+);\s*"\s*\n'
    r'\s*"double\s+val_\{var\}\s*=\s*([^"]+)"',
    re.MULTILINE
)

def repl4(m):
    buf_name = m.group(1)
    handle = m.group(2)
    bufnum = m.group(3)
    tmp_var = m.group(4)
    close_var = m.group(5)
    close_expr = m.group(6)
    val_expr = m.group(7)
    return (f'"mt5_buffer_read": (\n'
            f'            "double _tmp_buf = SafeCopyBuf({handle}, {bufnum}); "\n'
            f'            "if(_tmp_buf == EMPTY_VALUE) {{ indicatorFailed = true; val_{{var}} = 0; }} "\n'
            f'            "else {{ double {tmp_var} = _tmp_buf; "\n'
            f'            "double {close_var} = {close_expr}; "\n'
            f'            "double val_{{var}} = {val_expr} }}"')

content = pattern4.sub(repl4, content)
count4 = len(pattern4.findall(original_content))
total_changes += count4

# Pattern 5: Multiline with two EMA buffers (EMA crossover)
pattern5 = re.compile(
    r'"mt5_buffer_read":\s*\(\s*\n'
    r'\s*"double\s+([a-z_{}0-9]+)\[1\];\s*CopyBuffer\(([a-z_{}0-9]+),(\d+),0,1,\1\);\s*"\s*\n'
    r'\s*"double\s+([a-z_{}0-9]+)\[1\];\s*CopyBuffer\(([a-z_{}0-9]+),(\d+),0,1,\4\);\s*"\s*\n'
    r'\s*"double\s+val_\{var\}\s*=\s*\1\[0\]\s*-\s*\4\[0\];"',
    re.MULTILINE
)

def repl5(m):
    buf1 = m.group(1)
    handle1 = m.group(2)
    bufnum1 = m.group(3)
    buf2 = m.group(4)
    handle2 = m.group(5)
    bufnum2 = m.group(6)
    return (f'"mt5_buffer_read": (\n'
            f'            "double _tmp1 = SafeCopyBuf({handle1}, {bufnum1}); "\n'
            f'            "double _tmp2 = SafeCopyBuf({handle2}, {bufnum2}); "\n'
            f'            "if(_tmp1 == EMPTY_VALUE || _tmp2 == EMPTY_VALUE) {{ indicatorFailed = true; val_{{var}} = 0; }} "\n'
            f'            "else {{ double val_{{var}} = _tmp1 - _tmp2; }}"')

content = pattern5.sub(repl5, content)
count5 = len(pattern5.findall(original_content))
total_changes += count5

# Pattern 6: Multiline Keltner Channel (two buffers with calculation)
pattern6 = re.compile(
    r'"mt5_buffer_read":\s*\(\s*\n'
    r'\s*"double\s+([a-z_{}0-9]+)\[1\];\s*CopyBuffer\(([a-z_{}0-9]+),(\d+),0,1,\1\);\s*"\s*\n'
    r'\s*"double\s+([a-z_{}0-9]+)\[1\];\s*CopyBuffer\(([a-z_{}0-9]+),(\d+),0,1,\4\);\s*"\s*\n'
    r'\s*"double\s+([a-z_{}0-9]+)\s*=\s*\1\[0\];\s*"\s*\n'
    r'\s*"double\s+([a-z_{}0-9]+)\s*=\s*\4\[0\];\s*"\s*\n'
    r'\s*"double\s+([a-z_{}0-9]+)\s*=\s*([^;]+);\s*"\s*\n'
    r'\s*"double\s+([a-z_{}0-9]+)\s*=\s*([^;]+);\s*"\s*\n'
    r'\s*"double\s+val_\{var\}\s*=\s*([^"]+)"',
    re.MULTILINE
)

def repl6(m):
    buf1 = m.group(1)
    handle1 = m.group(2)
    bufnum1 = m.group(3)
    buf2 = m.group(4)
    handle2 = m.group(5)
    bufnum2 = m.group(6)
    # Reconstruct the calculation
    return (f'"mt5_buffer_read": (\n'
            f'            "double _tmp_ema = SafeCopyBuf({handle1}, {bufnum1}); "\n'
            f'            "double _tmp_atr = SafeCopyBuf({handle2}, {bufnum2}); "\n'
            f'            "if(_tmp_ema == EMPTY_VALUE || _tmp_atr == EMPTY_VALUE) {{ indicatorFailed = true; val_{{var}} = 0; }} "\n'
            f'            "else {{ double {m.group(7)} = _tmp_ema; "\n'
            f'            "double {m.group(8)} = _tmp_atr; "\n'
            f'            "double {m.group(9)} = {m.group(10)}; "\n'
            f'            "double {m.group(11)} = {m.group(12)}; "\n'
            f'            "double val_{{var}} = {m.group(13)} }}"')

content = pattern6.sub(repl6, content)
count6 = len(pattern6.findall(original_content))
total_changes += count6

# Check for remaining CopyBuffer instances
remaining_copybuffer = re.findall(r'CopyBuffer\(', content)
count_remaining = len(remaining_copybuffer)

print(f"Converted {count1} simple single-line CopyBuffer patterns")
print(f"Converted {count2} double single-line CopyBuffer patterns")
print(f"Converted {count3} multiline Momentum-style patterns")
print(f"Converted {count4} multiline EMA distance patterns")
print(f"Converted {count5} multiline EMA crossover patterns")
print(f"Converted {count6} multiline Keltner Channel patterns")
print(f"Total conversions: {total_changes}")
print(f"Remaining CopyBuffer instances: {count_remaining}")

if count_remaining > 0:
    print("\nRemaining CopyBuffer instances (may be in helper functions, not mt5_buffer_read):")
    for i, line in enumerate(content.split('\n'), 1):
        if 'CopyBuffer(' in line:
            print(f"  Line {i}: {line.strip()[:120]}")

with open(PATH, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\nUpdated {PATH}")
