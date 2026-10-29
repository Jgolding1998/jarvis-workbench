"""Explicit local setup for optional camera, hand and voice dependencies."""

from __future__ import annotations

import hashlib
import os
import re
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, HTTPSHandler

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
MODEL_SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"
SRT_SOURCE_URL = "https://files.pythonhosted.org/packages/66/b7/4a1bc231e0681ebf339337b0cd05b91dc6a0d701fa852bb812e244b7a030/srt-3.5.3.tar.gz"
SRT_SOURCE_SHA256 = "4884315043a4f0740fd1f878ed6caa376ac06d70e135f306a6dc44632eed0cc0"
MAX_MODEL_BYTES = 12 * 1024 * 1024
PACKAGES = {"opencv-contrib-python": "4.13.0.92", "mediapipe": "1.2.0", "vosk": "0.3.45", "sounddevice": "0.5.6", "srt": "3.5.3"}
REQUIREMENTS = {
    "requirements-camera.txt": ({"opencv-contrib-python"}, {"opencv-contrib-python"}),
    "requirements-hand.txt": ({"mediapipe"}, {"mediapipe", "opencv-contrib-python"}),
    "requirements-voice.txt": ({"vosk", "sounddevice", "srt"}, {"vosk", "sounddevice", "srt"}),
}
FEATURES = {
    "camera": ("requirements-camera.txt",),
    "hand": ("requirements-camera.txt", "requirements-hand.txt"),
    "voice": ("requirements-voice.txt",),
    "all": tuple(REQUIREMENTS),
}
CHOICES = {"1": "camera", "2": "hand", "3": "voice", "4": "all"}
CHECK_ENV = "import pathlib,sys; p=pathlib.Path(sys.argv[1]).resolve(); raise SystemExit(0 if sys.version_info >= (3,11) and pathlib.Path(sys.prefix).resolve()==p and sys.base_prefix!=sys.prefix else 1)"
CHECK_OPENCV = "import importlib.metadata as m; import sys; names={d.metadata.get('Name','').lower().replace('_','-') for d in m.distributions()}; raise SystemExit(1 if names & {'opencv-python','opencv-python-headless','opencv-contrib-python-headless'} else 0)"


class SetupError(ValueError):
    pass


def reject_links(path):
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise SetupError("Linked folders and files are unsupported. Nothing was replaced.")
        try:
            attributes = getattr(item.lstat(), "st_file_attributes", 0)
        except FileNotFoundError:
            attributes = 0
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise SetupError("Windows reparse points are unsupported. Nothing was replaced.")


def local_path(root, *parts):
    root = Path(root).absolute()
    reject_links(root)
    if not root.is_dir():
        raise SetupError("The Jarvis folder is unavailable.")
    root = root.resolve()
    path = root.joinpath(*parts)
    reject_links(path)
    if not path.resolve().is_relative_to(root):
        raise SetupError("Setup paths must stay inside this Jarvis folder.")
    return path


def selected_packages(root, feature):
    if feature not in FEATURES:
        raise SetupError("Choose Camera, Hand, Voice or All.")
    selected = {}
    for name in FEATURES[feature]:
        file = local_path(root, name)
        if not file.is_file() or file.stat().st_size > 8192 or file.stat().st_nlink > 1:
            raise SetupError("An optional requirements file is missing or unsupported.")
        entries = {}
        for line in file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.fullmatch(r"([a-z][a-z0-9-]*)==([0-9][0-9.]*)", line)
            if not match or match[1] not in PACKAGES or PACKAGES[match[1]] != match[2] or match[1] in entries:
                raise SetupError("Optional requirements differ from the reviewed package pins.")
            entries[match[1]] = match[2]
        required, allowed = REQUIREMENTS[name]
        if not required <= entries.keys() <= allowed:
            raise SetupError("Optional requirements do not match the selected feature.")
        selected.update(entries)
    return [f"{name}=={version}" for name, version in sorted(selected.items())]


def safe_environment(temporary=None):
    allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "LANG", "LC_ALL"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env["PIP_CONFIG_FILE"] = os.devnull
    if temporary is not None:
        for name in ("TEMP", "TMP", "TMPDIR"):
            env[name] = str(temporary)
    return env


