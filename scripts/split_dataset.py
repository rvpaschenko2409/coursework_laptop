from pathlib import Path

import pandas as pd
from sklearn.model_selection import GroupShuffleSplit

project_dir = Path(__file__).resolve().parents[1]
data = pd.read_csv(
    project_dir / "data" / "synthetic" / "laptops_synthetic.csv"
)

features = [
    "cpu_cores",
    "cpu_threads",
    "cpu_frequency_ghz",
    "ram_gb",
    "storage_type",
    "storage_gb",
]

# Все строки одной конфигурации получают одинаковый номер группы.
data["configuration_id"] = data.groupby(
    features, sort=True
).ngroup()

splitter = GroupShuffleSplit(
    n_splits=1,
    test_size=0.2,
    random_state=42,
)

train_indices, test_indices = next(
    splitter.split(data[features], groups=data["configuration_id"])
)

train = data.iloc[train_indices].copy()
test = data.iloc[test_indices].copy()

train_groups = set(train["configuration_id"])
test_groups = set(test["configuration_id"])
overlap = train_groups & test_groups

assert not overlap, "Обнаружено пересечение конфигураций!"
assert len(train) + len(test) == len(data), "Потеряны строки!"

output_dir = project_dir / "data" / "processed"
output_dir.mkdir(parents=True, exist_ok=True)

train.to_csv(output_dir / "train.csv", index=False)
test.to_csv(output_dir / "test.csv", index=False)

print("Обучение: наблюдений =", len(train),
      "; конфигураций =", len(train_groups))
print("Проверка: наблюдений =", len(test),
      "; конфигураций =", len(test_groups))
print("Пересечений конфигураций:", len(overlap))
print("Файлы сохранены в:", output_dir)