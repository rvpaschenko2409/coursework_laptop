import csv
import io
from pathlib import Path
from zipfile import ZipFile

project_dir = Path(__file__).resolve().parents[1]
archive_path = (
    project_dir / "data" / "raw" / "notebook_cpu_performance.zip"
)

rows = []

with ZipFile(archive_path) as archive:
    csv_files = [
        name
        for name in archive.namelist()
        if "/Geekbench 6/" in name and name.endswith(".csv")
    ]

    for name in csv_files:
        text = archive.read(name).decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows.extend(reader)

print("Файлов с результатами:", len(csv_files))
print("Записей тестирования:", len(rows))

unique_urls = {row["URL"] for row in rows}
print("Повторяющихся ссылок:", len(rows) - len(unique_urls))

if rows:
    print("\nКоличество пропусков по столбцам:")
    for column in rows[0]:
        missing = sum(
            not (row.get(column) or "").strip()
            for row in rows
        )
        print(f"{column}: {missing}")