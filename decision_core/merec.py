"""Расчёт весов критериев методом MEREC."""

import numpy as np
import pandas as pd

from .criteria import configuration


def calculate_merec(matrix: pd.DataFrame) -> pd.DataFrame:
    """Возвращает эффекты исключения и веса критериев."""
    CRITERIA, MINIMIZE = configuration(matrix)
    if matrix.empty:
        raise ValueError("Матрица альтернатив пуста.")

    if matrix.columns.has_duplicates:
        raise ValueError("Названия критериев повторяются.")

    if matrix.index.has_duplicates:
        raise ValueError("Идентификаторы альтернатив повторяются.")

    if set(matrix.columns) != set(CRITERIA):
        raise ValueError("Состав критериев не соответствует заданию.")

    values = matrix.loc[:, list(CRITERIA)].astype(float).copy()

    if not np.isfinite(values.to_numpy()).all():
        raise ValueError("Матрица содержит пропуски или бесконечность.")

    if (values <= 0).any().any():
        raise ValueError("Значения критериев должны быть положительными.")

    normalized = values.copy()

    # Специальное нормирование MEREC: меньше — лучше.
    for column in CRITERIA:
        if column in MINIMIZE:
            normalized[column] = values[column] / values[column].max()
        else:
            normalized[column] = values[column].min() / values[column]

    log_values = np.abs(np.log(normalized.to_numpy()))
    n_criteria = len(CRITERIA)
    total_logs = log_values.sum(axis=1, keepdims=True)

    scores = np.log1p(total_logs[:, 0] / n_criteria)
    scores_without = np.log1p(
        (total_logs - log_values) / n_criteria
    )

    effects = np.abs(scores_without - scores[:, None]).sum(axis=0)
    total_effect = float(effects.sum())

    if not np.isfinite(effects).all() or total_effect <= 0:
        raise ValueError("По этой матрице веса MEREC не определены.")

    return pd.DataFrame({
        "criterion": list(CRITERIA),
        "removal_effect": effects,
        "weight": effects / total_effect,
    })