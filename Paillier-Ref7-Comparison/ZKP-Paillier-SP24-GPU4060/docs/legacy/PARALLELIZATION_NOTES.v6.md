# OpenMP parallelization notes

This version keeps the ZKP-Paillier protocol and constraints unchanged.

The parallelized part is the independent normal-message Paillier encryption:

```text
for i in [0, msgCount):
    Cm[i] = Enc(m[i], Rm[i])
```

To avoid NTL thread-safety issues, all NTL values are converted to decimal
strings before entering the OpenMP region. Worker threads perform Paillier
encryption using GMP local `mpz_t` variables only:

```text
c = (1 + n*m) * r^n mod n^2
```

After the OpenMP region, the ciphertext strings are converted back to NTL
`ZZ_p` values serially.

The following parts intentionally remain serial to preserve the original
witness/constraint assignment order:

- `CRj`
- `Cm_`
- `m_`
- `calculateLj()`
- `wireUp()`
- `run()`

This version is intended to fix failures such as:

```text
t0 should be zero, the arguments A, B, C do not match with constrains Wa, Wb, Wc, Kq
```

which can occur when NTL arithmetic is executed concurrently in worker threads.
