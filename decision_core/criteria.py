"""Порядок критериев и направления предпочтения."""

CRITERIA = (
    "price_rub",
    "performance_score",
    "mass_kg",
    "battery_hours",
    "warranty_months",
)

MINIMIZE = frozenset({
    "price_rub",
    "mass_kg",
})

MAXIMIZE = frozenset({
    "performance_score",
    "battery_hours",
    "warranty_months",
})


def configuration(matrix):
    """Explicit directions for variable matrices; legacy calls retain the template contract."""
    directions = matrix.attrs.get('directions')
    if directions is None:
        if set(matrix.columns) != set(CRITERIA):
            raise ValueError('Состав критериев не соответствует заданию.')
        return list(CRITERIA), MINIMIZE
    if not isinstance(directions, dict) or set(directions) != set(matrix.columns) or not 2 <= len(directions) <= 10:
        raise ValueError('Нужны от 2 до 10 критериев с направлениями для каждого столбца.')
    if any(v not in ('min', 'max') for v in directions.values()):
        raise ValueError('Направление критерия: min или max.')
    return list(matrix.columns), frozenset(k for k, v in directions.items() if v == 'min')
