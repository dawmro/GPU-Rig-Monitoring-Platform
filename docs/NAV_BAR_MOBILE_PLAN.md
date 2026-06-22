# Mobile Nav Bar — Implementation Plan

## Problem
On screens <768px, the nav bar is a single flex row:
- Left: Logo + Fleet + Tags + API Keys + Transfer Keys (staff)
- Right: Email + Logout

The right side gets pushed off-screen on narrow devices, making logout invisible.

## Solution: Hamburger Menu for Mobile

### Desktop (≥768px):
Keep current layout — all links visible in a single row.

### Mobile (<768px):
- Left: Logo ("🖥️ GPU Rig Monitor") — always visible
- Right: User email (truncated) + hamburger icon (☰)
- Clicking hamburger expands a dropdown with: Fleet, Tags, API Keys, Transfer Keys (staff), Logout
- Click outside or press Escape to close

### Implementation Details:

**HTML Structure:**
```html
<nav class="bg-gray-800 border-b border-gray-700 px-4 md:px-6 py-3">
    <!-- Desktop nav (hidden on mobile) -->
    <div class="hidden md:flex items-center justify-between">
        <!-- Current desktop layout unchanged -->
    </div>
    
    <!-- Mobile nav (hidden on desktop) -->
    <div class="flex md:hidden items-center justify-between">
        <a href="..." class="text-lg font-bold">🖥️ GPU Rig Monitor</a>
        <div class="flex items-center gap-3">
            <span class="text-gray-400 text-sm truncate max-w-[100px]">{{ user.email }}</span>
            <button id="mobile-menu-btn" class="text-gray-400 hover:text-white p-1">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/>
                </svg>
            </button>
        </div>
    </div>
    
    <!-- Mobile dropdown menu -->
    <div id="mobile-menu" class="hidden md:hidden mt-3 pb-3 border-t border-gray-700 pt-3">
        <div class="flex flex-col gap-2">
            <a href="..." class="text-gray-400 hover:text-white px-2 py-1">Fleet</a>
            <a href="..." class="text-gray-400 hover:text-white px-2 py-1">Tags</a>
            <a href="..." class="text-gray-400 hover:text-white px-2 py-1">API Keys</a>
            {% if user.is_staff %}
            <a href="..." class="text-gray-400 hover:text-white px-2 py-1">Transfer Keys</a>
            {% endif %}
            <hr class="border-gray-700 my-1">
            <a href="..." class="text-red-400 hover:text-red-300 px-2 py-1">Logout</a>
        </div>
    </div>
</nav>
```

**JavaScript (vanilla, no dependencies):**
```javascript
document.getElementById('mobile-menu-btn').addEventListener('click', function() {
    document.getElementById('mobile-menu').classList.toggle('hidden');
});

// Close on click outside
document.addEventListener('click', function(e) {
    var menu = document.getElementById('mobile-menu');
    var btn = document.getElementById('mobile-menu-btn');
    if (!menu.contains(e.target) && !btn.contains(e.target)) {
        menu.classList.add('hidden');
    }
});

// Close on Escape
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        document.getElementById('mobile-menu').classList.add('hidden');
    }
});
```

### Edge Cases:
1. **Staff users** — Transfer Keys link shown conditionally in mobile menu
2. **Email truncation** — `truncate max-w-[100px]` prevents long emails from breaking layout
3. **HTMX compatibility** — Menu is pure HTML/JS, no HTMX interference
4. **Click outside to close** — Standard UX pattern
5. **Escape to close** — Accessibility
6. **Active page highlighting** — Can be added later if needed
7. **No page reload** — All links work normally after menu closes
8. **Menu state on navigation** — Menu auto-closes when user clicks a link (page reloads)

### Pros:
- ✅ Standard mobile pattern users recognize
- ✅ All links accessible
- ✅ Logout always visible in menu
- ✅ No external dependencies
- ✅ Works with HTMX
- ✅ Desktop layout unchanged

### Cons:
- ⚠️ Requires one-time JS (but vanilla, no library needed)
- ⚠️ One extra click to access nav links on mobile (standard trade-off)
