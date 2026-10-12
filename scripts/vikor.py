from pathlib import Path

import numpy as np
import pandas as pd

project_dir = Path(__file__).resolve().parents[1]
output_dir = project_dir / "results" / "decision"

matrix = pd.read_csv(
    output_dir / "decision_matrix.csv",
    index_col=0,
)
weight_table = pd.read_csv(output_dir / "merec_weights.csv")

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
    raise ValueError("Значения критериев должны быть конечными и положительными.")

if weight_table["criterion"].duplicated().any():
    raise ValueError("В таблице весов повторяются критерии.")

weights = weight_table.set_index("criterion")["weight"]
if set(weights.index) != set(matrix.columns):
    raise ValueError("Критерии матрицы и весов различаются.")

weights = weights.reindex(matrix.columns)
if (
    not np.isfinite(weights.to_numpy()).all()
    or (weights < 0).any()
    or not np.isclose(weights.sum(), 1.0)
):
    raise ValueError("Некорректные веса.")

distances = pd.DataFrame(
    0.0, index=matrix.index, columns=matrix.columns
)

for column in matrix.columns:
    minimum = matrix[column].min()
    maximum = matrix[column].max()
    span = maximum - minimum

    # Одинаковые значения не различают альтернативы.
    if span == 0:
        continue

    if column in cost_criteria:
        distances[column] = (matrix[column] - minimum) / span
    else:
        distances[column] = (maximum - matrix[column]) / span

weighted = distances.mul(weights, axis="columns")

summary = pd.DataFrame({
    "S": weighted.sum(axis=1),
    "R": weighted.max(axis=1),
})

distances.to_csv(output_dir / "vikor_distances.csv")
weighted.to_csv(output_dir / "vikor_weighted_distances.csv")
summary.to_csv(output_dir / "vikor_sr.csv")

print("Нормированные отставания:")
print(distances.round(6).to_string())
print("\nПоказатели S и R:")
print(summary.round(6).to_string())
v = 0.5

S = summary["S"]
R = summary["R"]

S_span = float(S.max() - S.min())
R_span = float(R.max() - R.min())

# Если показатель одинаков у всех, он не различает варианты.
S_normalized = (
    (S - S.min()) / S_span
    if S_span > 1e-12
    else pd.Series(0.0, index=summary.index)
)
R_normalized = (
    (R - R.min()) / R_span
    if R_span > 1e-12
    else pd.Series(0.0, index=summary.index)
)

summary["Q"] = v * S_normalized + (1 - v) * R_normalized
summary["rank"] = summary["Q"].rank(
    ascending=True, method="min"
).astype(int)

ranking = summary.sort_values("Q")
ranking.to_csv(output_dir / "vikor_ranking.csv")

print("\nРейтинг VIKOR при v = 0.5:")
print(ranking.round(6).to_string())
if len(ranking) < 2:
    raise ValueError("Для проверки условий нужны минимум две альтернативы.")

threshold = 1 / (len(ranking) - 1)
leader = ranking.index[0]
gap = float(ranking["Q"].iloc[1] - ranking["Q"].iloc[0])

acceptable_advantage = gap >= threshold
acceptable_stability = bool(
    np.isclose(summary.loc[leader, "S"], S.min(), atol=1e-10, rtol=0)
    or np.isclose(summary.loc[leader, "R"], R.min(), atol=1e-10, rtol=0)
)

if not acceptable_advantage:
    compromise = ranking.loc[
        ranking["Q"] - ranking["Q"].iloc[0] < threshold
    ].copy()
elif not acceptable_stability:
    compromise = ranking.iloc[:2].copy()
else:
    compromise = ranking.iloc[:1].copy()

compromise.to_csv(output_dir / "vikor_compromise.csv")

pd.DataFrame([{
    "v": v,
    "leader": leader,
    "advantage_threshold": threshold,
    "gap_to_second": gap,
    "acceptable_advantage": acceptable_advantage,
    "acceptable_stability": acceptable_stability,
    "compromise_set": ", ".join(compromise.index),
}]).to_csv(output_dir / "vikor_conditions.csv", index=False)

print("\nПроверка условий VIKOR:")
print("Порог преимущества:", threshold)
print("Разрыв между первым и вторым:", round(gap, 6))
print("Приемлемое преимущество:", acceptable_advantage)
print("Приемлемая стабильность:", acceptable_stability)
print("Компромиссный набор:", ", ".join(compromise.index))