# Plan: Sticky Header Menu

## Goal
Make the navigation menu stick to the top of the page while scrolling.

## Current State
- Base template: `/home/qrv/workspace/GPU-Rig-Monitoring-Platform/gpu_monitor/templates/base.html`
- Nav element at lines 25-69 (inside `{% if user.is_authenticated %}`)
- Nav classes: `bg-gray-800 border-b border-gray-700 px-4 md:px-6 py-3`
- Main content at line 71: `<main class="p-6">`

## Required Changes
1. **Add sticky positioning to nav**: Add `sticky top-0 z-50` classes to the `<nav>` element
2. **Optional - Add backdrop blur**: Add `backdrop-blur-sm` for visual polish when content scrolls under header

## Implementation Details

### Nav Element (line 25)
```html
<!-- Before -->
<nav class="bg-gray-800 border-b border-gray-700 px-4 md:px-6 py-3">

<!-- After -->
<nav class="bg-gray-800 border-b border-gray-700 px-4 md:px-6 py-3 sticky top-0 z-50">
```

The `sticky top-0` makes the nav stick to the top of its scrolling container (the viewport). The `z-50` ensures it stays above other content when scrolling.

### No changes needed to main content
- The main element already has `p-6` padding which provides space below the header
- With `sticky` positioning, the header remains in flow until it hits the top, then stays there

## Testing
1. Load a page with enough content to scroll (e.g., rig list with many rigs)
2. Scroll down - header should remain fixed at top
3. Verify mobile menu still works correctly
4. Verify dropdown menu appears above content (z-50 handles this)

## Branch
`plan/sticky-header-menu` (created)