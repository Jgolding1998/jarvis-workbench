"""Local setup orchestration. Secrets never enter the saved setup file."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from queue import Empty, Queue

from . import install_tools, planner

CLOUD = frozenset({"nvidia", "openai", "anthropic"})
PROVIDERS = CLOUD | {"none", "ollama"}


class OnboardingError(ValueError):
    pass


class Onboarding:
    def __init__(self, root, *, vault=None, responder=None, installer=None, launch=None):
        self.root = Path(root)
        self._vault = vault
        self._responder = responder
        self._installer = installer or self._install
        self._launch = launch or self._launch_desktop
        self._lock = threading.RLock()
        self._request_lock = threading.Lock()
        self._choices = None
        self._install_state = "idle"
        self._install_message = "Choose your setup, then install the selected tools."
        self._installed_feature = None
        self._last_call = 0.0
        self._desktop_url = None
        self._desktop_process = None
        self._launching = False
        try:
            path = install_tools.local_path(self.root, "runtime", "quickstart.json")
            if path.is_file() and path.stat().st_size <= 1024 and path.stat().st_nlink == 1:
                from .quickstart import validate_choices
                self._choices = validate_choices(planner.parse_json(path.read_bytes()))
        except (ValueError, OSError, TypeError):
            pass

    def _credentials(self):
        if self._vault is None:
            from .credential_store import WindowsCredentialStore
            self._vault = WindowsCredentialStore()
        return self._vault

    def snapshot(self):
        from .quickstart import recommend
        from .provider_client import catalog
        credentials = {}
        for provider in sorted(CLOUD):
            try:
                credentials[provider] = bool(self._credentials().status(provider))
            except (ValueError, OSError):
                credentials[provider] = False
        with self._lock:
            return {"local": True, "choices": self._choices.copy() if self._choices else None,
                    "plan": recommend(self._choices) if self._choices else None,
                    "credentials": credentials,
                    "install": {"state": self._install_state, "message": self._install_message},
                    "providers": catalog()}

    def save(self, choices):
        from .quickstart import validate_choices, recommend
        choices = validate_choices(choices)
        plan = recommend(choices)
        with self._lock:
            self._refresh_desktop()
            if self._install_state == "running":
                raise OnboardingError("Wait for installation to finish before changing your setup.")
            if self._launching:
                raise OnboardingError("Wait for the desktop app to finish starting.")
            if self._desktop_url is not None and self._installed_feature != self._feature(plan):
                raise OnboardingError("Close the desktop app and restart setup before changing its device features.")
            directory = install_tools.local_path(self.root, "runtime")
            directory.mkdir(exist_ok=True)
            target = install_tools.local_path(self.root, "runtime", "quickstart.json")
            if target.exists() and (not target.is_file() or target.stat().st_nlink > 1):
                raise OnboardingError("The saved setup file is unsupported. Existing files were preserved.")
            temporary = None
            try:
                with tempfile.NamedTemporaryFile("w", dir=directory, prefix="quickstart-", suffix=".tmp", encoding="utf-8", delete=False) as handle:
                    temporary = Path(handle.name)
                    json.dump(choices, handle, ensure_ascii=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                install_tools.local_path(self.root, "runtime", "quickstart.json")
                os.replace(temporary, target)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            self._choices = choices
            feature = self._feature(plan)
            if self._installed_feature != feature:
                self._install_state = "idle"
                self._install_message = "Install the tools for your selected setup."
        return self.snapshot()

    @staticmethod
    def _feature(plan):
        features = plan.get("features", [])
        return "hand" if "hand" in features or "hand_control" in features else "camera"

    @staticmethod
    def _provider(provider, *, cloud_only=False):
        if type(provider) is not str or provider not in (CLOUD if cloud_only else PROVIDERS):
            raise OnboardingError("Choose a supported AI provider.")
        return provider

    def save_key(self, provider, key):
        self._credentials().save(self._provider(provider, cloud_only=True), key)
        return self.snapshot()

    def delete_key(self, provider):
        self._credentials().delete(self._provider(provider, cloud_only=True))
        return self.snapshot()

    def chat(self, provider, prompt, consent):
        provider = self._provider(provider)
        if consent is not True:
            raise OnboardingError("Confirm the provider request first. It may use credits.")
        if type(prompt) is not str or not prompt.strip() or len(prompt) > 4000:
            raise OnboardingError("Enter between 1 and 4,000 characters.")
        if not self._request_lock.acquire(blocking=False):
            raise OnboardingError("An AI request is already running. Wait for it to finish.")
        key = None
        try:
            if time.monotonic() - self._last_call < 2:
                raise OnboardingError("Wait a moment before sending another request.")
            self._last_call = time.monotonic()
            if provider in CLOUD:
                key = self._credentials().read(provider)
                if not key:
                    raise OnboardingError("Save your own API key for this provider first.")
            from .provider_client import respond
            reply = (self._responder or respond)(provider, prompt, key=key)
            return {"reply": reply}
        finally:
            key = None
            self._request_lock.release()

    def start_install(self, consent):
        if consent is not True:
            raise OnboardingError("Confirm the package and model downloads first.")
        from .quickstart import recommend
        with self._lock:
            self._refresh_desktop()
            if self._choices is None:
                raise OnboardingError("Save your setup choices first.")
            if self._install_state == "running":
                raise OnboardingError("Installation is already running.")
            if self._launching or self._desktop_url is not None:
                raise OnboardingError("Close the desktop app before reinstalling its tools.")
            feature = self._feature(recommend(self._choices))
            self._install_state = "running"
            self._install_message = "Preparing the selected tools. Devices remain off."

        def worker():
            try:
                self._installer(feature)
            except Exception:
                with self._lock:
                    self._install_state = "error"
                    self._install_message = "Installation did not finish. Existing files were preserved. Try again or follow the manual installation guide."
            else:
                with self._lock:
                    self._installed_feature = feature
                    self._install_state = "ready"
                    self._install_message = "Your desktop tools are ready. Open the dashboard when you are ready."

        threading.Thread(target=worker, daemon=True, name="jarvis-install").start()
        return self.snapshot()

    def _install(self, feature):
        from . import bundle
        if bundle.is_bundled(self.root):
            if not bundle.feature_ready(self.root, feature):
                raise OnboardingError("The portable package is incomplete. Download a fresh release.")
            if feature == "hand":
                install_tools.download_model(self.root)
            return
        install_tools.install(self.root, feature, install_tools.selected_packages(self.root, feature))

    def launch(self, consent):
        if consent is not True:
            raise OnboardingError("Confirm opening the local desktop app first.")
        with self._lock:
            self._refresh_desktop()
            if self._install_state != "ready":
                raise OnboardingError("Finish installing the selected tools first.")
            if self._desktop_url is not None:
                return {"url": self._desktop_url}
            if self._launching:
                raise OnboardingError("The desktop app is already starting.")
            self._launching = True
            feature = self._installed_feature
        try:
            address = self._launch(feature)
            with self._lock:
                self._desktop_url = address
            return {"url": address}
        finally:
            with self._lock:
                self._launching = False

    def _refresh_desktop(self):
        if self._desktop_process is not None and self._desktop_process.poll() is not None:
            self._desktop_process = None
            self._desktop_url = None

    def _launch_desktop(self, feature):
        if sys.platform != "win32":
            raise OnboardingError("Desktop control requires Windows. Browser practice is available here.")
        from . import bundle
        executable = Path(sys.executable) if bundle.is_bundled(self.root) else install_tools.local_path(self.root, ".venv", "Scripts", "python.exe")
        if not executable.is_file():
            raise OnboardingError("The local Python environment is missing. Run installation again.")
        env = install_tools.safe_environment()
        process = subprocess.Popen([str(executable), "-u", "-m", "jarvis.desktop", "--feature", feature],
                                   cwd=self.root, env=env, shell=False, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with self._lock:
            self._desktop_process = process
        result = Queue(maxsize=1)

        def read_address():
            try:
                for _ in range(8):
                    line = process.stdout.readline(256)
                    if not line:
                        break
                    if line.startswith("Jarvis: http://127.0.0.1:"):
                        address = line.strip().removeprefix("Jarvis: ")
                        port = address.removeprefix("http://127.0.0.1:")
                        if port.isascii() and port.isdecimal() and 1 <= int(port) <= 65535:
                            result.put(address + "/")
                            while process.stdout.read(4096):
                                pass
                            return
            except (OSError, ValueError):
                pass
            finally:
                process.stdout.close()

        threading.Thread(target=read_address, daemon=True).start()
        try:
            return result.get(timeout=45)
        except Empty:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
            raise OnboardingError("The desktop app could not start. Check the selected tools and try again.") from None