def command(argv, root, env, run=subprocess.run):
    try:
        result = run(list(argv), cwd=str(root), env=env, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300, check=False)
    except subprocess.TimeoutExpired:
        raise SetupError("Setup exceeded its five-minute command timeout. Existing files were preserved.") from None
    except OSError:
        raise SetupError("A setup command could not start. Existing files were preserved.") from None
    if result.returncode != 0:
        raise SetupError(f"A setup command did not finish successfully (exit code {int(result.returncode)}). Raw output is hidden.")


def environment(root, run=subprocess.run):
    venv = local_path(root, ".venv")
    if not venv.exists():
        command([sys.executable, "-I", "-m", "venv", "--copies", str(venv)], root, safe_environment(), run)
    config = local_path(root, ".venv", "pyvenv.cfg")
    python = local_path(root, ".venv", "Scripts", "python.exe") if os.name == "nt" else local_path(root, ".venv", "bin", "python")
    if not venv.is_dir() or not config.is_file() or not python.is_file() or config.stat().st_size > 8192:
        raise SetupError("The existing local environment is incomplete. It was not deleted or replaced.")
    settings = {key.strip(): value.strip().lower() for line in config.read_text(encoding="utf-8").splitlines() if "=" in line for key, value in [line.split("=", 1)]}
    if settings.get("include-system-site-packages") != "false":
        raise SetupError("The existing environment allows system packages. It was left unchanged.")
    temporary = local_path(root, ".venv", ".install-tmp")
    temporary.mkdir(exist_ok=True)
    env = safe_environment(temporary)
    command([str(python), "-I", "-c", CHECK_ENV, str(venv)], root, env, run)
    return python, venv, env


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise SetupError("The model server redirected the request. No model was installed.")


def publish_model(temporary, target, *, windows=None):
    windows = os.name == "nt" if windows is None else windows
    if windows:
        os.rename(temporary, target)
        return
    os.link(temporary, target)
    try:
        temporary.unlink()
    except OSError:
        if target.exists() and os.path.samefile(temporary, target):
            target.unlink()
        raise SetupError("The model could not be finalized. No ready model was installed.") from None


def download_model(root, *, opener=None, expected_sha=MODEL_SHA256):
    directory = local_path(root, "models")
    directory.mkdir(exist_ok=True)
    target = local_path(root, "models", "hand_landmarker.task")
    if target.exists():
        if not target.is_file() or target.stat().st_size > MAX_MODEL_BYTES or target.stat().st_nlink > 1:
            raise SetupError("The existing hand model is unsupported and was preserved.")
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for block in iter(lambda: handle.read(65536), b""):
                digest.update(block)
        if digest.hexdigest() != expected_sha:
            raise SetupError("A different hand model already exists. It was preserved; move it before retrying.")
        return
    opener = opener or build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=directory, prefix=".hand-model-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            request = Request(MODEL_URL, headers={"User-Agent": "Jarvis-optional-setup/1.0"})
            with opener.open(request, timeout=10) as response:
                if response.geturl() != MODEL_URL or response.status != 200:
                    raise SetupError("The model source did not return the expected secure download.")
                declared = response.headers.get("Content-Length")
                if declared is not None and (not declared.isascii() or not declared.isdecimal() or not 0 < int(declared) <= MAX_MODEL_BYTES):
                    raise SetupError("The model exceeds the supported download size.")
                digest, size, deadline = hashlib.sha256(), 0, time.monotonic() + 90
                while True:
                    if time.monotonic() > deadline:
                        raise SetupError("The model download exceeded its deadline.")
                    block = response.read1(65536)
                    if not block:
                        break
                    size += len(block)
                    if size > MAX_MODEL_BYTES:
                        raise SetupError("The model exceeds the supported download size.")
                    digest.update(block)
                    output.write(block)
            if not size or digest.hexdigest() != expected_sha:
                raise SetupError("The model checksum did not match. No existing model was replaced.")
            output.flush()
            os.fsync(output.fileno())
        local_path(root, "models", "hand_landmarker.task")
        publish_model(temporary, target)
    except SetupError:
        raise
    except (OSError, ValueError, UnicodeError):
        raise SetupError("The model could not be downloaded or saved. Existing files were preserved.") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def reviewed_manifest(packages):
    allowed = {f"{name}=={version}" for name, version in PACKAGES.items()}
    if any(package not in allowed for package in packages):
        raise SetupError("A package differs from the reviewed pins.")
    source = f"srt @ {SRT_SOURCE_URL}#sha256={SRT_SOURCE_SHA256}"
    return [source if package == "srt==3.5.3" else package for package in packages]


