"""Метод VIKOR и проверка условий компромиссного решения."""

import numpy as np
import pandas as pd

from .criteria import CRITERIA
from .normalization import normalize_minmax


def calculate_vikor(
    matrix: pd.DataFrame,
    weights: pd.Series,
    v: float = 0.5,
) -> tuple[pd.DataFrame, dict]:
    """Возвращает рейтинг и результаты проверки условий VIKOR."""

    if len(matrix) < 2:
        raise ValueError("Нужны минимум две альтернативы.")

    if not np.isfinite(v) or not 0 <= v <= 1:
        raise ValueError("Параметр v должен находиться от 0 до 1.")

    # Полезность min–max и отставание дополняют друг друга до 1.
    # Постоянный критерий получает нулевое отставание.
    distances = 1.0 - normalize_minmax(matrix)

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

    weighted = distances.mul(aligned_weights, axis="columns")
    S = weighted.sum(axis=1)
    R = weighted.max(axis=1)

    S_span = float(S.max() - S.min())
    R_span = float(R.max() - R.min())

    S_normalized = (
        (S - S.min()) / S_span
        if S_span > 1e-12
        else pd.Series(0.0, index=matrix.index)
    )
    R_normalized = (
        (R - R.min()) / R_span
        if R_span > 1e-12
        else pd.Series(0.0, index=matrix.index)
    )

    Q = v * S_normalized + (1 - v) * R_normalized

    ranking = pd.DataFrame({"S": S, "R": R, "Q": Q})
    if not np.isfinite(ranking.to_numpy()).all():
        raise ValueError("Расчёт дал неконечные значения.")

    ranking["rank"] = Q.rank(
        ascending=True, method="min"
    ).astype(int)
    ranking = ranking.sort_values("Q", kind="stable")

    leader = ranking.index[0]
    threshold = 1 / (len(ranking) - 1)
    gap = float(ranking["Q"].iloc[1] - ranking["Q"].iloc[0])

    advantage = bool(gap >= threshold)
    stability = bool(
        np.isclose(S.loc[leader], S.min(), atol=1e-10, rtol=0)
        or np.isclose(R.loc[leader], R.min(), atol=1e-10, rtol=0)
    )

    if not advantage:
        compromise = ranking.index[
            ranking["Q"] - ranking["Q"].iloc[0] < threshold
        ].tolist()
    elif not stability:
        compromise = ranking.index[:2].tolist()
    else:
        compromise = [leader]

    conditions = {
        "v": float(v),
        "leader": leader,
        "advantage_threshold": threshold,
        "gap_to_second": gap,
        "acceptable_advantage": advantage,
        "acceptable_stability": stability,
        "compromise_set": compromise,
    }

    return ranking, conditions