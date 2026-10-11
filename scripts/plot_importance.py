from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

project_dir = Path(__file__).resolve().parents[1]
data = pd.read_csv(project_dir / "results" / "feature_importance.csv")
data = data.sort_values("mae_increase_mean")

labels = {
    "cpu_cores": "Количество ядер",
    "cpu_threads": "Количество потоков",
    "cpu_frequency_ghz": "Частота процессора",
    "ram_gb": "Объём оперативной памяти",
    "storage_gb": "Ёмкость накопителя",
    "storage_type": "Тип накопителя",
}

fig, ax = plt.subplots(figsize=(9, 5), layout="constrained")

ax.barh(
    data["feature"].map(labels),
    data["mae_increase_mean"],
    xerr=data["mae_increase_std"],
    color="#2463A0",
    capsize=4,
)
ax.axvline(0, color="black", linewidth=0.8)
ax.set_xlabel("Среднее увеличение MAE, баллы")
ax.set_title("Перестановочная важность признаков\n"
             "Случайный лес, синтетическая тестовая выборка")
ax.set_axisbelow(True)
ax.grid(axis="x", alpha=0.2)

fig.supxlabel(
    "Отрезки: ±1 стандартное отклонение по 30 перемешиваниям",
    fontsize=9,
)

output_dir = project_dir / "results" / "figures"
output_dir.mkdir(parents=True, exist_ok=True)
output_path = output_dir / "02_feature_importance.png"

fig.savefig(output_path, dpi=300, facecolor="white")
plt.close(fig)

print("График сохранён:", output_path)