"""Build the portable Windows x64 Jarvis ZIP that needs no separate Python installation.

Layout inside the ZIP:

    Jarvis-Windows/
      Start-Jarvis-Windows.cmd   runs runtime\\python.exe -I -m jarvis.launcher
      runtime/                   official python.org CPython embeddable distribution (unmodified except pythonXY._pth)
      app/                       tracked Jarvis sources, web assets, verified hand model, bundle-manifest.json
      vendor/                    optional packages prebuilt from hash-pinned PyPI wheels (no runtime pip)
      licenses/                  Python, dependency and model license texts plus THIRD-PARTY-NOTICES.txt
      BUILD-PROVENANCE.json      source URLs and SHA-256 values for everything bundled

Run on a Windows build host with Python 3.11+ and git, from a clean checkout:

    python -I scripts/build_windows.py --output <dir>/Jarvis-Windows.zip

The build downloads only from www.python.org, pypi.org/files.pythonhosted.org and the fixed Google model URL,
verifies every artifact against scripts/windows_bundle_manifest.json, then audits and smoke-tests the result.
"""
from __future__ import annotations

import argparse
import base64
import csv
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "scripts" / "windows_bundle_manifest.json"
TOOLS_LOCK_PATH = ROOT / "scripts" / "windows-build-tools.lock"
DEFAULT_WORK = ROOT / "work" / "windows-bundle"
LAUNCHER = "Start-Jarvis-Windows.cmd"
BUILD_SCHEMA = "jarvis-windows-build/1"
SHA256 = re.compile(r"[0-9a-f]{64}")
ALLOWED_PREFIXES = {
    "python": ("https://www.python.org/ftp/python/",),
    "sdist": ("https://files.pythonhosted.org/packages/",),
    "model": ("https://storage.googleapis.com/mediapipe-models/",),
    "license": ("https://raw.githubusercontent.com/", "https://www.gnu.org/licenses/"),
}
LICENSE_NAME = re.compile(r"(?i)^(licen[cs]e|copying|notice|authors|third[-_]party|license[-_]3rd[-_]party)")
FORBIDDEN_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache"}
APP_FORBIDDEN_PARTS = FORBIDDEN_PARTS | {".github", "tests", "work", "site-dist", "node_modules", "website"}
FORBIDDEN_SUFFIXES = (".pyc", ".pyo", ".log", ".wav", ".mp3", ".mp4", ".key", ".pem", ".p12", ".pfx")
ALLOWED_SPECIAL = {"vendor/certifi/cacert.pem"}
SECRET_PATTERNS = {
    "nvidia-style key": re.compile(rb"nvapi-[A-Za-z0-9_-]{32,}"),
    "openai-style key": re.compile(rb"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}"),
    "aws access key": re.compile(rb"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])"),
    "github token": re.compile(rb"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{60,}"),
    "google api key": re.compile(rb"AIza[0-9A-Za-z_-]{35}"),
    "slack token": re.compile(rb"xox[abposr]-[0-9A-Za-z-]{10,}"),
    "private key block": re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
}
SECRET_ENV = re.compile(r"(?i)(key|token|secret|passw|credential|auth)")


class BuildError(RuntimeError):
    pass


def log(message):
    print("[build] " + message, flush=True)


# ---------------------------------------------------------------- manifest

def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BuildError("Duplicate key in build manifest: " + key)
        result[key] = value
    return result


def _url_ok(url, kind):
    return isinstance(url, str) and url.startswith(ALLOWED_PREFIXES[kind]) and urlsplit(url).scheme == "https" and ".." not in url


def load_build_manifest(path=MANIFEST_PATH, install_tools=None):
    """Load and cross-check the reviewed build inputs against the app's own reviewed pins."""
    data = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_no_duplicates)
    if data.get("schema") != BUILD_SCHEMA:
        raise BuildError("Unsupported build manifest schema")
    python = data["python"]
    if not _url_ok(python["url"], "python") or not _url_ok(python["sigstore_bundle_url"], "python"):
        raise BuildError("Python must come from www.python.org/ftp/python")
    if not SHA256.fullmatch(python["sha256"]) or not re.fullmatch(r"3\.\d+\.\d+", python["version"]):
        raise BuildError("Invalid Python pin")
    short = "".join(python["version"].split(".")[:2])
    if python["pth_name"] != f"python{short}._pth" or python["stdlib_zip"] != f"python{short}.zip":
        raise BuildError("The ._pth or stdlib name does not match the Python version")
    if not python["url"].endswith(f"/{python['version']}/python-{python['version']}-embed-amd64.zip"):
        raise BuildError("Only the official x64 embeddable package is supported")
    names = set()
    for wheel in data["wheels"]:
        if not SHA256.fullmatch(wheel["sha256"]) or not wheel["filename"].endswith(".whl") or "/" in wheel["filename"] or "\\" in wheel["filename"]:
            raise BuildError("Invalid wheel pin")
        if wheel["name"] in names:
            raise BuildError("Duplicate wheel pin")
        names.add(wheel["name"])
    for sdist in data["sdists"]:
        if not _url_ok(sdist["url"], "sdist") or not SHA256.fullmatch(sdist["sha256"]) or sdist["name"] in names:
            raise BuildError("Invalid source pin")
        names.add(sdist["name"])
    if [sdist["name"] for sdist in data["sdists"]] != ["srt"]:
        raise BuildError("srt 3.5.3 is the only reviewed source-build exception")
    model = data["hand_model"]
    if not _url_ok(model["url"], "model") or not SHA256.fullmatch(model["sha256"]):
        raise BuildError("Invalid hand model pin")
    versions = {wheel["name"]: wheel["version"] for wheel in data["wheels"]}
    versions.update({sdist["name"]: sdist["version"] for sdist in data["sdists"]})
    files = set()
    for supplement in data["license_supplements"]:
        if (not _url_ok(supplement["url"], "license") or not SHA256.fullmatch(supplement["sha256"]) or supplement["applies_to"] not in versions
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.txt", supplement["file"]) or supplement["file"] in files):
            raise BuildError("Invalid license supplement")
        files.add(supplement["file"])
    for feature, packages in data["features"].items():
        if feature not in ("camera", "hand", "voice") or any(package not in versions for package in packages):
            raise BuildError("A feature references an unpinned package")
    if install_tools is not None:
        for name, version in install_tools.PACKAGES.items():
            if versions.get(name) != version:
                raise BuildError(f"{name} must match the reviewed pin {version} from jarvis/install_tools.py")
        if model["url"] != install_tools.MODEL_URL or model["sha256"] != install_tools.MODEL_SHA256:
            raise BuildError("The hand model must match jarvis/install_tools.py")
        sdist = data["sdists"][0]
        if sdist["url"] != install_tools.SRT_SOURCE_URL or sdist["sha256"] != install_tools.SRT_SOURCE_SHA256:
            raise BuildError("The srt source must match jarvis/install_tools.py")
    data["_versions"] = versions
    return data


# ---------------------------------------------------------------- helpers

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise BuildError("Unexpected redirect; refusing to follow it")


def download(url, target, expected_sha, max_bytes=256 * 1024 * 1024):
    """Download url to target over verified HTTPS without redirects, keeping a cached copy only if it verifies."""
    target = Path(target)
    if target.is_file() and sha256_file(target) == expected_sha:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    partial = target.with_name(target.name + ".part")
    digest, size = hashlib.sha256(), 0
    with opener.open(Request(url, headers={"User-Agent": "jarvis-windows-build/1"}), timeout=60) as response, open(partial, "wb") as output:
        if response.status != 200 or response.geturl() != url:
            raise BuildError("Unexpected response for " + url)
        while block := response.read(1 << 20):
            size += len(block)
            if size > max_bytes:
                raise BuildError("Download too large: " + url)
            digest.update(block)
            output.write(block)
    if digest.hexdigest() != expected_sha:
        partial.unlink(missing_ok=True)
        raise BuildError("SHA-256 mismatch for " + url)
    os.replace(partial, target)
    return target


def safe_env(temporary):
    env = {key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "LC_ALL"}}
    system_root = env.get("SYSTEMROOT", r"C:\Windows")
    env["PATH"] = os.pathsep.join([str(Path(system_root) / "System32"), system_root])
    env["PIP_CONFIG_FILE"] = os.devnull
    for name in ("TEMP", "TMP", "TMPDIR"):
        env[name] = str(temporary)
    return env


