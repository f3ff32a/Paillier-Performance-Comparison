#include "./PolynomialCommitment.hpp"

#ifdef _OPENMP
#include <omp.h>
#endif
#include <gmp.h>
#include <cstdlib>
#include "acceleration/CudaBackend.hpp"
#include "acceleration/GmpSupport.hpp"


namespace
{
string zzToDecimalString(const ZZ &x)
{
  stringstream ss;
  ss << x;
  return ss.str();
}

string zzpToDecimalString(const ZZ_p &x)
{
  return zzToDecimalString(conv<ZZ>(x));
}

ZZ decimalStringToZZ(const string &s)
{
  stringstream ss(s);
  ZZ z;
  ss >> z;
  return z;
}

// Thread-local GMP implementation of one Pedersen-style polynomial commitment row.
// It computes ret = g^r * prod_i gi[i]^mi[i] mod Q.
// The product part uses the same 8-bit bucket idea as the original NTL version,
// but all mutable big integers are local to the calling thread.
string commitRowGMP(
    const mpz_t Q,
    const mpz_t g,
    const vector<acceleration::BigInt> &gi,
    const vector<string> &miDec,
    const string &rDec)
{
  const size_t n = miDec.size();
  const unsigned int bucketSize = 256;

  mpz_t r, ret;
  mpz_init(r);
  mpz_init(ret);
  mpz_set_str(r, rDec.c_str(), 10);
  mpz_powm(ret, g, r, Q);

  mpz_t *mi = new mpz_t[n];
  size_t bitLength = 0;
  for (size_t i = 0; i < n; i++)
  {
    mpz_init(mi[i]);
    mpz_set_str(mi[i], miDec[i].c_str(), 10);
    if (mpz_sgn(mi[i]) != 0)
    {
      size_t bits = mpz_sizeinbase(mi[i], 2);
      if (bits > bitLength)
        bitLength = bits;
    }
  }

  const size_t rounds = (bitLength + 7) / 8;
  size_t shift = 0;

  mpz_t pow256, shifted, R, Rpow, cumulative, tmp;
  mpz_init_set_ui(pow256, 1);
  mpz_init(shifted);
  mpz_init(R);
  mpz_init(Rpow);
  mpz_init(cumulative);
  mpz_init(tmp);

  mpz_t S[bucketSize];
  for (unsigned int k = 0; k < bucketSize; k++)
    mpz_init(S[k]);

  for (size_t round = 0; round < rounds; round++)
  {
    for (unsigned int k = 0; k < bucketSize; k++)
      mpz_set_ui(S[k], 1);

    for (size_t i = 0; i < n; i++)
    {
      mpz_tdiv_q_2exp(shifted, mi[i], shift);
      unsigned long digit = mpz_get_ui(shifted) & 0xFFUL;
      if (digit != 0)
      {
        mpz_mul(S[digit], S[digit], gi[i].value);
        mpz_mod(S[digit], S[digit], Q);
      }
    }

    // Equivalent to the original suffix bucket computation:
    // T[255]=S[255], T[k]=T[k+1]*S[k], R=prod_{k=1}^{255}T[k].
    mpz_set_ui(cumulative, 1);
    mpz_set_ui(R, 1);
    for (int k = (int)bucketSize - 1; k >= 1; k--)
    {
      mpz_mul(cumulative, cumulative, S[k]);
      mpz_mod(cumulative, cumulative, Q);
      mpz_mul(R, R, cumulative);
      mpz_mod(R, R, Q);
    }

    mpz_powm(Rpow, R, pow256, Q);
    mpz_mul(ret, ret, Rpow);
    mpz_mod(ret, ret, Q);

    shift += 8;
    mpz_mul_ui(pow256, pow256, bucketSize);
  }

  char *raw = mpz_get_str(nullptr, 10, ret);
  string result(raw);
  std::free(raw);

  for (unsigned int k = 0; k < bucketSize; k++)
    mpz_clear(S[k]);

  mpz_clear(pow256);
  mpz_clear(shifted);
  mpz_clear(R);
  mpz_clear(Rpow);
  mpz_clear(cumulative);
  mpz_clear(tmp);

  for (size_t i = 0; i < n; i++)
    mpz_clear(mi[i]);
  delete[] mi;

  mpz_clear(r);
  mpz_clear(ret);

  return result;
}
} // namespace

PolynomialCommitment::PolynomialCommitment(const shared_ptr<PaillierEncryption> &crypto, size_t n)
    : PolynomialCommitment::PolynomialCommitment(crypto, crypto->genGenerators(n))
{
}

