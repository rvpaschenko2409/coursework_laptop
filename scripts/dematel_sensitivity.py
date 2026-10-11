from pathlib import Path

import numpy as np
import pandas as pd

project_dir = Path(__file__).resolve().parents[1]
table = pd.read_csv(
    project_dir / "data" / "decision" / "dematel_direct.csv",
    index_col=0,
)
A = table.to_numpy(dtype=float)
names = list(table.index)


def calculate(matrix):
    divisor = max(
        matrix.sum(axis=1).max(),
        matrix.sum(axis=0).max(),
    )
    if divisor <= 0:
        raise ValueError("Невозможно нормировать нулевую матрицу.")

    normalized = matrix / divisor
    radius = np.max(np.abs(np.linalg.eigvals(normalized)))
    if radius >= 1:
        raise ValueError("Не выполнено условие сходимости.")

    identity = np.eye(len(matrix))
    total = np.linalg.solve(
        (identity - normalized).T, normalized.T
    ).T

    outgoing = total.sum(axis=1)
    incoming = total.sum(axis=0)
    return outgoing + incoming, outgoing - incoming


def classify(balance):
    return np.where(
        balance > 1e-9,
        "cause",
        np.where(balance < -1e-9, "effect", "neutral"),
    )


base_prominence, base_balance = calculate(A)
base_classes = classify(base_balance)

scenarios = []
details = []

for i, j in np.argwhere(A > 0):
    for change in [-1, 1]:
        modified = A.copy()
        new_value = A[i, j] + change

        if not 0 <= new_value <= 4:
            continue

        modified[i, j] = new_value
        prominence, balance = calculate(modified)
        classes = classify(balance)

        scenario = f"{names[i]} -> {names[j]}: {new_value:g}"

        scenarios.append({
            "scenario": scenario,
            "changed_classes": int(np.sum(classes != base_classes)),
            "max_change_D_plus_R": float(
                np.max(np.abs(prominence - base_prominence))
            ),
            "max_change_D_minus_R": float(
                np.max(np.abs(balance - base_balance))
            ),
        })

        for k, criterion in enumerate(names):
            details.append({
                "scenario": scenario,
                "criterion": criterion,
                "D_plus_R": float(prominence[k]),
                "D_minus_R": float(balance[k]),
                "classification": classes[k],
            })

output_dir = project_dir / "results" / "dematel"
output_dir.mkdir(parents=True, exist_ok=True)

summary = pd.DataFrame(scenarios)
summary.to_csv(output_dir / "sensitivity_summary.csv", index=False)
pd.DataFrame(details).to_csv(
    output_dir / "sensitivity_details.csv", index=False
)

print("Проверено сценариев:", len(summary))
print(summary.round(4).to_string(index=False))