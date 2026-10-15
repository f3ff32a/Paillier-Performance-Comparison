#pragma once
#include <cstddef>
#include <cstdint>

// These algebra/indexing routines are shared by the CUDA kernels and host GMP
// oracle tests. A host test of them is NOT a CUDA hardware test.
#ifdef __CUDACC__
#define ZKP_MATH_FN __device__ __forceinline__
#else
#define ZKP_MATH_FN inline
#endif
namespace polyu { namespace acceleration { namespace math {

// R_w = product_j base_j ^ digit(exponent_j, w), w is an 8-bit window.
// The final base/exponent pair is g/rho, so masking uses the identical path.
template<class Ops>
ZKP_MATH_FN void bucketWindow(Ops &o, std::size_t window) {
  typename Ops::Number one, a, b, cumulative, result;
  o.one(one);
  for (unsigned k=0;k<256;++k) o.storeBucket(k,one);
  for (std::size_t j=0;j<o.columns();++j) {
    const auto digit=o.digit(j,window);
    if (digit) {
      o.loadBucket(a,digit); o.loadBase(b,j); o.multiply(a,a,b);
      o.storeBucket(digit,a);
    }
  }
  o.one(cumulative); o.one(result);
  for (int k=255;k>=1;--k) {
    o.loadBucket(a,k); o.multiply(cumulative,cumulative,a);
    o.multiply(result,result,cumulative);
  }
  o.saveWindow(result);
}

// High-to-low Horner reconstruction: acc <- acc^256 * R_w.
// Only 8 squarings per window, instead of separately exponentiating R_w by 256^w.
template<class Ops>
ZKP_MATH_FN void combineWindows(Ops &o, std::size_t windows) {
  typename Ops::Number result, part;
  o.one(result);
  for (std::size_t w=windows;w-- > 0;) {
    for (unsigned k=0;k<8;++k) o.square(result,result);
    o.loadWindow(part,w); o.multiply(result,result,part);
  }
  o.saveResult(result);
}
} } }
#undef ZKP_MATH_FN
