# Commit Parallelization Notes - v5 experimental

This version is based on `ZKP-Paillier-SP24-parallel-v4gmp`.

Additional change:

- `src/cpp/app/PolynomialCommitment.cpp`
  - `PolynomialCommitment::commit(const Mat<ZZ_p>&, const Vec<ZZ_p>&, Vec<ZZ_p>&)` now uses OpenMP to parallelize commitments by matrix row.
  - Mutable arithmetic inside the OpenMP region is implemented with thread-local GMP `mpz_t` values instead of NTL `ZZ_p` operations.
  - NTL-to-string conversion is done before the OpenMP region; string-to-NTL conversion is done after the OpenMP region.

The optimized formula is:

```text
Com(M_i, r_i) = g^{r_i} * product_j g_j^{M_{i,j}} mod Q
```

For a matrix M, each row commitment is independent, so the implementation applies:

```text
parallel for i in [0, rows):
    ret[i] = Com(M_i, r_i)
```

Important:

- This is an experimental version.
- It should be validated with `make run_test` before using the timing result in the paper.
- Because this path serializes NTL values to decimal strings before entering OpenMP, small matrices may not become faster.
- It is intended to test whether the large `P.commit time` can be reduced when matrix sizes are larger.
