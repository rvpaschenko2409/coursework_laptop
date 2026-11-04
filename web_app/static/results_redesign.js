(() => {
  const baseline = document.querySelector('[data-results-base-weight]');
  const criterion = document.querySelector('#preview-criterion');
  if (!baseline || !criterion) return;
  const format = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 4});

  function updateBaseline() {
    const weight = Number(criterion.selectedOptions[0]?.dataset.originalWeight);
    baseline.textContent = Number.isFinite(weight) ?
      'Исходный вес выбранного критерия · ' + format.format(weight) : '';
  }

  criterion.addEventListener('change', updateBaseline);
  criterion.form.addEventListener('reset', () => setTimeout(updateBaseline, 0));
  updateBaseline();
})();