def install(root, feature, packages, *, run=subprocess.run, model_fetch=download_model):
    if packages != selected_packages(root, feature):
        raise SetupError("The selected package list changed. Open setup again.")
    python, venv, env = environment(root, run)
    if feature in {"camera", "hand", "all"}:
        command([str(python), "-I", "-c", CHECK_OPENCV], root, env, run)
    manifest = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=venv, prefix="jarvis-optional-", suffix=".txt", delete=False) as handle:
            manifest = Path(handle.name)
            handle.write("\n".join(reviewed_manifest(packages)) + "\n")
        options = ["--use-pep517"] if "srt==3.5.3" in packages else []
        command([str(python), "-I", "-m", "pip", "install", "--isolated", "--only-binary=:all:", "--index-url", "https://pypi.org/simple", "--disable-pip-version-check", "--no-input", "--no-cache-dir", *options, "--requirement", str(manifest)], root, env, run)
    finally:
        if manifest is not None:
            manifest.unlink(missing_ok=True)
    if feature in {"hand", "all"}:
        model_fetch(root)


def main(*, root=None, ask=input, tell=print, run=subprocess.run, model_fetch=download_model):
    if sys.version_info < (3, 11):
        tell("Python 3.11 or newer is required. Install Python from python.org and open this tool again.")
        return 2
    root = Path(root) if root is not None else Path(__file__).absolute().parent.parent
    tell("Jarvis optional tools\n1. Camera\n2. Camera + hand gestures\n3. Offline voice\n4. All optional tools\nQ. Cancel")
    try:
        choice = ask("Choose 1, 2, 3, 4 or Q: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        tell("Cancelled. Nothing was changed.")
        return 0
    if choice == "q":
        tell("Cancelled. Nothing was changed.")
        return 0
    feature = CHOICES.get(choice)
    if feature is None:
        tell("Invalid choice. Nothing was changed.")
        return 2
    try:
        packages = selected_packages(root, feature)
        tell("This will create or reuse only this project's .venv and download reviewed optional packages from PyPI:")
        for package in packages:
            tell("  " + package)
        if feature in {"hand", "all"}:
            tell("It will also download the official Google hand model (up to 12 MiB), verify SHA-256, and save it under models.")
        if feature in {"voice", "all"}:
            tell("Voice setup builds exactly srt 3.5.3 from its official checksum-verified source archive in an isolated PEP 517 build. Other dependencies use wheels.")
            tell("A Vosk voice model is a separate manual download. Follow the existing voice guide; no voice archive is downloaded here.")
        tell("No camera or microphone will be started. Existing environments and model files are never deleted.")
        if ask("Continue with these downloads? [y/N]: ").strip().lower() != "y":
            tell("Cancelled. Nothing was changed.")
            return 0
        tell("Preparing the local environment and installing the selected tools. This can take several minutes.")
        tell("Each setup command has a five-minute time limit.")
        install(root, feature, packages, run=run, model_fetch=model_fetch)
        tell("Optional tools are ready. Close this window and open Start-Jarvis.cmd. Enable devices only when you choose to.")
        return 0
    except (EOFError, KeyboardInterrupt):
        tell("Setup stopped. Existing files were preserved.")
        return 2
    except (SetupError, OSError, UnicodeError) as error:
        tell(str(error) if isinstance(error, SetupError) else "Local setup could not finish. Existing files were preserved.")
        tell("If an older environment contains opencv-python or an incomplete install, keep it as a backup and follow the manual setup guide before retrying.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
