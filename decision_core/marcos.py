"""Ранжирование альтернатив методом MARCOS."""

import numpy as np
import pandas as pd

from .criteria import configuration
from .normalization import normalize_ratio


def calculate_marcos(
    matrix: pd.DataFrame,
    weights: pd.Series,
) -> pd.DataFrame:
    """Возвращает показатели MARCOS и рейтинг: больше utility — лучше."""
    CRITERIA, MINIMIZE = configuration(matrix)

    # Функция нормирования также проверяет входную матрицу.
    normalized = normalize_ratio(matrix)

    if weights.index.has_duplicates:
        raise ValueError("Названия критериев в весах повторяются.")

    if set(weights.index) != set(CRITERIA):
        raise ValueError("Состав весов не соответствует критериям.")

    aligned_weights = weights.reindex(list(CRITERIA)).astype(float)

    if (
        not np.isfinite(aligned_weights.to_numpy()).all()
        or (aligned_weights < 0).any()
        or not np.isclose(
            aligned_weights.sum(), 1.0, atol=1e-10, rtol=0
        )
    ):
        raise ValueError("Веса должны быть неотрицательными с суммой 1.")

    values = matrix.loc[:, list(CRITERIA)].astype(float)

    # Идеал после нормирования состоит из единиц.
    S_ai = float(aligned_weights.sum())

    # Нормированные значения антиидеала.
    anti_normalized = pd.Series(index=list(CRITERIA), dtype=float)

    for column in CRITERIA:
        minimum = values[column].min()
        maximum = values[column].max()

        # Для min: идеал / антиидеал.
        # Для max: антиидеал / идеал.
        # В обоих случаях получается minimum / maximum.
        anti_normalized[column] = minimum / maximum

    S_aai = float((anti_normalized * aligned_weights).sum())

    if S_aai <= 0:
        raise ValueError("Сумма антиидеала должна быть положительной.")

    scores = normalized.mul(
        aligned_weights, axis="columns"
    ).sum(axis=1)

    K_minus = scores / S_aai
    K_plus = scores / S_ai

    f_minus = K_plus / (K_plus + K_minus)
    f_plus = K_minus / (K_plus + K_minus)

    utility = (K_plus + K_minus) / (
        1
        + (1 - f_plus) / f_plus
        + (1 - f_minus) / f_minus
    )

    ranking = pd.DataFrame({
        "S": scores,
        "K_minus": K_minus,
        "K_plus": K_plus,
        "f_minus": f_minus,
        "f_plus": f_plus,
        "utility": utility,
    })

    if not np.isfinite(ranking.to_numpy()).all():
        raise ValueError("Расчёт дал неконечные значения.")

    ranking["rank"] = ranking["utility"].rank(
        ascending=False, method="min"
    ).astype(int)

    return ranking.sort_values("utility", ascending=False)