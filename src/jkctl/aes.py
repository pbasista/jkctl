"""AES-256-CBC for the .jkbms / .jsonds containers, with nothing to install.

The Windows app uses AES-256-CBC (IV = all zero) over the container payload.
Decrypting it is the only cryptographic operation the tool performs, and it runs
exactly once per firmware file, on a blob of at most 20 MiB.  So a pure-Python
implementation is fast enough, and shipping one means jkctl imports and
runs on any architecture -- notably aarch64 -- without needing pycrypto (which
has no prebuilt aarch64 wheel and requires a C toolchain to build).

``decrypt_cbc`` prefers a fast native backend when one is available and silently
falls back to the bundled pure-Python cipher otherwise:

    1. PyCryptodome / PyCrypto   (``from Crypto.Cipher import AES``)
    2. cryptography              (OpenSSL, via its Python wheel)
    3. the system libcrypto      (OpenSSL, via stdlib ctypes -- no install)
    4. the pure-Python AES below

Backend 3 is the important one for small/weak/exotic hosts (e.g. a 32-bit
armv7l Raspberry Pi): OpenSSL's ``libcrypto`` is already present on virtually
every system, and ctypes is in the standard library, so it gives C-speed AES
with nothing to install and no compiler -- whereas ``cryptography`` needs Rust
and ``pycryptodome`` a C toolchain when no wheel exists for the architecture.

All backends are byte-for-byte identical; ``backend_name()`` reports which is in
use, and the self-test at the bottom checks the fallbacks against FIPS-197.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
#  Pure-Python AES-256 (decrypt path only -- that is all the tool needs)       #
# --------------------------------------------------------------------------- #

# AES-256 with a 16-byte block: the key is 32 bytes and the schedule works in
# four-byte words.
KEY_BYTES = 32
BLOCK_BYTES = 16
WORD = 4

_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16"
)
_INV_SBOX = bytearray(256)
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i
_INV_SBOX = bytes(_INV_SBOX)

_RCON = (
    0x01,
    0x02,
    0x04,
    0x08,
    0x10,
    0x20,
    0x40,
    0x80,
    0x1B,
    0x36,
    0x6C,
    0xD8,
    0xAB,
    0x4D,
)


def _mul(a: int, b: int) -> int:
    """Multiply two bytes in GF(2^8) with the AES reduction polynomial."""
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _expand_key(key: bytes) -> list[list[int]]:
    """AES-256 key schedule -> 60 four-byte words."""
    if len(key) != KEY_BYTES:
        raise ValueError("AES-256 needs a 32-byte key, got %d" % len(key))
    nk, nr = 8, 14
    w = [list(key[WORD * i : WORD * i + WORD]) for i in range(nk)]
    for i in range(nk, WORD * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]  # RotWord
            t = [_SBOX[b] for b in t]  # SubWord
            t[0] ^= _RCON[i // nk - 1]
        elif i % nk == WORD:
            t = [_SBOX[b] for b in t]  # SubWord (AES-256 only)
        w.append([a ^ b for a, b in zip(w[i - nk], t)])
    return w


# The four inverse-cipher steps, named as FIPS-197 names them.  Each works on
# the 16-byte state in place, column-major, so `s[r + 4 * c]` is row r of
# column c.


def _inv_shift_rows(s: list[int]) -> None:
    """Rotate row r right by r bytes."""
    for r in range(1, 4):
        row = [s[r + 4 * c] for c in range(4)]
        row = row[-r:] + row[:-r]
        for c in range(4):
            s[r + 4 * c] = row[c]


def _inv_sub_bytes(s: list[int]) -> None:
    """Substitute every byte through the inverse S-box."""
    for i in range(16):
        s[i] = _INV_SBOX[s[i]]


def _inv_mix_columns(s: list[int]) -> None:
    """Multiply each column by the inverse MDS matrix, over GF(2^8)."""
    for c in range(4):
        a = [s[4 * c + r] for r in range(4)]
        s[4 * c + 0] = _mul(a[0], 14) ^ _mul(a[1], 11) ^ _mul(a[2], 13) ^ _mul(a[3], 9)
        s[4 * c + 1] = _mul(a[0], 9) ^ _mul(a[1], 14) ^ _mul(a[2], 11) ^ _mul(a[3], 13)
        s[4 * c + 2] = _mul(a[0], 13) ^ _mul(a[1], 9) ^ _mul(a[2], 14) ^ _mul(a[3], 11)
        s[4 * c + 3] = _mul(a[0], 11) ^ _mul(a[1], 13) ^ _mul(a[2], 9) ^ _mul(a[3], 14)


def _add_round_key(s: list[int], w: list[list[int]], rnd: int) -> None:
    """XOR in the four round-key words for round ``rnd``."""
    for c in range(4):
        k = w[rnd * 4 + c]
        for r in range(4):
            s[r + 4 * c] ^= k[r]


def _decrypt_block(block: bytes, w: list[list[int]]) -> bytes:
    """Run the AES-256 inverse cipher over one 16-byte block."""
    nr = 14  # rounds, for a 256-bit key
    s = list(block)
    _add_round_key(s, w, nr)
    for rnd in range(nr - 1, 0, -1):
        _inv_shift_rows(s)
        _inv_sub_bytes(s)
        _add_round_key(s, w, rnd)
        _inv_mix_columns(s)
    # The final round is the same without InvMixColumns.
    _inv_shift_rows(s)
    _inv_sub_bytes(s)
    _add_round_key(s, w, 0)
    return bytes(s)


def _pure_decrypt_cbc(key: bytes, iv: bytes, data: bytes) -> bytes:
    if len(data) % 16:
        raise ValueError("ciphertext not a multiple of 16 bytes")
    w = _expand_key(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        block = data[i : i + 16]
        clear = _decrypt_block(block, w)
        out += bytes(a ^ b for a, b in zip(clear, prev))
        prev = block
    return bytes(out)


# --------------------------------------------------------------------------- #
#  Backend selection                                                           #
# --------------------------------------------------------------------------- #


def _try_crypto(key, iv, data):
    # pycryptodome or pycrypto; an optional backend, so the checker cannot
    # be expected to resolve it.
    from Crypto.Cipher import AES  # ty: ignore[unresolved-import]

    return AES.new(key, AES.MODE_CBC, iv).decrypt(data)


def _try_cryptography(key, iv, data):
    from cryptography.hazmat.primitives.ciphers import (  # ty: ignore[unresolved-import]
        Cipher,
        algorithms,
        modes,
    )

    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return dec.update(data) + dec.finalize()


# -- system libcrypto via ctypes (no install, no compiler) ------------------- #

_LIBCRYPTO = None
_LIBCRYPTO_TRIED = False


def _load_libcrypto():
    """Locate and load OpenSSL's libcrypto, or return None.  Cached."""
    global _LIBCRYPTO, _LIBCRYPTO_TRIED
    if _LIBCRYPTO_TRIED:
        return _LIBCRYPTO
    _LIBCRYPTO_TRIED = True
    import ctypes
    import ctypes.util

    names = []
    found = ctypes.util.find_library("crypto")
    if found:
        names.append(found)
    names += [
        "libcrypto.so.3",
        "libcrypto.so.1.1",
        "libcrypto.so.1.0.0",
        "libcrypto.so",
        "libcrypto.dylib",
        "libcrypto-3.dll",
        "libcrypto-1_1.dll",
        "libeay32.dll",
    ]
    for n in names:
        try:
            lib = ctypes.CDLL(n)
        except OSError:
            continue
        if hasattr(lib, "EVP_DecryptUpdate") and hasattr(lib, "EVP_aes_256_cbc"):
            _LIBCRYPTO = lib
            break
    return _LIBCRYPTO


