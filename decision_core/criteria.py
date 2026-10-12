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