from pathlib import Path

import pandas as pd

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

print("Наблюдений:", len(data))
print("Уникальных конфигураций:", len(data[features].drop_duplicates()))
print("Повторов sample_id:", int(data["sample_id"].duplicated().sum()))
print("Пропусков:", int(data.isna().sum().sum()))
print("Неположительных баллов:", int((data["synthetic_score"] <= 0).sum()))

print("\nРаспределение баллов:")
print(data["synthetic_score"].describe().round(2).to_string())

print("\nКоличество наблюдений по объёму памяти:")
print(data["ram_gb"].value_counts().sort_index().to_string())

print("\nКоличество наблюдений по типу накопителя:")
print(data["storage_type"].value_counts().to_string())