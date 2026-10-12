import numpy as np
import pandas as pd
import pytest

from decision_core.criteria import CRITERIA
from decision_core.vikor import calculate_vikor


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


def test_manual_unique_winner(matrix, weights):
    ranking, conditions = calculate_vikor(matrix, weights)

    np.testing.assert_allclose(
        ranking.loc[["A1", "A2"], ["S", "R", "Q"]],
        [[0, 0, 0], [1, 0.2, 1]],
        rtol=0,
        atol=1e-12,
    )

    assert conditions["acceptable_advantage"] is True
    assert conditions["acceptable_stability"] is True
    assert conditions["compromise_set"] == ["A1"]


def test_identical_alternatives(matrix, weights):
    matrix.iloc[1] = matrix.iloc[0]
    ranking, conditions = calculate_vikor(matrix, weights)

    np.testing.assert_allclose(
        ranking[["S", "R", "Q"]],
        0,
        rtol=0,
        atol=1e-12,
    )

    assert ranking["rank"].tolist() == [1, 1]
    assert conditions["acceptable_advantage"] is False
    assert set(conditions["compromise_set"]) == {"A1", "A2"}


@pytest.mark.parametrize("v", [0.0, 1.0])
def test_v_endpoints(matrix, weights, v):
    matrix.loc["A2", "price_rub"] = 50

    weights = pd.Series(
        [0.4, 0.15, 0.15, 0.15, 0.15],
        index=list(CRITERIA),
    )

    ranking, _ = calculate_vikor(matrix, weights, v=v)
    indicator = ranking["R"] if v == 0 else ranking["S"]

    expected = (
        (indicator - indicator.min())
        / (indicator.max() - indicator.min())
    )

    np.testing.assert_allclose(
        ranking["Q"],
        expected,
        rtol=0,
        atol=1e-12,
    )


@pytest.mark.parametrize("v", [-0.1, 1.1, np.nan])
def test_invalid_v(matrix, weights, v):
    with pytest.raises(ValueError):
        calculate_vikor(matrix, weights, v=v)


def test_one_alternative_is_rejected(matrix, weights):
    with pytest.raises(ValueError):
        calculate_vikor(matrix.iloc[:1], weights)


def test_input_is_unchanged(matrix, weights):
    matrix_before = matrix.copy(deep=True)
    weights_before = weights.copy(deep=True)

    calculate_vikor(matrix, weights)

    pd.testing.assert_frame_equal(matrix, matrix_before)
    pd.testing.assert_series_equal(weights, weights_before)


def test_weight_order_does_not_matter(matrix, weights):
    ranking, conditions = calculate_vikor(matrix, weights)

    reversed_ranking, reversed_conditions = calculate_vikor(
        matrix, weights.iloc[::-1]
    )

    pd.testing.assert_frame_equal(ranking, reversed_ranking)
    assert conditions == reversed_conditions