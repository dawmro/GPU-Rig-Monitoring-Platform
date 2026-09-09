/* =====================================================================
 * app-base.js — Page utilities loaded on every page
 * =====================================================================
 *
 * Phase 0.4 JS extraction: holds the small utilities that were inline
 * in base.html. These apply to every page (clocks, mobile menu, email
 * toggle), not just the rig detail view.
 *
 * Functions:
 *   - initClocks(): writes "Loaded @ HH:MM:SS" to known clock elements
 *   - updateClocksOnHtmxSwap(): updates the clocks to "Refreshed @ ..." on
 *     any HTMX swap (so the user knows when the page last refreshed)
 *   - toggleAllOwnerEmails(): fleet overview's "show/hide emails" button
 *   - initMobileMenu(): hamburger menu on small screens
 *
 * These were inline in base.html (90+ lines). Centralizing them:
 *   - Makes base.html readable again
 *   - Allows the system check to verify these files exist
 *   - Makes the mobile menu logic testable
 *
 * Depends on:
 *   - htmx (loaded via CDN in base.html) — for the swap listener
 *
 * Exposes: window.GRM.AppBase with init() that wires everything up.
 * ===================================================================== */

(function () {
    'use strict';

    // IDs of elements that show the loaded/refreshed timestamp.
    var CLOCK_IDS = [
        'global-refresh-clock',           // Rig detail header
        'rig-table-container-clock',      // Fleet Overview table
    ];

    // Format a Date as "HH:MM:SS" (local time, zero-padded).
    function formatTime(d) {
        return String(d.getHours()).padStart(2, '0') + ':' +
               String(d.getMinutes()).padStart(2, '0') + ':' +
               String(d.getSeconds()).padStart(2, '0');
    }

    // Write "Loaded @ HH:MM:SS" to all known clock elements.
    function initClocks() {
        var text = 'Loaded @ ' + formatTime(new Date());
        CLOCK_IDS.forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.textContent = text;
        });
    }

    // Update clocks to "Refreshed @ ..." on any HTMX swap.
    // This tells the user when the page was last auto-updated.
    function updateClocksOnHtmxSwap() {
        document.body.addEventListener('htmx:afterSwap', function (evt) {
            var targetId = evt.detail && evt.detail.target && evt.detail.target.id;
            if (!targetId) return;
            var text = 'Refreshed @ ' + formatTime(new Date());
            CLOCK_IDS.forEach(function (id) {
                var el = document.getElementById(id);
                if (el) el.textContent = text;
            });
        });
    }

    // Fleet overview's "show/hide emails" button. Toggles visibility of
    // .owner-safe vs .owner-email within each .owner-row.
    function toggleAllOwnerEmails() {
        var btn = document.getElementById('toggle-owner-emails');
        if (!btn) return;
        var showEmails = btn.querySelector('span').textContent === 'Show emails';
        document.querySelectorAll('.owner-row').forEach(function (row) {
            var safe = row.querySelector('.owner-safe');
            var email = row.querySelector('.owner-email');
            if (showEmails) {
                if (safe) safe.classList.add('hidden');
                if (email) email.classList.remove('hidden');
            } else {
                if (safe) safe.classList.remove('hidden');
                if (email) email.classList.add('hidden');
            }
        });
        btn.querySelector('span').textContent = showEmails ? 'Hide emails' : 'Show emails';
    }

    // Mobile hamburger menu: open on tap, close on outside click or
    // Escape or any link click (since the link will navigate).
    function initMobileMenu() {
        var btn = document.getElementById('mobile-menu-btn');
        var menu = document.getElementById('mobile-menu');
        if (!btn || !menu) return;
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            menu.classList.toggle('hidden');
        });
        document.addEventListener('click', function (e) {
            if (!menu.classList.contains('hidden') &&
                !menu.contains(e.target) &&
                !btn.contains(e.target)) {
                menu.classList.add('hidden');
            }
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && !menu.classList.contains('hidden')) {
                menu.classList.add('hidden');
            }
        });
        menu.querySelectorAll('a').forEach(function (link) {
            link.addEventListener('click', function () {
                menu.classList.add('hidden');
            });
        });
    }

    // Wire everything up at DOMContentLoaded.
    function init() {
        document.addEventListener('DOMContentLoaded', function () {
            initClocks();
            initMobileMenu();
        });
        // The HTMX swap listener must be set up before any swap, so
        // we set it up immediately (not on DOMContentLoaded).
        updateClocksOnHtmxSwap();
        // Expose toggleAllOwnerEmails globally so the inline onclick=
        // on the fleet overview button can call it.
        window.toggleAllOwnerEmails = toggleAllOwnerEmails;
    }

    // Public API
    window.GRM = window.GRM || {};
    window.GRM.AppBase = {
        init: init,
        // Also exposed for tests
        initClocks: initClocks,
        toggleAllOwnerEmails: toggleAllOwnerEmails,
        initMobileMenu: initMobileMenu,
        formatTime: formatTime,
    };
})();
