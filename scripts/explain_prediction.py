import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import shap

project_dir = Path(__file__).resolve().parents[1]
test = pd.read_csv(project_dir / "data" / "processed" / "test.csv")
pipeline = joblib.load(
    project_dir / "models" / "random_forest.joblib"
)

numeric_features = [
    "cpu_cores",
    "cpu_threads",
    "cpu_frequency_ghz",
    "ram_gb",
    "storage_gb",
]
features = numeric_features + ["storage_type"]

# Выбираем первую запись, не подбирая удобный результат.
record = test.iloc[[0]]
X = record[features]

preprocessing = pipeline.named_steps["preprocessing"]
forest = pipeline.named_steps["model"]
transformed = preprocessing.transform(X)

explainer = shap.TreeExplainer(
    forest,
    feature_perturbation="tree_path_dependent",
    model_output="raw",
)
explanation = explainer(transformed, check_additivity=True)

values = np.asarray(explanation.values).reshape(-1)
base_value = float(np.asarray(explanation.base_values).reshape(-1)[0])
prediction = float(pipeline.predict(X)[0])

# Первые пять столбцов — числовые признаки.
# Остальные — индикаторы HDD/SSD после OneHotEncoder.
contributions = list(values[:len(numeric_features)])
contributions.append(float(values[len(numeric_features):].sum()))

table = pd.DataFrame({
    "feature": features,
    "value": [X.iloc[0][name] for name in features],
    "shap_contribution": contributions,
})

reconstructed = base_value + sum(contributions)
difference = abs(prediction - reconstructed)

if not np.isclose(prediction, reconstructed, atol=1e-6, rtol=1e-8):
    raise ValueError("Сумма вкладов не совпала с прогнозом!")

result_dir = project_dir / "results"
result_dir.mkdir(exist_ok=True)

table.to_csv(result_dir / "local_shap.csv", index=False)

summary = {
    "sample_id": int(record["sample_id"].iloc[0]),
    "configuration_id": int(record["configuration_id"].iloc[0]),
    "explainer_mode": "tree_path_dependent",
    "base_value": base_value,
    "prediction": prediction,
    "synthetic_target": float(record["synthetic_score"].iloc[0]),
    "reconstructed_prediction": reconstructed,
    "additivity_error": difference,
}
(result_dir / "local_shap_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print("Номер наблюдения:", summary["sample_id"])
print("\nХарактеристики и вклады:")
print(table.round(3).to_string(index=False))
print(f"\nБазовый балл: {base_value:.3f}")
print(f"Прогноз модели: {prediction:.3f}")
print(f"Базовый балл + вклады: {reconstructed:.3f}")
print(f"Расхождение: {difference:.10f}")
print(f"Целевой синтетический балл: {summary['synthetic_target']:.3f}")