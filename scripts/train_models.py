import json
from pathlib import Path

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import GridSearchCV, GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

project_dir = Path(__file__).resolve().parents[1]
train = pd.read_csv(project_dir / "data" / "processed" / "train.csv")

numeric_features = [
    "cpu_cores",
    "cpu_threads",
    "cpu_frequency_ghz",
    "ram_gb",
    "storage_gb",
]
categorical_features = ["storage_type"]
features = numeric_features + categorical_features

X = train[features]
y = train["synthetic_score"]
groups = train["configuration_id"]

# Одинаковые разбиения используются для обеих моделей.
cv = list(GroupKFold(n_splits=5).split(X, y, groups))

models = {
    "ridge": (
        Ridge(),
        {"model__alpha": [0.1, 1.0, 10.0]},
    ),
    "random_forest": (
        RandomForestRegressor(
            n_estimators=200,
            random_state=42,
            n_jobs=1,
        ),
        {
            "model__max_depth": [5, None],
            "model__min_samples_leaf": [1, 5],
        },
    ),
}

model_dir = project_dir / "models"
result_dir = project_dir / "results"
model_dir.mkdir(exist_ok=True)
result_dir.mkdir(exist_ok=True)

summary = []

for name, (model, parameters) in models.items():
    print(f"\nОбучение: {name}", flush=True)

    preprocessing = ColumnTransformer([
        ("numeric", StandardScaler(), numeric_features),
        (
            "categorical",
            OneHotEncoder(handle_unknown="ignore", sparse_output=False),
            categorical_features,
        ),
    ])

    pipeline = Pipeline([
        ("preprocessing", preprocessing),
        ("model", model),
    ])

    search = GridSearchCV(
        estimator=pipeline,
        param_grid=parameters,
        scoring="neg_mean_absolute_error",
        cv=cv,
        n_jobs=1,
        refit=True,
        error_score="raise",
    )
    search.fit(X, y)

    cv_mae = float(-search.best_score_)
    summary.append({
        "model": name,
        "cv_mae": cv_mae,
        "best_parameters": json.dumps(search.best_params_),
    })

    # Сохраняем модель вместе с обработкой признаков.
    joblib.dump(search.best_estimator_, model_dir / f"{name}.joblib")
    pd.DataFrame(search.cv_results_).to_csv(
        result_dir / f"{name}_cv.csv", index=False
    )

    print("Параметры:", search.best_params_)
    print("Средняя ошибка CV, баллы:", round(cv_mae, 2))

comparison = pd.DataFrame(summary).sort_values("cv_mae")
comparison.to_csv(result_dir / "model_comparison_cv.csv", index=False)

selected_model = comparison.iloc[0]["model"]
(result_dir / "selected_model.txt").write_text(
    selected_model, encoding="utf-8"
)

print("\nВыбрана по результатам CV:", selected_model)
print("Тестовая выборка не использовалась.")