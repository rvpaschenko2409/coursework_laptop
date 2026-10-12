import numpy as np
import pandas as pd
import pytest

from decision_core.criteria import CRITERIA
from decision_core.marcos import calculate_marcos


@pytest.fixture
def matrix():
    return pd.DataFrame(
        [
            [100, 200, 1, 20, 24],
            [200, 100, 2, 10, 12],
        ],
        index=["A1", "A2"],
        columns=list(CRITERIA),
        dtype=float,
    )


@pytest.fixture
def weights():
    return pd.Series(0.2, index=list(CRITERIA))


def test_manual_example(matrix, weights):
    result = calculate_marcos(matrix, weights)

    np.testing.assert_allclose(
        result.loc[["A1", "A2"], "S"],
        [1.0, 0.5], rtol=0, atol=1e-12,
    )
    np.testing.assert_allclose(
        result.loc[["A1", "A2"], "utility"],
        [6 / 7, 3 / 7], rtol=0, atol=1e-12,
    )
    assert result.loc["A1", "rank"] == 1
    assert result.loc["A2", "rank"] == 2


def test_equal_alternatives_share_rank(matrix, weights):
    matrix.iloc[1] = matrix.iloc[0]
    result = calculate_marcos(matrix, weights)

    np.testing.assert_allclose(
        result["utility"], 2 / 3, rtol=0, atol=1e-12
    )
    assert result["rank"].tolist() == [1, 1]


def test_weight_order_does_not_matter(matrix, weights):
    shuffled = weights.iloc[::-1]
    pd.testing.assert_frame_equal(
        calculate_marcos(matrix, shuffled),
        calculate_marcos(matrix, weights),
    )


def test_input_is_unchanged(matrix, weights):
    original_matrix = matrix.copy(deep=True)
    original_weights = weights.copy(deep=True)

    calculate_marcos(matrix, weights)

    pd.testing.assert_frame_equal(matrix, original_matrix)
    pd.testing.assert_series_equal(weights, original_weights)


def test_wrong_weight_sum_is_rejected(matrix, weights):
    with pytest.raises(ValueError):
        calculate_marcos(matrix, weights * 2)


def test_negative_weight_is_rejected(matrix, weights):
    weights.iloc[0] = -0.1
    weights.iloc[1] = 0.5  # Сумма остаётся равной 1.

    with pytest.raises(ValueError):
        calculate_marcos(matrix, weights)


def test_missing_weight_is_rejected(matrix, weights):
    with pytest.raises(ValueError):
        calculate_marcos(matrix, weights.drop("mass_kg"))


def test_nonfinite_weight_is_rejected(matrix, weights):
    weights.iloc[0] = np.nan

    with pytest.raises(ValueError):
        calculate_marcos(matrix, weights)