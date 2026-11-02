"""Stage the pinned US-English Piper model and corresponding license/source files."""

from __future__ import annotations

import hashlib
from pathlib import Path
import urllib.request


PIPER_VERSION = "1.8.0"
VOICE_REVISION = "b4e358ffcb232591bf73a540e70f7de528e4174a"
VOICE_BASE = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/"
    f"{VOICE_REVISION}/en/en_US/ljspeech/medium"
)
PIPER_SOURCE_URL = (
    "https://files.pythonhosted.org/packages/02/cc/95b18b58d9c235d8e0bcece7321b7a7347873c14f9fea2c734b31ef04ff2/"
    f"piper_tts-{PIPER_VERSION}.tar.gz"
)
PIPER_LICENSE_URL = f"https://raw.githubusercontent.com/OHF-Voice/piper1-gpl/v{PIPER_VERSION}/COPYING"
DESTINATION = Path(__file__).resolve().parents[1] / "sidecar" / "piper-voice"
FILES = (
    ("en_US-ljspeech-medium.onnx", f"{VOICE_BASE}/en_US-ljspeech-medium.onnx",
     "6f52a751e2349abe7a76735eb09dc1875298c77ea2342ffd2fef79ff81b87f22"),
    ("en_US-ljspeech-medium.onnx.json", f"{VOICE_BASE}/en_US-ljspeech-medium.onnx.json",
     "141d612cc0a95ed7efc1ca936b845c2364967f2e9217c5dbfcf69fc4d6c65860"),
    ("MODEL_CARD", f"{VOICE_BASE}/MODEL_CARD",
     "fbee1529c89d36b3fe76d7e9f3f832dce17f44900a52d76a9bda735654766b4d"),
    (f"piper_tts-{PIPER_VERSION}.tar.gz", PIPER_SOURCE_URL,
     "830588aded347df579c91a32703e0fc2a3685d84f1e3533b14f2de69135d4904"),
    ("COPYING-Piper", PIPER_LICENSE_URL,
     "0ae0485a5bd37a63e63603596417e4eb0e653334fa6c7f932ca3a0e85d4af227"),
)


def download(url: str, expected_hash: str) -> bytes:
    with urllib.request.urlopen(url, timeout=180) as response:
        content = response.read()
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected_hash:
        raise RuntimeError(f"SHA256 mismatch for {url}: expected {expected_hash}, got {actual}")
    return content


def main() -> None:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for name, url, checksum in FILES:
        (DESTINATION / name).write_bytes(download(url, checksum))
    (DESTINATION / "SOURCE.txt").write_text(
        "Piper neural text-to-speech engine " + PIPER_VERSION + " (GPL-3.0-or-later).\n"
        f"Source: {PIPER_SOURCE_URL}\n"
        "The complete corresponding source archive and COPYING-Piper are included here.\n"
        "Voice: en_US-ljspeech-medium, US English female, 22050 Hz.\n"
        f"Voice repository revision: {VOICE_REVISION}\n"
        "The model card identifies the LJ Speech training dataset as public domain; see MODEL_CARD.\n",
        encoding="utf-8",
    )
    print(f"Staged verified Piper voice and source in {DESTINATION}")


if __name__ == "__main__":
    main()
