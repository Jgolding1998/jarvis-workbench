"""Bounded text replies from fixed providers. No desktop tools or credentials cache."""
from __future__ import annotations

import http.client
import ipaddress
import json
import math
import queue
import socket
import ssl
import threading
import time
from types import MappingProxyType
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request

MAX_PROMPT_CHARS = 4000
MAX_PROMPT_BYTES = 16 * 1024
MAX_OUTPUT_CHARS = 16000
MAX_OUTPUT_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_OUTPUT_TOKENS = 1024
REQUEST_TIMEOUT = 45.0
DNS_TIMEOUT = 3.0
_DNS_SLOT = threading.BoundedSemaphore(1)
_IPV6_PUBLIC = ipaddress.ip_network("2000::/3")
_IPV6_SPECIAL = tuple(ipaddress.ip_network(value) for value in ("2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20"))

PROVIDERS = MappingProxyType({
    "none": MappingProxyType({"id": "none", "name": "No AI", "model": None, "setup_url": None,
        "requires_key": False, "remote": False, "tradeoffs": "Use the local dashboard without AI chat."}),
    "nvidia": MappingProxyType({"id": "nvidia", "name": "NVIDIA API", "model": "meta/llama-3.3-70b-instruct",
        "setup_url": "https://build.nvidia.com/meta/llama-3_3-70b-instruct", "requires_key": True, "remote": True,
        "tradeoffs": "Sends chat text to NVIDIA. Your account must have access and available API quota."}),
    "openai": MappingProxyType({"id": "openai", "name": "OpenAI API", "model": "gpt-6-luna",
        "setup_url": "https://platform.openai.com/api-keys", "requires_key": True, "remote": True,
        "tradeoffs": "Sends chat text to OpenAI with response storage disabled. API billing is separate from subscriptions."}),
    "anthropic": MappingProxyType({"id": "anthropic", "name": "Claude API", "model": "claude-haiku-5-5",
        "setup_url": "https://platform.claude.com/settings/keys", "requires_key": True, "remote": True,
        "tradeoffs": "Sends chat text to Anthropic. API billing and access are separate from chat subscriptions."}),
    "ollama": MappingProxyType({"id": "ollama", "name": "Local Ollama", "model": "gemma3:4b",
        "setup_url": "https://ollama.com/download", "requires_key": False, "remote": False,
        "tradeoffs": "Uses local hardware and the separately installed gemma3:4b model. No API key is sent."}),
})
_ENDPOINTS = MappingProxyType({
    "nvidia": "https://integrate.api.nvidia.com/v1/chat/completions",
    "openai": "https://api.openai.com/v1/responses",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "ollama": "http://127.0.0.1:11434/api/chat",
})
_MESSAGES = MappingProxyType({
    "invalid_provider": "Choose a supported AI provider.",
    "disabled": "AI chat is disabled. Choose a provider to send a message.",
    "invalid_prompt": "Enter nonempty text within the 4,000 character chat limit.",
    "missing_key": "Save your own API key for this provider first.",
    "invalid_key": "The API key must contain 1 to 2,048 printable ASCII characters.",
    "auth": "The provider rejected this API key. Check your provider account and key.",
    "quota": "The provider rejected the request because of rate or account limits. No retry was made.",
    "configuration": "The provider rejected the selected model or request. Check your account access.",
    "redirect": "The provider returned a redirect. Redirects are disabled.",
    "busy": "A provider DNS lookup is already running. Try again later.",
    "timeout": "The provider request exceeded its time limit. No retry was made.",
    "invalid_response": "The provider returned an unsupported or oversized text response.",
    "unavailable": "The provider request failed. No request or provider response details were logged.",
})


class ProviderError(ValueError):
    def __init__(self, code="unavailable"):
        self.code = code if isinstance(code, str) and code in _MESSAGES else "unavailable"
        self.message = _MESSAGES[self.code]
        super().__init__(self.message)


def catalog():
    return [dict(value) for value in PROVIDERS.values()]


provider_catalog = catalog


def _valid_key(key):
    return (isinstance(key, str) and 1 <= len(key) <= 2048 and bool(key.strip())
            and all(32 <= ord(character) <= 126 for character in key))


def _resolve(host, port, timeout):
    if not _DNS_SLOT.acquire(blocking=False):
        raise ProviderError("busy")
    channel = queue.Queue(maxsize=1)

    def resolve():
        try:
            channel.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except Exception:
            channel.put(None)
        finally:
            _DNS_SLOT.release()

    try:
        threading.Thread(target=resolve, daemon=True).start()
    except RuntimeError:
        _DNS_SLOT.release()
        raise ProviderError("unavailable") from None
    try:
        records = channel.get(timeout=timeout)
    except queue.Empty:
        raise ProviderError("timeout") from None
    if not records or len(records) > 64:
        raise ProviderError("unavailable")
    for family, _, _, _, address in records:
        if family not in (socket.AF_INET, socket.AF_INET6):
            raise ProviderError("unavailable")
        parsed = ipaddress.ip_address(address[0])
        special = isinstance(parsed, ipaddress.IPv6Address) and (parsed.is_site_local or parsed not in _IPV6_PUBLIC or any(parsed in network for network in _IPV6_SPECIAL))
        if not parsed.is_global or parsed.is_multicast or parsed.is_reserved or special:
            raise ProviderError("unavailable")
    return records[0][0], records[0][4]


