import { spawnSync } from "node:child_process";

const cargo = spawnSync("cargo", ["--version"], {
  encoding: "utf8",
  stdio: ["ignore", "pipe", "pipe"],
});

if (cargo.status === 0) {
  process.exit(0);
}

console.error(`
Tauri requires Rust/Cargo, but \`cargo\` was not found on PATH.

Install Rust, then open a new shell and run this command again.

Recommended:
  rustup: https://rustup.rs/

Arch Linux:
  sudo pacman -S rustup
  rustup default stable

If Rust is already installed, make sure Cargo's bin directory is on PATH:
  source "$HOME/.cargo/env"
`);

process.exit(1);