def run(argv, env, cwd=None, timeout=900):
    result = subprocess.run([str(part) for part in argv], cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode != 0:
        raise BuildError("Command failed (%d): %s\n%s" % (result.returncode, " ".join(map(str, argv[:6])), (result.stdout + result.stderr)[-4000:]))
    return result.stdout


def clean_dir(path, work):
    path, work = Path(path), Path(work).resolve()
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise BuildError("Refusing to clean a linked directory")
    path = path.resolve()
    if path == work or not path.is_relative_to(work):
        raise BuildError("Refusing to clean a directory outside the build work folder")
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def safe_member(name):
    """Return the POSIX parts of a ZIP member name, rejecting traversal, absolute and drive paths."""
    if not name or "\\" in name or name.startswith("/") or ":" in name:
        raise BuildError("Unsafe archive member: " + repr(name))
    parts = PurePosixPath(name).parts
    if any(part in ("", ".", "..") for part in parts):
        raise BuildError("Unsafe archive member: " + repr(name))
    return parts


def extract_zip(archive_path, target):
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            parts = safe_member(info.filename.rstrip("/"))
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise BuildError("Symbolic links are not allowed in archives")
            destination = Path(target).joinpath(*parts)
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(destination, "xb") as output:
                shutil.copyfileobj(source, output)


# ---------------------------------------------------------------- source selection

def _matches(path, patterns):
    parts = PurePosixPath(path).parts
    for pattern in patterns:
        wanted = PurePosixPath(pattern).parts
        if len(wanted) == len(parts) and all(fnmatch.fnmatchcase(part, glob) for part, glob in zip(parts, wanted)):
            return True
    return False


def select_app_files(tracked, untracked, include, extras, exists=lambda _path: True, deferred=()):
    """Pick app files from git-tracked paths plus explicitly reviewed new files.

    Untracked, non-ignored files that match the include patterns must be listed explicitly, either as an extra
    (ship it) or as deferred (leave it out on purpose), so a new module is never silently dropped or shipped.
    Ignored files can never be selected.
    """
    tracked, untracked, deferred = set(tracked), set(untracked), set(deferred)
    if deferred & set(extras) or not deferred <= untracked:
        raise BuildError("Deferred files must be untracked and not also listed as extras")
    selected = set()
    for path in tracked:
        if _matches(path, include):
            if not exists(path):
                raise BuildError("Tracked app file is missing from the working tree: " + path)
            selected.add(path)
    for path in extras:
        if path not in tracked and path not in untracked:
            raise BuildError("Extra file is ignored by git or does not exist: " + path)
        if not _matches(path, include):
            raise BuildError("Extra file is outside the app allowlist: " + path)
        selected.add(path)
    pending = sorted(path for path in untracked if _matches(path, include) and path not in selected and path not in deferred)
    if pending:
        raise BuildError("New app files need review. Commit them, or pass --extra (ship) or --defer (leave out) for each: " + ", ".join(pending))
    for path in selected:
        problem = forbidden_reason("app/" + path)
        if problem:
            raise BuildError(f"Refusing to bundle {path}: {problem}")
    return sorted(selected)


def git_lines(*args):
    output = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=True).stdout
    return [line.decode("utf-8") for line in output.split(b"\0") if line]


# ---------------------------------------------------------------- wheels

