from pathlib import Path

import numpy as np
import pandas as pd

project_dir = Path(__file__).resolve().parents[1]
output_dir = project_dir / "results" / "decision"

matrix = pd.read_csv(
    output_dir / "decision_matrix.csv", index_col=0
)
weight_table = pd.read_csv(output_dir / "merec_weights.csv")

if weight_table["criterion"].duplicated().any():
    raise ValueError("Повторяющиеся критерии в таблице весов.")

weights = weight_table.set_index("criterion")["weight"]
if set(weights.index) != set(matrix.columns):
    raise ValueError("Критерии и веса не совпадают.")

weights = weights.reindex(matrix.columns)

if (
    not np.isfinite(weights.to_numpy()).all()
    or (weights < 0).any()
    or not np.isclose(weights.sum(), 1.0)
):
    raise ValueError("Некорректные веса.")

values = matrix.to_numpy(dtype=float)
if not np.isfinite(values).all() or (values <= 0).any():
    raise ValueError("Нужны конечные положительные значения.")

cost_criteria = {"price_rub", "mass_kg"}

ratio = matrix.astype(float).copy()
minmax = matrix.astype(float).copy()

for column in matrix.columns:
    minimum = matrix[column].min()
    maximum = matrix[column].max()
    span = maximum - minimum

    if column in cost_criteria:
        ratio[column] = minimum / matrix[column]
    else:
        ratio[column] = matrix[column] / maximum

    if span == 0:
        # Критерий одинаков у всех, поэтому не различает варианты.
        minmax[column] = 1.0
    elif column in cost_criteria:
        minmax[column] = (maximum - matrix[column]) / span
    else:
        minmax[column] = (matrix[column] - minimum) / span

comparison = pd.DataFrame(index=matrix.index)

for name, normalized in [("ratio", ratio), ("minmax", minmax)]:
    score = normalized.mul(weights, axis="columns").sum(axis=1)
    comparison[f"{name}_score"] = score
    comparison[f"{name}_rank"] = score.rank(
        ascending=False, method="min"
    ).astype(int)
    normalized.to_csv(output_dir / f"comparison_{name}_matrix.csv")

comparison.to_csv(output_dir / "normalization_comparison.csv")

print("Сравнение нормирования при фиксированных весах:")
print(comparison.round(6).to_string())
print("\nБольше итоговая оценка — лучше.")