PolynomialCommitment::PolynomialCommitment(const shared_ptr<PaillierEncryption> &crypto, const Vec<ZZ_p> &gi)
    : PolynomialCommitment::PolynomialCommitment(crypto->getGroupQ(), crypto->getGroupP(), crypto->getGroupG(), gi)
{
}

PolynomialCommitment::PolynomialCommitment(const ZZ &Q, const ZZ &p, const ZZ_p &g, const Vec<ZZ_p> &gi)
{
  this->Q = Q;
  this->p = p;
  this->g = g;
  this->gi = gi;
}

// DEPRECATED: in secure generators gi
PolynomialCommitment::PolynomialCommitment(const ZZ &Q, const ZZ &p, const ZZ_p &g, size_t n)
{
  this->Q = Q;
  this->p = p;
  this->g = g;
  this->gi.SetLength(n);

  ZZ_pPush push(Q);
  ZZ_p gx = g;
  for (size_t i = 0; i < n; i++)
  {
    mul(gx, gx, g);
    this->gi[i] = gx;
  }
}

// original version
// ZZ_p PolynomialCommitment::commit(const Vec<ZZ_p> &mi, const ZZ_p &r)
// {
//   ZZ_pPush push(Q);

//   ZZ_p gx;
//   ZZ_p ret = power(g, conv<ZZ>(r));

//   for (size_t i = 0; i < mi.length(); i++)
//   {
//     power(gx, gi[i], conv<ZZ>(mi[i]));
//     mul(ret, ret, gx);
//   }
//   return ret;
// }

 // allen's version
ZZ_p PolynomialCommitment::commit(const Vec<ZZ_p> &mi, const ZZ_p &r)
{
  //unsigned short bitlength = r.ModulusSize();
  
  //cout << "is this correct bitlength?:" << p.size() << endl; 
  //cout << "is this correct Q length?:" << Q.size() << endl; 

  Vec<ZZ> zmi; 
  zmi.SetLength(mi.length());
  conv(zmi,mi);
  
  if (mi.length() > gi.length())
    throw invalid_argument("insufficient commitment generators");
  unsigned long bitlength = 0;
  for (int i=0;i<mi.length();i++) {
 //   cout << "zmi[" << i << "]=" << zmi[i] << endl;
 //   cout << "mi[" << i << "]=" << mi[i] << endl; 
 //   cout << "numBits:" << NumBits(zmi[i]) << endl;
    if (bitlength < NumBits(zmi[i]))
      bitlength = NumBits(zmi[i]); 
  }
//  cout << "real BitLength" << bitlength << endl;

  ZZ_pPush push(Q);

  ZZ_p gx;
  ZZ_p ret = power(g, conv<ZZ>(r));

  


  const unsigned int bucket_size = 256; // see below. the 4.0 depends on the bucket_size. the number should be log bucket_size
 
  unsigned long round = ceil(bitlength/8.0); //1024-bit exponent, 4-bit bucket => 256 rounds 
  // has to tune the 4.0 to represent the bucket size of 16
  
  ZZ pow = conv<ZZ>(1);
  unsigned long shift = 0;
  unsigned long d = 1;

  for (unsigned int j = 0; j < round; j++ ) {
   // cout << "====Round: " << j <<"===="<< endl;
    ZZ_p S[bucket_size]; 
    ZZ_p T[bucket_size]; 
    for (unsigned int k=0; k<bucket_size;k++) {
      S[k]=conv<ZZ_p>(1);
      T[k]=conv<ZZ_p>(1);
    }
    
    long t = 0;
    ZZ flag = ConvertUtils::hexToZZ("FF");
    ZZ tmp;
    
    for (unsigned int i =0; i < zmi.length(); i++ ) {
      NTL::bit_and(tmp, zmi[i] >> shift, flag);
      t = conv<int>(tmp);
   //   cout << "zmi[i]" << zmi[i] << "|t:" << t << endl;
      if (t!=0)
        mul(S[t], S[t], gi[i]);
    }
    //list bucket:
   // for (int i=0;i<bucket_size;i++) {
   //   cout << "i=" << i << ":" << S[i] << "|";
   // }

    shift = shift + 8;
    T[bucket_size-1] = S[bucket_size-1];
    for (unsigned int i=bucket_size-1; i>1; i--) {
      mul (T[i-1], T[i], S[i-1]);
    }

    ZZ_p R = conv<ZZ_p>(1);
    for (unsigned int i = 1; i < bucket_size; i++) {
      mul(R, R, T[i]);      
    }
    

    //cout << "Round:" << j << "Power:" << pow << endl;
   power(R, R, pow);
   mul(pow, pow, bucket_size);
    
    
    mul(ret, ret, R);
  }

  return ret;
}


