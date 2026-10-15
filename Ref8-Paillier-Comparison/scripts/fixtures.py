"""Shared synthetic Paillier operands. Uses only the Python standard library."""
from pathlib import Path
import argparse
import hashlib
import json
import math
import random
import secrets

def probable_prime(n, rng, rounds=48):
    if n < 2:
        return False
    for p in (2,3,5,7,11,13,17,19,23,29,31,37,41,43,47):
        if n == p:
            return True
        if n % p == 0:
            return False
    d, s = n-1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        a = rng.randrange(2,n-1)
        x = pow(a,d,n)
        if x in (1,n-1):
            continue
        for _ in range(s-1):
            x = x*x % n
            if x == n-1:
                break
        else:
            return False
    return True

def prime(bits, rng):
    while True:
        candidate = rng.getrandbits(bits) | (1 << (bits-1)) | 1
        if probable_prime(candidate,rng):
            return candidate

def make_case(bits, count, dataset="structured", slot_bytes=4, seed=None):
    if bits < 64 or bits % 16 or bits > 4096:
        raise ValueError("bits must be a multiple of 16 between 64 and 4096")
    if count < 1 or count > 1000000 or slot_bytes < 1:
        raise ValueError("invalid count or slot size")
    if dataset not in ("structured","uniform","edges"):
        raise ValueError("unknown dataset")
    rng = random.Random(seed) if seed is not None else secrets.SystemRandom()
    while True:
        p,q = prime(bits//2,rng),prime(bits//2,rng)
        n=p*q
        phi=(p-1)*(q-1)
        if p != q and math.gcd(phi,n)==1:
            break
    slots=((n.bit_length()+7)//8)//slot_bytes
    if slots < 1:
        raise ValueError("slot_bytes exceeds modulus byte length")
    pairs=[]
    for i in range(count):
        if dataset == "structured":
            # EndToEnd.cpp: i mod 2^slots, binary digits packed into slot_bytes.
            m=0
            for digit in bin(i % (1 << slots))[2:]:
                m=(m << (8*slot_bytes))+int(digit)
        elif dataset == "uniform":
            m=rng.randrange(n)
        else:
            m=(0,1,n-1,n//2)[i % 4]
        if not 0 <= m < n:
            raise ValueError("structured message does not fit modulus")
        while True:
            r=rng.randrange(1,n)
            if math.gcd(r,n)==1:
                break
        c=encrypt(n,m,r)
        # Algebraic check with the generated secret factors, outside performance runs.
        decoded=((pow(c,phi,n*n)-1)//n * pow(phi,-1,n)) % n
        if decoded != m:
            raise AssertionError("fixture key or message invalid")
        pairs.append((m,r))
    meta={"requested_n_bits":bits,"actual_n_bits":n.bit_length(),
          "n2_bits":(n*n).bit_length(),"prime_bits":bits//2,"dataset":dataset,
          "slot_bytes":slot_bytes,"slots_per_message":slots,"count":count,
          "seed":seed,"randomness":"synthetic deterministic" if seed is not None else "OS randomness",
          "generator":"n+1","primality":"Miller-Rabin 48 rounds",
          "key_checked_by_decryption":True}
    return n,pairs,meta

def encrypt(n,m,r):
    return ((1+n*m)*pow(r,n,n*n)) % (n*n)

def write_case(path,n,pairs,meta):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    content=str(n)+"\n"+"".join(f"{m} {r}\n" for m,r in pairs)
    with path.open("x",encoding="ascii",newline="\n") as f:
        f.write(content)
    meta=dict(meta,sha256=hashlib.sha256(content.encode("ascii")).hexdigest())
    with path.with_suffix(".json").open("x",encoding="utf-8") as f:
        json.dump(meta,f,ensure_ascii=False,indent=2)
    return meta

def read_case(path):
    lines=Path(path).read_text(encoding="ascii").splitlines()
    if not lines or not lines[0].isdigit():
        raise ValueError("missing decimal n")
    n=int(lines[0])
    if n<=2 or n%2==0:
        raise ValueError("n must be odd and greater than 2")
    pairs=[]
    for line in lines[1:]:
        values=line.split()
        if len(values)!=2 or any(not v.isdigit() for v in values):
            raise ValueError("fixture row must have two nonnegative decimal operands")
        m,r=map(int,values)
        if not 0<=m<n or not 0<r<n or math.gcd(r,n)!=1:
            raise ValueError("invalid m or r")
        pairs.append((m,r))
    if not pairs:
        raise ValueError("empty fixture")
    return n,pairs

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bits",type=int,default=1024)
    ap.add_argument("--messages",type=int,default=100)
    ap.add_argument("--dataset",choices=["structured","uniform","edges"],default="structured")
    ap.add_argument("--slot-bytes",type=int,default=4)
    ap.add_argument("--seed",type=int)
    ap.add_argument("--output",type=Path,required=True)
    a=ap.parse_args()
    n,pairs,meta=make_case(a.bits,a.messages,a.dataset,a.slot_bytes,a.seed)
    print(json.dumps(write_case(a.output,n,pairs,meta)))

if __name__ == "__main__":
    main()
