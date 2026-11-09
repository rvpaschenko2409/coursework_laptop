(() => {
  const target = document.getElementById("plotly-ranks");
  if (!target) return;

  target.addEventListener("htmx:afterSwap", async () => {
    const chart = target.querySelector("[data-rank-figure]");
    const status = target.querySelector("[data-rank-status]");
    if (!chart || !status) return;

    try {
      if (!window.Plotly) {
        await new Promise((resolve, reject) => {
          const script = document.createElement("script");
          script.src = chart.dataset.plotlySrc;
          script.onload = resolve;
          script.onerror = reject;
          document.head.append(script);
        });
      }
      const figure = JSON.parse(chart.dataset.rankFigure);
      await window.Plotly.newPlot(chart, figure.data, figure.layout, {
        responsive: true,
        displayModeBar: false,
      });
      status.textContent = "График загружен.";
    } catch {
      status.textContent = "График не загрузился. Ранги доступны в таблице выше.";
    }
  });
})();
