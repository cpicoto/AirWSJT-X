"""Keep desktop release metadata aligned with the release tag."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


def sync_version(root: Path, tag: str) -> None:
    version = tag.removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version):
        raise ValueError(f"Invalid release version: {tag!r}")
    cargo_path = root / "src-tauri" / "Cargo.toml"
    cargo, count = re.subn(
        r'(?m)^version\s*=\s*"[^"]+"$',
        f'version = "{version}"',
        cargo_path.read_text(encoding="utf-8"),
        count=1,
    )
    if count != 1:
        raise ValueError("No package version in src-tauri/Cargo.toml")
    for path in (root / "package.json", root / "src-tauri" / "tauri.conf.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = version
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    cargo_path.write_text(cargo, encoding="utf-8")


if __name__ == "__main__":
    sync_version(Path(__file__).resolve().parent.parent, os.environ["RELEASE_VERSION"])
