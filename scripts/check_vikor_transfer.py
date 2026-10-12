from pathlib import Path

import numpy as np
import pandas as pd

from decision_core.vikor import calculate_vikor

project_dir = Path(__file__).resolve().parents[1]
folder = project_dir / "results" / "decision"

matrix = pd.read_csv(folder / "decision_matrix.csv", index_col=0)
weights = pd.read_csv(folder / "merec_weights.csv").set_index(
    "criterion"
)["weight"]

actual, conditions = calculate_vikor(matrix, weights)

expected = pd.read_csv(folder / "vikor_ranking.csv", index_col=0)
expected = expected.loc[actual.index, actual.columns]

error = float(np.max(np.abs(
    actual.to_numpy() - expected.to_numpy()
)))

assert error < 1e-12
assert conditions["acceptable_advantage"] is False
assert conditions["acceptable_stability"] is False
assert conditions["compromise_set"] == ["A2", "A3", "A5"]

print("Максимальное отклонение:", error)
print("Компромиссный набор:", ", ".join(conditions["compromise_set"]))
print("Проверки условий пройдены.")