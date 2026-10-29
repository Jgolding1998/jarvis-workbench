"""API keys in the current Windows user's local Credential Manager only."""
from __future__ import annotations

import ctypes
import os
from types import MappingProxyType

MAX_KEY_LENGTH = 2048
_PROVIDERS = frozenset({"none", "nvidia", "openai", "anthropic", "ollama"})
_CLOUD = frozenset({"nvidia", "openai", "anthropic"})
_TARGET_PREFIX = "JarvisWorkbench/API/"
_MESSAGES = MappingProxyType({
    "invalid_provider": "Choose a supported AI provider.",
    "not_required": "This provider does not use an API key.",
    "invalid_key": "The API key must contain 1 to 2,048 printable ASCII characters.",
    "unsupported": "Persistent API key storage requires Windows Credential Manager on Windows.",
    "unavailable": "Windows Credential Manager is unavailable. No key was written to a file.",
    "invalid_stored_key": "The saved credential is invalid. Delete it and save your key again.",
})


class CredentialError(ValueError):
    def __init__(self, code="unavailable"):
        self.code = code if isinstance(code, str) and code in _MESSAGES else "unavailable"
        self.message = _MESSAGES[self.code]
        super().__init__(self.message)


def _valid_key(key):
    return (isinstance(key, str) and 1 <= len(key) <= MAX_KEY_LENGTH and bool(key.strip())
            and all(32 <= ord(character) <= 126 for character in key))


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _Credential(ctypes.Structure):
    _fields_ = [("Flags", ctypes.c_uint32), ("Type", ctypes.c_uint32),
                ("TargetName", ctypes.c_wchar_p), ("Comment", ctypes.c_wchar_p),
                ("LastWritten", _FileTime), ("CredentialBlobSize", ctypes.c_uint32),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", ctypes.c_uint32),
                ("AttributeCount", ctypes.c_uint32), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", ctypes.c_wchar_p), ("UserName", ctypes.c_wchar_p)]


_CredentialPointer = ctypes.POINTER(_Credential)


class _WindowsBackend:
    def __init__(self, api=None, last_error=None):
        if api is None:
            if os.name != "nt":
                raise CredentialError("unsupported")
            api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
            api.CredWriteW.argtypes = [ctypes.POINTER(_Credential), ctypes.c_uint32]
            api.CredWriteW.restype = ctypes.c_int32
            api.CredReadW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(_CredentialPointer)]
            api.CredReadW.restype = ctypes.c_int32
            api.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
            api.CredDeleteW.restype = ctypes.c_int32
            api.CredFree.argtypes = [ctypes.c_void_p]
            api.CredFree.restype = None
        self._api = api
        self._last_error = last_error or ctypes.get_last_error

    def read(self, target):
        pointer = _CredentialPointer()
        if not self._api.CredReadW(target, 1, 0, ctypes.byref(pointer)):
            if self._last_error() == 1168:
                return None
            raise CredentialError("unavailable")
        size, blob = 0, None
        try:
            if not pointer:
                raise CredentialError("invalid_stored_key")
            credential = pointer.contents
            size, blob = credential.CredentialBlobSize, credential.CredentialBlob
            if credential.Type != 1 or credential.TargetName != target or not 1 <= size <= MAX_KEY_LENGTH or not blob:
                raise CredentialError("invalid_stored_key")
            try:
                key = ctypes.string_at(blob, size).decode("ascii")
            except UnicodeError:
                raise CredentialError("invalid_stored_key") from None
            if not _valid_key(key):
                raise CredentialError("invalid_stored_key")
            return key
        finally:
            if blob and 1 <= size <= MAX_KEY_LENGTH:
                ctypes.memset(blob, 0, size)
            self._api.CredFree(pointer)

    def save(self, target, key):
        buffer = ctypes.create_string_buffer(key.encode("ascii"))
        credential = _Credential(Type=1, TargetName=target, Comment="Jarvis Workbench API credential",
                                 CredentialBlobSize=len(key), CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
                                 Persist=2, UserName="JarvisWorkbench")
        try:
            if not self._api.CredWriteW(ctypes.byref(credential), 0):
                raise CredentialError("unavailable")
        finally:
            ctypes.memset(buffer, 0, ctypes.sizeof(buffer))

    def delete(self, target):
        if not self._api.CredDeleteW(target, 1, 0) and self._last_error() != 1168:
            raise CredentialError("unavailable")


class WindowsCredentialStore:
    def __init__(self, backend=None):
        self._backend = backend

    def __repr__(self):
        return "WindowsCredentialStore()"

    def _target(self, provider):
        if not isinstance(provider, str) or provider not in _PROVIDERS:
            raise CredentialError("invalid_provider")
        return _TARGET_PREFIX + provider if provider in _CLOUD else None

    def _call(self, operation, target, *args):
        failure, result = None, None
        try:
            if self._backend is None:
                self._backend = _WindowsBackend()
            result = getattr(self._backend, operation)(target, *args)
        except CredentialError as error:
            failure = error.code
        except Exception:
            failure = "unavailable"
        if failure:
            args = ()
            raise CredentialError(failure)
        return result

    def read(self, provider):
        target = self._target(provider)
        if target is None:
            return None
        key = self._call("read", target)
        if key is not None and not _valid_key(key):
            key = None
            raise CredentialError("invalid_stored_key")
        return key

    def save(self, provider, key):
        target = self._target(provider)
        if target is None:
            raise CredentialError("not_required")
        if not _valid_key(key):
            raise CredentialError("invalid_key")
        self._call("save", target, key)

    def delete(self, provider):
        target = self._target(provider)
        if target is not None:
            self._call("delete", target)

    def status(self, provider):
        return self.read(provider) is not None
