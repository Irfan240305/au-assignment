"""PII protection.

- Phones: keyed HMAC-SHA256 of the canonical phone. Same phone -> same hash,
  so events still match leads, but the hash can't be reversed without the key.
  (A plain SHA-256 is NOT enough: there are only ~4e9 valid Indian mobiles,
  so an attacker could hash them all and reverse it in minutes.)
- Other PII (e.g. customer name): Fernet (AES + HMAC) encryption. Not needed
  by any report, but kept recoverable for a future authorised use.

Keys come from environment variables. For local convenience, if they are
missing we read (or create) a git-ignored `.env` file in the working directory.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet

HASH_KEY_VAR = "AU_PII_HASH_KEY"
ENC_KEY_VAR = "AU_PII_ENC_KEY"


@dataclass(frozen=True)
class PiiKeys:
    hash_key: bytes
    enc_key: bytes

    def fingerprint(self) -> str:
        """Non-secret identifier, stored in the DB to detect key changes."""
        return hashlib.sha256(self.hash_key + b"|" + self.enc_key).hexdigest()[:16]


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def load_keys(env_file: Path = Path(".env")) -> PiiKeys:
    file_values = _read_env_file(env_file)
    hash_key = os.environ.get(HASH_KEY_VAR) or file_values.get(HASH_KEY_VAR)
    enc_key = os.environ.get(ENC_KEY_VAR) or file_values.get(ENC_KEY_VAR)

    if not hash_key or not enc_key:
        hash_key = hash_key or secrets.token_hex(32)
        enc_key = enc_key or Fernet.generate_key().decode()
        with env_file.open("a", encoding="utf-8") as handle:
            if HASH_KEY_VAR not in file_values:
                handle.write(f"{HASH_KEY_VAR}={hash_key}\n")
            if ENC_KEY_VAR not in file_values:
                handle.write(f"{ENC_KEY_VAR}={enc_key}\n")
        print(f"[pii] No keys found; generated new ones in {env_file} (git-ignored). "
              "Keep this file: the database can only be read with these keys.")

    return PiiKeys(hash_key=hash_key.encode(), enc_key=enc_key.encode())


class PiiProtector:
    def __init__(self, keys: PiiKeys):
        self._hash_key = keys.hash_key
        self._fernet = Fernet(keys.enc_key)

    def hash_phone(self, canonical_phone: str) -> str:
        return hmac.new(self._hash_key, canonical_phone.encode(), hashlib.sha256).hexdigest()

    def encrypt_json(self, values: dict) -> str:
        return self._fernet.encrypt(json.dumps(values, sort_keys=True).encode()).decode()

    def decrypt_json(self, token: str | None) -> dict:
        if not token:
            return {}
        return json.loads(self._fernet.decrypt(token.encode()))


def mask_phone(last4: str) -> str:
    return "+91******" + last4