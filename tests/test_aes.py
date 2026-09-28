"""The AES-256-CBC backends: every one of them must agree with the standard."""

from __future__ import annotations

import pytest

from jkctl import aes

# FIPS-197 appendix C.3: the AES-256 known-answer vector.
KAT_KEY = bytes(range(32))
KAT_PLAIN = bytes.fromhex("00112233445566778899aabbccddeeff")
KAT_CIPHER = bytes.fromhex("8ea2b7ca516745bfeafc49904b496089")

BACKENDS = [name for name, available, _ in aes._BACKENDS if available()]


def test_at_least_the_pure_python_backend_is_always_there():
    assert "pure-python" in BACKENDS
    assert aes.backend_name() in BACKENDS


@pytest.mark.parametrize("name", BACKENDS)
def test_every_available_backend_passes_the_standard_vector(name):
    decrypt = next(fn for n, _, fn in aes._BACKENDS if n == name)
    # With a zero IV, a single-block CBC decryption is the raw block cipher.
    assert decrypt(KAT_KEY, bytes(16), KAT_CIPHER) == KAT_PLAIN


@pytest.mark.parametrize("name", BACKENDS)
def test_every_available_backend_agrees_over_several_blocks(name):
    decrypt = next(fn for n, _, fn in aes._BACKENDS if n == name)
    data = KAT_CIPHER * 8
    reference = aes._pure_decrypt_cbc(KAT_KEY, bytes(16), data)
    assert decrypt(KAT_KEY, bytes(16), data) == reference


def test_the_selector_returns_the_same_bytes_as_the_fallback():
    data = KAT_CIPHER * 4
    assert aes.decrypt_cbc(KAT_KEY, bytes(16), data) == aes._pure_decrypt_cbc(
        KAT_KEY, bytes(16), data
    )


ENC_BACKENDS = [name for name, available, _ in aes._ENC_BACKENDS if available()]


@pytest.mark.parametrize("name", ENC_BACKENDS)
def test_every_encrypt_backend_passes_the_standard_vector(name):
    encrypt = next(fn for n, _, fn in aes._ENC_BACKENDS if n == name)
    # With a zero IV, a single-block CBC encryption is the raw block cipher.
    assert encrypt(KAT_KEY, bytes(16), KAT_PLAIN) == KAT_CIPHER


@pytest.mark.parametrize("name", ENC_BACKENDS)
def test_encrypt_then_decrypt_is_the_identity_over_several_blocks(name):
    encrypt = next(fn for n, _, fn in aes._ENC_BACKENDS if n == name)
    data = KAT_PLAIN * 8
    ct = encrypt(KAT_KEY, bytes(16), data)
    assert ct != data
    assert aes.decrypt_cbc(KAT_KEY, bytes(16), ct) == data


def test_the_encrypt_selector_agrees_with_the_fallback():
    data = KAT_PLAIN * 4
    assert aes.encrypt_cbc(KAT_KEY, bytes(16), data) == aes._pure_encrypt_cbc(
        KAT_KEY, bytes(16), data
    )