def _try_openssl(key, iv, data):
    import ctypes as c

    lib = _load_libcrypto()
    if lib is None:
        raise ImportError("libcrypto not available")
    if len(key) != KEY_BYTES:
        raise ValueError("AES-256 needs a 32-byte key, got %d" % len(key))
    if len(data) % 16:
        raise ValueError("ciphertext not a multiple of 16 bytes")

    lib.EVP_CIPHER_CTX_new.restype = c.c_void_p
    lib.EVP_aes_256_cbc.restype = c.c_void_p
    lib.EVP_DecryptInit_ex.argtypes = [
        c.c_void_p,
        c.c_void_p,
        c.c_void_p,
        c.c_char_p,
        c.c_char_p,
    ]
    lib.EVP_DecryptInit_ex.restype = c.c_int
    lib.EVP_CIPHER_CTX_set_padding.argtypes = [c.c_void_p, c.c_int]
    lib.EVP_CIPHER_CTX_set_padding.restype = c.c_int
    lib.EVP_DecryptUpdate.argtypes = [
        c.c_void_p,
        c.c_char_p,
        c.POINTER(c.c_int),
        c.c_char_p,
        c.c_int,
    ]
    lib.EVP_DecryptUpdate.restype = c.c_int
    lib.EVP_DecryptFinal_ex.argtypes = [c.c_void_p, c.c_char_p, c.POINTER(c.c_int)]
    lib.EVP_DecryptFinal_ex.restype = c.c_int
    lib.EVP_CIPHER_CTX_free.argtypes = [c.c_void_p]
    lib.EVP_CIPHER_CTX_free.restype = None

    ctx = lib.EVP_CIPHER_CTX_new()
    if not ctx:
        raise RuntimeError("EVP_CIPHER_CTX_new failed")
    try:
        if lib.EVP_DecryptInit_ex(ctx, lib.EVP_aes_256_cbc(), None, key, iv) != 1:
            raise RuntimeError("EVP_DecryptInit_ex failed")
        lib.EVP_CIPHER_CTX_set_padding(ctx, 0)  # container manages its own length
        out = c.create_string_buffer(len(data) + 16)
        outlen = c.c_int(0)
        if lib.EVP_DecryptUpdate(ctx, out, c.byref(outlen), data, len(data)) != 1:
            raise RuntimeError("EVP_DecryptUpdate failed")
        total = outlen.value
        fin = c.create_string_buffer(16)
        finlen = c.c_int(0)
        if lib.EVP_DecryptFinal_ex(ctx, fin, c.byref(finlen)) != 1:
            raise RuntimeError("EVP_DecryptFinal_ex failed")
        return out.raw[:total] + fin.raw[: finlen.value]
    finally:
        lib.EVP_CIPHER_CTX_free(ctx)