void PolynomialCommitment::commit(const Mat<ZZ_p> &ms, const Vec<ZZ_p> &rs, Vec<ZZ_p> &ret)
{
  const auto m = ms.NumRows();
  const auto n = ms.NumCols();
  if (rs.length() != m || gi.length() < n)
    throw invalid_argument("commitment matrix, randomness or generator dimensions mismatch");
  ret.SetLength(m);

  // Serialize NTL values before launching any workers. Their modular contexts
  // and protocol random state are never accessed by either backend's workers.
  string qDec = zzToDecimalString(Q);
  string gDec = zzpToDecimalString(g);
  vector<string> giDec(n), rsDec(m), retDec(m);
  vector<vector<string>> msDec(m, vector<string>(n));
  vector<acceleration::BigInt> giGMP(n);
  acceleration::BigInt qGMP(qDec), gGMP(gDec);
  for (long j = 0; j < n; ++j) {
    giDec[j] = zzpToDecimalString(gi[j]);
    mpz_set_str(giGMP[j].value, giDec[j].c_str(), 10);
  }
  for (long i = 0; i < m; ++i) {
    rsDec[i] = zzpToDecimalString(rs[i]);
    for (long j = 0; j < n; ++j) msDec[i][j] = zzpToDecimalString(ms[i][j]);
  }
  auto reference = [&](size_t i) {
    return commitRowGMP(qGMP.value, gGMP.value, giGMP, msDec[i], rsDec[i]);
  };
  const auto config = acceleration::options();
  string device;
  bool available = config.mode != acceleration::Mode::CPU &&
                   acceleration::cudaAvailable(config.device, device);
  unique_ptr<acceleration::CudaCommit> gpu;
  acceleration::schedule("Commit", m, config, available, device,
      [&](size_t i) { retDec[i] = reference(i); },
      [&](size_t first, size_t count) {
        if (!gpu) gpu = acceleration::makeCudaCommit(qDec, gDec, giDec, config);
        return gpu->compute(msDec, rsDec, retDec, first, count);
      },
      [&](size_t i) {
        if (retDec[i] != reference(i))
          throw runtime_error("GPU/CPU commitment mismatch at row " + to_string(i));
      });

  // Reconstruct NTL output on the original calling thread and restore its modulus.
  ZZ_pPush push(Q);
  for (long i = 0; i < m; ++i)
    ret[i] = conv<ZZ_p>(decimalStringToZZ(retDec[i]));
}

void PolynomialCommitment::calcT(
    size_t m1, size_t m2, size_t n,
    const ZZ_pX &tx, Mat<ZZ_p> &ret)
{
  if ((m1 + m2) * n < deg(tx))
    throw invalid_argument("m1, m2, n do not match with the t(x) length");
  if (m1 <= 0 || m2 <= 0)
    throw invalid_argument("m1, m2 must be positive integer");

  ZZ_pPush push(p);
  auto maxDeg = deg(tx);

  // group to m1+m2 x n matrix
  ret.SetDims(m1 + m2 + 1, n);

  auto t1Max = m1 * n;
  auto t2Max = t1Max + m2 * n + 1;
  size_t i, j;
  i = j = 0;
  for (size_t d = 0; d < t1Max && d <= maxDeg; d++)
  {
    ret[i][j] = tx[d];
    ++j == n && (i++);
    j == n && (j = 0);
  }
  for (size_t d = t1Max + 1; d < t2Max && d <= maxDeg; d++)
  {
    ret[i][j] = tx[d];
    ++j == n && (i++);
    j == n && (j = 0);
  }

  // Random vector u
  Vec<ZZ_p> u;
  u.SetLength(n);
  MathUtils::randVecZZ_p(n, p, u, true);
  u[n - 1] = ZZ_p();

  ret[m1 + m2] = u;

  // mask vector t0'
  for (size_t i = 0; i < n - 1; i++)
  {
    ret[m1][i + 1] -= u[i];
  }
}

