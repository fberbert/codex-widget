from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

DEFAULT_APPLICATION = "chrome"
DEFAULT_KEYRING_LABEL = "Chrome Safe Storage"
_CHROME_ROOTS = (
    "~/.config/google-chrome",
    "~/.config/google-chrome-beta",
    "~/.config/chromium",
    "~/.config/BraveSoftware/Brave-Browser",
    "~/.config/microsoft-edge",
    "~/.config/vivaldi",
)


class ChromeCookieError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChromeCookie:
    name: str
    value: str
    host_key: str


def load_cookies(
    domain: str,
    *,
    profile: str | None = None,
    application: str = DEFAULT_APPLICATION,
) -> tuple[ChromeCookie, ...]:
    database = _find_cookie_database(domain, profile)
    rows = _read_rows(database, domain)
    if not rows:
        raise ChromeCookieError(
            f"Nenhum cookie de {domain} encontrado em {database}"
        )

    keys = _candidate_keys(application)
    cookies: list[ChromeCookie] = []
    for host_key, name, encrypted in rows:
        value = _decrypt_cookie(encrypted, host_key, keys)
        cookies.append(ChromeCookie(name=name, value=value, host_key=host_key))
    return tuple(cookies)


def cookie_header(cookies: Iterable[ChromeCookie]) -> str:
    return "; ".join(f"{cookie.name}={cookie.value}" for cookie in cookies)


def _cookie_roots() -> tuple[Path, ...]:
    override = os.environ.get("CHROME_USER_DATA_DIR")
    roots = [Path(override).expanduser()] if override else []
    roots.extend(Path(root).expanduser() for root in _CHROME_ROOTS)
    return tuple(root for root in roots if root.is_dir())


def _find_cookie_database(domain: str, profile: str | None) -> Path:
    candidates: list[Path] = []
    for root in _cookie_roots():
        if profile:
            database = root / profile / "Cookies"
            if database.is_file():
                candidates.append(database)
            continue
        candidates.extend(path for path in root.glob("*/Cookies") if path.is_file())

    if not candidates:
        raise ChromeCookieError(
            "Banco de cookies do Chrome não encontrado; abra o Chrome e faça login em "
            f"https://{domain}"
        )

    best: tuple[int, float, Path] | None = None
    for database in candidates:
        try:
            count = _count_rows(database, domain)
        except ChromeCookieError:
            continue
        score = (count, database.stat().st_mtime, database)
        if count and (best is None or (score[0], score[1]) > (best[0], best[1])):
            best = score
    if best is None:
        raise ChromeCookieError(
            f"Nenhum perfil do Chrome tem cookies de {domain}; faça login no console"
        )
    return best[2]


def _count_rows(database: Path, domain: str) -> int:
    hosts = _matching_hosts(domain)
    placeholders = ",".join("?" * len(hosts))
    with _copied_database(database) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM cookies "
            f"WHERE host_key IN ({placeholders}) AND {_not_expired()}",
            (*hosts, _now_chrome_epoch()),
        ).fetchone()
    return int(row[0]) if row else 0


def _read_rows(database: Path, domain: str) -> list[tuple[str, str, bytes]]:
    hosts = _matching_hosts(domain)
    placeholders = ",".join("?" * len(hosts))
    with _copied_database(database) as connection:
        rows = connection.execute(
            "SELECT host_key, name, encrypted_value FROM cookies "
            f"WHERE host_key IN ({placeholders}) AND {_not_expired()} "
            "ORDER BY host_key, name",
            (*hosts, _now_chrome_epoch()),
        ).fetchall()
    return [(str(host), str(name), bytes(value)) for host, name, value in rows if value]


class _copied_database:
    def __init__(self, source: Path) -> None:
        self._source = source
        self._directory: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> sqlite3.Connection:
        self._directory = tempfile.TemporaryDirectory(prefix="codex-widget-cookies-")
        target = Path(self._directory.name) / "Cookies"
        shutil.copy2(self._source, target)
        for suffix in ("-wal", "-shm"):
            sidecar = self._source.with_name(self._source.name + suffix)
            if sidecar.is_file():
                shutil.copy2(sidecar, target.with_name(target.name + suffix))
        self._connection = sqlite3.connect(target)
        return self._connection

    def __exit__(self, *exc_info: object) -> None:
        self._connection.close()
        if self._directory is not None:
            self._directory.cleanup()


def _matching_hosts(domain: str) -> tuple[str, ...]:
    hosts = {domain, f".{domain}"}
    parts = domain.split(".")
    if len(parts) > 2:
        parent = ".".join(parts[-2:])
        hosts.add(parent)
        hosts.add(f".{parent}")
    return tuple(sorted(hosts))


def _not_expired() -> str:
    return "(expires_utc = 0 OR expires_utc > ?)"


def _now_chrome_epoch() -> int:
    return int((time.time() + 11_644_473_600) * 1_000_000)


def _candidate_keys(application: str) -> tuple[bytes, ...]:
    keys: list[bytes] = []
    secret = _keyring_secret(application)
    if secret is not None:
        keys.append(_derive_key(secret))
    keys.append(_derive_key(b"peanuts"))
    return tuple(keys)


def _derive_key(password: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1, 16)


def _keyring_secret(application: str) -> bytes | None:
    try:
        import secretstorage
    except ImportError as exc:
        raise ChromeCookieError(
            "secretstorage não instalado; instale as dependências do projeto"
        ) from exc

    try:
        bus = secretstorage.dbus_init()
        collection = secretstorage.get_default_collection(bus)
        if collection.is_locked():
            collection.unlock()
        items = collection.get_all_items()
    except Exception:
        return None

    for item in items:
        attributes = item.get_attributes()
        if (
            attributes.get("application") == application
            and item.get_label().endswith("Safe Storage")
        ):
            return bytes(item.get_secret())

    fallback = [
        item
        for item in items
        if item.get_label() == DEFAULT_KEYRING_LABEL
    ]
    if fallback:
        return bytes(fallback[0].get_secret())
    return None


def _decrypt_cookie(encrypted: bytes, host_key: str, keys: tuple[bytes, ...]) -> str:
    if encrypted[:3] not in (b"v10", b"v11"):
        raise ChromeCookieError(
            f"Formato de cookie não suportado para {host_key} (prefixo {encrypted[:3]!r})"
        )

    host_hash = hashlib.sha256(host_key.encode("utf-8")).digest()
    plaintexts: list[bytes] = []
    for key in keys:
        try:
            plaintext = _decrypt_cbc(key, encrypted[3:])
        except ValueError:
            continue
        if plaintext.startswith(host_hash) and len(plaintext) > len(host_hash):
            plaintexts.insert(0, plaintext[len(host_hash):])
        else:
            plaintexts.append(plaintext)

    for plaintext in plaintexts:
        try:
            value = plaintext.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1]
        return value

    raise ChromeCookieError(
        f"Não foi possível decifrar os cookies de {host_key}; refaça o login no Chrome"
    )


def _decrypt_cbc(key: bytes, ciphertext: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()
