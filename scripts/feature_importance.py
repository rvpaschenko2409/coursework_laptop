from pathlib import Path

import joblib
import pandas as pd
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error

project_dir = Path(__file__).resolve().parents[1]

test = pd.read_csv(
    project_dir / "data" / "processed" / "test.csv"
)
model = joblib.load(
    project_dir / "models" / "random_forest.joblib"
)

features = [
    "cpu_cores",
    "cpu_threads",
    "cpu_frequency_ghz",
    "ram_gb",
    "storage_gb",
    "storage_type",
]

X = test[features]
y = test["synthetic_score"]

baseline_mae = mean_absolute_error(y, model.predict(X))

importance = permutation_importance(
    model,
    X,
    y,
    scoring="neg_mean_absolute_error",
    n_repeats=30,
    random_state=42,
    n_jobs=1,
)

summary = pd.DataFrame({
    "feature": features,
    "mae_increase_mean": importance.importances_mean,
    "mae_increase_std": importance.importances_std,
}).sort_values("mae_increase_mean", ascending=False)

result_dir = project_dir / "results"
result_dir.mkdir(exist_ok=True)

summary.to_csv(
    result_dir / "feature_importance.csv",
    index=False,
)
pd.DataFrame(
    importance.importances.T,
    columns=features,
).to_csv(
    result_dir / "feature_importance_repeats.csv",
    index=False,
)

print(f"Исходная MAE: {baseline_mae:.2f}")
print("\nРост MAE после перемешивания признака:")
print(summary.round(3).to_string(index=False))
print("\nРезультаты сохранены в results.")