void PolynomialCommitment::commit(
    size_t m1, size_t m2, size_t n,
    const Mat<ZZ_p> &T, Vec<ZZ_p> &ri,
    Vec<ZZ_p> &ret)
{
  size_t m = m1 + m2 + 1;
  if (T.NumRows() != m || T.NumCols() != n)
    throw invalid_argument("m1, m2, n do not match with the dimension of matrix T");
  if (ri.length() != 0 && ri.length() != m)
    throw invalid_argument("ri.size() do not match (m1 + m2 + 1)");

  // Generate randomness if it isn't provided
  if (IsZero(ri))
  {
    MathUtils::randVecZZ_p(m, p, ri, true);
  }

  // calculate commitment
  commit(T, ri, ret);
}

void PolynomialCommitment::eval(
    size_t m1, size_t m2, size_t n,
    const Mat<ZZ_p> &T, const Vec<ZZ_p> &ri, const ZZ_p &x,
    Vec<ZZ_p> &ret)
{
  size_t m = m1 + m2 + 1;
  if (T.NumRows() != m || T.NumCols() != n)
    throw invalid_argument("m1, m2, n do not match with the dimension of matrix T");
  if (ri.length() != 0 && ri.length() != m)
    throw invalid_argument("ri.size() do not match (m1 + m2 + 1)");

  ZZ_pPush push(p);

  // Compute Z(X)
  auto xn = power(x, n);
  Vec<ZZ_p> xns;
  MathUtils::powerVecZZ_p(xn, max(m1 + 1, m2), p, xns);

  Vec<ZZ_p> Z;
  Z.SetLength(m1 + m2 + 1);
  for (size_t i = 0; i < m1; i++)
  {
    inv(Z[i], xns[m1 - i]);
  }
  for (size_t i = 0; i < m2; i++)
  {
    mul(Z[m1 + i], xns[i], x);
  }
  mul(Z[m1 + m2], x, x);

  // ret = [t_..., r_]
  // t_ = Z * T
  mul(ret, Z, T);

  // r_ = Z * r
  Mat<ZZ_p> r_;
  r_.SetDims(1, ri.length());
  r_[0] = ri;
  transpose(r_, r_);

  Vec<ZZ_p> r__;
  mul(r__, Z, r_);

  ret.append(r__);
}

bool PolynomialCommitment::verify(
    size_t m1, size_t m2, size_t n,
    const Vec<ZZ_p> &pc, const Vec<ZZ_p> &pe, const ZZ_p &x)
{
  size_t m = m1 + m2 + 1;
  if (pc.length() != m)
    throw invalid_argument("commitments count does not match with (m1 + m2 + 1)");
  if (pe.length() != n + 1)
    throw invalid_argument("evaluate results count does not match with (n + 1)");

  ZZ_pPush push;

  Vec<ZZ_p> t_;
  ConvertUtils::subVec(pe, t_, 0, n);
  ZZ_p r = pe[n];

  auto c1 = commit(t_, r);

  // U ^ (x ^ 2)
  ZZ_p exp;
  ZZ_p Ti;
  ZZ_p c2 = pc[pc.length() - 1];
  {
    ZZ_p::init(p);

    mul(exp, x, x);

    ZZ_p::init(Q);
    power(c2, c2, conv<ZZ>(exp));
  }

  // Product(Ti' ^ (x ^ ((i - m1) * n)))
  for (size_t i = 0; i < m1; i++)
  {
    // TODO: use powerVec to calc
    ZZ_p::init(p);
    power(exp, x, (m1 - i) * n);
    inv(exp, exp);

    ZZ_p::init(Q);
    power(Ti, pc[i], conv<ZZ>(exp));
    mul(c2, c2, Ti);
  }

  // Product(Ti'' ^ (x ^ (i * n + 1)))
  for (size_t i = 0; i < m2; i++)
  {
    ZZ_p::init(p);
    power(exp, x, i * n + 1);

    ZZ_p::init(Q);
    power(Ti, pc[m1 + i], conv<ZZ>(exp));
    mul(c2, c2, Ti);
  }

  // verify
  return c1 == c2;
}

ZZ_p PolynomialCommitment::calcV(size_t n, const Vec<ZZ_p> &pe, const ZZ_p &x)
{
  ZZ_pPush push(p);

  // v = t_ * X(x)
  Vec<ZZ_p> t;
  ConvertUtils::subVec(pe, t, 0, pe.length() - 1);

  Vec<ZZ_p> X;
  MathUtils::powerVecZZ_p(x, n, p, X);
  Mat<ZZ_p> X_;
  X_.SetDims(1, X.length());
  X_[0] = X;
  transpose(X_, X_);

  Vec<ZZ_p> v;
  mul(v, t, X_);
  return v[0];
}
