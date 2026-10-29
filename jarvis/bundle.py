"""Read-only checks for the portable Windows bundle. No network, no imports of optional packages.

The portable layout is:

    Jarvis-Windows/
      runtime/   official CPython embeddable distribution (python.exe, pythonXY._pth)
      app/       this project (root): jarvis/, web/, models/, bundle-manifest.json
      vendor/    prebuilt optional packages, already installed at build time

Runtime pip is never needed or supported inside the bundle; scripts/build_windows.py installs everything.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
import sys
from pathlib import Path

MANIFEST_NAME = "bundle-manifest.json"
SCHEMA = "jarvis-windows-bundle/1"
FEATURES = ("camera", "hand", "voice")
HAND_MODEL = "models/hand_landmarker.task"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_MODEL_BYTES = 12 * 1024 * 1024
KEYS = {"schema", "platform", "python", "features", "packages", "hand_model_sha256", "voice_model_included", "source"}
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
VERSION = re.compile(r"[0-9][0-9A-Za-z.+-]{0,31}")
SHA256 = re.compile(r"[0-9a-f]{64}")


class BundleError(ValueError):
    pass


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BundleError("Duplicate key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise BundleError("Non-finite number")


def _plain_file(path):
    try:
        info = path.lstat()
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        return False
    return True


def validate_manifest(data):
    """Return the manifest when it matches the exact schema, otherwise raise BundleError."""
    if not isinstance(data, dict) or set(data) != KEYS or data["schema"] != SCHEMA:
        raise BundleError("Unsupported bundle manifest")
    if data["platform"] != "win-amd64" or not isinstance(data["python"], str) or not re.fullmatch(r"3\.[0-9]{1,2}\.[0-9]{1,3}", data["python"]):
        raise BundleError("Unsupported bundle platform")
    packages = data["packages"]
    if not isinstance(packages, dict) or not 0 < len(packages) <= 100:
        raise BundleError("Invalid package list")
    for name, version in packages.items():
        if not isinstance(name, str) or not isinstance(version, str) or not NAME.fullmatch(name) or not VERSION.fullmatch(version):
            raise BundleError("Invalid package entry")
    features = data["features"]
    if not isinstance(features, dict) or not set(features) <= set(FEATURES):
        raise BundleError("Invalid feature list")
    for names in features.values():
        if not isinstance(names, list) or not names or any(not isinstance(name, str) or name not in packages for name in names):
            raise BundleError("A feature references a package that is not bundled")
    model = data["hand_model_sha256"]
    if model is not None and (not isinstance(model, str) or not SHA256.fullmatch(model)):
        raise BundleError("Invalid hand model checksum")
    if "hand" in features and model is None:
        raise BundleError("Hand support needs the bundled hand model")
    if data["voice_model_included"] is not False:
        raise BundleError("Voice models are not bundled")
    source = data["source"]
    if (not isinstance(source, dict) or set(source) != {"commit", "dirty"} or not isinstance(source["commit"], str)
            or not re.fullmatch(r"[0-9a-f]{40}", source["commit"]) or not isinstance(source["dirty"], bool)):
        raise BundleError("Invalid source record")
    return data


def load_manifest(root):
    """Load and validate root/bundle-manifest.json. Raises BundleError when absent or invalid."""
    path = Path(root) / MANIFEST_NAME
    if not _plain_file(path) or path.stat().st_size > MAX_MANIFEST_BYTES:
        raise BundleError("No supported bundle manifest")
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    except (OSError, UnicodeError, ValueError, RecursionError, OverflowError):
        raise BundleError("Unreadable bundle manifest") from None
    return validate_manifest(data)


def _manifest_or_none(root):
    try:
        return load_manifest(root)
    except (BundleError, OSError):
        return None


def _same_path(first, second):
    try:
        return os.path.normcase(str(Path(first).resolve())) == os.path.normcase(str(Path(second).resolve()))
    except OSError:
        return False


def _layout_ok(root, executable):
    base = Path(root).parent
    python = base / "runtime" / "python.exe"
    return _plain_file(python) and (base / "vendor").is_dir() and _same_path(executable, python)


def is_bundled(root, *, executable=None):
    """True only when root has a valid bundle manifest and this process runs the bundled interpreter."""
    return _manifest_or_none(root) is not None and _layout_ok(root, executable or sys.executable)


def _installed(root):
    found = {}
    try:
        entries = list((Path(root).parent / "vendor").iterdir())
    except OSError:
        return found
    for entry in entries:
        if entry.name.endswith(".dist-info") and entry.is_dir() and not entry.is_symlink() and (entry / "METADATA").is_file():
            name, _, version = entry.name[:-len(".dist-info")].partition("-")
            found[re.sub(r"[-_.]+", "-", name).lower()] = version
    return found


def _hand_model_ok(root, expected):
    path = Path(root) / HAND_MODEL
    try:
        if not _plain_file(path) or path.stat().st_size > MAX_MODEL_BYTES:
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == expected
    except OSError:
        return False


def _ready(root, manifest, feature, installed):
    names = manifest["features"].get(feature)
    if not names or any(installed.get(name) != manifest["packages"][name] for name in names):
        return False
    return feature != "hand" or _hand_model_ok(root, manifest["hand_model_sha256"])


def feature_ready(root, feature, *, executable=None):
    """True when the bundle already contains everything this feature needs.

    Voice readiness covers the bundled packages only; a Vosk voice model is always a separate manual download.
    """
    if feature not in FEATURES:
        return False
    manifest = _manifest_or_none(root)
    if manifest is None or not _layout_ok(root, executable or sys.executable):
        return False
    return _ready(root, manifest, feature, _installed(root))


def runtime_info(root, *, executable=None):
    """Return a JSON-safe summary without paths, user names or secrets."""
    manifest = _manifest_or_none(root)
    bundled = manifest is not None and _layout_ok(root, executable or sys.executable)
    info = {"bundled": bundled, "python": platform.python_version(), "bundle_python": None, "platform": None,
            "features": {feature: False for feature in FEATURES}, "packages": {},
            "hand_model_included": False, "voice_model_included": False, "source_commit": None}
    if not bundled:
        return info
    installed = _installed(root)
    info.update({"bundle_python": manifest["python"], "platform": manifest["platform"],
                 "features": {feature: _ready(root, manifest, feature, installed) for feature in FEATURES},
                 "packages": dict(sorted(manifest["packages"].items())),
                 "hand_model_included": manifest["hand_model_sha256"] is not None and _hand_model_ok(root, manifest["hand_model_sha256"]),
                 "source_commit": manifest["source"]["commit"][:12]})
    return info
