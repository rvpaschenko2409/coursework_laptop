from pathlib import Path

import numpy as np
import pandas as pd

project_dir = Path(__file__).resolve().parents[1]
matrix = pd.read_csv(
    project_dir / "results" / "decision" / "decision_matrix.csv",
    index_col=0,
)

cost_criteria = ["price_rub", "mass_kg"]
benefit_criteria = [
    "performance_score",
    "battery_hours",
    "warranty_months",
]

expected_columns = set(cost_criteria + benefit_criteria)
if set(matrix.columns) != expected_columns:
    raise ValueError("Состав критериев не соответствует заданию.")

values = matrix.to_numpy(dtype=float)
if not np.isfinite(values).all() or (values <= 0).any():
    raise ValueError("Для MEREC нужны конечные положительные значения.")

normalized = matrix.astype(float).copy()

for column in cost_criteria:
    normalized[column] = matrix[column] / matrix[column].max()

for column in benefit_criteria:
    normalized[column] = matrix[column].min() / matrix[column]

output_dir = project_dir / "results" / "decision"
normalized.to_csv(output_dir / "merec_normalized.csv")

print("Нормированная матрица MEREC:")
print(normalized.round(6).to_string())
# Модули логарифмов нормированных значений.
log_values = np.abs(np.log(normalized.to_numpy(dtype=float)))

n_criteria = normalized.shape[1]
scores = np.log1p(log_values.sum(axis=1) / n_criteria)

score_table = pd.DataFrame(
    {"S": scores},
    index=matrix.index,
)
score_table.to_csv(output_dir / "merec_scores.csv")

print("\nОбщие оценки S:")
print(score_table.round(6).to_string())
# В каждой колонке исключаем один критерий.
total_logs = log_values.sum(axis=1, keepdims=True)
scores_without = np.log1p(
    (total_logs - log_values) / n_criteria
)

# Эффект исключения: сумма абсолютных изменений по альтернативам.
effects = np.abs(scores_without - scores[:, None]).sum(axis=0)

if effects.sum() <= 0:
    raise ValueError("Все эффекты равны нулю: веса MEREC не определены.")

weights = effects / effects.sum()

if not np.isclose(weights.sum(), 1.0):
    raise ValueError("Сумма весов отличается от единицы.")

pd.DataFrame(
    scores_without,
    index=matrix.index,
    columns=matrix.columns,
).to_csv(output_dir / "merec_scores_without.csv")

weight_table = pd.DataFrame({
    "criterion": matrix.columns,
    "removal_effect": effects,
    "weight": weights,
})
weight_table.to_csv(output_dir / "merec_weights.csv", index=False)

print("\nЭффекты исключения и веса MEREC:")
print(weight_table.round(6).to_string(index=False))
print("\nСумма весов:", round(float(weights.sum()), 10))