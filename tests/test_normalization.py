import numpy as np
import pandas as pd
import pytest

from decision_core.criteria import CRITERIA
from decision_core.normalization import (
    normalize_minmax,
    normalize_ratio,
)


@pytest.fixture
def matrix():
    return pd.DataFrame(
        [
            [100, 100, 1, 10, 12],
            [200, 200, 2, 20, 24],
        ],
        index=["A1", "A2"],
        columns=list(CRITERIA),
        dtype=float,
    )


@pytest.mark.parametrize(
    "function, expected",
    [
        (
            normalize_ratio,
            [[1, 0.5, 1, 0.5, 0.5],
             [0.5, 1, 0.5, 1, 1]],
        ),
        (
            normalize_minmax,
            [[1, 0, 1, 0, 0],
             [0, 1, 0, 1, 1]],
        ),
    ],
)
def test_manual_example(matrix, function, expected):
    result = function(matrix)
    np.testing.assert_allclose(
        result.to_numpy(), expected, rtol=0, atol=1e-12
    )


@pytest.mark.parametrize("function", [normalize_ratio, normalize_minmax])
def test_constant_column(matrix, function):
    matrix["warranty_months"] = 12
    result = function(matrix)
    np.testing.assert_allclose(result["warranty_months"], 1.0)


@pytest.mark.parametrize("function", [normalize_ratio, normalize_minmax])
def test_input_is_unchanged(matrix, function):
    original = matrix.copy(deep=True)
    function(matrix)
    pd.testing.assert_frame_equal(matrix, original)


@pytest.mark.parametrize("function", [normalize_ratio, normalize_minmax])
def test_column_order(matrix, function):
    shuffled = matrix.loc[:, list(reversed(CRITERIA))]
    result = function(shuffled)
    pd.testing.assert_frame_equal(result, function(matrix))


@pytest.mark.parametrize("function", [normalize_ratio, normalize_minmax])
@pytest.mark.parametrize("bad_value", [0, -1, np.nan, np.inf])
def test_invalid_values(matrix, function, bad_value):
    matrix.iloc[0, 0] = bad_value
    with pytest.raises(ValueError):
        function(matrix)


@pytest.mark.parametrize("function", [normalize_ratio, normalize_minmax])
def test_empty_matrix(matrix, function):
    with pytest.raises(ValueError):
        function(matrix.iloc[:0])


@pytest.mark.parametrize("function", [normalize_ratio, normalize_minmax])
def test_missing_criterion(matrix, function):
    with pytest.raises(ValueError):
        function(matrix.drop(columns=["mass_kg"]))


@pytest.mark.parametrize("function", [normalize_ratio, normalize_minmax])
def test_duplicate_columns(matrix, function):
    duplicate = pd.concat([matrix, matrix[["price_rub"]]], axis=1)
    with pytest.raises(ValueError):
        function(duplicate)


@pytest.mark.parametrize("function", [normalize_ratio, normalize_minmax])
def test_duplicate_alternatives(matrix, function):
    matrix.index = ["A1", "A1"]
    with pytest.raises(ValueError):
        function(matrix)