from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

project_dir = Path(__file__).resolve().parents[1]
data = pd.read_csv(project_dir / "results" / "test_predictions.csv")

columns = ["ridge_prediction", "random_forest_prediction"]
titles = ["Линейная модель Ridge", "Случайный лес"]

values = data[["synthetic_score"] + columns]
lower = values.min().min()
upper = values.max().max()
margin = (upper - lower) * 0.05
limits = (lower - margin, upper + margin)

fig, axes = plt.subplots(
    1, 2, figsize=(10, 5), sharex=True, sharey=True,
    layout="constrained",
)

for ax, column, title in zip(axes, columns, titles):
    ax.scatter(
        data["synthetic_score"],
        data[column],
        s=18,
        alpha=0.45,
        color="#2463A0",
        edgecolors="none",
        label="Тестовые наблюдения",
    )
    ax.plot(
        limits, limits,
        color="#B94040",
        linestyle="--",
        linewidth=1.5,
        label="Идеальный прогноз",
    )
    ax.set_title(title)
    ax.set_xlabel("Целевой синтетический балл")
    ax.set_xlim(limits)
    ax.set_ylim(limits)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(alpha=0.2)
    ax.legend(fontsize=8)

axes[0].set_ylabel("Прогноз модели, баллы")
fig.suptitle("Прогнозы на отложенных конфигурациях")

output_dir = project_dir / "results" / "figures"
output_dir.mkdir(parents=True, exist_ok=True)

output_path = output_dir / "01_test_predictions.png"
fig.savefig(output_path, dpi=300, facecolor="white")
plt.close(fig)

print("График сохранён:", output_path)