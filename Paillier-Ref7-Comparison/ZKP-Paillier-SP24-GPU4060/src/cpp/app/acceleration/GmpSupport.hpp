#pragma once
#include <gmp.h>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace polyu { namespace acceleration {
struct BigInt {
  mpz_t value;
  BigInt() { mpz_init(value); }
  explicit BigInt(const std::string &s) : BigInt() {
    if (mpz_set_str(value,s.c_str(),10)!=0)
      throw std::invalid_argument("invalid decimal big integer");
  }
  BigInt(const BigInt &x) { mpz_init_set(value,x.value); }
  BigInt &operator=(const BigInt &x) { mpz_set(value,x.value); return *this; }
  ~BigInt() { mpz_clear(value); }
};
inline std::string decimal(mpz_srcptr x) {
  std::vector<char> s(mpz_sizeinbase(x,10)+3);
  mpz_get_str(s.data(),10,x); return std::string(s.data());
}
inline std::size_t checkedProduct(std::size_t a, std::size_t b) {
  if (b && a>std::numeric_limits<std::size_t>::max()/b)
    throw std::overflow_error("GPU buffer size overflow");
  return a*b;
}
inline unsigned storageBits(mpz_srcptr modulus) {
  if (mpz_cmp_ui(modulus,1)<=0 || mpz_even_p(modulus))
    throw std::invalid_argument("CUDA Montgomery modulus must be odd and greater than 1");
  const auto bits=mpz_sizeinbase(modulus,2);
  for (unsigned width : {256u,512u,1024u,1536u,2048u,3072u,4096u,6144u,8192u})
    if (bits<=width) return width;
  throw std::invalid_argument("CUDA modulus exceeds the supported 8192-bit storage width");
}
inline unsigned montgomeryRadixBits(unsigned bits) {
  // CGBN uses UNPADDED_BITS = TPI * ceil(BITS / (32*TPI)) * 32.
  // With our 32-thread groups, BITS=256/512/1536 use R=2^1024/1024/2048.
  // R=2^BITS is WRONG for these padded environments (see CGBN impl_cuda.cu).
  return ((bits+1023)/1024)*1024;
}
inline void packWords(std::uint32_t *out, unsigned bits, mpz_srcptr x) {
  if (mpz_sgn(x)<0 || (mpz_sgn(x)>0 && mpz_sizeinbase(x,2)>bits))
    throw std::invalid_argument("negative or oversized GPU integer; refusing truncation");
  for (unsigned i=0;i<bits/32;++i) out[i]=0;
  std::size_t written=0;
  // Least significant WORD first; each word uses native byte order.
  mpz_export(out,&written,-1,sizeof(std::uint32_t),0,0,x);
}
inline std::string unpackWords(const std::uint32_t *in, unsigned bits) {
  BigInt x;
  mpz_import(x.value,bits/32,-1,sizeof(std::uint32_t),0,0,in);
  return decimal(x.value);
}
inline std::uint32_t montgomeryInverse(std::uint32_t lowWord) {
  // Newton iteration modulo 2^32; unsigned overflow is intentional and defined.
  std::uint32_t inverse=1;
  for (unsigned i=0;i<5;++i) inverse*=2-lowWord*inverse;
  return 0-inverse;
}
} }
