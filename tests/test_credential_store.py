import ctypes
import json
import traceback
import unittest
from unittest.mock import Mock, patch

from jarvis import credential_store as vault


KEY = "synthetic-vault-token-691428"


class FakeWindowsAPI:
    def __init__(self):
        self.records, self.allocations, self.freed_blobs = {}, [], []
        self.error, self.write_metadata, self.calls = 0, None, []

    def CredWriteW(self, pointer, flags):
        credential = ctypes.cast(pointer, ctypes.POINTER(vault._Credential)).contents
        self.calls.append(("write", credential.TargetName, flags))
        self.write_metadata = (credential.Type, credential.Persist, credential.CredentialBlobSize, credential.UserName)
        self.records[credential.TargetName] = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return 1

    def CredReadW(self, target, kind, flags, output):
        self.calls.append(("read", target, kind, flags))
        if target not in self.records:
            self.error = 1168
            return 0
        data = self.records[target]
        buffer = ctypes.create_string_buffer(data)
        credential = vault._Credential(Type=1, TargetName=target, CredentialBlobSize=len(data),
                                       CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        pointer = ctypes.pointer(credential)
        self.allocations.append((buffer, credential, pointer))
        ctypes.cast(output, ctypes.POINTER(vault._CredentialPointer))[0] = pointer
        return 1

    def CredDeleteW(self, target, kind, flags):
        self.calls.append(("delete", target, kind, flags))
        if target not in self.records:
            self.error = 1168
            return 0
        del self.records[target]
        return 1

    def CredFree(self, pointer):
        self.calls.append(("free",))
        if pointer:
            credential = pointer.contents
            self.freed_blobs.append(ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize))
        self.allocations.clear()


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeWindowsAPI()
        self.backend = vault._WindowsBackend(self.api, lambda: self.api.error)
        self.store = vault.WindowsCredentialStore(self.backend)

    def test_native_contract_roundtrip_status_and_idempotent_delete(self):
        self.assertIs(self.store.status("openai"), False)
        self.store.save("openai", KEY)
        self.assertEqual(self.api.write_metadata, (1, 2, len(KEY), "JarvisWorkbench"))
        self.assertIs(self.store.status("openai"), True)
        self.assertEqual(self.store.read("openai"), KEY)
        self.assertTrue(all(blob == b"\x00" * len(KEY) for blob in self.api.freed_blobs))
        self.store.delete("openai")
        self.store.delete("openai")
        self.assertIsNone(self.store.read("openai"))
        self.assertIs(self.store.status("openai"), False)
        self.assertFalse(self.api.allocations)

    def test_native_credential_layout_uses_fixed_width_windows_fields(self):
        self.assertEqual(ctypes.sizeof(vault._FileTime), 8)
        self.assertEqual(ctypes.sizeof(vault._Credential), 80 if ctypes.sizeof(ctypes.c_void_p) == 8 else 52)

    def test_provider_targets_are_fixed_separate_and_use_generic_current_user_credentials(self):
        for provider in ("nvidia", "openai", "anthropic"):
            self.store.save(provider, KEY + provider)
        self.assertEqual(set(self.api.records), {"JarvisWorkbench/API/nvidia", "JarvisWorkbench/API/openai", "JarvisWorkbench/API/anthropic"})
        self.store.delete("openai")
        self.assertEqual(self.store.read("anthropic"), KEY + "anthropic")
        self.assertEqual(self.store.read("nvidia"), KEY + "nvidia")
        for call in self.api.calls:
            if call[0] in {"read", "delete"}:
                self.assertEqual(call[2:], (1, 0))

    def test_none_and_ollama_never_access_the_windows_backend(self):
        backend = Mock()
        store = vault.WindowsCredentialStore(backend)
        for provider in ("none", "ollama"):
            self.assertFalse(store.status(provider))
            self.assertIsNone(store.read(provider))
            store.delete(provider)
            with self.assertRaises(vault.CredentialError) as error:
                store.save(provider, KEY)
            self.assertEqual(error.exception.code, "not_required")
        self.assertEqual(backend.mock_calls, [])

    def test_non_windows_persistence_is_explicitly_unsupported_without_fallback(self):
        with patch.object(vault.os, "name", "posix"), patch("builtins.open", side_effect=AssertionError("No file fallback")):
            store = vault.WindowsCredentialStore()
            for operation, args in [("read", ("openai",)), ("status", ("openai",)), ("save", ("openai", KEY)), ("delete", ("openai",))]:
                with self.subTest(operation=operation), self.assertRaises(vault.CredentialError) as error:
                    getattr(store, operation)(*args)
                self.assertEqual(error.exception.code, "unsupported")

    def test_invalid_provider_cannot_read_an_unrelated_windows_credential(self):
        for provider in (None, [], {}, "OPENAI", "../../private", "openai\x00another", "CustomService", KEY):
            for operation, extra in [("read", ()), ("save", (KEY,)), ("delete", ()), ("status", ())]:
                with self.subTest(operation=operation), self.assertRaises(vault.CredentialError) as error:
                    getattr(self.store, operation)(provider, *extra)
                self.assertEqual(error.exception.code, "invalid_provider")
        self.assertEqual(self.api.calls, [])

    def test_key_validation_prevents_headers_controls_unicode_and_oversized_blobs(self):
        for key in ("", " ", "\t", "\x00", KEY + "\r", KEY + "\n", KEY + "\x7f", "é", "\ud800", "a" * 2049, None, 123, {}):
            with self.subTest(kind=type(key).__name__), self.assertRaises(vault.CredentialError) as error:
                self.store.save("openai", key)
            self.assertEqual(error.exception.code, "invalid_key")
        self.assertEqual(self.api.calls, [])
        self.store.save("openai", "a" * 2048)
        self.assertEqual(len(self.store.read("openai")), 2048)

    def test_write_buffer_is_wiped_on_success_and_failure(self):
        original = ctypes.memset
        for succeeds in (True, False):
            with patch.object(self.api, "CredWriteW", return_value=int(succeeds)), patch.object(vault.ctypes, "memset", wraps=original) as wipe:
                if succeeds:
                    self.store.save("openai", KEY)
                else:
                    with self.assertRaises(vault.CredentialError):
                        self.store.save("openai", KEY)
                self.assertEqual(wipe.call_args.args[1:], (0, len(KEY) + 1))

    def test_corrupt_saved_keys_are_rejected_and_read_allocations_freed(self):
        for data in (b"", b"a" * 2049, b"\xff", b"key\ninvalid", b" "):
            self.api.records["JarvisWorkbench/API/openai"] = data
            with self.subTest(size=len(data)), self.assertRaises(vault.CredentialError) as error:
                self.store.read("openai")
            self.assertEqual(error.exception.code, "invalid_stored_key")
            self.assertFalse(self.api.allocations)
        self.assertEqual(sum(call[0] == "free" for call in self.api.calls), 5)

    def test_untrusted_backend_failures_have_no_secret_path_or_exception_context(self):
        backend = Mock()
        for method in (backend.read, backend.save, backend.delete):
            method.side_effect = OSError("private/path/" + KEY)
        store = vault.WindowsCredentialStore(backend)
        for operation, extra in [("read", ()), ("save", (KEY,)), ("delete", ()), ("status", ())]:
            with self.subTest(operation=operation), self.assertRaises(vault.CredentialError) as error:
                getattr(store, operation)("openai", *extra)
            self.assertEqual(error.exception.code, "unavailable")
            self.assertIsNone(error.exception.__context__)
            self.assertNotIn(KEY, "".join(traceback.format_exception(error.exception)))
            self.assertNotIn("private/path", repr(error.exception))

    def test_invalid_injected_backend_values_are_not_exposed(self):
        for value in (b"not-a-string", 123, {}, KEY + "\n"):
            backend = Mock()
            backend.read.return_value = value
            with self.subTest(kind=type(value).__name__), self.assertRaises(vault.CredentialError) as error:
                vault.WindowsCredentialStore(backend).read("openai")
            self.assertEqual(error.exception.code, "invalid_stored_key")
            self.assertNotIn(KEY, str(error.exception))

    def test_store_does_not_cache_keys_or_write_files_environment_or_logs(self):
        with patch("builtins.open", side_effect=AssertionError("No file storage")), patch.dict(vault.os.environ, {"OPENAI_API_KEY": "ignored-environment-value"}):
            self.store.save("openai", KEY)
            self.assertTrue(self.store.status("openai"))
            self.assertEqual(self.store.read("openai"), KEY)
            self.store.delete("openai")
        self.assertEqual(set(vars(self.store)), {"_backend"})
        self.assertNotIn(KEY, repr(self.store))
        self.assertNotIn(KEY, json.dumps(vars(vault.CredentialError(KEY))))


if __name__ == "__main__":
    unittest.main()