class _Connection(http.client.HTTPConnection):
    def __init__(self, host, port, address, family, tls, deadline):
        super().__init__(host, port=port, timeout=REQUEST_TIMEOUT)
        self.address, self.family, self.tls, self.deadline = address, family, tls, deadline
        self.cancelled = threading.Event()
        self.live_socket = None

    def connect(self):
        left = self.deadline - time.monotonic()
        if left <= 0 or self.cancelled.is_set():
            raise TimeoutError()
        raw = socket.socket(self.family, socket.SOCK_STREAM)
        self.sock = self.live_socket = raw
        try:
            raw.settimeout(left)
            raw.connect(self.address)
            if self.tls:
                left = self.deadline - time.monotonic()
                if left <= 0 or self.cancelled.is_set():
                    raise TimeoutError()
                raw.settimeout(left)
                self.sock = self.live_socket = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host)
            if self.cancelled.is_set():
                raise TimeoutError()
        except BaseException:
            raw.close()
            self.abort()
            raise

    def abort(self):
        self.cancelled.set()
        if self.live_socket is not None:
            try:
                self.live_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.live_socket.close()
        self.close()
        self._buffer.clear()


class _Reply:
    def __init__(self, response, connection, timer, url):
        self._response, self._connection, self._timer, self._url = response, connection, timer, url
        self.status, self.headers = response.status, response.headers

    def geturl(self):
        return self._url

    def read1(self, amount):
        left = self._connection.deadline - time.monotonic()
        if left <= 0 or self._connection.cancelled.is_set():
            raise TimeoutError()
        self._connection.live_socket.settimeout(left)
        chunk = self._response.read1(amount)
        if self._connection.cancelled.is_set() or time.monotonic() >= self._connection.deadline:
            raise TimeoutError()
        return chunk

    def close(self):
        self._timer.cancel()
        self._connection.abort()
        self._response.close()


class _DirectOpener:
    def open(self, request, timeout):
        if request.full_url not in _ENDPOINTS.values() or request.get_method() != "POST":
            raise ProviderError("invalid_provider")
        parts = urlsplit(request.full_url)
        deadline = time.monotonic() + timeout
        port = parts.port or 443
        if parts.scheme == "http":
            family, address = socket.AF_INET, ("127.0.0.1", 11434)
        else:
            family, address = _resolve(parts.hostname, port, min(DNS_TIMEOUT, timeout))
        connection = _Connection(parts.hostname, port, address, family, parts.scheme == "https", deadline)
        expired = threading.Event()

        def expire():
            expired.set()
            connection.abort()

        timer = threading.Timer(max(0, deadline - time.monotonic()), expire)
        timer.daemon = True
        timer.start()
        try:
            connection.request("POST", parts.path, body=request.data, headers=dict(request.header_items()))
            response = connection.getresponse()
            if expired.is_set() or time.monotonic() >= deadline:
                response.close()
                raise TimeoutError()
            return _Reply(response, connection, timer, request.full_url)
        except BaseException:
            timer.cancel()
            connection.abort()
            if expired.is_set() or time.monotonic() >= deadline:
                raise TimeoutError() from None
            raise


def _json(body):
    text = body.decode("utf-8")
    level, quoted, escaped = 0, False, False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            level += 1
            if level > 16:
                raise ProviderError("invalid_response")
        elif character in "]}":
            level -= 1

    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ProviderError("invalid_response")
            result[key] = value
        return result

    def constant(_):
        raise ProviderError("invalid_response")

    value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    pending, nodes = [value], 0
    while pending:
        item = pending.pop()
        nodes += 1
        if nodes > 5000:
            raise ProviderError("invalid_response")
        if isinstance(item, dict):
            if len(item) > 256:
                raise ProviderError("invalid_response")
            pending.extend(item.values())
        elif isinstance(item, list):
            if len(item) > 256:
                raise ProviderError("invalid_response")
            pending.extend(item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ProviderError("invalid_response")
    if not isinstance(value, dict):
        raise ProviderError("invalid_response")
    return value


def _status_error(status):
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "quota"
    if status in (400, 404, 422):
        return "configuration"
    if type(status) is int and 300 <= status <= 399:
        return "redirect"
    return "unavailable"


def _text(provider, value):
    if provider == "openai":
        parts = [part["text"] for item in value.get("output", []) if isinstance(item, dict) and item.get("type") == "message" and item.get("role") == "assistant"
                 for part in item.get("content", []) if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str)]
        return "\n".join(parts)
    if provider == "anthropic":
        if value.get("role") != "assistant":
            raise ProviderError("invalid_response")
        return "\n".join(part["text"] for part in value.get("content", []) if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str))
    if provider == "nvidia":
        choices = value.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError("invalid_response")
        message = choices[0].get("message")
    else:
        if value.get("done") is not True:
            raise ProviderError("invalid_response")
        message = value.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant" or not isinstance(message.get("content"), str):
        raise ProviderError("invalid_response")
    return message["content"]


