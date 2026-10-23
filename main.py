"""Сравнение ноутбуков методами MEREC, MARCOS и VIKOR."""

from pathlib import Path

import pandas as pd

from decision_core.marcos import calculate_marcos
from decision_core.merec import calculate_merec
from decision_core.vikor import calculate_vikor


ROOT = Path(__file__).resolve().parent


def main() -> None:
    matrix_path = ROOT / "results/decision/decision_matrix.csv"

    if not matrix_path.exists():
        raise FileNotFoundError(
            f"Не найдена матрица критериев: {matrix_path}"
        )

    matrix = pd.read_csv(matrix_path, index_col=0)

    merec = calculate_merec(matrix)

    # Передаём веса с названиями критериев вместо индексов 0–4.
    weights = merec.set_index("criterion")["weight"]

    marcos = calculate_marcos(matrix, weights)
    vikor, conditions = calculate_vikor(matrix, weights, v=0.5)

    print("Количество альтернатив:", len(matrix))

    print("\nВеса MEREC:")
    print(merec.round(6).to_string(index=False))

    print("\nРейтинг MARCOS — больше полезность, лучше:")
    print(marcos.round(6).to_string())

    print("\nРейтинг VIKOR — меньше Q, лучше:")
    print(vikor.round(6).to_string())

    print("\nУсловия и компромиссное решение VIKOR:")
    for name, value in conditions.items():
        print(f"{name}: {value}")


if __name__ == "__main__":
    main()