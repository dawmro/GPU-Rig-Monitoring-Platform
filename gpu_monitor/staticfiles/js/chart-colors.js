/* =====================================================================
 * chart-colors.js — Centralized chart color palettes
 * =====================================================================
 *
 * Phase 0.4 JS extraction: this file consolidates 4 separate color
 * palettes that were defined inline in different chart loaders:
 *
 *   - GPU_COLORS (16 colors for multi-GPU/multi-device charts)
 *   - loadavg colors (3 colors for CPU load avg: 1m/5m/15m)
 *   - mem colors (3 colors for memory chart: used/free/swap)
 *   - network colors (3 colors for network combined: RX/TX/Errors)
 *
 * The original code duplicated inline color objects in each loader.
 * Consolidating them here makes it possible to tweak the palette
 * in one place (e.g. to add a "dark mode" or improve accessibility)
 * and exposes them as named exports for tests.
 *
 * Exposes (via window.GRM.ChartColors):
 *   - GPU_COLORS: 16-color palette for distinct series (multi-GPU, etc.)
 *   - LOADAVG: 3-color palette for CPU load average
 *   - MEM: 3-color palette for memory chart (used/free/swap)
 *   - NETWORK: 3-color palette for network combined (RX/TX/Errors)
 * ===================================================================== */

(function () {
    'use strict';

    // 16-color palette for distinguishing many series (multi-GPU,
    // multi-disk, multi-interface). Each entry is {border, bg} where
    // border is opaque and bg is the same color with 15% alpha.
    var GPU_COLORS = [
        { border: 'rgba(59, 130, 246, 0.9)',  bg: 'rgba(59, 130, 246, 0.15)' },  // blue
        { border: 'rgba(16, 185, 129, 0.9)',  bg: 'rgba(16, 185, 129, 0.15)' },  // green
        { border: 'rgba(245, 158, 11, 0.9)',  bg: 'rgba(245, 158, 11, 0.15)' },  // amber
        { border: 'rgba(239, 68, 68, 0.9)',   bg: 'rgba(239, 68, 68, 0.15)' },   // red
        { border: 'rgba(168, 85, 247, 0.9)',  bg: 'rgba(168, 85, 247, 0.15)' },  // purple
        { border: 'rgba(14, 165, 233, 0.9)',  bg: 'rgba(14, 165, 233, 0.15)' },  // sky
        { border: 'rgb(236, 72, 153, 0.9)',   bg: 'rgb(236, 72, 153, 0.15)' },   // pink
        { border: 'rgb(251, 146, 60, 0.9)',   bg: 'rgb(251, 146, 60, 0.15)' },   // orange
        { border: 'rgb(20, 184, 166, 0.9)',   bg: 'rgb(20, 184, 166, 0.15)' },   // teal
        { border: 'rgb(139, 92, 246, 0.9)',   bg: 'rgb(139, 92, 246, 0.15)' },   // violet
        { border: 'rgb(244, 114, 182, 0.9)',  bg: 'rgb(244, 114, 182, 0.15)' },  // rose
        { border: 'rgb(250, 204, 21, 0.9)',   bg: 'rgb(250, 204, 21, 0.15)' },   // yellow
        { border: 'rgb(45, 212, 191, 0.9)',   bg: 'rgb(45, 212, 191, 0.15)' },   // cyan
        { border: 'rgb(124, 58, 237, 0.9)',   bg: 'rgb(124, 58, 237, 0.15)' },   // indigo
        { border: 'rgb(217, 70, 239, 0.9)',   bg: 'rgb(217, 70, 239, 0.15)' },   // fuchsia
        { border: 'rgb(8, 145, 178, 0.9)',    bg: 'rgb(8, 145, 178, 0.15)' },    // dark cyan
    ];

    // 3-color palette for CPU load average (1m, 5m, 15m series).
    // Green → amber → red: matches the intuition that higher load is
    // more concerning.
    var LOADAVG = [
        { border: 'rgba(16, 185, 129, 0.8)',  bg: 'rgba(16, 185, 129, 0.15)' },  // 1m
        { border: 'rgba(245, 158, 11, 0.8)',  bg: 'rgba(245, 158, 11, 0.15)' },  // 5m
        { border: 'rgba(239, 68, 68, 0.8)',   bg: 'rgba(239, 68, 68, 0.15)' },   // 15m
    ];

    // 3-color palette for memory chart (used / free / swap).
    var MEM = [
        { border: 'rgba(14, 165, 233, 0.9)',  bg: 'rgba(14, 165, 233, 0.15)' },  // used - blue
        { border: 'rgba(34, 197, 94, 0.9)',   bg: 'rgba(34, 197, 94, 0.1)'  },   // free - green
        { border: 'rgba(239, 68, 68, 0.9)',   bg: 'rgba(239, 68, 68, 0.1)'  },   // swap - red
    ];

    // 3-color palette for network combined (RX / TX / Errors).
    // RX and TX are on the left Y-axis; Errors on the right.
    var NETWORK = [
        { border: 'rgba(34, 197, 94, 0.9)',   bg: 'rgba(34, 197, 94, 0.1)' },   // RX - green
        { border: 'rgba(14, 165, 233, 0.9)',  bg: 'rgba(14, 165, 233, 0.1)' },  // TX - blue
        { border: 'rgba(239, 68, 68, 0.9)',   bg: 'rgba(239, 68, 68, 0.5)' },  // Errors - red
    ];

    // Get a color by index, cycling through the palette if the index
    // exceeds the palette length. Used by multi-GPU / multi-disk charts
    // where the number of series is not known ahead of time.
    function colorAt(palette, index) {
        return palette[index % palette.length];
    }

    // Public API
    window.GRM = window.GRM || {};
    window.GRM.ChartColors = {
        GPU_COLORS: GPU_COLORS,
        LOADAVG: LOADAVG,
        MEM: MEM,
        NETWORK: NETWORK,
        colorAt: colorAt,
    };
})();
