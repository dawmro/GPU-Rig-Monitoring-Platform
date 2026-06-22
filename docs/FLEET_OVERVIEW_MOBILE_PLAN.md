# Mobile UX Fix — Fleet Overview Layout Plan

## Problem
On screens <896px wide, the Fleet Overview header is a single flex row:
- Left: "Fleet Overview" + rig counts + optional staff button
- Right: Search input + Status dropdown + Tags dropdown

This causes:
1. Search/selectors pushed off right edge
2. Elements partially hidden
3. Rig table compressed from right side

## Solution
Use Tailwind responsive classes to restructure at `md:` breakpoint (768px):

**≥768px (md: and above):**
- Single line: title + counts + search/selectors (current layout)

**<768px (below md:):**
- Line 1: "Fleet Overview" + rig counts (inline)
- Line 2: Search + Status + Tags (stacked below, wrapping as needed)

## Implementation

### rig_list.html changes:
1. Change header from single `flex items-center justify-between` to responsive layout
2. Use `flex-col md:flex-row` for the main container
3. Line 1: `flex items-center gap-4` with title + counts
4. Line 2: `flex flex-wrap items-center gap-2 md:gap-3` with search/selectors
5. On small screens, search input uses `flex-1 min-w-[120px]` to fill space
6. Dropdowns use `flex-shrink-0` to prevent shrinking

### Edge Cases:
1. **Staff "Show emails" button** — stays on line 1 with title/counts
2. **HTMX triggers** — unchanged, form still works
3. **Table scroll** — `overflow-x-auto` already handles this
4. **Very small screens** — search + dropdowns wrap to multiple lines naturally with `flex-wrap`
5. **Empty state** — unchanged
6. **Rig counts** — keep inline on line 1, not moved

### Tailwind classes used:
- `md:flex-row` — horizontal on medium+, vertical on small
- `flex-col` — stack vertically on small
- `flex-wrap` — allow wrapping on very small screens
- `flex-1` — search input fills available width
- `min-w-[120px]` — prevent search from collapsing too small
- `flex-shrink-0` — prevent dropdowns from shrinking
- `gap-2 md:gap-3` — tighter gap on small screens
