"""Расчёт DEMATEL и сохранение результатов учебного примера."""

from pathlib import Path

import numpy as np
import pandas as pd

from decision_core.dematel import calculate_dematel


def main() -> None:
    project_dir = Path(__file__).resolve().parents[1]

    table = pd.read_csv(
        project_dir / "data/decision/dematel_direct.csv",
        index_col=0,
    )

    normalized, total, summary = calculate_dematel(table)

    A = table.to_numpy(dtype=float)
    row_sums = A.sum(axis=1)
    column_sums = A.sum(axis=0)
    divisor = float(max(row_sums.max(), column_sums.max()))

    print("Суммы строк:", row_sums)
    print("Суммы столбцов:", column_sums)
    print("Делитель:", divisor)

    print("\nНормированная матрица:")
    print(normalized.round(3).to_string())

    print("\nМатрица полного влияния:")
    print(total.round(3).to_string())

    print("\nПоказатели DEMATEL:")
    print(summary.round(3).to_string())

    print("\nСумма D:", round(float(summary["D"].sum()), 3))
    print("Сумма R:", round(float(summary["R"].sum()), 3))
    print(
        "Для этой матрицы T совпадает с N:",
        np.allclose(total.to_numpy(), normalized.to_numpy()),
    )

    # Контрольные значения относятся к исходному учебному примеру.
    expected = pd.DataFrame(
        {
            "D": [0, 1, 0, 0, 0.25],
            "R": [0.625, 0, 0.25, 0.375, 0],
        },
        index=[
            "price",
            "performance",
            "mass",
            "battery_life",
            "warranty",
        ],
    )

    if set(summary.index) != set(expected.index):
        raise ValueError(
            "Набор критериев отличается от контрольного примера."
        )

    actual = summary.loc[expected.index, ["D", "R"]]
    control_error = float(
        np.max(np.abs(actual.to_numpy() - expected.to_numpy()))
    )

    if control_error > 0.001:
        raise ValueError(
            "Результат отличается от контрольного расчёта."
        )

    output_dir = project_dir / "results/dematel"
    output_dir.mkdir(parents=True, exist_ok=True)

    normalized.to_csv(output_dir / "normalized_matrix.csv")
    total.to_csv(output_dir / "total_influence.csv")
    summary.to_csv(output_dir / "indicators.csv")

    print(
        "\nМаксимальное отклонение от ручного расчёта:",
        control_error,
    )
    print("Результаты сохранены:", output_dir)


if __name__ == "__main__":
    main()