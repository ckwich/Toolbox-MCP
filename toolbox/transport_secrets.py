from __future__ import annotations

import base64
import copy
import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


CURRENT_SECRET_ENVELOPE_VERSION = 1
WINDOWS_DPAPI_PROVIDER = "windows-dpapi"
LOCAL_AESGCM_PROVIDER = "local-aesgcm-keyfile"


class TransportSecretUnavailableError(ValueError):
    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider


class TransportSecretManager:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self._current_provider = self._select_current_provider()

    def protect_transport(self, transport: dict[str, Any]) -> dict[str, Any]:
        protected = copy.deepcopy(transport)
        args = [str(item) for item in transport.get("args", [])]
        env = {str(key): str(value) for key, value in transport.get("env", {}).items()}
        if not args and not env:
            return protected

        protected["secret_envelope"] = self._current_provider.protect_bundle({"args": args, "env": env})
        protected["args"] = []
        protected["env"] = {}
        return protected

    def restore_transport(self, transport: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        restored = copy.deepcopy(transport)
        envelope = restored.pop("secret_envelope", None)
        if not isinstance(envelope, dict):
            return restored, False

        provider = self._provider_for_envelope(envelope)
        bundle = provider.reveal_bundle(envelope)
        restored["args"] = bundle["args"]
        restored["env"] = bundle["env"]
        return restored, True

    def needs_migration(self, transport: dict[str, Any]) -> bool:
        if not isinstance(transport, dict):
            return False
        if isinstance(transport.get("secret_envelope"), dict):
            return False
        return bool(transport.get("args") or transport.get("env"))

    def _select_current_provider(self) -> "_BaseTransportSecretProvider":
        if sys.platform == "win32":
            return _WindowsDpapiTransportSecretProvider()
        return _LocalKeyAesGcmTransportSecretProvider(self.state_path.with_suffix(".key"))

    def _provider_for_envelope(self, envelope: dict[str, Any]) -> "_BaseTransportSecretProvider":
        provider_name = envelope.get("provider")
        if provider_name == WINDOWS_DPAPI_PROVIDER:
            if sys.platform != "win32":
                raise TransportSecretUnavailableError(
                    "State file uses Windows DPAPI transport protection and cannot be opened on this platform",
                    provider=WINDOWS_DPAPI_PROVIDER,
                )
            return _WindowsDpapiTransportSecretProvider()
        if provider_name == LOCAL_AESGCM_PROVIDER:
            return _LocalKeyAesGcmTransportSecretProvider(self.state_path.with_suffix(".key"))
        raise TransportSecretUnavailableError(
            f"Unsupported transport secret provider: {provider_name}",
            provider=str(provider_name) if provider_name is not None else None,
        )


class _BaseTransportSecretProvider:
    provider_name: str

    def protect_bundle(self, bundle: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def reveal_bundle(self, envelope: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def _encode_bundle(bundle: dict[str, Any]) -> bytes:
        return json.dumps(_normalize_bundle(bundle), separators=(",", ":"), sort_keys=True).encode("utf-8")

    @staticmethod
    def _decode_bundle(payload: bytes) -> dict[str, Any]:
        decoded = json.loads(payload.decode("utf-8"))
        return _normalize_bundle(decoded)


class _WindowsDpapiTransportSecretProvider(_BaseTransportSecretProvider):
    provider_name = WINDOWS_DPAPI_PROVIDER

    def protect_bundle(self, bundle: dict[str, Any]) -> dict[str, Any]:
        ciphertext = _crypt_protect(self._encode_bundle(bundle))
        return {
            "version": CURRENT_SECRET_ENVELOPE_VERSION,
            "provider": self.provider_name,
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }

    def reveal_bundle(self, envelope: dict[str, Any]) -> dict[str, Any]:
        ciphertext = _base64_field(envelope, "ciphertext")
        plaintext = _crypt_unprotect(ciphertext)
        return self._decode_bundle(plaintext)


class _LocalKeyAesGcmTransportSecretProvider(_BaseTransportSecretProvider):
    provider_name = LOCAL_AESGCM_PROVIDER

    def __init__(self, key_path: Path) -> None:
        self.key_path = key_path

    def protect_bundle(self, bundle: dict[str, Any]) -> dict[str, Any]:
        key = self._load_or_create_key()
        nonce = os.urandom(12)
        ciphertext = AESGCM(key).encrypt(nonce, self._encode_bundle(bundle), None)
        return {
            "version": CURRENT_SECRET_ENVELOPE_VERSION,
            "provider": self.provider_name,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }

    def reveal_bundle(self, envelope: dict[str, Any]) -> dict[str, Any]:
        key = self._load_or_create_key()
        nonce = _base64_field(envelope, "nonce")
        ciphertext = _base64_field(envelope, "ciphertext")
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
        return self._decode_bundle(plaintext)

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            key = self.key_path.read_bytes()
            if len(key) != 32:
                raise ValueError("Invalid Toolbox transport secret key length")
            return key

        key = AESGCM.generate_key(bit_length=256)
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.write_bytes(key)
        try:
            os.chmod(self.key_path, 0o600)
        except PermissionError:
            pass
        return key


def _normalize_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    args = bundle.get("args", [])
    env = bundle.get("env", {})
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ValueError("Transport secret bundle args must be a list of strings")
    if not isinstance(env, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in env.items()):
        raise ValueError("Transport secret bundle env must be a string-to-string mapping")
    return {"args": list(args), "env": dict(env)}


def _base64_field(payload: dict[str, Any], field: str) -> bytes:
    encoded = payload.get(field)
    if not isinstance(encoded, str):
        raise ValueError(f"Transport secret envelope missing field: {field}")
    return base64.b64decode(encoded.encode("ascii"))


if sys.platform == "win32":
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]


def _blob_from_bytes(payload: bytes) -> "DATA_BLOB":
    buffer = ctypes.create_string_buffer(payload)
    return DATA_BLOB(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))


def _bytes_from_blob(blob: "DATA_BLOB") -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _crypt_protect(payload: bytes) -> bytes:
    if sys.platform != "win32":
        raise ValueError("Windows DPAPI protection is only available on Windows")

    input_blob = _blob_from_bytes(payload)
    output_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "Toolbox transport secrets",
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError()

    try:
        return _bytes_from_blob(output_blob)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _crypt_unprotect(payload: bytes) -> bytes:
    if sys.platform != "win32":
        raise ValueError("Windows DPAPI protection is only available on Windows")

    input_blob = _blob_from_bytes(payload)
    output_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError()

    try:
        return _bytes_from_blob(output_blob)
    finally:
        kernel32.LocalFree(output_blob.pbData)
