from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

project_dir = Path(__file__).resolve().parents[1]
test = pd.read_csv(project_dir / "data" / "processed" / "test.csv")

features = [
    "cpu_cores",
    "cpu_threads",
    "cpu_frequency_ghz",
    "ram_gb",
    "storage_gb",
    "storage_type",
]

X_test = test[features]
y_test = test["synthetic_score"]

result_dir = project_dir / "results"
result_dir.mkdir(exist_ok=True)

metrics = []
predictions = test[
    ["sample_id", "configuration_id", "synthetic_score"]
].copy()

for name in ["ridge", "random_forest"]:
    model = joblib.load(project_dir / "models" / f"{name}.joblib")
    predicted = model.predict(X_test)

    mae = mean_absolute_error(y_test, predicted)
    rmse = np.sqrt(mean_squared_error(y_test, predicted))
    r2 = r2_score(y_test, predicted)

    metrics.append({
        "model": name,
        "test_mae": float(mae),
        "test_rmse": float(rmse),
        "test_r2": float(r2),
    })
    predictions[f"{name}_prediction"] = predicted

    print(f"\nМодель: {name}")
    print(f"MAE: {mae:.2f}")
    print(f"RMSE: {rmse:.2f}")
    print(f"R²: {r2:.4f}")

pd.DataFrame(metrics).to_csv(
    result_dir / "model_comparison_test.csv", index=False
)
predictions.to_csv(
    result_dir / "test_predictions.csv", index=False
)

print("\nМетрики и прогнозы сохранены в папке results.")