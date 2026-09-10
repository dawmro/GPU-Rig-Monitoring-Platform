/* =====================================================================
 * rig-detail.js — Rig detail page interactions
 * =====================================================================
 *
 * Phase 0.4 JS extraction: handles the page-level UI interactions
 * for the rig detail view (base template: dashboard/rig_detail.html).
 * These were previously inline at the bottom of rig_detail.html.
 *
 * Responsibilities:
 *   - Tab switching (Metrics / Charts / Containers / Errors / Report)
 *   - Report range selection (24h / 7d / 30d)
 *   - Inline rename form toggle
 *   - Delete confirmation modal (show/hide, Esc to close, click outside)
 *   - Lazy-load charts when Charts tab is first opened
 *   - Lazy-load report data when Report tab is first opened
 *
 * Depends on:
 *   - htmx (loaded via CDN in base.html)
 *   - window.GRM.ChartRuntime (for chart loading)
 *   - window.GRM.RigState (set by the page, e.g. { rigUuid: '...' })
 *
 * Exposes (via window.RD = window.RD || ...):
 *   - switchTab, selectReportRange, toggleRenameForm,
 *     showDeleteModal, hideDeleteModal, setChartRange
 *
 * The function names are kept short and global so the inline
 * `onclick="..."` attributes in the template don't need to change.
 * ===================================================================== */

(function () {
    'use strict';

    var Runtime = window.GRM.ChartRuntime;

    // ---------------------------------------------------------------
    // Tab switching
    // ---------------------------------------------------------------
    function switchTab(tabName) {
        // Hide all tab contents
        document.querySelectorAll('.tab-content').forEach(function (el) {
            el.classList.add('hidden');
        });
        // Reset all tab buttons
        document.querySelectorAll('.tab-btn').forEach(function (el) {
            el.classList.remove('border-blue-400', 'grm-text-blue');
            el.classList.add('border-transparent', 'grm-text-gray');
        });
        // Show the selected tab
        var content = document.getElementById('tab-content-' + tabName);
        if (content) content.classList.remove('hidden');
        var btn = document.getElementById('tab-' + tabName);
        if (btn) {
            btn.classList.remove('border-transparent', 'grm-text-gray');
            btn.classList.add('border-blue-400', 'grm-text-blue');
        }

        // Lazy-load charts when Charts tab is first opened
        if (tabName === 'charts' && !Runtime.chartsLoaded) {
            Runtime.chartsLoaded = true;
            Runtime.loadCharts(window.GRM.RigState.rigUuid);
        }
        // Load report data when Report tab is first opened
        if (tabName === 'report') {
            selectReportRange(24);
            if (!window.GRM.RigState.reportLoaded) {
                window.GRM.RigState.reportLoaded = true;
                htmx.ajax(
                    'GET',
                    window.GRM.RigState.reportUrl,
                    '#report-table-container'
                );
            }
        }
        // Show/hide the chart timeframe button row
        var tf = document.getElementById('chart-timeframe');
        if (tf) {
            if (tabName === 'charts') {
                tf.classList.remove('hidden');
            } else {
                tf.classList.add('hidden');
            }
        }
    }

    // ---------------------------------------------------------------
    // Report range selection
    // ---------------------------------------------------------------
    function selectReportRange(hours) {
        var ranges = [24, 168, 720];
        for (var i = 0; i < ranges.length; i++) {
            var btn = document.getElementById('report-range-' + ranges[i]);
            if (!btn) continue;
            if (ranges[i] === hours) {
                btn.classList.remove('bg-gray-700', 'grm-text-gray', 'hover:bg-gray-600');
                btn.classList.add('bg-blue-600', 'text-white');
            } else {
                btn.classList.remove('bg-blue-600', 'text-white');
                btn.classList.add('bg-gray-700', 'grm-text-gray', 'hover:bg-gray-600');
            }
        }
    }

    // ---------------------------------------------------------------
    // Inline rename form
    // ---------------------------------------------------------------
    function toggleRenameForm() {
        var form = document.getElementById('rig-rename-form');
        var text = document.getElementById('rig-name-text');
        var btn = document.getElementById('rig-rename-btn');
        if (form.classList.contains('hidden')) {
            form.classList.remove('hidden');
            text.classList.add('opacity-50');
            btn.classList.add('hidden');
            form.querySelector('input').focus();
        } else {
            form.classList.add('hidden');
            text.classList.remove('opacity-50');
            btn.classList.remove('hidden');
        }
    }

    // After HTMX swaps the rig name back, hide the form.
    // The user submits a rename, HTMX swaps the name span with the
    // updated one, and we close the form.
    document.body.addEventListener('htmx:afterSettle', function (evt) {
        if (evt.detail.target && evt.detail.target.id === 'rig-name-text') {
            var form = document.getElementById('rig-rename-form');
            if (form && !form.classList.contains('hidden')) {
                toggleRenameForm();
            }
        }
    });

    // ---------------------------------------------------------------
    // Delete confirmation modal
    // ---------------------------------------------------------------
    function showDeleteModal() {
        var modal = document.getElementById('delete-modal');
        modal.classList.remove('hidden');
        modal.classList.add('flex');
        document.addEventListener('keydown', deleteModalEscHandler);
    }

    function hideDeleteModal() {
        var modal = document.getElementById('delete-modal');
        modal.classList.add('hidden');
        modal.classList.remove('flex');
        document.removeEventListener('keydown', deleteModalEscHandler);
    }

    function deleteModalEscHandler(evt) {
        if (evt.key === 'Escape') {
            hideDeleteModal();
        }
    }

    // Close modal when clicking outside (on the backdrop, not the
    // modal content). Set up at DOMContentLoaded to ensure the modal
    // element exists in the DOM by then.
    document.addEventListener('DOMContentLoaded', function () {
        var modal = document.getElementById('delete-modal');
        if (modal) {
            modal.addEventListener('click', function (evt) {
                if (evt.target === this) {
                    hideDeleteModal();
                }
            });
        }
    });

    // Chart range button wrapper. Calls the runtime with the new range.
    // Kept as a thin wrapper so the inline onclick="setChartRange(...)"
    // attributes in the template don't need to change.
    function setChartRange(rangeHours, bucketMinutes) {
        if (Runtime && Runtime.setChartRange) {
            Runtime.setChartRange(rangeHours, bucketMinutes);
        } else {
            console.error('ChartRuntime not loaded — setChartRange not available');
        }
    }

    // Public API. Names are kept short to match the existing inline
    // onclick handlers in rig_detail.html (which we don't need to
    // change because we leave the attribute syntax as-is — the global
    // function name is the same).
    window.RD = {
        switchTab: switchTab,
        selectReportRange: selectReportRange,
        toggleRenameForm: toggleRenameForm,
        showDeleteModal: showDeleteModal,
        hideDeleteModal: hideDeleteModal,
        setChartRange: setChartRange,
    };
    // Also expose the functions directly on window for the inline
    // onclick="..." attributes to find them. The same references,
    // just two paths.
    window.switchTab = switchTab;
    window.selectReportRange = selectReportRange;
    window.toggleRenameForm = toggleRenameForm;
    window.showDeleteModal = showDeleteModal;
    window.hideDeleteModal = hideDeleteModal;
    window.setChartRange = setChartRange;
})();