def _mod_available(mod: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:  # noqa: BLE001 - a probe that raises is a backend that is absent
        return False


# (name, availability probe, decrypt fn) in preference order.
_BACKENDS = (
    ("pycryptodome", lambda: _mod_available("Crypto"), _try_crypto),
    ("cryptography", lambda: _mod_available("cryptography"), _try_cryptography),
    ("openssl", lambda: _load_libcrypto() is not None, _try_openssl),
    ("pure-python", lambda: True, _pure_decrypt_cbc),
)


def backend_name() -> str:
    """Name of the backend that decrypt_cbc will use, without decrypting."""
    for name, avail, _fn in _BACKENDS:
        try:
            if avail():
                return name
        except Exception:  # noqa: BLE001 - an unusable backend is not the one to name
            continue
    return "pure-python"


def decrypt_cbc(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-256-CBC decrypt, using the first backend that is available."""
    last = None
    for _name, avail, fn in _BACKENDS:
        try:
            if not avail():
                continue
            return fn(key, iv, data)
        except ImportError:
            continue
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - the next backend gets its turn
            last = exc
            continue
    raise RuntimeError("no AES backend succeeded: %r" % last)


if __name__ == "__main__":
    # FIPS-197 Appendix C.3 known-answer test for AES-256, run against every
    # backend that is available on this host (all must agree with the vector).
    key = bytes.fromhex(
        "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
    )
    ct = bytes.fromhex("8ea2b7ca516745bfeafc49904b496089")
    pt = bytes.fromhex("00112233445566778899aabbccddeeff")
    for name, avail, fn in _BACKENDS:
        try:
            ok = avail()
        except Exception:  # noqa: BLE001 - a self-test reports on backends, it does not need them
            ok = False
        if not ok:
            print("%-12s: not available" % name)
            continue
        got = fn(key, b"\x00" * 16, ct)
        assert got == pt, "%s KAT FAIL: %s" % (name, got.hex())
        print("%-12s: AES-256 KAT OK" % name)
    print("selected backend:", backend_name())