def _record_hash(data):
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def install_wheel(wheel_path, target):
    """Unpack a wheel into target after checking every file against its RECORD. Scripts, data and headers are skipped."""
    target = Path(target)
    written = []
    with zipfile.ZipFile(wheel_path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        tops = {safe_member(info.filename)[0] for info in infos}
        dist_infos = [top for top in tops if top.endswith(".dist-info")]
        if len(dist_infos) != 1:
            raise BuildError("Wheel must contain exactly one .dist-info: " + Path(wheel_path).name)
        record_name = dist_infos[0] + "/RECORD"
        records = {}
        for row in csv.reader(io.StringIO(archive.read(record_name).decode("utf-8"))):
            if row:
                records[row[0]] = row[1] if len(row) > 1 else ""
        for info in infos:
            parts = safe_member(info.filename)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise BuildError("Symbolic link in wheel: " + info.filename)
            data = archive.read(info)
            if info.filename != record_name and not info.filename.startswith(dist_infos[0] + "/RECORD."):
                expected = records.get(info.filename)
                if not expected or expected != _record_hash(data):
                    raise BuildError("RECORD mismatch in %s: %s" % (Path(wheel_path).name, info.filename))
            if parts[0].endswith(".data"):
                if parts[1] in ("purelib", "platlib"):
                    parts = parts[2:]
                elif parts[1] in ("scripts", "data", "headers"):
                    continue  # console launchers, man pages and C headers would go outside sys.path; not needed at runtime
                else:
                    raise BuildError("Unsupported wheel data scheme %s in %s" % (parts[1], Path(wheel_path).name))
            destination = target.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(destination, "xb") as output:
                    output.write(data)
            except FileExistsError:
                raise BuildError("Two wheels provide the same file: " + "/".join(parts)) from None
            written.append("/".join(parts))
    return dist_infos[0], written


def collect_licenses(vendor, bundle_root):
    """Map each vendored distribution to the license files it ships (in its dist-info or top-level packages)."""
    result = {}
    for dist in sorted(Path(vendor).glob("*.dist-info")):
        metadata = (dist / "METADATA").read_text(encoding="utf-8", errors="replace").split("\n\n", 1)[0]
        fields = {}
        for line in metadata.splitlines():
            key, _, value = line.partition(": ")
            if key in ("Name", "Version", "License-Expression") or (key == "License" and "\n" not in value and len(value) < 120):
                fields.setdefault(key, value.strip())
            elif key == "Classifier" and value.startswith("License ::"):
                fields.setdefault("Classifiers", []).append(value.strip())
        files = [path for path in dist.rglob("*") if path.is_file() and (LICENSE_NAME.match(path.name) or "licenses" in path.relative_to(dist).parts)]
        tops = set()
        for row in csv.reader(io.StringIO((dist / "RECORD").read_text(encoding="utf-8"))):
            if row and "/" in row[0] and not row[0].split("/")[0].endswith((".dist-info", ".data")):
                tops.add(row[0].split("/")[0])
        for top in sorted(tops):
            package = Path(vendor) / top
            if package.is_dir():
                files += [path for path in package.iterdir() if path.is_file() and LICENSE_NAME.match(path.name)]
        result[dist.name] = {"name": fields.get("Name", dist.name), "version": fields.get("Version", ""),
                             "license": fields.get("License-Expression") or fields.get("License") or "; ".join(fields.get("Classifiers", [])) or "see files",
                             "files": sorted({path.relative_to(bundle_root).as_posix() for path in files})}
    return result


# ---------------------------------------------------------------- audit

def forbidden_reason(relative):
    """Why a bundle-relative POSIX path must not ship, or None when acceptable."""
    parts = PurePosixPath(relative).parts
    lowered = relative.lower()
    if relative in ALLOWED_SPECIAL:
        return None
    if any(part in (APP_FORBIDDEN_PARTS if parts[0] == "app" else FORBIDDEN_PARTS) for part in parts):
        return "development or cache folder"
    name = parts[-1].lower()
    if name == ".env" or name.startswith(".env.") or name in {".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519", "credentials.json", "setup-config.json"}:
        return "credential or local configuration file"
    if lowered.endswith(FORBIDDEN_SUFFIXES):
        return "cache, media, log or key material"
    if len(parts) > 1 and parts[0] == "app" and parts[1] in ("runtime", "models") and relative != "app/models/hand_landmarker.task":
        return "local runtime state or unreviewed model"
    return None


def audit_needles(extra=()):
    """Values that must never appear in the artifact: this build host's home, user name, git email and secret env values."""
    needles = {}
    home = str(Path.home())
    user = os.environ.get("USERNAME") or os.environ.get("USER") or Path(home).name
    for value in {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}:
        needles["build home path"] = needles.get("build home path", set()) | {value}
    if user and len(user) >= 3:
        needles["build user name"] = {f"\\Users\\{user}\\", f"/Users/{user}/", f"/home/{user}/", f"\\\\Users\\\\{user}\\\\"}
    try:
        email = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True, cwd=str(ROOT)).stdout.strip()
    except OSError:
        email = ""
    emails = {email} if "@" in email else set()
    emails |= {value for value in extra if "@" in value}
    if emails:
        needles["git or configured email"] = emails
    secrets_found = {value for key, value in os.environ.items() if SECRET_ENV.search(key) and len(value) >= 12 and not os.path.isabs(value)}
    if secrets_found:
        needles["secret-like environment value"] = secrets_found
    others = {value for value in extra if "@" not in value and len(value) >= 4}
    if others:
        needles["configured audit value"] = others
    encoded = []
    for label, values in needles.items():
        for value in values:
            for variant in {value, value.lower()}:
                encoded.append((label, variant.encode("utf-8")))
                encoded.append((label, variant.encode("utf-16-le")))
    return encoded


def scan_bytes(label, data, needles, findings):
    lowered = data.lower()
    for kind, needle in needles:
        if needle.lower() in lowered:
            findings.append(f"{label}: contains {kind}")
    for kind, pattern in SECRET_PATTERNS.items():
        if pattern.search(data):
            findings.append(f"{label}: matches {kind} pattern")


def audit_tree(bundle_root, needles):
    """Return a list of problems: forbidden files, links, or private/credential-like content anywhere (including nested ZIPs)."""
    findings = []
    bundle_root = Path(bundle_root)
    for path in sorted(bundle_root.rglob("*")):
        relative = path.relative_to(bundle_root).as_posix()
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            findings.append(relative + ": link or reparse point")
            continue
        if not path.is_file():
            continue
        reason = forbidden_reason(relative)
        if reason:
            findings.append(f"{relative}: {reason}")
        data = path.read_bytes()
        scan_bytes(relative, data, needles, findings)
        if data[:4] == b"PK\x03\x04" and path.suffix.lower() in (".zip", ".whl", ".jar"):
            with zipfile.ZipFile(path) as nested:
                for info in nested.infolist():
                    if not info.is_dir():
                        scan_bytes(f"{relative}!{info.filename}", nested.read(info), needles, findings)
    return findings


# ---------------------------------------------------------------- build steps

def tools_python(work, manifest, env):
    """A fresh build-host environment installed from a complete hash lock, never shipped."""
    venv = Path(work) / "tools-venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    stamp = venv / "jarvis-tools.json"
    tools_lock = validate_tools_lock(TOOLS_LOCK_PATH, manifest["build_tools"])
    clean_dir(venv, work)
    run([sys.executable, "-I", "-m", "venv", venv], env)
    run([python, "-I", "-m", "pip", "install", "--isolated", "--disable-pip-version-check", "--no-input", "--no-cache-dir",
         "--only-binary=:all:", "--require-hashes", "--index-url", "https://pypi.org/simple", "--requirement", TOOLS_LOCK_PATH], env)
    stamp.write_text(json.dumps({"sha256": tools_lock, "fresh": True}), encoding="utf-8")
    return python


