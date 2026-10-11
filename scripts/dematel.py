from pathlib import Path

import numpy as np
import pandas as pd

project_dir = Path(__file__).resolve().parents[1]
table = pd.read_csv(
    project_dir / "data" / "decision" / "dematel_direct.csv",
    index_col=0,
)

if list(table.index) != list(table.columns):
    raise ValueError("Порядок критериев в строках и столбцах различается.")

A = table.to_numpy(dtype=float)

if not np.isfinite(A).all():
    raise ValueError("Матрица содержит пропуски или бесконечные значения.")

if ((A < 0) | (A > 4)).any():
    raise ValueError("Оценки должны находиться в диапазоне от 0 до 4.")

if not np.allclose(np.diag(A), 0):
    raise ValueError("На диагонали должны быть нули.")

row_sums = A.sum(axis=1)
column_sums = A.sum(axis=0)

print("Суммы строк:", row_sums)
print("Суммы столбцов:", column_sums)
s = max(row_sums.max(), column_sums.max())
print("Делитель:", s)
if s == 0:
    raise ValueError("Все оценки равны нулю: нормирование невозможно.")

N = A / s

print("\nНормированная матрица:")
print(pd.DataFrame(
    N,
    index=table.index,
    columns=table.columns,
).round(3).to_string())
# Проверяем условие сходимости полного влияния.
spectral_radius = float(np.max(np.abs(np.linalg.eigvals(N))))
if spectral_radius >= 1:
    raise ValueError("Матрица не удовлетворяет условию сходимости DEMATEL.")

I = np.eye(len(N))

# Формула T = N @ (I - N)^(-1).
# Решаем эквивалентную систему без явного обращения матрицы.
T = np.linalg.solve((I - N).T, N.T).T

D = T.sum(axis=1)
R = T.sum(axis=0)

summary = pd.DataFrame({
    "D": D,
    "R": R,
    "D_plus_R": D + R,
    "D_minus_R": D - R,
}, index=table.index)

print("\nМатрица полного влияния:")
print(pd.DataFrame(
    T,
    index=table.index,
    columns=table.columns,
).round(3).to_string())

print("\nПоказатели DEMATEL:")
print(summary.round(3).to_string())
print("\nСумма D:", round(float(D.sum()), 3))
print("Сумма R:", round(float(R.sum()), 3))
print("Для этой матрицы T совпадает с N:", np.allclose(T, N))
# Контрольные значения из ручного расчёта.
expected_D = np.array([0, 1, 0, 0, 0.25])
expected_R = np.array([0.625, 0, 0.25, 0.375, 0])

control_error = max(
    float(np.max(np.abs(D - expected_D))),
    float(np.max(np.abs(R - expected_R))),
)

if control_error > 0.001:
    raise ValueError("Результат отличается от контрольного расчёта.")

output_dir = project_dir / "results" / "dematel"
output_dir.mkdir(parents=True, exist_ok=True)

pd.DataFrame(
    N, index=table.index, columns=table.columns
).to_csv(output_dir / "normalized_matrix.csv")

pd.DataFrame(
    T, index=table.index, columns=table.columns
).to_csv(output_dir / "total_influence.csv")

summary.to_csv(output_dir / "indicators.csv")

print("\nМаксимальное отклонение от ручного расчёта:", control_error)
print("Результаты сохранены:", output_dir)