def respond(provider, prompt, key=None, opener=None):
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ProviderError("invalid_provider")
    if provider == "none":
        raise ProviderError("disabled")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
        raise ProviderError("invalid_prompt")
    try:
        valid_prompt = len(prompt.encode("utf-8")) <= MAX_PROMPT_BYTES
    except UnicodeError:
        valid_prompt = False
    if not valid_prompt:
        raise ProviderError("invalid_prompt")
    if PROVIDERS[provider]["requires_key"]:
        if key is None:
            raise ProviderError("missing_key")
        if not _valid_key(key):
            raise ProviderError("invalid_key")
    else:
        key = None
    if key:
        prompt = prompt.replace(key, "[redacted]")
        if len(prompt) > MAX_PROMPT_CHARS or len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise ProviderError("invalid_prompt")
    model = PROVIDERS[provider]["model"]
    messages = [{"role": "user", "content": prompt}]
    headers = {"Content-Type": "application/json", "Accept": "application/json", "Accept-Encoding": "identity", "Connection": "close", "User-Agent": "JarvisWorkbench/1.0"}
    if provider == "openai":
        payload = {"model": model, "input": prompt, "store": False, "max_output_tokens": MAX_OUTPUT_TOKENS, "reasoning": {"effort": "none"}}
    elif provider == "anthropic":
        payload = {"model": model, "messages": messages, "max_tokens": MAX_OUTPUT_TOKENS, "stream": False}
    elif provider == "nvidia":
        payload = {"model": model, "messages": messages, "max_tokens": MAX_OUTPUT_TOKENS, "stream": False}
    else:
        payload = {"model": model, "messages": messages, "stream": False, "options": {"num_predict": MAX_OUTPUT_TOKENS}}
    if provider == "anthropic":
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    elif key:
        headers["Authorization"] = "Bearer " + key
    request = Request(_ENDPOINTS[provider], data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    response, failure, text = None, None, None
    deadline = time.monotonic() + REQUEST_TIMEOUT
    try:
        response = (opener or _DirectOpener()).open(request, timeout=REQUEST_TIMEOUT)
        if time.monotonic() >= deadline:
            raise ProviderError("timeout")
        if response.status != 200:
            raise ProviderError(_status_error(response.status))
        if response.geturl() != _ENDPOINTS[provider]:
            raise ProviderError("redirect")
        if len(response.headers) > 64 or sum(len(k) + len(v) for k, v in response.headers.items()) > 16384:
            raise ProviderError("invalid_response")
        if response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json" or response.headers.get("Content-Encoding", "identity").strip().lower() != "identity":
            raise ProviderError("invalid_response")
        declared = response.headers.get("Content-Length")
        if declared is not None and (not declared.isascii() or not declared.isdecimal() or len(declared) > 9 or int(declared) > MAX_RESPONSE_BYTES):
            raise ProviderError("invalid_response")
        chunks, count = [], 0
        while True:
            if time.monotonic() >= deadline:
                raise ProviderError("timeout")
            read = getattr(response, "read1", None) or response.read
            chunk = read(min(8192, MAX_RESPONSE_BYTES + 1 - count))
            if not isinstance(chunk, bytes):
                raise ProviderError("invalid_response")
            if not chunk:
                break
            count += len(chunk)
            if count > MAX_RESPONSE_BYTES:
                raise ProviderError("invalid_response")
            chunks.append(chunk)
        if declared is not None and count != int(declared):
            raise ProviderError("invalid_response")
        text = _text(provider, _json(b"".join(chunks)))
        if key:
            text = text.replace(key, "[redacted]")
        if not text.strip() or len(text) > MAX_OUTPUT_CHARS or len(text.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise ProviderError("invalid_response")
    except ProviderError as error:
        failure = error.code
    except HTTPError as error:
        failure = _status_error(error.code)
        try:
            error.close()
        except Exception:
            pass
    except (TimeoutError, socket.timeout):
        failure = "timeout"
    except (UnicodeError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        failure = "invalid_response"
    except Exception:
        failure = "unavailable"
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        response = None
        request.headers.clear()
        headers.clear()
        key = None
    if failure:
        chunks, chunk, read = [], None, None
        prompt = payload = messages = opener = text = None
        raise ProviderError(failure)
    return text
