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