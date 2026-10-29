# Portable Windows bundle

`Jarvis-Windows.zip` runs Jarvis on Windows 10/11 x64 without installing Python. Extract the whole ZIP to a folder you own and double-click `Start-Jarvis-Windows.cmd`. Nothing is installed system-wide, no administrator rights are requested, and the camera, microphone, mouse and Windows actions stay off until you enable them in Jarvis. AI providers are optional; any API key you add is your own and stays on your computer.

## What is inside

```
Jarvis-Windows/
  Start-Jarvis-Windows.cmd  runs runtime\python.exe -I -m jarvis.launcher
  runtime/                  official python.org CPython embeddable package (x64)
  app/                      Jarvis sources, web assets, bundle-manifest.json, models/hand_landmarker.task
  vendor/                   camera, hand and voice packages, installed at build time
  licenses/                 THIRD-PARTY-NOTICES.txt, Python license, Apache-2.0 text
  BUILD-PROVENANCE.json     source URL and SHA-256 for every bundled input
  README.txt
```

- **Python**: the unmodified python.org *Windows embeddable package (64-bit)*. Its SHA-256 and size are pinned in `scripts/windows_bundle_manifest.json`. The build checks them against the download, verifies the python.org Sigstore bundle for the release manager's identity, and checks that every runtime `.exe`/`.dll`/`.pyd` has a valid Authenticode signature from the Python Software Foundation (Microsoft for the VC runtime). The only change is `runtime/pythonXY._pth`, which lists exactly `pythonXY.zip`, `.`, `../app` and `../vendor`. Without `import site`, user site-packages, `PYTHONPATH` and the current folder are never on the import path. The launcher also passes `-I`.
- **Packages**: each wheel is pinned by file name and SHA-256 and downloaded with `pip download --require-hashes` from `https://pypi.org/simple` only. Each file is checked against its wheel `RECORD` before it is unpacked into `vendor/`. Console-script launchers, man pages and C headers from wheel `.data` folders are not shipped. The single reviewed source-build exception is `srt 3.5.3`: its official sdist is checksum-verified, then built offline (`--no-index`, no build isolation) with a pinned setuptools in a throwaway build-host environment.
- **Hand model**: the MediaPipe Hand Landmarker model (Apache-2.0, per its official model card) is fetched by the same verified downloader as `jarvis/install_tools.py` (fixed URL, no redirects, SHA-256 checked) and shipped in `app/models/`, so hand gestures need no download.
- **Voice model**: not included. Offline voice packages are bundled, but a Vosk model is still a manual download (see the main README).

Runtime `pip` is not used and is not supported inside the bundle; the embeddable distribution is not meant for general package management. `jarvis.bundle` tells the app it is running from the bundle:

```python
from jarvis import bundle
bundle.is_bundled(root)              # valid manifest and this process is runtime\python.exe
bundle.feature_ready(root, "hand")   # "camera" | "hand" | "voice": bundled packages (and hand model) present
bundle.runtime_info(root)            # JSON-safe summary; no paths, user names or secrets
```

`root` is the app folder (the parent of `jarvis/`). `feature_ready("voice")` covers packages only; the voice model is reported separately by `planner.diagnostics`.

## Building

On a Windows build host with Python 3.11+ (only used to run the build) and git, from a clean checkout:

```
python -I scripts/build_windows.py --output <folder>\Jarvis-Windows.zip
```

`--source head` requires a clean tracked tree and reads app files and the executable launcher from the committed `HEAD`. Commit the reviewed launcher, build-tools lock and app sources first. Untracked files named with `--extra` are still read from the working tree and recorded as dirty. The provenance records the mode, launcher checksum, any uncommitted extras and any deferred files. Keep the output ZIP outside the build work folder.

The build:

1. Cross-checks the build manifest against the pins and hand-model constants in `jarvis/install_tools.py`.
2. Selects app files from `git ls-files` using the manifest allowlist (`jarvis/*.py`, `web/*`, `docs/*.md`, licenses and READMEs), plus the reviewed new files in `app.extra_files` and any `--extra PATH`. If a new, untracked, non-ignored file matches the allowlist, the build stops until it is either shipped (`--extra PATH`) or deliberately left out (`--defer PATH`), so a new module is never silently dropped or shipped. The build prints the matching rebuild command. Ignored files (`.env`, `runtime/`, `models/`, `work/`) can never be selected.
3. Downloads and verifies Python, the wheels, the srt source and the hand model into `work/windows-bundle/cache`.
4. Stages the bundle and writes the license notices and `BUILD-PROVENANCE.json`.
5. Audits every staged file, including the contents of nested ZIPs. It looks for development folders, `.pyc` caches, credential-like files, links, the build host's home path, user name, git e-mail, values of secret-like environment variables, and common API-key formats. Add more values to look for with `JARVIS_BUNDLE_AUDIT_VALUES="value1;value2"`.
6. Writes a deterministic ZIP (sorted entries, commit timestamp) and `Jarvis-Windows.zip.sha256`.
7. Smoke-tests a fresh extraction under a path with spaces, using only the bundled interpreter with a minimal environment. `PYTHONPATH`, `PYTHONHOME` and a working folder full of canary modules are all set and must be ignored. The smoke test checks `sys.path`, imports `numpy`, `cv2`, `mediapipe`, `vosk`, `sounddevice` and `srt` from `vendor/`, and runs real MediaPipe hand inference on blank frames. It also runs `Start-Jarvis-Windows.cmd --check`, starts the local wizard, fetches its pages and diagnostics, and exits it through its own exit route. It never opens a camera, microphone stream, mouse or Windows lock. Importing `sounddevice` initializes PortAudio, which lists audio devices but does not open them.

Build-host tools (`sigstore`, `setuptools` and their dependencies) are installed fresh from `scripts/windows-build-tools.lock` with exact versions and required wheel hashes. The lock checksum is recorded in provenance. The built srt Python files must match the checksum-verified source archive byte for byte. Build tools live in `work/windows-bundle/tools-venv` and are never shipped. The smoke test also saves choices, checks bundled hand installation, starts the real desktop app idle, verifies permissions are off, and exits it. No device session or AI request is started. To smoke-test an existing ZIP again: `python -I scripts/build_windows.py --smoke-only <zip>`.

## Limits

- Windows 10/11 x64 only. The bundle has not been tested on a clean VM or with third-party antivirus.
- The ZIP and launcher are not code-signed by this project (the python.org runtime binaries keep their PSF signatures), so SmartScreen may ask for confirmation.
- Extract to a short path such as `Documents\Jarvis-Windows`. Some vendored paths are long, and the full path should stay under Windows' 260-character limit unless long paths are enabled.
- Updating a package means editing the manifest pins (and `jarvis/install_tools.py` for reviewed top-level pins) and rebuilding.
