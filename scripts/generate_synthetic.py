from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
N_ROWS = 2000

rng = np.random.default_rng(SEED)

# Условные профили: ядра, потоки, частота в ГГц.
# Это учебные конфигурации, не модели реальных процессоров.
profiles = np.array([
    [2, 4, 2.0],
    [4, 4, 2.5],
    [4, 8, 2.5],
    [6, 12, 2.8],
    [8, 16, 3.0],
])

selected = profiles[rng.integers(0, len(profiles), N_ROWS)]
cores = selected[:, 0].astype(int)
threads = selected[:, 1].astype(int)
frequency = selected[:, 2]

ram = rng.choice([4, 8, 16, 32], size=N_ROWS)
storage_type = rng.choice(["SSD", "HDD"], size=N_ROWS)

storage_gb = np.empty(N_ROWS, dtype=int)
ssd_mask = storage_type == "SSD"
storage_gb[ssd_mask] = rng.choice(
    [256, 512, 1024], size=ssd_mask.sum()
)
storage_gb[~ssd_mask] = rng.choice(
    [500, 1000], size=(~ssd_mask).sum()
)

# Формула из описания эксперимента.
cpu_factor = (cores / 4) ** 0.7 * (frequency / 2.5)
memory_factor = (ram / 8) ** 0.3
disk_factor = np.where(ssd_mask, 1.0, 0.4)

relative_time = (
    0.7 / cpu_factor
    + 0.2 / memory_factor
    + 0.1 / disk_factor
)
score_without_noise = 1000 / relative_time

# Мультипликативный шум: среднее множителя равно 1.
# Стандартное отклонение логарифма множителя — 0,05.
sigma = 0.05
noise = rng.lognormal(
    mean=-(sigma ** 2) / 2,
    sigma=sigma,
    size=N_ROWS,
)
score = np.round(score_without_noise * noise, 2)

data = pd.DataFrame({
    "sample_id": np.arange(1, N_ROWS + 1),
    "cpu_cores": cores,
    "cpu_threads": threads,
    "cpu_frequency_ghz": frequency,
    "ram_gb": ram,
    "storage_type": storage_type,
    "storage_gb": storage_gb,
    "synthetic_score": score,
    "data_origin": "synthetic",
})

project_dir = Path(__file__).resolve().parents[1]
output_dir = project_dir / "data" / "synthetic"
output_dir.mkdir(parents=True, exist_ok=True)

output_path = output_dir / "laptops_synthetic.csv"
data.to_csv(output_path, index=False, encoding="utf-8")

print("Создано наблюдений:", len(data))
print("Зерно генератора:", SEED)
print("Пропусков:", int(data.isna().sum().sum()))
print("Файл:", output_path)
print("\nПервые пять строк:")
print(data.head().to_string(index=False))