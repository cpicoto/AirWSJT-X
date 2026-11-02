from __future__ import annotations

from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from airwsjtx.speech import MAX_TEXT_LENGTH, PiperSpeech, _player_command
from sidecar import airwsjtx_bridge as bridge


class FakeStdin:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        if self.closed:
            raise BrokenPipeError
        self.data.extend(data)

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.stdin = FakeStdin()
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


class FakeVoice:
    config = SimpleNamespace(sample_rate=22050)

    def __init__(self, gate: threading.Event | None = None) -> None:
        self.gate = gate
        self.texts: list[str] = []

    def synthesize(self, text: str):
        self.texts.append(text)
        if self.gate is not None:
            self.gate.wait(2)
        yield SimpleNamespace(audio_int16_bytes=b"neural-audio")


class FailingVoice(FakeVoice):
    def synthesize(self, text: str):
        self.texts.append(text)
        raise RuntimeError("synthesis failed")
        yield  # pragma: no cover - preserve generator semantics


def wait_for(predicate, timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for speech worker")
        time.sleep(0.01)


class PiperSpeechTests(unittest.TestCase):
    def manager(self, tmp: Path, voice: FakeVoice, processes: list[FakeProcess]) -> PiperSpeech:
        model = tmp / "voice.onnx"
        model.write_bytes(b"model")

        def spawn(command, **_kwargs):
            process = FakeProcess(command)
            processes.append(process)
            return process

        return PiperSpeech(model_path=model, voice_loader=lambda _path: voice,
                           which=lambda name: f"/usr/bin/{name}" if name == "paplay" else None,
                           popen=spawn)

    def test_prefers_paplay_and_has_portable_fallbacks(self):
        name, command = _player_command(lambda candidate: f"/bin/{candidate}", 22050)
        self.assertEqual(name, "paplay")
        self.assertEqual(command[:3], ["/bin/paplay", "--raw", "--format=s16le"])
        name, command = _player_command(
            lambda candidate: f"/bin/{candidate}" if candidate == "pw-play" else None, 22050)
        self.assertEqual(name, "pw-play")
        self.assertNotIn("--raw", command)
        with self.assertRaisesRegex(RuntimeError, "no audio player"):
            _player_command(lambda _candidate: None, 22050)

    def test_keeps_voice_loaded_and_streams_normalized_text(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory, patch("airwsjtx.speech.sys.platform", "linux"):
            voice, processes = FakeVoice(), []
            manager = self.manager(Path(directory), voice, processes)
            first = manager.speak(" Azimuth  10\nElevation 20 ")
            wait_for(lambda: len(processes) == 1 and processes[0].returncode is not None)
            second = manager.speak("Azimuth 11")
            wait_for(lambda: len(processes) == 2 and processes[1].returncode is not None)
            self.assertEqual(first, second)
            self.assertEqual(voice.texts, ["Azimuth 10 Elevation 20", "Azimuth 11"])
            self.assertEqual(processes[0].stdin.data, b"neural-audio")
            self.assertTrue(manager.status()["loaded"])
            self.assertEqual(manager.probe()["audio_bytes"], len(b"neural-audio"))
            manager.shutdown()

    def test_synthesis_failure_terminates_and_reaps_player(self):
        import tempfile
        errors: list[str] = []
        with tempfile.TemporaryDirectory() as directory, patch("airwsjtx.speech.sys.platform", "linux"):
            voice, processes = FailingVoice(), []
            manager = self.manager(Path(directory), voice, processes)
            manager._on_error = errors.append
            manager.speak("Azimuth 10")
            wait_for(lambda: bool(errors))
            self.assertTrue(processes[0].terminated)
            self.assertIsNotNone(processes[0].returncode)
            manager.shutdown()

    def test_stop_terminates_only_current_player(self):
        import tempfile
        gate = threading.Event()
        with tempfile.TemporaryDirectory() as directory, patch("airwsjtx.speech.sys.platform", "linux"):
            voice, processes = FakeVoice(gate), []
            manager = self.manager(Path(directory), voice, processes)
            manager.speak("Azimuth 10")
            wait_for(lambda: bool(voice.texts))
            manager.stop()
            self.assertTrue(processes[0].terminated)
            gate.set()
            manager.shutdown()

    def test_rejects_missing_runtime_inputs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory, patch("airwsjtx.speech.sys.platform", "linux"):
            manager = PiperSpeech(model_path=Path(directory) / "missing.onnx")
            with self.assertRaisesRegex(RuntimeError, "voice is missing"):
                manager.speak("test")
            with self.assertRaisesRegex(ValueError, "empty"):
                manager.speak("  ")
            with self.assertRaisesRegex(ValueError, "exceeds"):
                manager.speak("x" * (MAX_TEXT_LENGTH + 1))
            manager.shutdown()

    def test_bridge_exposes_start_stop_and_status(self):
        speech = Mock()
        speech.status.return_value = {"supported": True, "engine": "piper"}
        speech.probe.return_value = {"engine": "piper", "sample_rate": 22050, "audio_bytes": 128}
        speech.speak.return_value = {"engine": "piper", "voice": "test"}
        with patch.object(bridge, "_speech", speech), patch.object(bridge, "_emit") as emit:
            bridge.handle({"id": "status", "method": "speech_status", "params": {}})
            bridge.handle({"id": "probe", "method": "speech_probe", "params": {}})
            bridge.handle({"id": "speak", "method": "speech_speak", "params": {"text": "Azimuth 10"}})
            bridge.handle({"id": "stop", "method": "speech_stop", "params": {}})
        speech.speak.assert_called_once_with("Azimuth 10")
        speech.stop.assert_called_once()
        speech.probe.assert_called_once()
        replies = [call.args[0] for call in emit.call_args_list]
        self.assertEqual([reply["id"] for reply in replies], ["status", "probe", "speak", "stop"])
        self.assertTrue(all(reply["ok"] for reply in replies))


if __name__ == "__main__":
    unittest.main()
