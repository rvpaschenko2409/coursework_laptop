"""Нормирование матрицы критериев."""

import numpy as np
import pandas as pd

from .criteria import CRITERIA, MINIMIZE


def normalize_ratio(matrix: pd.DataFrame) -> pd.DataFrame:
    """Нормирование относительно лучшего: больше — лучше."""
    if matrix.empty:
        raise ValueError("Матрица альтернатив пуста.")

    if matrix.columns.has_duplicates:
        raise ValueError("Названия критериев повторяются.")

    if matrix.index.has_duplicates:
        raise ValueError("Идентификаторы альтернатив повторяются.")

    if set(matrix.columns) != set(CRITERIA):
        raise ValueError("Состав критериев не соответствует заданию.")

    # Работаем с копией и фиксируем порядок столбцов.
    values = matrix.loc[:, list(CRITERIA)].astype(float).copy()

    if not np.isfinite(values.to_numpy()).all():
        raise ValueError("Матрица содержит пропуски или бесконечность.")

    if (values <= 0).any().any():
        raise ValueError("Значения критериев должны быть положительными.")

    result = values.copy()

    for column in CRITERIA:
        if column in MINIMIZE:
            result[column] = values[column].min() / values[column]
        else:
            result[column] = values[column] / values[column].max()

    return result
def normalize_minmax(matrix: pd.DataFrame) -> pd.DataFrame:
    """Нормирование min–max: больше — лучше."""
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

    result = values.copy()

    for column in CRITERIA:
        minimum = values[column].min()
        maximum = values[column].max()
        span = maximum - minimum

        if span == 0:
            # Одинаковое значение не различает альтернативы.
            result[column] = 1.0
        elif column in MINIMIZE:
            result[column] = (maximum - values[column]) / span
        else:
            result[column] = (values[column] - minimum) / span

    return result