def validate_tools_lock(path, expected):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 512 * 1024:
        raise BuildError("The build-tools hash lock is missing or too large")
    packages = {}
    text = re.sub(r"\\\n[ \t]*", " ", path.read_text(encoding="utf-8"))
    for line in text.splitlines():
        line = " ".join(line.split())
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([a-z0-9][a-z0-9._-]*)==([a-zA-Z0-9.+-]+)(?: --hash=sha256:[0-9a-f]{64})+", line)
        if not match or match[1] in packages:
            raise BuildError("Unsupported entry in the build-tools hash lock")
        packages[match[1]] = match[2]
    if not packages or any(packages.get(name) != version for name, version in expected.items()):
        raise BuildError("The build-tools lock does not match the reviewed tool versions")
    return sha256_file(path)


def fetch_python(work, manifest, tools, env):
    pin = manifest["python"]
    cache = Path(work) / "cache" / "python"
    archive = download(pin["url"], cache / Path(urlsplit(pin["url"]).path).name, pin["sha256"], 64 * 1024 * 1024)
    if archive.stat().st_size != pin["size"]:
        raise BuildError("Python archive size differs from python.org metadata")
    bundle = cache / (archive.name + ".sigstore")
    opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(Request(pin["sigstore_bundle_url"], headers={"User-Agent": "jarvis-windows-build/1"}), timeout=60) as response:
        bundle.write_bytes(response.read(1024 * 1024))
    run([tools, "-I", "-m", "sigstore", "verify", "identity", "--bundle", bundle,
         "--cert-identity", pin["sigstore_identity"], "--cert-oidc-issuer", pin["sigstore_issuer"], archive], env)
    log(f"Python {pin['version']} embeddable verified: SHA-256 {pin['sha256']} and Sigstore identity {pin['sigstore_identity']}")
    return archive


def verify_authenticode(runtime, subject, env=None):
    """Check every runtime binary carries a valid Authenticode signature from the PSF (or Microsoft for the VC runtime)."""
    if os.name != "nt":
        raise BuildError("Authenticode verification needs Windows")
    literal = str(runtime).replace("'", "''")
    script = ("$ErrorActionPreference = 'Stop'; Get-ChildItem -LiteralPath '%s' -File | Where-Object { $_.Extension -in '.exe','.dll','.pyd' } | ForEach-Object { "
              "$s = Get-AuthenticodeSignature -LiteralPath $_.FullName; [pscustomobject]@{ Name = $_.Name; Status = $s.Status.ToString(); "
              "Subject = if ($s.SignerCertificate) { $s.SignerCertificate.Subject } else { '' } } } | ConvertTo-Json -Compress" % literal)
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    powershell = str(Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    clean_env = {key: value for key, value in (env or os.environ).items() if key.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "TEMP", "TMP", "TMPDIR"}}
    process = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=300, env=clean_env)
    try:
        if process.returncode != 0 or not process.stdout or len(process.stdout) > 64 * 1024:
            raise ValueError()
        results = json.loads(process.stdout)
    except (ValueError, TypeError):
        raise BuildError("The Windows signature checker could not return a valid result") from None
    results = results if isinstance(results, list) else [results]
    problems = []
    for item in results:
        owner = "Microsoft Corporation" if item["Name"].lower().startswith("vcruntime") else subject
        if item["Status"] != "Valid" or not re.search(r"(?:^|,)\s*O=" + re.escape(owner) + r"(?:,|$)", item["Subject"]):
            problems.append(f"{item['Name']}: {item['Status']} {item['Subject']}")
    if problems or not results:
        raise BuildError("Runtime Authenticode check failed: " + "; ".join(problems))
    return {"checked": len(results), "status": "Valid", "subjects": sorted({item["Subject"].split(",")[0] for item in results})}


def fetch_wheels(work, manifest, env):
    target = manifest["target"]
    directory = Path(work) / "cache" / "wheels"
    directory.mkdir(parents=True, exist_ok=True)
    requirements = Path(work) / "wheel-lock.txt"
    requirements.write_text("".join(f"{w['name']}=={w['version']} --hash=sha256:{w['sha256']}\n" for w in manifest["wheels"]), encoding="utf-8")
    run([sys.executable, "-I", "-m", "pip", "download", "--isolated", "--disable-pip-version-check", "--no-input", "--no-cache-dir",
         "--no-deps", "--only-binary=:all:", "--require-hashes", "--index-url", "https://pypi.org/simple",
         "--platform", target["platform"], "--python-version", target["python_version"], "--implementation", target["implementation"],
         "--abi", target["abi"], "--abi", "abi3", "--abi", "none", "-d", directory, "-r", requirements], env)
    wheels = []
    for wheel in manifest["wheels"]:
        path = directory / wheel["filename"]
        if not path.is_file() or sha256_file(path) != wheel["sha256"]:
            raise BuildError("Pinned wheel missing or altered: " + wheel["filename"])
        wheels.append(path)
    return wheels


def build_srt(work, manifest, tools, env):
    sdist = manifest["sdists"][0]
    archive = download(sdist["url"], Path(work) / "cache" / "sdist" / Path(urlsplit(sdist["url"]).path).name, sdist["sha256"], 4 * 1024 * 1024)
    output = clean_dir(Path(work) / "srt-wheel", work)
    run([tools, "-I", "-m", "pip", "wheel", "--isolated", "--disable-pip-version-check", "--no-input", "--no-cache-dir",
         "--no-deps", "--no-build-isolation", "--no-index", "-w", output, archive], dict(env, SOURCE_DATE_EPOCH="315532800"))
    built = list(output.glob("srt-3.5.3-*.whl"))
    if len(built) != 1 or not built[0].name.endswith("-none-any.whl"):
        raise BuildError("srt did not build into exactly one pure-Python wheel")
    verify_srt_payload(archive, built[0])
    return archive, built[0]


