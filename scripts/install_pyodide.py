"""Install the pinned browser Python runtime into the application's static directory."""

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/"


def install(source: Path | None) -> None:
    target = ROOT / "web_app/static/pyodide"
    assets = json.loads(Path(__file__).with_name("pyodide_assets.json").read_text(encoding="utf-8"))
    target.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=target) as temporary:
        staging = Path(temporary)
        for name, digest in assets.items():
            destination = target / name
            if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == digest:
                continue
            if source is None:
                with urlopen(BASE_URL + name, timeout=60) as response:
                    content = response.read(16 * 1024 * 1024 + 1)
            else:
                content = (source / name).read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError(f"SHA-256 mismatch: {name}")
            (staging / name).write_bytes(content)
        for path in staging.iterdir():
            path.replace(target / path.name)

    print(f"Pyodide 0.27.7: {len(assets)} assets verified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Use a local directory of Pyodide assets")
    args = parser.parse_args()
    install(args.source)
