import { spawnSync } from "node:child_process";
import { chmodSync, copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const frontendDir = resolve(scriptDir, "..");
const repoDir = resolve(frontendDir, "..");
const tauriDir = join(frontendDir, "src-tauri");
const venvDir = join(frontendDir, ".venv-backend-package");
const entryPoint = join(tauriDir, "python", "kajas_backend_entry.py");
const distDir = join(tauriDir, "target", "pyinstaller-dist");
const workDir = join(tauriDir, "target", "pyinstaller-work");
const specDir = join(tauriDir, "target", "pyinstaller-spec");
const binariesDir = join(tauriDir, "binaries");
const rawBinaryName = process.platform === "win32" ? "kajas-backend.exe" : "kajas-backend";

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? frontendDir,
    encoding: "utf8",
    stdio: options.stdio ?? "inherit",
  });
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
  return result;
}

function output(command, args) {
  const result = spawnSync(command, args, {
    cwd: frontendDir,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "inherit"],
  });
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
  return result.stdout.trim();
}

function pythonPath() {
  if (process.platform === "win32") {
    return join(venvDir, "Scripts", "python.exe");
  }
  return join(venvDir, "bin", "python");
}

if (!existsSync(pythonPath())) {
  run("python3", ["-m", "venv", venvDir]);
}

const python = pythonPath();
const depsReady = spawnSync(
  python,
  [
    "-c",
    [
      "import PyInstaller",
      "import fastapi",
      "import uvicorn",
      "import pydantic",
      "import yaml",
      "import argon2",
      "import httpx",
      "import watchfiles",
      "import multipart",
    ].join("; "),
  ],
  {
    cwd: frontendDir,
    encoding: "utf8",
    stdio: "ignore",
  },
);

if (depsReady.status !== 0) {
  run(python, ["-m", "pip", "install", "--upgrade", "pip"]);
  const dependencies = output(python, [
  "-c",
  [
    "import json, pathlib, tomllib",
    "data = tomllib.loads(pathlib.Path('../backend/pyproject.toml').read_text())",
    "print(json.dumps(data['project']['dependencies']))",
  ].join("; "),
  ]);
  run(python, [
    "-m",
    "pip",
    "install",
    "--upgrade",
    "pyinstaller",
    ...JSON.parse(dependencies),
  ]);
}

mkdirSync(distDir, { recursive: true });
mkdirSync(workDir, { recursive: true });
mkdirSync(specDir, { recursive: true });
mkdirSync(binariesDir, { recursive: true });

run(python, [
  "-m",
  "PyInstaller",
  "--clean",
  "--noconfirm",
  "--onefile",
  "--name",
  "kajas-backend",
  "--paths",
  resolve(repoDir, "backend"),
  "--distpath",
  distDir,
  "--workpath",
  workDir,
  "--specpath",
  specDir,
  "--collect-submodules",
  "uvicorn",
  "--collect-submodules",
  "uvicorn.protocols",
  "--collect-submodules",
  "uvicorn.loops",
  "--collect-submodules",
  "uvicorn.lifespan",
  "--hidden-import",
  "yaml",
  "--hidden-import",
  "multipart",
  "--hidden-import",
  "argon2",
  entryPoint,
]);

const targetTriple = output("rustc", ["--print", "host-tuple"]);
const extension = process.platform === "win32" ? ".exe" : "";
const source = join(distDir, rawBinaryName);
const target = join(binariesDir, `kajas-backend-${targetTriple}${extension}`);

copyFileSync(source, target);
if (process.platform !== "win32") {
  chmodSync(target, 0o755);
}

console.log(`Packaged backend sidecar: ${target}`);
