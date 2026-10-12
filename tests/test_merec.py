import math

import numpy as np
import pandas as pd
import pytest

from decision_core.criteria import CRITERIA
from decision_core.merec import calculate_merec


@pytest.fixture
def matrix():
    # A1 вдвое лучше A2 по каждому критерию.
    return pd.DataFrame(
        [
            [100, 200, 1, 20, 24],
            [200, 100, 2, 10, 12],
        ],
        index=["A1", "A2"],
        columns=list(CRITERIA),
        dtype=float,
    )


def test_symmetric_manual_example(matrix):
    result = calculate_merec(matrix)

    # Для A1 все нормированные значения равны 0,5,
    # для A2 — 1. Эффект исключения одинаков у всех критериев.
    expected_effect = (
        math.log(1 + math.log(2))
        - math.log(1 + 4 * math.log(2) / 5)
    )

    np.testing.assert_allclose(
        result["removal_effect"], expected_effect,
        rtol=0, atol=1e-12,
    )
    np.testing.assert_allclose(
        result["weight"], 0.2, rtol=0, atol=1e-12,
    )


def test_constant_criterion_has_zero_weight(matrix):
    matrix["warranty_months"] = 12
    result = calculate_merec(matrix).set_index("criterion")

    assert result.loc["warranty_months", "removal_effect"] == 0
    assert result.loc["warranty_months", "weight"] == 0
    assert result["weight"].sum() == pytest.approx(1.0)


def test_identical_alternatives_are_rejected(matrix):
    matrix.iloc[1] = matrix.iloc[0]
    with pytest.raises(ValueError, match="не определены"):
        calculate_merec(matrix)


def test_change_of_units_does_not_change_weights(matrix):
    converted = matrix.copy()
    converted["price_rub"] /= 1000
    converted["mass_kg"] *= 1000

    np.testing.assert_allclose(
        calculate_merec(converted)["weight"],
        calculate_merec(matrix)["weight"],
        rtol=0, atol=1e-12,
    )


def test_input_is_unchanged(matrix):
    original = matrix.copy(deep=True)
    calculate_merec(matrix)
    pd.testing.assert_frame_equal(matrix, original)


def test_column_order_does_not_change_result(matrix):
    shuffled = matrix.loc[:, list(reversed(CRITERIA))]
    pd.testing.assert_frame_equal(
        calculate_merec(shuffled),
        calculate_merec(matrix),
    )