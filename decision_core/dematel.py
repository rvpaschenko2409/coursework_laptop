"""Расчёт полного влияния и показателей DEMATEL."""

import numpy as np
import pandas as pd


def calculate_dematel(
    direct: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Вернуть нормированную матрицу, полное влияние и показатели.

    Прямые оценки задаются по шкале от 0 до 4.
    Строка обозначает влияющий критерий, столбец — получающий влияние.
    """
    if not isinstance(direct, pd.DataFrame):
        raise TypeError("Ожидается таблица pandas.DataFrame.")

    if direct.empty or direct.shape[0] != direct.shape[1]:
        raise ValueError("Матрица должна быть непустой и квадратной.")

    if not direct.index.is_unique or not direct.columns.is_unique:
        raise ValueError("Названия критериев не должны повторяться.")

    if not direct.index.equals(direct.columns):
        raise ValueError(
            "Порядок критериев в строках и столбцах различается."
        )

    try:
        A = direct.to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("Оценки должны быть числовыми.") from exc

    if not np.isfinite(A).all():
        raise ValueError(
            "Матрица содержит пропуски или бесконечные значения."
        )

    if ((A < 0) | (A > 4)).any():
        raise ValueError("Оценки должны находиться в диапазоне от 0 до 4.")

    if np.any(np.diag(A) != 0):
        raise ValueError("На диагонали должны быть нули.")

    divisor = float(max(A.sum(axis=1).max(), A.sum(axis=0).max()))

    if divisor == 0:
        raise ValueError(
            "Все оценки равны нулю: нормирование невозможно."
        )

    N = A / divisor

    spectral_radius = float(np.max(np.abs(np.linalg.eigvals(N))))
    if spectral_radius >= 1:
        raise ValueError(
            "Матрица не удовлетворяет условию сходимости DEMATEL."
        )

    identity = np.eye(len(N))

    # T = N @ (I - N)^(-1), без явного обращения матрицы.
    T = np.linalg.solve((identity - N).T, N.T).T

    D = T.sum(axis=1)
    R = T.sum(axis=0)

    normalized = pd.DataFrame(
        N, index=direct.index, columns=direct.columns
    )
    total = pd.DataFrame(
        T, index=direct.index, columns=direct.columns
    )
    indicators = pd.DataFrame(
        {
            "D": D,
            "R": R,
            "D_plus_R": D + R,
            "D_minus_R": D - R,
        },
        index=direct.index,
    )

    return normalized, total, indicators