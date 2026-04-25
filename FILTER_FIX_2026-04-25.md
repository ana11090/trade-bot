# Saved Rules Panel - Profitable Filter Applied

## Summary
Added a "Show only profitable rules" filter to the Saved Rules panel in Project 2, **defaulting to ON** to show only viable strategies by default.

---

## File Modified
✅ `project2_backtesting/panels/saved_rules_panel.py`

---

## Changes Made

### 1. Added Global Filter Variable
**Location:** Line 19

```python
_filter_profitable = None  # BooleanVar for "Show only profitable" filter
```

### 2. Added Filter UI Controls
**Location:** Lines 127-146

**Added:**
- Filter frame with light gray background
- "🔍 Filters:" label
- Checkbox: "Show only profitable rules"
- **Default state: CHECKED (value=True)**
- Auto-refresh on checkbox toggle

```python
_filter_profitable = tk.BooleanVar(value=True)  # Default: show only profitable

filter_frame = tk.Frame(inner, bg="#f8f9fa", padx=10, pady=8)
filter_frame.pack(fill="x", padx=20, pady=(5, 0))

tk.Label(filter_frame, text="🔍 Filters:", ...)

tk.Checkbutton(filter_frame, text="Show only profitable rules",
               variable=_filter_profitable,
               command=lambda: _refresh_list(inner, canvas, window_id),
               ...)
```

### 3. Added Filter Logic in _refresh_list()
**Location:** Lines 179-204

**Filter Criteria - A rule is considered PROFITABLE if ANY of these are true:**
- `total_pips > 0` (positive pip profit)
- `net_total_pips > 0` (positive net pip profit after costs)
- `net_profit_factor > 1.0` (wins are larger than losses)

```python
if _filter_profitable and _filter_profitable.get():
    filtered_entries = []
    for entry in all_entries:
        rule = entry.get('rule', {})
        total_pips = rule.get('total_pips', 0) or 0
        net_total_pips = rule.get('net_total_pips', 0) or 0
        profit_factor = rule.get('net_profit_factor', 0) or 0

        is_profitable = (
            total_pips > 0 or
            net_total_pips > 0 or
            profit_factor > 1.0
        )

        if is_profitable:
            filtered_entries.append(entry)

    all_entries = filtered_entries
```

### 4. Updated Count Display
**Location:** Lines 206-213

Shows filtered count vs total count when filter is active:
- **Filter ON:** "5 of 20 rules (profitable only)"
- **Filter OFF:** "20 saved rules"

### 5. Added Empty Filter Message
**Location:** Lines 215-221

When filter is ON but no profitable rules exist:
```
No profitable rules found.

Uncheck the filter to see all 20 rules.
```

---

## Visual Design

### Filter Bar
- Background: Light gray (`#f8f9fa`)
- Icon: 🔍 (magnifying glass)
- Checkbox with hover cursor
- Positioned between action buttons and rule cards

### Filter Position
```
[Action Buttons: Refresh | Use Selected | Delete All | Clean Up Stale]
┌─────────────────────────────────────────────────────────┐
│ 🔍 Filters:  ☑ Show only profitable rules              │
└─────────────────────────────────────────────────────────┘
[Rule Count: 5 of 20 rules (profitable only)]
[Rule Cards...]
```

---

## User Experience

### On Panel Load
1. ✅ Checkbox is **CHECKED** by default
2. ✅ Only profitable rules are shown
3. ✅ Count shows: "X of Y rules (profitable only)"

### User Actions
- **Uncheck:** Shows ALL rules (profitable + unprofitable)
- **Check:** Filters to show only profitable rules
- **Auto-refresh:** List updates immediately on toggle

### Benefits
- ✅ Focus on viable strategies first
- ✅ Reduces clutter from failed experiments
- ✅ Easy to toggle if user wants to see everything
- ✅ Clear feedback on filtered count

---

## Definition of "Profitable"

A rule is profitable if **ANY** of these conditions are met:

1. **Total Pips > 0**
   - Raw pip profit (before costs)
   - Example: `total_pips: 1250`

2. **Net Total Pips > 0**
   - Pip profit after spread/commission
   - Example: `net_total_pips: 987`

3. **Profit Factor > 1.0**
   - Total wins / Total losses
   - Example: `net_profit_factor: 1.85`
   - Means wins are 85% larger than losses

**Why use OR logic?**
- Some rules may have `total_pips` set but not `net_total_pips`
- Some may have `profit_factor` but pips not calculated yet
- Catches all variations of profitable strategies

---

## Testing Checklist

### Test 1: Default Behavior
- [ ] Open Saved Rules panel
- [ ] Filter checkbox should be **CHECKED**
- [ ] Only profitable rules should be visible
- [ ] Count should show "X of Y rules (profitable only)"

### Test 2: Toggle Filter
- [ ] Uncheck filter
- [ ] All rules should appear
- [ ] Count should show "Y saved rules"
- [ ] Re-check filter
- [ ] Only profitable rules should appear again

### Test 3: No Profitable Rules
- [ ] If no profitable rules exist
- [ ] Should show message: "No profitable rules found. Uncheck to see all X rules."

### Test 4: All Rules Profitable
- [ ] If all rules are profitable
- [ ] Count should show "Y of Y rules (profitable only)"
- [ ] Same rules visible when filter is toggled off

---

## Example Scenarios

### Scenario A: User has 20 saved rules
- 12 profitable (positive pips or PF > 1.0)
- 8 unprofitable (negative pips and PF < 1.0)

**Panel loads:**
- ✅ Shows 12 profitable rules
- Display: "12 of 20 rules (profitable only)"
- Filter checkbox: CHECKED

**User unchecks filter:**
- Shows all 20 rules
- Display: "20 saved rules"

### Scenario B: User has 5 saved rules, none profitable
**Panel loads:**
- Message: "No profitable rules found. Uncheck filter to see all 5 rules."
- Filter checkbox: CHECKED

**User unchecks filter:**
- Shows all 5 rules
- Display: "5 saved rules"

---

## Code Quality

### WHY Comments Added
✅ Line 127-131: Explains default filter behavior
✅ Line 179-184: Documents profitable criteria
✅ Line 184: Documents change date

### Defensive Programming
- Checks `if _filter_profitable and _filter_profitable.get()` (handles None case)
- Uses `or 0` for numeric fields (handles None/empty values)
- Preserves original `all_entries` reference for count display

### Performance
- Filter runs in memory (no file I/O)
- O(n) complexity for filtering
- Instant UI update on toggle

---

## Backward Compatibility

✅ **No breaking changes**
- Existing saved rules work as-is
- Filter is additive (doesn't modify data)
- User can disable filter to see all rules

---

**Applied By:** Claude Code
**Date:** April 25, 2026
**Panel:** Project 2 → Saved Rules
**Default Filter State:** ON (show only profitable)
