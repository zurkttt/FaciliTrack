/* Shared Chart.js helpers for FaciliTrack (requires chart.js 4). */
(function () {
  // Categorical palette in fixed, validated order: a series keeps its color, never cycled.
  const PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
  const INK = { primary: "#16181d", secondary: "#4a4f57", muted: "#898781", grid: "#e9eaee", axis: "#c3c2b7" };
  const FONT = '"Overpass", system-ui, -apple-system, "Segoe UI", sans-serif';

  if (window.Chart) {
    Chart.defaults.font.family = FONT;
    Chart.defaults.color = INK.muted;
  }

  const pct = (x) => (x === null || x === undefined ? "—" : Number(x).toFixed(1) + "%");

  const tooltip = {
    backgroundColor: "#ffffff", titleColor: INK.primary, bodyColor: INK.secondary, borderColor: "rgba(11,11,11,0.10)",
    borderWidth: 1, padding: 10, boxPadding: 4, usePointStyle: true,
    callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${pct(ctx.chart.options.indexAxis === "y" ? ctx.parsed.x : ctx.parsed.y)}` },
  };

  // Solid hairline reference lines; labels sit outside the plot so they never cover data.
  const thresholds = {
    id: "thresholds",
    beforeDatasetsDraw(chart, args, opts) {
      const scale = chart.scales[opts.axis];
      if (!scale || !opts.lines) return;
      const { ctx, chartArea } = chart;
      ctx.save();
      ctx.strokeStyle = INK.axis; ctx.fillStyle = INK.muted; ctx.lineWidth = 1;
      ctx.font = "11px " + FONT;
      for (const [value, label] of opts.lines) {
        const p = Math.round(scale.getPixelForValue(value)) + 0.5;
        ctx.beginPath();
        if (opts.axis === "y") {
          ctx.moveTo(chartArea.left, p); ctx.lineTo(chartArea.right, p); ctx.stroke();
          ctx.textAlign = "left"; ctx.textBaseline = "middle"; ctx.fillText(label, chartArea.right + 6, p);
        } else {
          ctx.moveTo(p, chartArea.top); ctx.lineTo(p, chartArea.bottom); ctx.stroke();
          ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.fillText(label, p, chartArea.bottom + 6);
        }
      }
      ctx.restore();
    },
  };

  // Direct label at the end of each line (used when there are 4 series or fewer).
  const endLabels = {
    id: "endLabels",
    afterDatasetsDraw(chart, args, opts) {
      if (!opts || !opts.enabled) return;
      const { ctx, chartArea } = chart;
      // Treat the threshold labels (drawn in the same right margin) as already placed, so line labels step around them.
      const th = chart.options.plugins.thresholds;
      const placed = th && th.axis === "y" && th.lines && chart.scales.y
        ? th.lines.map(([value]) => chart.scales.y.getPixelForValue(value)) : [];
      ctx.save();
      ctx.font = "600 11px " + FONT;
      ctx.fillStyle = INK.secondary;
      ctx.textBaseline = "middle";
      chart.data.datasets.forEach((ds, i) => {
        const meta = chart.getDatasetMeta(i);
        if (meta.hidden) return;
        let last = null;
        meta.data.forEach((pt, j) => { if (ds.data[j] !== null && ds.data[j] !== undefined) last = pt; });
        if (!last) return;
        let y = last.y;
        for (let moved = true, guard = 0; moved && guard < 20; guard++) {  // step until clear of every placed label
          moved = false;
          for (const other of placed) {
            if (Math.abs(other - y) < 13) { y = other + 13; moved = true; }
          }
        }
        placed.push(y);
        ctx.fillText(ds.label, Math.min(last.x + 8, chartArea.right + 6), y);
      });
      ctx.restore();
    },
  };

  window.FTCharts = { PALETTE, INK, pct, tooltip, thresholds, endLabels };
})();
