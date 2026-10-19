"""Reject emulated x64 payloads and smoke-test the frozen ARM64 sidecar."""
from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import tempfile
from pathlib import Path


def require_arm64(path: Path) -> None:
    with path.open("rb") as binary:
        header = binary.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise ValueError(f"{path}: not a Windows PE executable")
        binary.seek(struct.unpack_from("<I", header, 60)[0])
        coff = binary.read(6)
    if len(coff) != 6 or coff[:4] != b"PE\0\0":
        raise ValueError(f"{path}: invalid PE header")
    machine = struct.unpack_from("<H", coff, 4)[0]
    if machine != 0xAA64:
        raise ValueError(f"{path}: expected native ARM64 (0xAA64), found 0x{machine:04X}")


def check_sidecar(path: Path) -> None:
    request = {"id": "arm64-smoke", "method": "get_status", "params": {}}
    with tempfile.TemporaryDirectory() as directory:
        result = subprocess.run(
            [str(path.resolve())],
            input=json.dumps(request) + "\n",
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
            cwd=directory,
            env={**os.environ, "AIRWSJTX_DATA_DIR": directory},
        )
    if result.returncode:
        raise RuntimeError(f"Sidecar exited {result.returncode}: {result.stderr}")
    messages = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    replies = [message for message in messages if message.get("id") == request["id"]]
    if len(replies) != 1 or replies[0].get("ok") is not True:
        raise RuntimeError(f"Missing successful get_status reply: {result.stdout}\n{result.stderr}")
    if replies[0].get("result", {}).get("state") != "idle":
        raise RuntimeError(f"Unexpected initial sidecar state: {replies[0]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--sidecar", required=True, type=Path)
    args = parser.parse_args()
    require_arm64(args.app)
    require_arm64(args.sidecar)
    check_sidecar(args.sidecar)
    print("App and sidecar are native ARM64; packaged sidecar get_status succeeded.")
