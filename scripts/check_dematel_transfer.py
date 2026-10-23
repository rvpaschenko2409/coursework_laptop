"""Проверка переноса DEMATEL в расчётный модуль."""

from pathlib import Path

import numpy as np
import pandas as pd

from decision_core.dematel import calculate_dematel


ROOT = Path(__file__).resolve().parents[1]

direct = pd.read_csv(
    ROOT / "data/decision/dematel_direct.csv",
    index_col=0,
)

actual_tables = calculate_dematel(direct)
filenames = (
    "normalized_matrix.csv",
    "total_influence.csv",
    "indicators.csv",
)

max_error = 0.0

for actual, filename in zip(actual_tables, filenames):
    expected = pd.read_csv(
        ROOT / "results/dematel" / filename,
        index_col=0,
    )

    pd.testing.assert_frame_equal(
        actual,
        expected,
        check_dtype=False,
        check_exact=False,
        rtol=0,
        atol=1e-12,
    )

    error = float(
        np.max(np.abs(actual.to_numpy() - expected.to_numpy()))
    )
    max_error = max(max_error, error)
    print(f"{filename}: совпадает")

# Отдельный пример с косвенным влиянием A → B → C.
# Делитель равен 2; прямые влияния после нормирования — 1 и 0.5.
# Косвенное влияние A → C равно 1 × 0.5 = 0.5.
chain = pd.DataFrame(
    [[0, 2, 0], [0, 0, 1], [0, 0, 0]],
    index=["A", "B", "C"],
    columns=["A", "B", "C"],
)

_, total, _ = calculate_dematel(chain)

expected_total = np.array([
    [0, 1, 0.5],
    [0, 0, 0.5],
    [0, 0, 0],
])

np.testing.assert_allclose(
    total.to_numpy(),
    expected_total,
    rtol=0,
    atol=1e-12,
)

print("Проверка косвенного влияния: пройдена")
print("Максимальное отклонение от сохранённых таблиц:", max_error)