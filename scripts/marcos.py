from pathlib import Path

import numpy as np
import pandas as pd

project_dir = Path(__file__).resolve().parents[1]
output_dir = project_dir / "results" / "decision"

matrix = pd.read_csv(
    output_dir / "decision_matrix.csv",
    index_col=0,
)

cost_criteria = ["price_rub", "mass_kg"]
benefit_criteria = [
    "performance_score",
    "battery_hours",
    "warranty_months",
]

if set(matrix.columns) != set(cost_criteria + benefit_criteria):
    raise ValueError("Неожиданный состав критериев.")

values = matrix.to_numpy(dtype=float)
if not np.isfinite(values).all() or (values <= 0).any():
    raise ValueError("Значения должны быть конечными и положительными.")

ideal = matrix.max().astype(float)
anti_ideal = matrix.min().astype(float)

for column in cost_criteria:
    ideal[column] = matrix[column].min()
    anti_ideal[column] = matrix[column].max()

extended = pd.concat([
    anti_ideal.to_frame().T.rename(index={0: "AAI"}),
    matrix,
    ideal.to_frame().T.rename(index={0: "AI"}),
])
extended.index.name = "alternative_id"

extended.to_csv(output_dir / "marcos_extended.csv")

print("Расширенная матрица MARCOS:")
print(extended.round(3).to_string())
normalized = extended.astype(float).copy()

for column in cost_criteria:
    normalized[column] = ideal[column] / extended[column]

for column in benefit_criteria:
    normalized[column] = extended[column] / ideal[column]

weight_table = pd.read_csv(output_dir / "merec_weights.csv")

if weight_table["criterion"].duplicated().any():
    raise ValueError("В таблице весов повторяются критерии.")

weights = weight_table.set_index("criterion")["weight"]

if set(weights.index) != set(matrix.columns):
    raise ValueError("Критерии матрицы и таблицы весов различаются.")

weights = weights.reindex(matrix.columns)

if not np.isfinite(weights.to_numpy()).all() or (weights < 0).any():
    raise ValueError("Веса должны быть конечными и неотрицательными.")

if not np.isclose(weights.sum(), 1.0):
    raise ValueError("Сумма весов должна равняться единице.")

weighted = normalized.mul(weights, axis="columns")
scores = weighted.sum(axis=1)

normalized.to_csv(output_dir / "marcos_normalized.csv")
weighted.to_csv(output_dir / "marcos_weighted.csv")
scores.to_frame("S").to_csv(output_dir / "marcos_scores.csv")

print("\nНормированная матрица MARCOS:")
print(normalized.round(6).to_string())

print("\nВзвешенные суммы S:")
print(scores.round(6).to_string())

print("\nПроверка S идеала = 1:", np.isclose(scores.loc["AI"], 1.0))
S_ai = float(scores.loc["AI"])
S_aai = float(scores.loc["AAI"])

if S_ai <= 0 or S_aai <= 0:
    raise ValueError("Суммы идеала и антиидеала должны быть положительными.")

# В рейтинг входят только исходные альтернативы.
alternative_scores = scores.loc[matrix.index]

K_minus = alternative_scores / S_aai
K_plus = alternative_scores / S_ai

f_minus = K_plus / (K_plus + K_minus)
f_plus = K_minus / (K_plus + K_minus)

utility = (K_plus + K_minus) / (
    1
    + (1 - f_plus) / f_plus
    + (1 - f_minus) / f_minus
)

ranking = pd.DataFrame({
    "S": alternative_scores,
    "K_minus": K_minus,
    "K_plus": K_plus,
    "f_minus": f_minus,
    "f_plus": f_plus,
    "utility": utility,
})

ranking["rank"] = ranking["utility"].rank(
    ascending=False, method="min"
).astype(int)

ranking = ranking.sort_values("utility", ascending=False)
ranking.to_csv(output_dir / "marcos_ranking.csv")

print("\nИтоговый рейтинг MARCOS:")
print(ranking.round(6).to_string())