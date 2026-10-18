"""Launch the packaged backend through an App Sandbox parent, not the CI shell."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import tempfile


def verify(app: Path, identity: str) -> None:
    with tempfile.TemporaryDirectory(prefix="airwsjtx-sandbox-probe-") as temporary:
        root = Path(temporary)
        probe = root / "SidecarProbe.app"
        shutil.copytree(app, probe, symlinks=True)
        contents = probe / "Contents"
        info_path = contents / "Info.plist"
        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
        executable = contents / "MacOS" / info["CFBundleExecutable"]
        info["CFBundleIdentifier"] = "us.ad7np.airwsjtx.sidecar-probe"
        with info_path.open("wb") as stream:
            plistlib.dump(info, stream)
        profile = contents / "embedded.provisionprofile"
        if profile.exists():
            profile.unlink()
        executable.unlink()
        subprocess.run([
            "clang", "-Wall", "-Wextra", "-Werror",
            str(Path(__file__).with_name("macos_sidecar_probe.c")),
            "-o", str(executable),
        ], check=True)

        entitlements = root / "sandbox.plist"
        with entitlements.open("wb") as stream:
            plistlib.dump({
                "com.apple.security.app-sandbox": True,
                "com.apple.security.network.client": True,
                "com.apple.security.network.server": True,
            }, stream)
        subprocess.run([
            "codesign", "--force", "--options", "runtime", "--timestamp",
            "--entitlements", str(entitlements), "--sign", identity, str(probe),
        ], check=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(probe)], check=True)

        with subprocess.Popen(
            [str(executable), str(contents / "MacOS/airwsjtx_bridge")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        ) as process:
            try:
                stdout, stderr = process.communicate(
                    json.dumps({"id": "sandbox-smoke", "method": "get_status"}) + "\n",
                    timeout=30,
                )
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                _, stderr = process.communicate()
                raise SystemExit(f"Sandboxed sidecar timed out: {stderr}")
        if process.returncode:
            raise SystemExit(f"Sandboxed sidecar failed ({process.returncode}): {stderr}")
        replies = [json.loads(line) for line in stdout.splitlines()]
        reply = next((item for item in replies if item.get("id") == "sandbox-smoke"), None)
        if not reply or not reply.get("ok"):
            raise SystemExit(f"Sandboxed sidecar did not answer get_status: {stderr}")
        print("Sandboxed packaged sidecar answered get_status successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--identity", required=True)
    args = parser.parse_args()
    verify(args.app.resolve(), args.identity)
