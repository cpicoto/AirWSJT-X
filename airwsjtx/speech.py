"""App-owned Piper speech worker for Linux desktop callouts."""

from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
from typing import Any, Callable


VOICE_NAME = "en_US-ljspeech-medium"
MAX_TEXT_LENGTH = 500


def voice_directory() -> Path:
    """Return the packaged voice directory in source and frozen builds."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1] / "sidecar"))
    return root / "piper-voice"


def _load_voice(model_path: Path) -> Any:
    piper = importlib.import_module("piper")
    return piper.PiperVoice.load(model_path)


def _player_command(which: Callable[[str], str | None], sample_rate: int) -> tuple[str, list[str]]:
    candidates = (
        ("paplay", ["paplay", "--raw", "--format=s16le", f"--rate={sample_rate}", "--channels=1"]),
        ("pw-play", ["pw-play", "--raw", "--format=s16", f"--rate={sample_rate}", "--channels=1", "-"]),
        ("aplay", ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(sample_rate), "-c", "1"]),
        ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-f", "s16le",
                    "-ar", str(sample_rate), "-ac", "1", "-"]),
    )
    for name, command in candidates:
        executable = which(name)
        if executable:
            return name, [executable, *command[1:]]
    raise RuntimeError("Piper is bundled, but no audio player is available (paplay, pw-play, aplay or ffplay)")


class PiperSpeech:
    """Keep the neural voice loaded while replacing/cancelling short callouts."""

    def __init__(
        self,
        *,
        model_path: Path | None = None,
        voice_loader: Callable[[Path], Any] = _load_voice,
        which: Callable[[str], str | None] = shutil.which,
        popen: Callable[..., Any] = subprocess.Popen,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.model_path = model_path or voice_directory() / f"{VOICE_NAME}.onnx"
        self._voice_loader = voice_loader
        self._which = which
        self._popen = popen
        self._on_error = on_error or (lambda message: print(message, file=sys.stderr, flush=True))
        self._voice: Any | None = None
        self._player: Any | None = None
        self._generation = 0
        self._lock = threading.RLock()
        self._requests: queue.Queue[tuple[int, str, Any] | None] = queue.Queue(maxsize=1)
        self._worker = threading.Thread(target=self._run, name="airwsjtx-piper", daemon=True)
        self._worker.start()

    @property
    def supported(self) -> bool:
        return (sys.platform.startswith("linux") and self.model_path.is_file()
                and importlib.util.find_spec("piper") is not None)

    def _ensure_voice(self) -> Any:
        if not sys.platform.startswith("linux"):
            raise RuntimeError("Piper speech is only available on Linux")
        if not self.model_path.is_file():
            raise RuntimeError("The bundled Piper US-English voice is missing")
        with self._lock:
            if self._voice is None:
                self._voice = self._voice_loader(self.model_path)
            return self._voice

    def _stop_player_locked(self) -> None:
        player = self._player
        self._player = None
        if player is None or player.poll() is not None:
            return
        player.terminate()
        try:
            player.wait(timeout=1)
        except subprocess.TimeoutExpired:
            player.kill()
            player.wait(timeout=1)

    def _drain_requests(self) -> None:
        while True:
            try:
                self._requests.get_nowait()
            except queue.Empty:
                return

    def speak(self, text: str) -> dict[str, str]:
        if not isinstance(text, str):
            raise ValueError("Speech text must be a string")
        text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
        if not text:
            raise ValueError("Speech text is empty")
        if len(text) > MAX_TEXT_LENGTH:
            raise ValueError(f"Speech text exceeds {MAX_TEXT_LENGTH} characters")
        voice = self._ensure_voice()
        sample_rate = int(voice.config.sample_rate)
        player_name, command = _player_command(self._which, sample_rate)
        player = self._popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if player.stdin is None:
            player.terminate()
            raise RuntimeError(f"Cannot open {player_name} audio input")
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._stop_player_locked()
            self._drain_requests()
            self._player = player
            self._requests.put_nowait((generation, text, player))
        return {"engine": "piper", "voice": VOICE_NAME, "player": player_name}

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            self._stop_player_locked()
            self._drain_requests()

    def shutdown(self) -> None:
        self.stop()
        self._requests.put(None)
        self._worker.join(timeout=2)

    def status(self) -> dict[str, object]:
        with self._lock:
            active = self._player is not None and self._player.poll() is None
            loaded = self._voice is not None
        return {"supported": self.supported, "engine": "piper", "voice": VOICE_NAME,
                "runtime_available": importlib.util.find_spec("piper") is not None,
                "model_available": self.model_path.is_file(), "loaded": loaded, "active": active}

    def _run(self) -> None:
        while True:
            request = self._requests.get()
            if request is None:
                return
            generation, text, player = request
            try:
                voice = self._voice
                if voice is None:
                    raise RuntimeError("Piper voice was not loaded")
                for chunk in voice.synthesize(text):
                    with self._lock:
                        if generation != self._generation or self._player is not player:
                            break
                    player.stdin.write(chunk.audio_int16_bytes)
                if player.stdin and not player.stdin.closed:
                    player.stdin.close()
                code = player.wait()
                with self._lock:
                    current = generation == self._generation and self._player is player
                if current and code:
                    raise RuntimeError(f"Audio player exited with status {code}")
            except (BrokenPipeError, OSError) as error:
                with self._lock:
                    current = generation == self._generation and self._player is player
                if current:
                    self._on_error(f"Piper audio failed: {error}")
            except Exception as error:
                with self._lock:
                    current = generation == self._generation and self._player is player
                if current:
                    self._on_error(f"Piper speech failed: {error}")
            finally:
                with self._lock:
                    if generation == self._generation and self._player is player:
                        self._player = None
