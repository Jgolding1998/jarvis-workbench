"""Local setup orchestration. Secrets never enter the saved setup file."""

from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from queue import Empty, Full, Queue

from . import install_tools, planner
from .budget import BudgetLedger, BudgetError

CLOUD = frozenset({"nvidia", "openai", "anthropic"})
STARTUP_SECONDS = 45
EOF_SECONDS = 5
CLOSE_JOIN_SECONDS = 10
CLOSING = "Setup is closing. Start Jarvis again to continue."
PROVIDERS = CLOUD | {"none", "ollama"}


class OnboardingError(ValueError):
    pass


class Onboarding:
    def __init__(self, root, *, vault=None, responder=None, installer=None, launch=None, budget=None, read_aloud=True):
        self.root = Path(root)
        # False when this process starts with offline voice commands: spoken replies could be heard as commands.
        self._read_aloud = read_aloud is True
        self._vault = vault
        self._responder = responder
        self._installer = installer or self._install
        self._launch = launch or self._launch_desktop
        self._budget = budget or BudgetLedger(self.root)
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
        self._closed = False
        self._install_cancel = self._install_thread = None
        # Saved-or-not flags only, read lazily and updated by this setup's own save, delete and chat key reads.
        self._saved = {}
        self._credential_lock = threading.Lock()
        self._included = False
        self._checked_feature = self._checked_ready = None
        try:
            path = install_tools.local_path(self.root, "runtime", "quickstart.json")
            if path.is_file() and path.stat().st_size <= 1024 and path.stat().st_nlink == 1:
                from .quickstart import validate_choices
                self._choices = validate_choices(planner.parse_json(path.read_bytes()))
        except (ValueError, OSError, TypeError):
            pass
        if self._choices is not None:
            from .quickstart import recommend
            self._use_bundled(self._feature(recommend(self._choices)))

    def _use_bundled(self, feature):
        """Mark a feature ready when the verified portable bundle already contains it. Never installs or downloads.

        The check (which hashes the hand model) runs once per selected feature, not on state polls or same-feature saves."""
        if feature != self._checked_feature:
            from . import bundle
            try:
                ready = bundle.is_bundled(self.root) and bundle.feature_ready(self.root, feature)
            except (OSError, ValueError):
                ready = False
            self._checked_feature, self._checked_ready = feature, ready
        if self._checked_ready:
            self._installed_feature = feature
            self._install_state = "ready"
            self._install_message = "This Jarvis package already includes the selected tools. Nothing was downloaded or switched on."
            self._included = True

    def _credentials(self):
        if self._vault is None:
            from .credential_store import WindowsCredentialStore
            self._vault = WindowsCredentialStore()
        return self._vault

    def _current_limit(self):
        try:
            path = install_tools.local_path(self.root, "runtime", "quickstart.json")
            if not path.exists():
                return 0
            if not path.is_file() or path.stat().st_nlink != 1:
                raise ValueError()
            with path.open("rb") as handle:
                data = handle.read(1025)
            if len(data) > 1024:
                raise ValueError()
            from .quickstart import validate_choices
            return validate_choices(planner.parse_json(data))["budget"]
        except (ValueError, OSError, TypeError):
            raise OnboardingError("The saved allowance is unavailable. Paid AI is paused until you save valid setup choices.") from None

    def _saved_flags(self):
        """Whether a key is saved, per provider. Never key material. A failed check reports not saved and is retried."""
        with self._credential_lock:
            for provider in sorted(CLOUD - self._saved.keys()):
                try:
                    self._saved[provider] = bool(self._credentials().status(provider))
                except (ValueError, OSError):
                    pass
            return {provider: self._saved.get(provider, False) for provider in sorted(CLOUD)}

    def snapshot(self):
        from .quickstart import recommend
        from .provider_client import catalog
        credentials = self._saved_flags()
        with self._lock:
            limit = self._choices["budget"] if self._choices else 0
            try:
                limit = self._current_limit()
                allowance = self._budget.snapshot(limit)
            except (BudgetError, OnboardingError):
                allowance = {"available": False, "kind": "local_allowance", "limit_usd": limit,
                             "message": "Paid AI is paused because local spending information is unavailable."}
            choices = {**self._choices, "budget": limit} if self._choices else None
            return {"local": True, "choices": choices,
                    "plan": recommend(choices) if choices else None,
                    "credentials": credentials,
                    "install": {"state": self._install_state, "message": self._install_message,
                                "included": self._included and self._install_state == "ready"},
                    "providers": catalog(), "budget": allowance, "read_aloud_allowed": self._read_aloud}

    def save(self, choices):
        from .quickstart import validate_choices, recommend
        choices = validate_choices(choices)
        plan = recommend(choices)
        with self._lock:
            if self._closed:
                raise OnboardingError(CLOSING)
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
                self._installed_feature = None
                self._install_state = "idle"
                self._install_message = "Install the tools for your selected setup."
                self._included = False
                self._use_bundled(feature)
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
        provider = self._provider(provider, cloud_only=True)
        with self._credential_lock:
            self._saved.pop(provider, None)
            self._credentials().save(provider, key)
            self._saved[provider] = True
        return self.snapshot()

    def delete_key(self, provider):
        provider = self._provider(provider, cloud_only=True)
        with self._credential_lock:
            self._saved.pop(provider, None)
            self._credentials().delete(provider)
            self._saved[provider] = False
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
            limit = self._current_limit() if provider in {"openai", "anthropic"} else 0
            if provider in {"openai", "anthropic"} and limit == 0:
                raise OnboardingError("Paid AI is paused at a $0 allowance. Choose a free option or raise your monthly allowance.")
            if provider in CLOUD:
                with self._credential_lock:  # Always a fresh read; it also corrects the saved flag.
                    self._saved.pop(provider, None)
                    key = self._credentials().read(provider)
                    self._saved[provider] = bool(key)
                if not key:
                    raise OnboardingError("Save your own API key for this provider first.")
            if provider in {"openai", "anthropic"}:
                self._budget.reserve(provider, limit)
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
            if self._closed:
                raise OnboardingError(CLOSING)
            self._refresh_desktop()
            if self._choices is None:
                raise OnboardingError("Save your setup choices first.")
            if self._install_state == "running":
                raise OnboardingError("Installation is already running.")
            if self._launching or self._desktop_url is not None:
                raise OnboardingError("Close the desktop app before reinstalling its tools.")
            feature = self._feature(recommend(self._choices))
            cancel = threading.Event()

            def worker():
                try:
                    self._installer(feature)
                    finished = True
                except Exception:
                    finished = False
                with self._lock:
                    if self._closed or cancel.is_set():  # A late finish never reports ready after close().
                        self._install_state = "idle"
                        self._install_message = "Setup closed before installation finished. Existing files were preserved."
                    elif finished:
                        self._installed_feature = feature
                        self._install_state = "ready"
                        self._install_message = "Your desktop tools are ready. Open the dashboard when you are ready."
                    else:
                        self._install_state = "error"
                        self._install_message = "Installation did not finish. Existing files were preserved. Try again or follow the manual installation guide."

            thread = threading.Thread(target=worker, daemon=True, name="jarvis-install")
            self._install_cancel, self._install_thread = cancel, thread
            self._install_state = "running"
            self._included = False
            self._install_message = "Preparing the selected tools. Devices remain off."
            thread.start()
        return self.snapshot()

    def _install(self, feature):
        from . import bundle
        with self._lock:
            cancel = self._install_cancel or threading.Event()
        fetch = functools.partial(install_tools.download_model, cancel=cancel)
        if bundle.is_bundled(self.root):
            if not bundle.feature_ready(self.root, feature):
                raise OnboardingError("The portable package is incomplete. Download a fresh release.")
            if feature == "hand":
                fetch(self.root)
            return
        install_tools.install(self.root, feature, install_tools.selected_packages(self.root, feature), model_fetch=fetch, cancel=cancel)

    def launch(self, consent):
        if consent is not True:
            raise OnboardingError("Confirm opening the local desktop app first.")
        with self._lock:
            if self._closed:
                raise OnboardingError("Setup is closing. Start Jarvis again to open the desktop app.")
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
                if self._closed:
                    raise OnboardingError("Setup is closing. Start Jarvis again to open the desktop app.")
                self._desktop_url = address
            return {"url": address}
        finally:
            with self._lock:
                self._launching = False

    def close(self):
        """Stop this setup's installation and desktop app, if any. Safe to call more than once.

        Later saves, installs and launches are refused. Cancelling the install stops its whole process tree; its
        thread then gets a bounded wait."""
        with self._lock:
            self._closed = True
            process, self._desktop_process, self._desktop_url = self._desktop_process, None, None
            cancel, worker = self._install_cancel, self._install_thread
        if cancel is not None:
            cancel.set()
        if process is not None:
            self._stop(process)
        if worker is not None and worker is not threading.current_thread():
            worker.join(CLOSE_JOIN_SECONDS)

    @staticmethod
    def _stop(process, grace=None):
        """Ask only our own child to exit by closing its parent-watch pipe, then terminate, kill and reap it."""
        grace = EOF_SECONDS if grace is None else grace
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        for stop, wait in ((None, grace), (process.terminate, 3), (process.kill, 3)):
            try:
                if stop is not None:
                    stop()
                process.wait(timeout=wait)
                return
            except subprocess.TimeoutExpired:
                continue
            except OSError:
                if process.poll() is not None:
                    return

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
        with self._lock:
            if self._closed:
                raise OnboardingError("Setup is closing. Start Jarvis again to open the desktop app.")
            # The child exits when this pipe reaches end of file: on close() and also when this process dies.
            process = subprocess.Popen([str(executable), "-u", "-m", "jarvis.desktop", "--feature", feature, "--parent-watch"],
                                       cwd=self.root, env=env, shell=False, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
                try:
                    result.put_nowait(None)  # No address: fail now instead of waiting for the startup limit.
                except Full:
                    pass

        threading.Thread(target=read_address, daemon=True).start()
        try:
            address = result.get(timeout=STARTUP_SECONDS)
        except Empty:
            address = None
        with self._lock:
            owned = self._desktop_process is process
            if address is None or not owned or self._closed:
                if owned:
                    self._desktop_process = None
                address = None
        if address is None:
            if owned:  # Otherwise close() already took this child and is stopping it.
                self._stop(process, grace=0)
            raise OnboardingError("The desktop app could not start. Check the selected tools and try again.")
        return address
