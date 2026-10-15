#include "CudaBackend.hpp"
#include "GmpSupport.hpp"
#include "WindowMath.hpp"
#include <cuda_runtime.h>
#include <gmp.h> // CGBN also uses GMP in nvcc's host compilation pass.
#include <cgbn/cgbn.h>
#include <algorithm>
#include <limits>
#include <stdexcept>

namespace polyu { namespace acceleration {
namespace {
void checked(cudaError_t status, const char *operation) {
  if (status!=cudaSuccess)
    throw std::runtime_error(std::string(operation)+": "+cudaGetErrorName(status)+
                             " ("+std::to_string(static_cast<int>(status))+") "+cudaGetErrorString(status));
}
using Context = cgbn_context_t<32>;
template<unsigned Bits> using Env = cgbn_env_t<Context,Bits>;
template<unsigned Bits> using Mem = cgbn_mem_t<Bits>;
constexpr unsigned blockThreads=128;

// CGBN's load API predates const-correct source pointers. The implementation
// only reads memory; keep the backend's public inputs const.
template<class E,class T>
__device__ __forceinline__ void loadNumber(E env,typename E::cgbn_t &value,const T *ptr) {
  cgbn_load(env,value,const_cast<T *>(ptr));
}


template<class T> struct DeviceBuffer {
  T *data=nullptr;
  int device=0;
  explicit DeviceBuffer(std::size_t count) {
    checked(cudaGetDevice(&device),"cudaGetDevice");
    if (count) checked(cudaMalloc(reinterpret_cast<void **>(&data),
                      checkedProduct(count,sizeof(T))),"cudaMalloc");
  }
  ~DeviceBuffer() { if (data) { cudaSetDevice(device); cudaFree(data); } }
  DeviceBuffer(const DeviceBuffer &)=delete;
  DeviceBuffer &operator=(const DeviceBuffer &)=delete;
};
struct KernelTimer {
  cudaEvent_t begin=nullptr,end=nullptr;
  KernelTimer() {
    checked(cudaEventCreate(&begin),"cudaEventCreate");
    const auto status=cudaEventCreate(&end);
    if(status!=cudaSuccess) { cudaEventDestroy(begin); checked(status,"cudaEventCreate"); }
  }
  ~KernelTimer() { if(begin) cudaEventDestroy(begin); if(end) cudaEventDestroy(end); }
  void start(cudaStream_t stream) { checked(cudaEventRecord(begin,stream),"cudaEventRecord"); }
  double finish(cudaStream_t stream) {
    checked(cudaEventRecord(end,stream),"cudaEventRecord");
    checked(cudaEventSynchronize(end),"cudaEventSynchronize");
    float elapsed=0;
    checked(cudaEventElapsedTime(&elapsed,begin,end),"cudaEventElapsedTime");
    return elapsed;
  }
};
struct Session {
  Options config;
  cudaStream_t stream=nullptr;
  cgbn_error_report_t *report=nullptr;
  std::size_t budget=0;
  explicit Session(const Options &o) : config(o) {
    checked(cudaSetDevice(o.device),"cudaSetDevice");
    std::size_t freeBytes=0,totalBytes=0;
    checked(cudaMemGetInfo(&freeBytes,&totalBytes),"cudaMemGetInfo");
    budget=std::min(checkedProduct(o.gpuMemoryMiB,1024*1024),freeBytes-freeBytes/4);
    checked(cudaStreamCreateWithFlags(&stream,cudaStreamNonBlocking),"cudaStreamCreate");
    auto status=cgbn_error_report_alloc(&report);
    if(status!=cudaSuccess) { cudaStreamDestroy(stream); stream=nullptr;
      checked(status,"cgbn_error_report_alloc"); }
  }
  virtual ~Session() {
    cudaSetDevice(config.device);
    if (stream) cudaStreamSynchronize(stream);
    if (report) cgbn_error_report_free(report);
    if (stream) cudaStreamDestroy(stream);
  }
  void checkArithmetic() {
    if(cgbn_error_report_check(report))
      throw std::runtime_error(std::string("CGBN: ")+cgbn_error_string(report));
  }
  template<class T> void upload(T *to, const std::vector<T> &from) {
    // Pageable transfers may block this GPU worker. The separate CPU workers
    // continue independently; no claim of transfer/kernel overlap is made.
    checked(cudaMemcpyAsync(to,from.data(),checkedProduct(from.size(),sizeof(T)),
                           cudaMemcpyHostToDevice,stream),"H2D copy");
    checked(cudaStreamSynchronize(stream),"H2D synchronization");
  }
};

template<unsigned Bits> struct BucketOps {
  using Number=typename Env<Bits>::cgbn_t;
  Env<Bits> &env;
  Number q,identity;
  std::uint32_t np0;
  const Mem<Bits> *bases;
  const Mem<Bits> *exponents;
  Mem<Bits> *buckets;
  Mem<Bits> *parts;
  std::size_t columnCount,row,task,windows;
  __device__ BucketOps(Env<Bits> &e,const Mem<Bits> *qPtr,const Mem<Bits> *one,
      std::uint32_t inverse,const Mem<Bits> *b,const Mem<Bits> *x,Mem<Bits> *s,
      Mem<Bits> *p,std::size_t cols,std::size_t r,std::size_t t,std::size_t w)
      : env(e),np0(inverse),bases(b),exponents(x),buckets(s),parts(p),
        columnCount(cols),row(r),task(t),windows(w) {
    loadNumber(env,q,qPtr); loadNumber(env,identity,one);
  }
  __device__ std::size_t columns() const { return columnCount; }
  __device__ void one(Number &v) { cgbn_set(env,v,identity); }
  __device__ unsigned digit(std::size_t col,std::size_t w) const {
    return (exponents[row*columnCount+col]._limbs[w/4]>>((w%4)*8))&255u;
  }
  __device__ void loadBase(Number &v,std::size_t col) { loadNumber(env,v,bases+col); }
  __device__ void loadBucket(Number &v,unsigned k) { loadNumber(env,v,buckets+k); }
  __device__ void storeBucket(unsigned k,const Number &v) {
    cgbn_store(env,buckets+k,v); __syncwarp();
  }
  __device__ void multiply(Number &v,const Number &a,const Number &b) {
    cgbn_mont_mul(env,v,a,b,q,np0);
  }
  __device__ void square(Number &v,const Number &a) { cgbn_mont_sqr(env,v,a,q,np0); }
  __device__ void saveWindow(const Number &v) { cgbn_store(env,parts+task,v); }
  __device__ void loadWindow(Number &v,std::size_t w) {
    loadNumber(env,v,parts+row*windows+w);
  }
  __device__ void saveResult(const Number &v) {
    Number normal;
    cgbn_mont2bn(env,normal,v,q,np0);
    cgbn_store(env,buckets+row,normal); // combine kernel uses buckets as output
  }
};

template<unsigned Bits>
__global__ void commitWindows(cgbn_error_report_t *report,const Mem<Bits> *q,
    const Mem<Bits> *one,std::uint32_t np0,const Mem<Bits> *bases,
    const Mem<Bits> *exponents,Mem<Bits> *scratch,Mem<Bits> *parts,
    std::size_t columns,std::size_t windows,std::size_t offset,std::size_t count) {
  const std::size_t local=(static_cast<std::size_t>(blockIdx.x)*blockDim.x+threadIdx.x)/32;
  if(local>=count) return; // complete 32-thread groups take this branch together
  const auto task=offset+local;
  Context context(cgbn_report_monitor,report,static_cast<std::uint32_t>(task));
  Env<Bits> env(context);
  BucketOps<Bits> ops(env,q,one,np0,bases,exponents,scratch+local*256,
                      parts,columns,task/windows,task,windows);
  math::bucketWindow(ops,task%windows);
}
template<unsigned Bits>
__global__ void commitCombine(cgbn_error_report_t *report,const Mem<Bits> *q,
    const Mem<Bits> *one,std::uint32_t np0,Mem<Bits> *parts,Mem<Bits> *output,
    std::size_t windows,std::size_t rows) {
  const std::size_t row=(static_cast<std::size_t>(blockIdx.x)*blockDim.x+threadIdx.x)/32;
  if(row>=rows) return;
  Context context(cgbn_report_monitor,report,static_cast<std::uint32_t>(row));
  Env<Bits> env(context);
  BucketOps<Bits> ops(env,q,one,np0,nullptr,nullptr,output,parts,0,row,0,windows);
  math::combineWindows(ops,windows);
}

template<unsigned Bits> struct Modulus {
  BigInt q,radix;
  std::vector<Mem<Bits>> constants;
  std::uint32_t np0;
  explicit Modulus(const std::string &qString) : q(qString),constants(2) {
    if (storageBits(q.value)>Bits) throw std::invalid_argument("invalid CUDA storage width");
    mpz_set_ui(radix.value,1);
    mpz_mul_2exp(radix.value,radix.value,montgomeryRadixBits(Bits));
    mpz_mod(radix.value,radix.value,q.value);
    packWords(constants[0]._limbs,Bits,q.value);
    packWords(constants[1]._limbs,Bits,radix.value);
    np0=montgomeryInverse(constants[0]._limbs[0]);
  }
  void montgomery(Mem<Bits> &to,mpz_srcptr x) {
    BigInt v; mpz_mul(v.value,x,radix.value); mpz_mod(v.value,v.value,q.value);
    packWords(to._limbs,Bits,v.value);
  }
};

template<unsigned Bits> struct CommitSession final : Session,CudaCommit {
  Modulus<Bits> modulus;
  std::size_t columns;
  DeviceBuffer<Mem<Bits>> deviceConstants,deviceBases;
  CommitSession(const std::string &q,const std::string &g,const Strings &gi,
      const Options &o) : Session(o),modulus(q),columns(gi.size()+1),
      deviceConstants(2),deviceBases(checkBaseCount(gi.size()+1)) {
    upload(deviceConstants.data,modulus.constants);
    std::vector<Mem<Bits>> bases(columns);
    for(std::size_t j=0;j<columns;++j) {
      BigInt base(j+1==columns ? g : gi[j]);
      modulus.montgomery(bases[j],base.value);
    }
    upload(deviceBases.data,bases);
  }
  std::size_t checkBaseCount(std::size_t count) {
    if(checkedProduct(count+2,sizeof(Mem<Bits>))>budget/2)
      throw std::runtime_error("commitment generators exceed the GPU memory budget");
    return count;
  }
  double compute(const StringMatrix &ms,const Strings &rs,Strings &out,
      std::size_t first,std::size_t count) override {
    if(first>ms.size() || count>ms.size()-first || rs.size()<ms.size() || out.size()<ms.size())
      throw std::invalid_argument("CUDA commit batch dimensions are inconsistent");
    const std::size_t staticBytes=checkedProduct(columns+2,sizeof(Mem<Bits>));
    const std::size_t available=budget-staticBytes;
    const std::size_t perRow=checkedProduct(columns+Bits/8+1,sizeof(Mem<Bits>));
    if(available/2<perRow || available/2<256*sizeof(Mem<Bits>))
      throw std::runtime_error("one commitment row does not fit the GPU memory budget");
    const auto maxRows=std::max<std::size_t>(1,(available/2)/perRow);
    double elapsed=0;
    for(std::size_t begin=first;begin<first+count;) {
      const auto rows=std::min(maxRows,first+count-begin);
      std::vector<Mem<Bits>> exponents(checkedProduct(rows,columns));
      std::size_t exponentBits=1;
      for(std::size_t i=0;i<rows;++i) {
        if(ms[begin+i].size()+1!=columns)
          throw std::invalid_argument("CUDA commitment column count mismatch");
        for(std::size_t j=0;j<columns;++j) {
          BigInt x(j+1==columns ? rs[begin+i] : ms[begin+i][j]);
          packWords(exponents[i*columns+j]._limbs,Bits,x.value);
          exponentBits=std::max(exponentBits,mpz_sizeinbase(x.value,2));
        }
      }
      const auto windows=(exponentBits+7)/8;
      const auto tasks=checkedProduct(rows,windows);
      const auto used=checkedProduct(rows,checkedProduct(columns+windows+1,sizeof(Mem<Bits>)));
      const auto tile=std::min<std::size_t>({tasks,256,(available-used)/(256*sizeof(Mem<Bits>))});
      if(tile==0) throw std::runtime_error("no room for CUDA commitment buckets");
      DeviceBuffer<Mem<Bits>> dExp(exponents.size()),parts(tasks),
          buckets(checkedProduct(tile,256)),result(rows);
      upload(dExp.data,exponents);
      cgbn_error_report_reset(report);
      KernelTimer timer; timer.start(stream);
      for(std::size_t offset=0;offset<tasks;offset+=tile) {
        const auto size=std::min(tile,tasks-offset);
        commitWindows<Bits><<<static_cast<unsigned>((size+3)/4),blockThreads,0,stream>>>(
            report,deviceConstants.data,deviceConstants.data+1,modulus.np0,
            deviceBases.data,dExp.data,buckets.data,parts.data,columns,windows,offset,size);
        checked(cudaGetLastError(),"commitWindows launch");
      }
      commitCombine<Bits><<<static_cast<unsigned>((rows+3)/4),blockThreads,0,stream>>>(
          report,deviceConstants.data,deviceConstants.data+1,modulus.np0,
          parts.data,result.data,windows,rows);
      checked(cudaGetLastError(),"commitCombine launch");
      elapsed+=timer.finish(stream); checkArithmetic();
      std::vector<Mem<Bits>> host(rows);
      checked(cudaMemcpyAsync(host.data(),result.data,rows*sizeof(Mem<Bits>),
                             cudaMemcpyDeviceToHost,stream),"commit D2H");
      checked(cudaStreamSynchronize(stream),"commit D2H synchronization");
      for(std::size_t i=0;i<rows;++i) out[begin+i]=unpackWords(host[i]._limbs,Bits);
      begin+=rows;
    }
    return elapsed;
  }
};

template<unsigned Bits> struct EncInput { Mem<Bits> rMont,leftMont; };
template<unsigned Bits>
__global__ void encryptMessages(cgbn_error_report_t *report,const Mem<Bits> *constants,
    const Mem<Bits> *exponent,std::uint32_t np0,const EncInput<Bits> *input,
    Mem<Bits> *output,std::size_t count,unsigned windows) {
  const std::size_t row=(static_cast<std::size_t>(blockIdx.x)*blockDim.x+threadIdx.x)/32;
  if(row>=count) return;
  Context context(cgbn_report_monitor,report,static_cast<std::uint32_t>(row));
  Env<Bits> env(context);
  using Number=typename Env<Bits>::cgbn_t;
  using Local=typename Env<Bits>::cgbn_local_t;
  Number q,power,base,result,tmp;
  Local table[16];
  loadNumber(env,q,constants); loadNumber(env,result,constants+1);
  loadNumber(env,power,exponent); loadNumber(env,base,&input[row].rMont);
  cgbn_store(env,table,result); cgbn_store(env,table+1,base);
  cgbn_set(env,tmp,base);
  for(unsigned k=2;k<16;++k) {
    cgbn_mont_mul(env,tmp,tmp,base,q,np0); cgbn_store(env,table+k,tmp);
  }
  for(unsigned w=windows;w-- > 0;) {
    for(unsigned k=0;k<4;++k) cgbn_mont_sqr(env,result,result,q,np0);
    const auto digit=cgbn_extract_bits_ui32(env,power,w*4,4);
    loadNumber(env,tmp,table+digit); cgbn_mont_mul(env,result,result,tmp,q,np0);
  }
  loadNumber(env,tmp,&input[row].leftMont);
  cgbn_mont_mul(env,result,result,tmp,q,np0);
  cgbn_mont2bn(env,result,result,q,np0); cgbn_store(env,output+row,result);
}
template<unsigned Bits> struct EncryptSession final : Session,CudaEncrypt {
  Modulus<Bits> modulus;
  BigInt n;
  DeviceBuffer<Mem<Bits>> deviceConstants,deviceExponent;
  unsigned windows;
  EncryptSession(const std::string &nString,const std::string &n2,const Options &o)
      : Session(o),modulus(n2),n(nString),deviceConstants(2),deviceExponent(1) {
    BigInt expected; mpz_mul(expected.value,n.value,n.value);
    if(mpz_cmp_ui(n.value,1)<=0 || mpz_cmp(expected.value,modulus.q.value)!=0)
      throw std::invalid_argument("Paillier n2 must equal n*n");
    upload(deviceConstants.data,modulus.constants);
    std::vector<Mem<Bits>> exponent(1);
    packWords(exponent[0]._limbs,Bits,n.value); upload(deviceExponent.data,exponent);
    windows=static_cast<unsigned>((mpz_sizeinbase(n.value,2)+3)/4);
  }
  double compute(const Strings &ms,const Strings &rs,Strings &out,
      std::size_t first,std::size_t count) override {
    if(first>ms.size() || count>ms.size()-first || rs.size()!=ms.size() || out.size()!=ms.size())
      throw std::invalid_argument("CUDA encryption batch dimensions are inconsistent");
    const auto maxRows=std::min<std::size_t>(65536,
        (budget-3*sizeof(Mem<Bits>))/(sizeof(EncInput<Bits>)+sizeof(Mem<Bits>)));
    if(maxRows==0) throw std::runtime_error("no room for CUDA encryption batch");
    double elapsed=0;
    for(std::size_t begin=first;begin<first+count;) {
      const auto rows=std::min(maxRows,first+count-begin);
      std::vector<EncInput<Bits>> input(rows);
      for(std::size_t i=0;i<rows;++i) {
        BigInt m(ms[begin+i]),r(rs[begin+i]),left;
        mpz_mul(left.value,n.value,m.value); mpz_add_ui(left.value,left.value,1);
        modulus.montgomery(input[i].leftMont,left.value);
        modulus.montgomery(input[i].rMont,r.value);
      }
      DeviceBuffer<EncInput<Bits>> dInput(rows);
      DeviceBuffer<Mem<Bits>> dOutput(rows);
      upload(dInput.data,input); cgbn_error_report_reset(report);
      KernelTimer timer; timer.start(stream);
      encryptMessages<Bits><<<static_cast<unsigned>((rows+3)/4),blockThreads,0,stream>>>(
          report,deviceConstants.data,deviceExponent.data,modulus.np0,
          dInput.data,dOutput.data,rows,windows);
      checked(cudaGetLastError(),"encryptMessages launch");
      elapsed+=timer.finish(stream); checkArithmetic();
      std::vector<Mem<Bits>> host(rows);
      checked(cudaMemcpyAsync(host.data(),dOutput.data,rows*sizeof(Mem<Bits>),
                             cudaMemcpyDeviceToHost,stream),"encrypt D2H");
      checked(cudaStreamSynchronize(stream),"encrypt D2H synchronization");
      for(std::size_t i=0;i<rows;++i) out[begin+i]=unpackWords(host[i]._limbs,Bits);
      begin+=rows;
    }
    return elapsed;
  }
};
} // anonymous namespace

bool cudaAvailable(int device,std::string &description) {
  int count=0;
  auto status=cudaGetDeviceCount(&count);
  if(status!=cudaSuccess) { description=cudaGetErrorString(status); return false; }
  if(device<0 || device>=count) { description="requested CUDA device is unavailable"; return false; }
  cudaDeviceProp props;
  status=cudaGetDeviceProperties(&props,device);
  if(status!=cudaSuccess) { description=cudaGetErrorString(status); return false; }
  if(props.major<7) { description="this backend requires compute capability 7.0 or newer"; return false; }
  description=std::string(props.name)+" (sm_"+std::to_string(props.major)+std::to_string(props.minor)+")";
  status=cudaSetDevice(device);
  if(status==cudaSuccess) {
    cudaFuncAttributes attributes{};
    status=cudaFuncGetAttributes(&attributes,commitWindows<256>);
  }
  if(status!=cudaSuccess) {
    description+=": "+std::string(cudaGetErrorName(status))+" ("+
                 std::to_string(static_cast<int>(status))+") "+cudaGetErrorString(status)+
                 "; check driver and CMAKE_CUDA_ARCHITECTURES (RTX 4060: 89-real)";
    return false;
  }
  return true;
}
std::unique_ptr<CudaCommit> makeCudaCommit(const std::string &q,const std::string &g,
    const Strings &gi,const Options &o) {
  BigInt modulus(q);
  switch(storageBits(modulus.value)) {
#define ZKP_CASE(B) case B:return std::unique_ptr<CudaCommit>(new CommitSession<B>(q,g,gi,o));
    ZKP_CASE(256) ZKP_CASE(512) ZKP_CASE(1024) ZKP_CASE(1536) ZKP_CASE(2048)
    ZKP_CASE(3072) ZKP_CASE(4096) ZKP_CASE(6144) ZKP_CASE(8192)
#undef ZKP_CASE
    default:throw std::logic_error("unhandled CUDA width");
  }
}
std::unique_ptr<CudaEncrypt> makeCudaEncrypt(const std::string &n,const std::string &n2,
    const Options &o) {
  BigInt modulus(n2);
  switch(storageBits(modulus.value)) {
#define ZKP_CASE(B) case B:return std::unique_ptr<CudaEncrypt>(new EncryptSession<B>(n,n2,o));
    ZKP_CASE(256) ZKP_CASE(512) ZKP_CASE(1024) ZKP_CASE(1536) ZKP_CASE(2048)
    ZKP_CASE(3072) ZKP_CASE(4096) ZKP_CASE(6144) ZKP_CASE(8192)
#undef ZKP_CASE
    default:throw std::logic_error("unhandled CUDA width");
  }
}
} }
