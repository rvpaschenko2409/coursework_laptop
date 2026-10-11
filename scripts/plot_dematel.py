from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

project_dir = Path(__file__).resolve().parents[1]
data = pd.read_csv(
    project_dir / "results" / "dematel" / "indicators.csv",
    index_col=0,
)

labels = {
    "price": "Цена",
    "performance": "Производительность",
    "mass": "Масса",
    "battery_life": "Автономность",
    "warranty": "Гарантия",
}

fig, ax = plt.subplots(figsize=(8, 5), layout="constrained")

for criterion, row in data.iterrows():
    x = row["D_plus_R"]
    y = row["D_minus_R"]
    color = "#2463A0" if y > 0 else "#B94040"

    ax.scatter(x, y, color=color, s=65, zorder=3)
    ax.annotate(
        labels[criterion],
        (x, y),
        xytext=(8, 8),
        textcoords="offset points",
        fontsize=10,
    )

ax.axhline(0, color="black", linewidth=1)
ax.set_xlabel("D + R — общая вовлечённость")
ax.set_ylabel("D − R — баланс влияния")
ax.set_title("Диаграмма DEMATEL\nУчебная матрица влияния критериев")
ax.set_xlim(0, 1.35)
ax.set_ylim(-0.85, 1.25)
ax.grid(alpha=0.2)
ax.set_axisbelow(True)

output_dir = project_dir / "results" / "figures"
output_dir.mkdir(parents=True, exist_ok=True)
output_path = output_dir / "03_dematel_diagram.png"

fig.savefig(output_path, dpi=300, facecolor="white")
plt.close(fig)

print("Диаграмма сохранена:", output_path)