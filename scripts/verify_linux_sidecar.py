"""Verify Linux ELF architecture and frozen backend startup outside the checkout."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


ELF_MACHINES = {"x86_64": 62, "aarch64": 183}


def require_header(header: bytes, machine: str, name: str) -> None:
    if len(header) != 20 or header[:6] != b"\x7fELF\x02\x01":
        raise RuntimeError(f"Not a little-endian 64-bit ELF binary: {name}")
    actual = int.from_bytes(header[18:20], "little")
    if actual != ELF_MACHINES[machine]:
        raise RuntimeError(f"Expected {machine} ELF machine, got {actual}: {name}")


def require_machine(path: Path, machine: str) -> None:
    with path.open("rb") as stream:
        require_header(stream.read(20), machine, str(path))


def check_receiver_archive(path: Path, machine: str) -> None:
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(path))
    for name in ("rtl-sdr/airwsjtx_adsb", "rtl-sdr/librtlsdr.so"):
        if name not in archive.toc:
            raise RuntimeError(f"Missing bundled receiver binary: {name}")
        require_header(archive.extract(name)[:20], machine, name)


def check_piper_archive(path: Path) -> None:
    from PyInstaller.archive.readers import CArchiveReader

    names = set(CArchiveReader(str(path)).toc)
    for name in (
        "piper-voice/en_US-ljspeech-medium.onnx",
        "piper-voice/en_US-ljspeech-medium.onnx.json",
        "piper-voice/MODEL_CARD",
        "piper-voice/COPYING-Piper",
        "piper-voice/SOURCE.txt",
        "piper-voice/piper_tts-1.8.0.tar.gz",
    ):
        if name not in names:
            raise RuntimeError(f"Missing bundled Piper file: {name}")


def check_sidecar(path: Path) -> None:
    requests = [
        {"id": "linux-status", "method": "get_status", "params": {}},
        {"id": "linux-receiver", "method": "rtlsdr_status", "params": {}},
        {"id": "linux-speech", "method": "speech_status", "params": {}},
    ]
    with tempfile.TemporaryDirectory(prefix="airwsjtx-linux-smoke-") as directory:
        result = subprocess.run(
            [str(path.resolve())],
            input="".join(json.dumps(request) + "\n" for request in requests),
            capture_output=True, text=True, timeout=30, cwd=directory,
            env={**os.environ, "AIRWSJTX_DATA_DIR": directory},
        )
    if result.returncode:
        raise RuntimeError(f"Sidecar exited {result.returncode}: {result.stderr}")
    messages = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    for request in requests:
        replies = [message for message in messages if message.get("id") == request["id"]]
        if len(replies) != 1 or replies[0].get("ok") is not True:
            raise RuntimeError(f"Missing successful {request['method']} reply: {result.stdout}\n{result.stderr}")
        payload = replies[0].get("result", {})
        if request["method"] == "get_status" and payload.get("state") != "idle":
            raise RuntimeError(f"Unexpected initial sidecar state: {payload}")
        if request["method"] == "rtlsdr_status" and payload.get("state") not in (
                "stopped", "connecting", "connected", "error"):
            raise RuntimeError(f"Unexpected receiver status: {payload}")
        if request["method"] == "speech_status" and not (
                payload.get("engine") == "piper" and payload.get("runtime_available") is True
                and payload.get("model_available") is True and payload.get("supported") is True):
            raise RuntimeError(f"Bundled Piper runtime is unavailable: {payload}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--machine", choices=ELF_MACHINES, required=True)
    args = parser.parse_args()
    require_machine(args.app, args.machine)
    require_machine(args.sidecar, args.machine)
    check_receiver_archive(args.sidecar, args.machine)
    check_piper_archive(args.sidecar)
    check_sidecar(args.sidecar)
    print(f"Linux {args.machine} app/sidecar verified; frozen backend, RTL-SDR and Piper succeeded.")
