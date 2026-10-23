import { execFileSync } from "node:child_process";
import { copyFileSync, cpSync, existsSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const binaries = path.join(root, "src-tauri", "binaries");
const target = execFileSync("rustc", ["-vV"], { encoding: "utf8" })
  .match(/^host: (.+)$/m)?.[1].trim();
if (!target) throw new Error("Cannot determine the Rust host target");

const venvPython = process.platform === "win32"
  ? path.join(root, ".venv", "Scripts", "python.exe")
  : path.join(root, ".venv", "bin", "python");
const python = existsSync(venvPython) ? venvPython : "python";

execFileSync(python, [
  "-m", "PyInstaller", "--noconfirm", "--clean",
  "--distpath", binaries,
  path.join(root, "sidecar", "airwsjtx_bridge.spec"),
], { cwd: root, stdio: "inherit" });

if (process.platform === "win32") {
  const output = path.join(binaries, "airwsjtx_bridge");
  copyFileSync(
    path.join(output, "airwsjtx_bridge.exe"),
    path.join(binaries, `airwsjtx_bridge-${target}.exe`),
  );
  const runtime = path.join(binaries, "airwsjtx_bridge-runtime");
  rmSync(runtime, { recursive: true, force: true });
  cpSync(path.join(output, "airwsjtx_bridge-runtime"), runtime, { recursive: true });
} else {
  copyFileSync(
    path.join(binaries, "airwsjtx_bridge"),
    path.join(binaries, `airwsjtx_bridge-${target}`),
  );
}
console.log(`Staged freshly built desktop sidecar for ${target}`);
