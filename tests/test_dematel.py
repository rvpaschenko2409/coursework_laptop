"""Контрольные примеры и проверки входных данных DEMATEL."""

import numpy as np
import pandas as pd
import pytest

from decision_core.dematel import calculate_dematel


def test_indirect_influence():
    direct = pd.DataFrame(
        [[0, 2, 0], [0, 0, 1], [0, 0, 0]],
        index=["A", "B", "C"],
        columns=["A", "B", "C"],
    )
    original = direct.copy(deep=True)

    normalized, total, indicators = calculate_dematel(direct)

    np.testing.assert_allclose(
        normalized,
        [[0, 1, 0], [0, 0, 0.5], [0, 0, 0]],
        rtol=0,
        atol=1e-12,
    )
    np.testing.assert_allclose(
        total,
        [[0, 1, 0.5], [0, 0, 0.5], [0, 0, 0]],
        rtol=0,
        atol=1e-12,
    )
    np.testing.assert_allclose(
        indicators,
        [
            [1.5, 0, 1.5, 1.5],
            [0.5, 1, 1.5, -0.5],
            [0, 1, 1, -1],
        ],
        rtol=0,
        atol=1e-12,
    )
    pd.testing.assert_frame_equal(direct, original)


def test_convergent_cycle():
    direct = pd.DataFrame(
        [[0, 2], [1, 0]],
        index=["A", "B"],
        columns=["A", "B"],
    )

    _, total, _ = calculate_dematel(direct)

    # Учитывается бесконечная последовательность взаимных влияний.
    np.testing.assert_allclose(
        total,
        [[1, 2], [1, 1]],
        rtol=0,
        atol=1e-12,
    )


def test_nonconvergent_cycle():
    direct = pd.DataFrame(
        [[0, 2], [2, 0]],
        index=["A", "B"],
        columns=["A", "B"],
    )

    with pytest.raises(ValueError, match="сходимости"):
        calculate_dematel(direct)


def test_zero_matrix():
    direct = pd.DataFrame(
        np.zeros((2, 2)),
        index=["A", "B"],
        columns=["A", "B"],
    )

    with pytest.raises(ValueError, match="Все оценки"):
        calculate_dematel(direct)


@pytest.mark.parametrize("value", [-1, 5, np.nan, np.inf])
def test_invalid_assessment(value):
    direct = pd.DataFrame(
        [[0, value], [1, 0]],
        index=["A", "B"],
        columns=["A", "B"],
    )

    with pytest.raises(ValueError):
        calculate_dematel(direct)


def test_mismatched_criteria():
    direct = pd.DataFrame(
        [[0, 2], [1, 0]],
        index=["A", "B"],
        columns=["B", "A"],
    )

    with pytest.raises(ValueError, match="Порядок критериев"):
        calculate_dematel(direct)