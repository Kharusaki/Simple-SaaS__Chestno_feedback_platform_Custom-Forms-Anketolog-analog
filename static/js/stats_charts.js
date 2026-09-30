/* Рисует графики статистики. Данные приходят в #charts-data из Jinja.
 *
 * График живёт внутри карточки своего вопроса, а не в отдельной сетке
 * сверху: одна и та же картинка дважды на странице только мешала читать.
 * Блок скрыт в разметке и показывается здесь, когда Chart.js загрузился —
 * библиотека приходит с CDN, и без сети пустой канвас выглядел бы как
 * сломанная страница.
 */
(function () {
  "use strict";

  var holder = document.getElementById("charts-data");
  if (!holder || typeof Chart === "undefined") {
    return;
  }

  var charts;
  try {
    charts = JSON.parse(holder.textContent);
  } catch (error) {
    console.error("Не удалось прочитать данные графиков", error);
    return;
  }

  var FONT_FAMILY = "system-ui, -apple-system, 'Segoe UI', sans-serif";
  Chart.defaults.font.family = FONT_FAMILY;
  Chart.defaults.font.size = 12;

  charts.forEach(function (chart) {
    var box = document.querySelector('[data-chart-for="' + chart.question_id + '"]');
    var canvas = document.getElementById("chart-q" + chart.question_id);
    if (!box || !canvas) {
      return;
    }

    var data = {
      labels: chart.labels,
      datasets: [
        {
          label: chart.chart,
          data: chart.values,
          backgroundColor: chart.colors,
          borderColor: chart.colors[0],
          borderWidth: chart.type === "line" ? 3 : 0,
          borderRadius: 6,
          fill: false,
          tension: 0.3
        }
      ]
    };

    var options = {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: chart.type === "doughnut" || chart.type === "pie" },
        tooltip: { rtl: false }
      },
      scales: chart.type === "doughnut" ? {} : { y: { beginAtZero: true, ticks: { precision: 0 } } }
    };

    if (chart.type === "line") {
      options.scales.y.ticks.precision = 0;
    }
    if (chart.type === "doughnut") {
      options.cutout = "55%";
    }

    /* eslint-disable no-new */
    new Chart(canvas, {
      type: chart.type,
      data: data,
      options: options
    });
    box.hidden = false;
  });
})();