def verify_srt_payload(source, wheel):
    expected = {}
    prefix = "srt-3.5.3/"
    with tarfile.open(source, "r:gz") as archive:
        for member in archive.getmembers():
            name = member.name.removeprefix(prefix)
            if member.name.startswith(prefix) and (name == "srt.py" or name.startswith("srt_tools/") and name.endswith(".py") and not name.startswith("srt_tools/tests/")):
                if not member.isfile() or member.size > 1024 * 1024 or name in expected:
                    raise BuildError("The srt source has an unsupported Python member")
                expected[name] = archive.extractfile(member).read()
    with zipfile.ZipFile(wheel) as archive:
        actual = {name: archive.read(name) for name in archive.namelist() if name.endswith(".py")}
    if "srt.py" not in expected or actual != expected:
        raise BuildError("The built srt Python payload differs from its checksum-verified source")


def pth_text(pin):
    """The embeddable ._pth: stdlib ZIP, runtime DLLs, app and vendor only. No 'import site', so no user site or .pth hooks."""
    return f"{pin['stdlib_zip']}\n.\n../app\n../vendor\n"


def write_text(path, text, crlf=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = text.replace("\r\n", "\n")
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def stage_bundle(work, manifest, app_contents, source, install_tools, bundle_module, extra_needles=(), launcher_content=None):
    work = Path(work)
    env = safe_env(clean_dir(work / "tmp", work))
    tools = tools_python(work, manifest, env)
    python_zip = fetch_python(work, manifest, tools, env)
    wheels = fetch_wheels(work, manifest, env)
    srt_sdist, srt_wheel = build_srt(work, manifest, tools, env)
    model_cache = work / "cache" / "hand"
    model_cache.mkdir(parents=True, exist_ok=True)
    install_tools.download_model(model_cache)
    supplements = [download(item["url"], work / "cache" / "licenses" / item["file"], item["sha256"], 1024 * 1024) for item in manifest["license_supplements"]]

    stage = clean_dir(work / "stage", work)
    bundle = stage / manifest["bundle_name"]
    runtime, app, vendor, licenses = bundle / "runtime", bundle / "app", bundle / "vendor", bundle / "licenses"
    for directory in (runtime, app, vendor, licenses):
        directory.mkdir(parents=True)

    pin = manifest["python"]
    extract_zip(python_zip, runtime)
    if not (runtime / "python.exe").is_file() or not (runtime / pin["stdlib_zip"]).is_file() or not (runtime / "LICENSE.txt").is_file():
        raise BuildError("The embeddable package layout is unexpected")
    authenticode = verify_authenticode(runtime, pin["authenticode_subject"], env=env)
    write_text(runtime / pin["pth_name"], pth_text(pin), crlf=True)

    installed = {}
    for wheel in [*wheels, srt_wheel]:
        dist, _files = install_wheel(wheel, vendor)
        installed[dist] = wheel.name

    for relative, content in sorted(app_contents.items()):
        destination = app.joinpath(*PurePosixPath(relative).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    (app / "models").mkdir()
    shutil.copyfile(model_cache / "models" / "hand_landmarker.task", app / "models" / "hand_landmarker.task")
    if sha256_file(app / "models" / "hand_landmarker.task") != manifest["hand_model"]["sha256"]:
        raise BuildError("Hand model checksum changed during staging")
    if not isinstance(launcher_content, bytes):
        raise BuildError("A reviewed launcher snapshot is required")
    launcher = launcher_content.decode("utf-8")
    write_text(bundle / LAUNCHER, launcher, crlf=True)

    commit, dirty = source["commit"], source["dirty"]
    packages = dict(sorted(manifest["_versions"].items()))
    runtime_manifest = {"schema": bundle_module.SCHEMA, "platform": "win-amd64", "python": pin["version"],
                        "features": manifest["features"], "packages": packages,
                        "hand_model_sha256": manifest["hand_model"]["sha256"], "voice_model_included": False,
                        "source": {"commit": commit, "dirty": dirty}}
    bundle_module.validate_manifest(runtime_manifest)
    write_text(app / bundle_module.MANIFEST_NAME, json.dumps(runtime_manifest, indent=2) + "\n")

    for item, path in zip(manifest["license_supplements"], supplements):
        shutil.copyfile(path, licenses / item["file"])
    notices = write_licenses(bundle, vendor, licenses, runtime, manifest)
    provenance = {
        "schema": "jarvis-windows-provenance/1",
        "source": {"commit": commit, "dirty": dirty, "mode": source["mode"], "app_files": sorted(app_contents),
                   "uncommitted_extras": source["extras"], "deferred_new_files": source["deferred"],
                   "launcher": {"path": LAUNCHER, "mode": source["mode"], "sha256": hashlib.sha256(launcher_content).hexdigest()}},
        "python": {"version": pin["version"], "url": pin["url"], "sha256": pin["sha256"], "size": pin["size"],
                   "sigstore": {"bundle": pin["sigstore_bundle_url"], "identity": pin["sigstore_identity"], "issuer": pin["sigstore_issuer"], "verified": True},
                   "authenticode": authenticode, "modified": [pin["pth_name"]]},
        "wheels": [{"filename": w["filename"], "sha256": w["sha256"], "source": "https://pypi.org/simple"} for w in manifest["wheels"]],
        "source_builds": [{"name": "srt", "version": "3.5.3", "sdist": manifest["sdists"][0]["url"], "sdist_sha256": sha256_file(srt_sdist),
                           "wheel": srt_wheel.name, "wheel_sha256": sha256_file(srt_wheel),
                           "built_with": {"setuptools": manifest["build_tools"]["setuptools"], "build_isolation": "pinned tools venv, --no-index"}}],
        "hand_model": {"url": manifest["hand_model"]["url"], "sha256": manifest["hand_model"]["sha256"], "license": manifest["hand_model"]["license"]},
        "build_tools": manifest["build_tools"],
        "build_tools_lock_sha256": sha256_file(TOOLS_LOCK_PATH),
        "runtime_pip": "not used; packages are preinstalled in vendor",
    }
    write_text(bundle / "BUILD-PROVENANCE.json", json.dumps(provenance, indent=2) + "\n")
    write_text(bundle / "README.txt", README_TEXT.format(python=pin["version"]), crlf=True)

    findings = audit_tree(bundle, audit_needles(extra_needles))
    if findings:
        raise BuildError("Bundle audit failed:\n  " + "\n  ".join(findings[:50]))
    log(f"Audit passed for {sum(1 for p in bundle.rglob('*') if p.is_file())} files")
    return bundle, provenance, notices


README_TEXT = """Jarvis for Windows (portable, x64)

1. Extract the whole ZIP to a folder you own, for example Documents\\Jarvis-Windows.
2. Double-click Start-Jarvis-Windows.cmd. The setup wizard opens in your browser in safe demo mode.

No separate Python installation is needed: runtime\\ holds the official python.org CPython {python}
embeddable package, and vendor\\ already contains the camera, hand-gesture and offline-voice packages.
Nothing is installed system-wide and no administrator rights are requested.

The camera, microphone, mouse and Windows actions stay off until you enable them in Jarvis.
AI providers are optional; any API key you add is your own and stays on this computer.
An offline voice (Vosk) model is not included; see app\\docs\\WINDOWS_BUNDLE.md for the manual step.

This package is not code-signed by the Jarvis project. Windows SmartScreen or antivirus software may
ask you to confirm. Checksums for every bundled file source are in BUILD-PROVENANCE.json, and
license texts are in licenses\\.
"""


def write_licenses(bundle, vendor, licenses, runtime, manifest):
    notices = collect_licenses(vendor, bundle)
    supplemented = {}
    for item in manifest["license_supplements"]:
        supplemented.setdefault(item["applies_to"], []).append(item)
    for info in notices.values():
        info["supplements"] = supplemented.get(re.sub(r"[-_.]+", "-", info["name"]).lower(), [])
    missing = [dist for dist, info in notices.items() if not info["files"] and not info["supplements"]]
    if missing:
        raise BuildError("No license file shipped for: " + ", ".join(missing))
    version = manifest["python"]["version"]
    shutil.copyfile(runtime / "LICENSE.txt", licenses / f"python-{version}-LICENSE.txt")
    mediapipe = next(info for info in notices.values() if info["name"].lower() == "mediapipe")
    apache = next((bundle / file for file in mediapipe["files"] if "Apache License" in (bundle / file).read_text(encoding="utf-8", errors="replace")
                   and "Version 2.0" in (bundle / file).read_text(encoding="utf-8", errors="replace")), None)
    if apache is None:
        raise BuildError("Apache-2.0 text for the hand model notice was not found")
    shutil.copyfile(apache, licenses / "Apache-2.0.txt")
    model = manifest["hand_model"]
    lines = ["Jarvis for Windows: third-party notices", "", "This bundle redistributes the components below unmodified, except that",
             f"runtime/{manifest['python']['pth_name']} is replaced to isolate the import path. Their license texts are kept",
             "where each component ships them (paths are relative to the Jarvis-Windows folder).", "",
             f"Python {version} (python.org Windows embeddable package)", "  License: Python Software Foundation License Version 2 (and bundled third-party licenses)",
             f"  Text: runtime/LICENSE.txt (copy: licenses/python-{version}-LICENSE.txt)", "",
             "MediaPipe Hand Landmarker model (app/models/hand_landmarker.task)", f"  Source: {model['url']}", f"  SHA-256: {model['sha256']}",
             f"  License: Apache License 2.0, per the official model card: {model['model_card']}", "  Text: licenses/Apache-2.0.txt", "",
             "Jarvis Workbench", "  License: MIT", "  Text: app/LICENSE", ""]
    for info in notices.values():
        lines += [f"{info['name']} {info['version']}", f"  License: {info['license']}"] + [f"  Text: {file}" for file in info["files"]]
        lines += [f"  Text: licenses/{item['file']} ({item['title']}; source {item['url']})" for item in info["supplements"]] + [""]
    write_text(licenses / "THIRD-PARTY-NOTICES.txt", "\n".join(lines), crlf=True)
    return notices


def make_zip(bundle, output, epoch):
    """Write a deterministic ZIP: sorted entries, fixed timestamps, no host metadata."""
    stamp = time.gmtime(max(epoch, 315532800))[:6]
    temporary = Path(str(output) + ".part")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(Path(bundle).rglob("*"), key=lambda item: item.relative_to(bundle.parent).as_posix()):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(path.relative_to(bundle.parent).as_posix(), date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0x20
            with open(path, "rb") as source, archive.open(info, "w") as target:
                shutil.copyfileobj(source, target, 1 << 20)
    os.replace(temporary, output)
    digest = sha256_file(output)
    write_text(Path(str(output) + ".sha256"), f"{digest} *{Path(output).name}\n")
    return digest


# ---------------------------------------------------------------- smoke test

SMOKE_CODE = r"""
import importlib, json, os, platform, sys
base = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
rel = lambda p: os.path.relpath(os.path.abspath(p), base).replace(os.sep, "/")
out = {"python": platform.python_version(), "bits": sys.maxsize > 2**32, "isolated": sys.flags.isolated,
       "ignore_environment": sys.flags.ignore_environment, "site_loaded": "site" in sys.modules,
       "user_site": sys.flags.no_user_site, "path": [rel(p) for p in sys.path], "prefix": rel(sys.prefix), "imports": {}}
for name in ("numpy", "cv2", "mediapipe", "vosk", "sounddevice", "srt", "jarvis"):
    module = importlib.import_module(name)
    out["imports"][name] = {"version": getattr(module, "__version__", None), "file": rel(module.__file__)}
import numpy
from jarvis import bundle, planner
from jarvis.hand_devices import MediaPipeHands
root = os.path.join(base, "app")
tracker = MediaPipeHands(os.path.join(root, "models", "hand_landmarker.task"))
try:
    blank = numpy.zeros((480, 640, 3), dtype=numpy.uint8)
    found = [len(tracker.detect(blank, 1.0 + index / 30)) for index in range(3)]
finally:
    tracker.close()
out["hand_inference"] = {"frames": len(found), "hands_per_frame": found}
out["runtime_info"] = bundle.runtime_info(root)
out["feature_ready"] = {feature: bundle.feature_ready(root, feature) for feature in bundle.FEATURES}
report = planner.diagnostics(root)
out["diagnostics"] = {key: report[key] for key in ("python", "python_ok", "packages", "hand_model", "voice_model")}
print("SMOKE-JSON:" + json.dumps(out, sort_keys=True), flush=True)
"""


def smoke_env(temporary, canary, profile):
    """A minimal but realistic user environment: a synthetic profile (so nothing touches the real one) plus
    hostile PYTHON* variables that an isolated interpreter must ignore."""
    env = safe_env(temporary)
    for name, folder in (("USERPROFILE", profile), ("APPDATA", profile / "AppData" / "Roaming"), ("LOCALAPPDATA", profile / "AppData" / "Local")):
        folder.mkdir(parents=True, exist_ok=True)
        env[name] = str(folder)
    env["HOMEDRIVE"], env["HOMEPATH"] = profile.drive, str(profile)[len(profile.drive):]
    env["PYTHONPATH"] = str(canary)
    env["PYTHONHOME"] = str(canary)
    env["PYTHONSTARTUP"] = str(canary / "json.py")
    env["PYTHONUSERBASE"] = str(canary)
    env["JARVIS_NO_PAUSE"] = "1"
    return env


def smoke_test(zip_path, work, pth_name, stdlib_zip):
    """Extract into a fresh folder with spaces and exercise the bundle with its own interpreter only."""
    import urllib.request
    work = Path(work)
    area = clean_dir(work / "smoke", work)
    extract_root = area / "Jarvis smoke test (with spaces)"
    extract_zip(zip_path, extract_root)
    bundle = next(extract_root.iterdir())
    python = bundle / "runtime" / "python.exe"
    canary = area / "canary"
    (canary / "jarvis").mkdir(parents=True)
    for name in ("json.py", "cv2.py", "site.py", "jarvis/__init__.py", "sitecustomize.py", "usercustomize.py"):
        write_text(canary / name, "raise SystemExit('canary module imported: isolation failed')\n")
    temporary = area / "tmp"
    temporary.mkdir()
    env = smoke_env(temporary, canary, area / "Smoke User Profile")
    results = {"extract_path_has_spaces": " " in str(bundle), "longest_relative_path": max(len(p.relative_to(bundle).as_posix()) for p in bundle.rglob("*"))}

    output = run([python, "-I", "-c", SMOKE_CODE], env, cwd=canary, timeout=300)
    data = json.loads(next(line for line in output.splitlines() if line.startswith("SMOKE-JSON:"))[len("SMOKE-JSON:"):])
    expected_path = [f"runtime/{stdlib_zip}", "runtime", "app", "vendor"]
    checks = {
        "sys.path is exactly the ._pth entries": data["path"] == expected_path,
        "64-bit interpreter": data["bits"],
        "site not imported": not data["site_loaded"],
        "isolated and environment ignored": data["isolated"] == 1 and data["ignore_environment"] == 1,
        "packages load from vendor": all(info["file"].startswith("vendor/") for name, info in data["imports"].items() if name != "jarvis"),
        "jarvis loads from app": data["imports"]["jarvis"]["file"].startswith("app/"),
        "hand inference ran on blank frames with no hands": data["hand_inference"]["frames"] == 3 and not any(data["hand_inference"]["hands_per_frame"]),
        "bundle.runtime_info bundled": data["runtime_info"]["bundled"] is True,
        "all features ready": all(data["feature_ready"].values()),
        "diagnostics see packages and hand model": all(data["diagnostics"]["packages"].values()) and data["diagnostics"]["hand_model"],
    }
    results["interpreter"] = data
    leaked = json.dumps(data["runtime_info"])
    checks["runtime_info has no paths"] = str(bundle) not in leaked and ":\\\\" not in leaked and "/Users/" not in leaked

    cmd = subprocess.run(f'cmd.exe /d /s /c ""{bundle / LAUNCHER}" --check"', env=env, cwd=str(canary), capture_output=True, text=True, errors="replace", timeout=120)
    results["launcher_check"] = {"exit": cmd.returncode, "output": cmd.stdout.strip()[-300:]}
    checks["Start-Jarvis-Windows.cmd --check"] = cmd.returncode == 0 and "prerequisites look ready" in cmd.stdout

    process = subprocess.Popen([str(python), "-I", "-u", "-m", "jarvis.launcher", "--no-browser", "--port", "0"], cwd=str(canary), env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    served = {}
    try:
        deadline = time.monotonic() + 60
        url = None
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line:
                break
            match = re.search(r"Jarvis: (http://127\.0\.0\.1:\d+)", line)
            if match:
                url = match[1]
                break
        if url is None:
            raise BuildError("Launcher did not report its local address")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for route in ("/setup", "/", "/app.js", "/setup.js", "/quickstart-catalog.json", "/api/quickstart", "/api/setup/diagnostics"):
            with opener.open(url + route, timeout=10) as response:
                served[route] = response.status
                if route == "/api/setup/diagnostics":
                    diagnostics = json.loads(response.read())
                    served["diagnostics_packages"] = diagnostics.get("packages")
                    served["diagnostics_hand_model"] = diagnostics.get("hand_model")
        with opener.open(url + "/api/session", timeout=10) as response:
            token = json.loads(response.read())["token"]

        def post_json(base, path, data, session):
            request = Request(base + path, data=json.dumps(data).encode(), method="POST",
                              headers={"Content-Type": "application/json", "X-Jarvis-Token": session, "Origin": base})
            with opener.open(request, timeout=60) as response:
                return json.loads(response.read())

        choices = {"role": "creator", "budget": 0, "existing": "none"}
        saved = post_json(url, "/api/quickstart/save", {"choices": choices}, token)
        expected_choices = {**choices, "dictation": "keyboard", "reply": "none", "automation": "none"}
        checks["legacy setup choices migrate to six explicit answers"] = saved["choices"] == expected_choices
        if not checks["legacy setup choices migrate to six explicit answers"]:
            raise BuildError("Bundled onboarding did not migrate saved choices")
        post_json(url, "/api/quickstart/install", {"consent": True}, token)
        deadline = time.monotonic() + 30
        while True:
            with opener.open(url + "/api/quickstart", timeout=10) as response:
                setup_state = json.loads(response.read())
            if setup_state["install"]["state"] != "running" or time.monotonic() >= deadline:
                break
            time.sleep(.1)
        checks["bundled onboarding installs hand tools without runtime pip"] = setup_state["install"]["state"] == "ready"
        if not checks["bundled onboarding installs hand tools without runtime pip"]:
            raise BuildError("Bundled onboarding did not become ready")
        desktop = post_json(url, "/api/quickstart/launch", {"consent": True}, token)["url"].rstrip("/")
        if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", desktop):
            raise BuildError("Desktop returned an unsupported local address")
        try:
            with opener.open(desktop + "/api/status", timeout=10) as response:
                desktop_state = json.loads(response.read())
            checks["real desktop starts idle with hand permissions disarmed"] = (desktop_state["hand"]["allowed"] and not desktop_state["active"]
                and not desktop_state["lock_armed"] and not desktop_state["sheet_armed"] and desktop_state["hand"]["mode"] == "off"
                and not desktop_state["hand"]["move_cursor"] and desktop_state["camera_owner"] is None)
        finally:
            with opener.open(desktop + "/api/session", timeout=10) as response:
                desktop_token = json.loads(response.read())["token"]
            post_json(desktop, "/api/setup/exit", {}, desktop_token)
        request = Request(url + "/api/setup/exit", data=b"{}", method="POST", headers={"Content-Type": "application/json", "X-Jarvis-Token": token})
        with opener.open(request, timeout=10) as response:
            served["exit"] = response.status
        process.wait(timeout=30)
        served["process_exit"] = process.returncode
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
    results["http"] = served
    checks["local wizard served and exited cleanly"] = (all(served.get(route) == 200 for route in ("/setup", "/", "/app.js", "/setup.js", "/quickstart-catalog.json", "/api/quickstart", "/api/setup/diagnostics"))
                                                         and served.get("exit") == 200 and served.get("process_exit") == 0
                                                         and served.get("diagnostics_hand_model") is True and all((served.get("diagnostics_packages") or {"x": False}).values()))
    with zipfile.ZipFile(zip_path) as archive:
        shipped = set(archive.namelist())
    writes = sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file() and "__pycache__" not in p.parts
                    and f"{bundle.name}/{p.relative_to(bundle).as_posix()}" not in shipped)
    results["new_files_after_run"] = writes
    results["checks"] = checks
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise BuildError("Smoke test failed: " + ", ".join(failed) + "\n" + json.dumps(results, indent=2)[:6000])
    return results


# ---------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, help="Path of the ZIP to write (a .sha256 file is written next to it)")
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK, help="Scratch folder (download cache, staging, smoke test)")
    parser.add_argument("--extra", action="append", default=[], help="Reviewed new app file (repo-relative) to include even if not yet tracked")
    parser.add_argument("--defer", action="append", default=[], help="New untracked app file (repo-relative) deliberately left out of this build")
    parser.add_argument("--source", choices=("worktree", "head"), default="worktree",
                        help="Read tracked app files from the working tree (default) or from the committed HEAD snapshot")
    parser.add_argument("--skip-smoke", action="store_true", help="Build and audit only")
    parser.add_argument("--smoke-only", type=Path, help="Only smoke-test an existing ZIP")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        raise SystemExit("The build needs Python 3.11 or newer on the build host")
    if args.output is None and args.smoke_only is None:
        parser.error("--output is required unless --smoke-only is used")
    sys.path.insert(0, str(ROOT))
    from jarvis import bundle as bundle_module, install_tools
    manifest = load_build_manifest(MANIFEST_PATH, install_tools)
    install_tools.selected_packages(ROOT, "all")
    work = args.work.resolve()
    for target in (args.output, args.smoke_only):
        if target is not None and target.resolve().is_relative_to(work):
            raise BuildError("The output and smoke ZIP must be outside the build work folder")
    work.mkdir(parents=True, exist_ok=True)
    if args.smoke_only:
        print(json.dumps(smoke_test(args.smoke_only.resolve(), work, manifest["python"]["pth_name"], manifest["python"]["stdlib_zip"]), indent=2))
        return 0
    extras = sorted(set(manifest["app"]["extra_files"]) | {Path(path).as_posix() for path in args.extra})
    deferred = sorted({Path(path).as_posix() for path in args.defer})
    head = args.source == "head"
    tracked = set(git_lines("ls-tree", "-r", "-z", "--name-only", "HEAD") if head else git_lines("ls-files", "-z"))
    if head:
        status = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"], capture_output=True, check=True).stdout
        if status.strip() or LAUNCHER not in tracked or TOOLS_LOCK_PATH.relative_to(ROOT).as_posix() not in tracked:
            raise BuildError("Commit the reviewed sources, launcher and tools lock, and keep the tracked tree clean before a HEAD build")
    app_files = select_app_files(tracked, git_lines("ls-files", "-z", "--others", "--exclude-standard"), manifest["app"]["include"], extras,
                                 exists=lambda path: head or (ROOT / path).is_file(), deferred=deferred)
    app_contents = {}
    for path in app_files:
        if head and path in tracked:
            app_contents[path] = subprocess.run(["git", "-C", str(ROOT), "show", "HEAD:" + path], capture_output=True, check=True).stdout
        else:
            local = ROOT.joinpath(*PurePosixPath(path).parts)
            if local.is_symlink() or not local.is_file():
                raise BuildError("App source must be a regular file: " + path)
            app_contents[path] = local.read_bytes()
    modified = "" if head else subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True, check=True).stdout.strip()
    uncommitted = sorted(path for path in app_files if path not in tracked)
    if LAUNCHER not in tracked:
        uncommitted.append(LAUNCHER)
    launcher_content = subprocess.run(["git", "-C", str(ROOT), "show", "HEAD:" + LAUNCHER], capture_output=True, check=True).stdout if head else (ROOT / LAUNCHER).read_bytes()
    source = {"commit": git_lines("rev-parse", "HEAD")[0].strip(), "dirty": bool(modified) or bool(uncommitted),
              "mode": args.source, "extras": uncommitted, "deferred": deferred}
    needles = [value for value in os.environ.get("JARVIS_BUNDLE_AUDIT_VALUES", "").split(";") if value.strip()]
    bundle, provenance, notices = stage_bundle(work, manifest, app_contents, source, install_tools, bundle_module, needles, launcher_content=launcher_content)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    epoch = int(subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%ct"], capture_output=True, text=True, check=True).stdout.strip())
    digest = make_zip(bundle, output, epoch)
    log(f"Wrote {output.name}: {output.stat().st_size} bytes, SHA-256 {digest}")
    with zipfile.ZipFile(output) as archive:
        bad = archive.testzip()
        if bad:
            raise BuildError("ZIP integrity check failed at " + bad)
    rebuild = ("python -I scripts/build_windows.py --output " + output.name + " --source " + args.source
               + "".join(" --extra " + extra for extra in args.extra) + "".join(" --defer " + path for path in deferred))
    summary = {"zip": output.name, "bytes": output.stat().st_size, "sha256": digest, "python": manifest["python"]["version"],
               "packages": len(notices), "app_files": len(app_files), "deferred": deferred, "rebuild": rebuild}
    if not args.skip_smoke:
        summary["smoke"] = smoke_test(output, work, manifest["python"]["pth_name"], manifest["python"]["stdlib_zip"])
    write_text(work / "last-build.json", json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as error:
        print("Build failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)
