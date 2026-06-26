# Kajas releases

Distribution binaries live on [GitHub Releases][releases], not in git
history. This directory holds ready-to-upload artifacts (gitignored)
plus their published checksums and the publish recipe.

[releases]: https://github.com/nikolasp/kajas/releases

## v0.1.1 — macOS (Apple Silicon)

| File | Target | Size | SHA-256 |
| --- | --- | --- | --- |
| `Kajas-0.1.1-macos-arm64.dmg` | macOS 11+ on Apple Silicon (arm64) | record after build | record after build |

Release notes:

- Normalizes legacy benchmark runs so old `agency.*` rows are shown under
  the current 50-point tool-calling score model.
- Adds a desktop-dev guard against silently reusing a stale backend already
  listening on port `8765`; set `KAJAS_DESKTOP_REUSE_BACKEND=1` to opt in.
- Keeps the agency benchmark tool filtering aligned with scenario-specific
  allowed tools.

### How this artifact is produced

```bash
cd frontend
npm run tauri -- build --bundles dmg
# -> frontend/src-tauri/target/release/bundle/dmg/Kajas_0.1.1_aarch64.dmg
cp ../frontend/src-tauri/target/release/bundle/dmg/Kajas_0.1.1_aarch64.dmg \
   releases/Kajas-0.1.1-macos-arm64.dmg
shasum -a 256 releases/Kajas-0.1.1-macos-arm64.dmg > \
   releases/Kajas-0.1.1-macos-arm64.dmg.sha256
```

### Publishing (run once per release)

```bash
gh release create v0.1.1 \
  releases/Kajas-0.1.1-macos-arm64.dmg \
  --title "Kajas v0.1.1" \
  --notes-file releases/README.md
```

After publishing, this link becomes live:

```
https://github.com/nikolasp/kajas/releases/download/v0.1.1/Kajas-0.1.1-macos-arm64.dmg
```

## v0.1.0 — macOS (Apple Silicon)

| File | Target | Size | SHA-256 |
| --- | --- | --- | --- |
| `Kajas-0.1.0-macos-arm64.dmg` | macOS 11+ on Apple Silicon (arm64) | 21 MB | `d1011ef932b18f22100b2f84c221961375cc18fd2d9bf338e67231224a6605b0` |

This build is **ad-hoc signed, not notarized**. It runs on macOS, but
Gatekeeper will block a double-click open. See the "Download" section of
the root [`README.md`](../README.md) for install instructions.

### How this artifact was produced

```bash
cd frontend
npm run tauri -- build --bundles dmg
# -> frontend/src-tauri/target/release/bundle/dmg/Kajas_0.1.0_aarch64.dmg
cp ../frontend/src-tauri/target/release/bundle/dmg/Kajas_0.1.0_aarch64.dmg \
   releases/Kajas-0.1.0-macos-arm64.dmg
shasum -a 256 releases/Kajas-0.1.0-macos-arm64.dmg > \
   releases/Kajas-0.1.0-macos-arm64.dmg.sha256
```

### Publishing (run once per release)

The dmg is gitignored on purpose. Publish it as a GitHub Release asset
so the README download link resolves:

```bash
gh release create v0.1.0 \
  releases/Kajas-0.1.0-macos-arm64.dmg \
  --title "Kajas v0.1.0" \
  --notes "macOS Apple Silicon desktop build (unsigned; ad-hoc signed)."
```

If you don't have the `gh` CLI, upload `releases/*.dmg` through the
[Releases web UI][new-release] under the `v0.1.0` tag. After publishing,
this link becomes live:

```
https://github.com/nikolasp/kajas/releases/download/v0.1.0/Kajas-0.1.0-macos-arm64.dmg
```

[new-release]: https://github.com/nikolasp/kajas/releases/new

## Notarization TODO

The current build is ad-hoc signed only, so macOS users must clear the
quarantine attribute (`xattr -cr /Applications/Kajas.app`) before first
launch. To ship a build that opens with a normal double-click:

1. Obtain an Apple Developer ID Application certificate.
2. Sign at build time:
   ```bash
   export APPLE_SIGNING_IDENTITY="Developer ID Application: <name>"
   npm run tauri -- build --bundles dmg
   ```
3. Notarize and staple:
   ```bash
   xcrun notarytool submit Kajas-0.1.1-macos-arm64.dmg \
     --apple-id <apple-id> --team-id <team-id> --wait
   xcrun stapler staple Kajas-0.1.1-macos-arm64.dmg
   ```
4. Re-record the SHA-256 and republish the release asset.
