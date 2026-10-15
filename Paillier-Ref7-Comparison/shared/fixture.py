"""Shared public benchmark fixtures; not production key generation."""
import hashlib
import json
import math
import secrets
from pathlib import Path

SCHEMA = "paillier-comparison-v1"
CORE_FIELDS = ("schema", "bits", "n", "n2", "messages", "randomness", "expected")


def fingerprint(data):
    core = {key: data[key] for key in CORE_FIELDS}
    return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("ascii")).hexdigest()


def decimal(value, label):
    if not isinstance(value, str) or not value or not value.isascii() or not value.isdecimal():
        raise ValueError(f"{label}: expected unsigned ASCII decimal string")
    if len(value) > 1 and value.startswith("0"):
        raise ValueError(f"{label}: noncanonical leading zero")
    return int(value)


def validate(data, check_oracle=True):
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError("unsupported fixture schema")
    bits = data.get("bits")
    if type(bits) is not int or not 64 <= bits <= 4096:
        raise ValueError("bits must be an integer in [64, 4096]")
    n = decimal(data["n"], "n")
    n2 = decimal(data["n2"], "n2")
    if n % 2 != 1 or n.bit_length() != bits or n2 != n * n:
        raise ValueError("invalid modulus or bit width")
    arrays = [data.get(key) for key in ("messages", "randomness", "expected")]
    if any(not isinstance(a, list) for a in arrays) or not 1 <= len(arrays[0]) <= 100000:
        raise ValueError("fixture arrays must be nonempty; at most 100000 ciphertexts")
    if len({len(a) for a in arrays}) != 1:
        raise ValueError("fixture arrays have different lengths")
    for i, (message, randomness, expected) in enumerate(zip(*arrays)):
        m, r, c = [decimal(v, f"item[{i}]") for v in (message, randomness, expected)]
        if not 0 <= m < n or not 0 < r < n or math.gcd(r, n) != 1 or not 0 <= c < n2:
            raise ValueError(f"item[{i}]: invalid message, randomness, or ciphertext")
        if check_oracle and c != ((1 + n * m) * pow(r, n, n2)) % n2:
            raise ValueError(f"item[{i}]: reference ciphertext mismatch")
    if data.get("dataset_id") != fingerprint(data):
        raise ValueError("dataset_id does not match canonical fixture contents")
    return data


def load(path, check_oracle=True):
    return validate(json.loads(Path(path).read_text(encoding="utf-8")), check_oracle)


def probably_prime(n, rounds=48):
    """Miller-Rabin for benchmark-only parameters; uses OS randomness."""
    small = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47)
    if n in small:
        return True
    if n < 2 or any(n % p == 0 for p in small):
        return False
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def prime(bits):
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if probably_prime(candidate):
            return candidate


def generate(bits, count, profile="full-domain"):
    if type(bits) is not int or not 64 <= bits <= 4096 or bits % 8:
        raise ValueError("bits must be a multiple of 8 in [64,4096]")
    if type(count) is not int or not 1 <= count <= 100000:
        raise ValueError("count must be in [1,100000]")
    if profile not in ("full-domain", "zkp-slots", "small-integers"):
        raise ValueError("unsupported message profile")
    while True:
        p, q = prime(bits // 2), prime(bits // 2)
        n = p * q
        if p != q and n.bit_length() == bits and math.gcd(math.lcm(p - 1, q - 1), n) == 1:
            break
    messages, randomness, expected = [], [], []
    for i in range(count):
        if profile == "full-domain":
            # Include boundaries alongside random full-width messages.
            m = (0, 1, n - 1)[i] if i < 3 else secrets.randbelow(n)
        elif profile == "zkp-slots":
            # Match EndToEnd.cpp default 4-byte slot layout, without auxiliary ciphertexts.
            m = sum(((i >> j) & 1) << (32 * j) for j in range(bits // 32))
        else:
            m = secrets.randbelow(1 << 16)
        while True:
            r = secrets.randbelow(n - 1) + 1
            if math.gcd(r, n) == 1:
                break
        messages.append(str(m))
        randomness.append(str(r))
        expected.append(str(((1 + n * m) * pow(r, n, n * n)) % (n * n)))
    data = dict(schema=SCHEMA, bits=bits, n=str(n), n2=str(n * n),
                messages=messages, randomness=randomness, expected=expected,
                metadata={"profile": profile, "generator": "OS-random/Miller-Rabin-48",
                          "purpose": "public benchmark inputs, no production secrets",
                          "slot_bytes": 4 if profile == "zkp-slots" else None})
    data["dataset_id"] = fingerprint(data)
    return validate(data)


def write_new(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
