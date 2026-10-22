import hashlib
import unittest

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from codex_widget.chrome_cookies import (
    ChromeCookie,
    ChromeCookieError,
    _decrypt_cookie,
    _derive_key,
    _matching_hosts,
    cookie_header,
)

HOST = "platform.xiaomimimo.com"
KEY = _derive_key(b"test-secret")


def _encrypt(plaintext: bytes, key: bytes = KEY) -> bytes:
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()
    return b"v10" + encryptor.update(padded) + encryptor.finalize()


class DecryptCookieTests(unittest.TestCase):
    def test_decrypts_domain_bound_cookie(self) -> None:
        plaintext = hashlib.sha256(HOST.encode()).digest() + b"session-token"

        value = _decrypt_cookie(_encrypt(plaintext), HOST, (KEY,))

        self.assertEqual(value, "session-token")

    def test_decrypts_legacy_cookie_without_domain_prefix(self) -> None:
        value = _decrypt_cookie(_encrypt(b"legacy-value"), HOST, (KEY,))

        self.assertEqual(value, "legacy-value")

    def test_strips_surrounding_quotes(self) -> None:
        plaintext = hashlib.sha256(HOST.encode()).digest() + b'"quoted-value"'

        value = _decrypt_cookie(_encrypt(plaintext), HOST, (KEY,))

        self.assertEqual(value, "quoted-value")

    def test_tries_multiple_keys(self) -> None:
        wrong = _derive_key(b"wrong")
        plaintext = hashlib.sha256(HOST.encode()).digest() + b"token"

        value = _decrypt_cookie(_encrypt(plaintext), HOST, (wrong, KEY))

        self.assertEqual(value, "token")

    def test_wrong_key_raises(self) -> None:
        plaintext = hashlib.sha256(HOST.encode()).digest() + b"token"

        with self.assertRaises(ChromeCookieError):
            _decrypt_cookie(_encrypt(plaintext), HOST, (_derive_key(b"nope"),))

    def test_unknown_prefix_raises(self) -> None:
        with self.assertRaises(ChromeCookieError):
            _decrypt_cookie(b"v99" + b"\x00" * 16, HOST, (KEY,))


class CookieHeaderTests(unittest.TestCase):
    def test_joins_cookies(self) -> None:
        cookies = (
            ChromeCookie(name="a", value="1", host_key=HOST),
            ChromeCookie(name="b", value="2", host_key=HOST),
        )

        self.assertEqual(cookie_header(cookies), "a=1; b=2")


class MatchingHostsTests(unittest.TestCase):
    def test_includes_exact_parent_and_dotted_hosts(self) -> None:
        hosts = _matching_hosts("platform.xiaomimimo.com")

        self.assertIn("platform.xiaomimimo.com", hosts)
        self.assertIn(".platform.xiaomimimo.com", hosts)
        self.assertIn("xiaomimimo.com", hosts)
        self.assertIn(".xiaomimimo.com", hosts)

    def test_two_label_domain_has_no_tld_parent(self) -> None:
        self.assertEqual(
            _matching_hosts("xiaomimimo.com"),
            (".xiaomimimo.com", "xiaomimimo.com"),
        )


if __name__ == "__main__":
    unittest.main()
