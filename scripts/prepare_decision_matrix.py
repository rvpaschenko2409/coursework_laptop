from pathlib import Path

import joblib
import numpy as np
import pandas as pd

project_dir = Path(__file__).resolve().parents[1]
alternatives = pd.read_csv(
    project_dir / "data" / "decision" / "alternatives.csv"
)

features = [
    "cpu_cores",
    "cpu_threads",
    "cpu_frequency_ghz",
    "ram_gb",
    "storage_gb",
    "storage_type",
]
criteria = [
    "price_rub",
    "performance_score",
    "mass_kg",
    "battery_hours",
    "warranty_months",
]

if alternatives.empty:
    raise ValueError("Список альтернатив пуст.")

if alternatives.isna().any().any():
    raise ValueError("В исходной таблице есть пропуски.")

if alternatives["alternative_id"].duplicated().any():
    raise ValueError("Идентификаторы альтернатив повторяются.")

# Проверяем соответствие конфигураций учебному генератору.
reference = pd.read_csv(
    project_dir / "data" / "synthetic" / "laptops_synthetic.csv"
)
known_configurations = set(
    reference[features].itertuples(index=False, name=None)
)
for row in alternatives[features].itertuples(index=False, name=None):
    if row not in known_configurations:
        raise ValueError(f"Конфигурация вне учебного набора: {row}")

model_path = project_dir / "models" / "random_forest.joblib"
model = joblib.load(model_path)

alternatives["performance_score"] = model.predict(
    alternatives[features]
)

values = alternatives[criteria].to_numpy(dtype=float)
if not np.isfinite(values).all() or (values <= 0).any():
    raise ValueError("Значения критериев должны быть конечными и положительными.")

budget = 100000
eligible = alternatives.loc[
    alternatives["price_rub"] <= budget
].copy()

if len(eligible) < 2:
    raise ValueError("Для сравнения нужны хотя бы две допустимые альтернативы.")

matrix = eligible.set_index("alternative_id")[criteria]

output_dir = project_dir / "results" / "decision"
output_dir.mkdir(parents=True, exist_ok=True)

alternatives.to_csv(
    output_dir / "alternatives_with_predictions.csv",
    index=False,
)
matrix.to_csv(output_dir / "decision_matrix.csv")

print("Всего альтернатив:", len(alternatives))
print("Допущено по бюджету:", len(eligible))
print("\nМатрица критериев:")
print(matrix.round(3).to_string())
print("\nРезультаты сохранены:", output_dir)