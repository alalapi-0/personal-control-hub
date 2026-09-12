#!/usr/bin/env python3
"""Run configured tools with a Keychain credential; never print credential data."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import os
from pathlib import Path
import re
import sys

INDEX = Path(__file__).resolve().parents[1] / "data/credentials/global_tools.json"


class CredentialError(Exception):
    """Safe, value-free error suitable for CLI output."""


class Keychain:
    """Small bridge to Apple's SecItem API; no subprocess carries secret data."""

    def __init__(self):
        if sys.platform != "darwin":
            raise CredentialError("macOS Keychain is required")
        self.cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        self.sec = ctypes.CDLL(ctypes.util.find_library("Security"))
        # Legacy login-Keychain items can ignore kSecUseAuthenticationUIFail.
        # Disable optional UI for this short-lived process as well.
        self.sec.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_ubyte]
        self.sec.SecKeychainSetUserInteractionAllowed.restype = ctypes.c_int32
        if self.sec.SecKeychainSetUserInteractionAllowed(False):
            raise CredentialError("Cannot disable interactive Keychain access")
        ptr = ctypes.c_void_p
        signatures = {
            "CFStringCreateWithCString": ([ptr, ctypes.c_char_p, ctypes.c_uint32], ptr),
            "CFDataCreate": ([ptr, ptr, ctypes.c_long], ptr),
            "CFDictionaryCreate": ([ptr, ptr, ptr, ctypes.c_long, ptr, ptr], ptr),
            "CFDataGetLength": ([ptr], ctypes.c_long),
            "CFDataGetBytePtr": ([ptr], ptr),
            "CFRelease": ([ptr], None),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.cf, name); fn.argtypes = args; fn.restype = result
        self.sec.SecItemCopyMatching.argtypes = [ptr, ctypes.POINTER(ptr)]
        self.sec.SecItemCopyMatching.restype = ctypes.c_int32
        self.sec.SecItemAdd.argtypes = [ptr, ctypes.POINTER(ptr)]
        self.sec.SecItemAdd.restype = ctypes.c_int32

    def constant(self, name, library=None):
        return ctypes.c_void_p.in_dll(library or self.sec, name).value

    def query(self, service, account, secret=None, read=False):
        # CFType callbacks retain values until the dictionary is released.
        owned = []
        def string(value):
            ref = self.cf.CFStringCreateWithCString(None, value.encode(), 0x08000100)
            if not ref:
                raise CredentialError("Keychain query allocation failed")
            owned.append(ref); return ref
        values = {"kSecClass": self.constant("kSecClassGenericPassword"),
                  "kSecAttrService": string(service), "kSecAttrAccount": string(account)}
        if read:
            values["kSecReturnData"] = self.constant("kCFBooleanTrue", self.cf)
            values["kSecMatchLimit"] = self.constant("kSecMatchLimitOne")
            values["kSecUseAuthenticationUI"] = self.constant("kSecUseAuthenticationUIFail")
        if secret is not None:
            data = self.cf.CFDataCreate(None, secret, len(secret))
            if not data:
                raise CredentialError("Keychain data allocation failed")
            owned.append(data); values["kSecValueData"] = data
        keys = (ctypes.c_void_p * len(values))(*(self.constant(k) for k in values))
        vals = (ctypes.c_void_p * len(values))(*values.values())
        key_callbacks = ctypes.addressof((ctypes.c_byte * 1).in_dll(self.cf, "kCFTypeDictionaryKeyCallBacks"))
        value_callbacks = ctypes.addressof((ctypes.c_byte * 1).in_dll(self.cf, "kCFTypeDictionaryValueCallBacks"))
        query = self.cf.CFDictionaryCreate(None, keys, vals, len(values), key_callbacks, value_callbacks)
        for ref in owned:
            self.cf.CFRelease(ref)
        if not query:
            raise CredentialError("Keychain query allocation failed")
        return query

    def get(self, service, account):
        query = self.query(service, account, read=True)
        result = ctypes.c_void_p()
        try:
            status = self.sec.SecItemCopyMatching(query, ctypes.byref(result))
            if status:
                raise CredentialError("Keychain retrieval failed (OSStatus %d)" % status)
            size = self.cf.CFDataGetLength(result)
            if not 0 < size <= 16384:
                raise CredentialError("Keychain credential is empty or invalid")
            return ctypes.string_at(self.cf.CFDataGetBytePtr(result), size)
        finally:
            if result.value:
                self.cf.CFRelease(result)
            self.cf.CFRelease(query)

    def add(self, service, account, secret):
        """Migration-only API: never overwrites an existing item."""
        query = self.query(service, account, secret=secret)
        try:
            status = self.sec.SecItemAdd(query, None)
            if status:
                raise CredentialError("Keychain insertion failed (OSStatus %d)" % status)
        finally:
            self.cf.CFRelease(query)


def load_entry(credential_id, path=INDEX):
    try:
        index = json.loads(path.read_text())
        entries = index["credentials"]
        matches = [e for e in entries if e["id"] == credential_id]
        if index["schema_version"] != "1.0" or len(matches) != 1:
            raise CredentialError("Unknown or ambiguous credential ID")
        entry = matches[0]
        if entry["auth_kind"] != "macos_keychain":
            raise CredentialError("Use this credential's existing provider route")
        for key in ("service", "account", "runtime_env"):
            if not isinstance(entry[key], str) or not entry[key]:
                raise CredentialError("Invalid credential locator")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN)", entry["runtime_env"]):
            raise CredentialError("Invalid credential environment name")
        return entry
    except (OSError, ValueError, KeyError, TypeError):
        raise CredentialError("Credential index is invalid or unavailable") from None


def credential_environment(entry, source_environment, backend):
    raw = backend.get(entry["service"], entry["account"])
    if not isinstance(raw, bytes) or not raw or not raw.strip() or b"\x00" in raw:
        raise CredentialError("Keychain credential is empty or invalid")
    try:
        value = raw.decode("utf-8")
    except UnicodeError:
        raise CredentialError("Keychain credential encoding is invalid") from None
    env = dict(source_environment)
    env[entry["runtime_env"]] = value
    return env


def main(argv=None, *, backend=None, execute=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "run"])
    parser.add_argument("credential_id")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        if (args.action == "run" and not command) or (args.action == "check" and command):
            raise CredentialError("Specify a command only for run")
        entry = load_entry(args.credential_id)
        env = credential_environment(entry, os.environ, backend or Keychain())
        if args.action == "check":
            print(json.dumps({"credential_id": args.credential_id, "available": True, "value_returned": False}))
        else:
            (execute or os.execvpe)(command[0], command, env)
        return 0
    except CredentialError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("Credential operation or tool launch failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
