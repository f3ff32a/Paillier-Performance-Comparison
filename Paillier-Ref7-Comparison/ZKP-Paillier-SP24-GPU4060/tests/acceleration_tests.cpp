#include "app/acceleration/Backend.hpp"
#include "app/acceleration/CudaBackend.hpp"
#include "app/acceleration/GmpSupport.hpp"
#include "app/acceleration/WindowMath.hpp"
#include "app/CBatchEnc.hpp"
#include <atomic>
#include <cstdlib>
#include <limits>
#include <thread>

namespace acc=polyu::acceleration;
namespace {
int testDevice=0;
void require(bool value,const std::string &message) {
  if(!value) throw std::runtime_error(message);
}
template<class Fn> void mustThrow(Fn fn,const std::string &message) {
  bool threw=false; try { fn(); } catch(const std::exception &) { threw=true; }
  require(threw,message);
}
std::string textZZ(const ZZ &x) { std::ostringstream out; out<<x; return out.str(); }

// Uses GMP to emulate the Montgomery representation and independently checks
// the exact window/bucket algorithm used in the CUDA kernels against mpz_powm.
struct HostWindowOps {
  using Number=acc::BigInt;
  Number q,radix,radixInverse,result;
  std::vector<Number> bases,exponents,buckets,parts;
  std::size_t active=0;
  HostWindowOps(const std::string &modulus,const acc::Strings &b,
                const acc::Strings &e,std::size_t windows)
      : q(modulus),bases(b.size()),exponents(e.size()),buckets(256),parts(windows) {
    const auto width=acc::storageBits(q.value);
    mpz_set_ui(radix.value,1);
    mpz_mul_2exp(radix.value,radix.value,acc::montgomeryRadixBits(width));
    mpz_mod(radix.value,radix.value,q.value);
    require(mpz_invert(radixInverse.value,radix.value,q.value)!=0,"Montgomery inverse");
    for(std::size_t j=0;j<b.size();++j) {
      mpz_set_str(bases[j].value,b[j].c_str(),10);
      mpz_mul(bases[j].value,bases[j].value,radix.value);
      mpz_mod(bases[j].value,bases[j].value,q.value);
      mpz_set_str(exponents[j].value,e[j].c_str(),10);
    }
  }
  std::size_t columns() const { return bases.size(); }
  void one(Number &n) { n=radix; }
  unsigned digit(std::size_t j,std::size_t w) {
    Number shifted; mpz_tdiv_q_2exp(shifted.value,exponents[j].value,w*8);
    return static_cast<unsigned>(mpz_get_ui(shifted.value)&255);
  }
  void loadBase(Number &n,std::size_t j) { n=bases[j]; }
  void loadBucket(Number &n,unsigned k) { n=buckets[k]; }
  void storeBucket(unsigned k,const Number &n) { buckets[k]=n; }
  void multiply(Number &out,const Number &a,const Number &b) {
    mpz_mul(out.value,a.value,b.value);
    mpz_mul(out.value,out.value,radixInverse.value); mpz_mod(out.value,out.value,q.value);
  }
  void square(Number &out,const Number &a) { multiply(out,a,a); }
  void saveWindow(const Number &n) { parts[active]=n; }
  void loadWindow(Number &n,std::size_t w) { n=parts[w]; }
  void saveResult(const Number &n) {
    mpz_mul(result.value,n.value,radixInverse.value); mpz_mod(result.value,result.value,q.value);
  }
};
std::string independentCommit(const std::string &qString,const acc::Strings &bases,
                              const acc::Strings &exponents) {
  acc::BigInt q(qString),result,tmp;
  mpz_set_ui(result.value,1);
  for(std::size_t j=0;j<bases.size();++j) {
    acc::BigInt b(bases[j]),e(exponents[j]);
    mpz_powm(tmp.value,b.value,e.value,q.value);
    mpz_mul(result.value,result.value,tmp.value); mpz_mod(result.value,result.value,q.value);
  }
  return acc::decimal(result.value);
}
void packingTests() {
  require(acc::montgomeryRadixBits(256)==1024 && acc::montgomeryRadixBits(512)==1024 &&
          acc::montgomeryRadixBits(1536)==2048 && acc::montgomeryRadixBits(3072)==3072,
          "CGBN padded Montgomery radix");
  for(unsigned bits : {256u,512u,1024u,1536u,2048u,3072u,4096u,6144u,8192u}) {
    acc::BigInt x;
    mpz_set_ui(x.value,1); mpz_mul_2exp(x.value,x.value,bits); mpz_sub_ui(x.value,x.value,1);
    std::vector<std::uint32_t> words(bits/32);
    acc::packWords(words.data(),bits,x.value);
    require(acc::unpackWords(words.data(),bits)==acc::decimal(x.value),"limb round trip");
    for(auto word:words) require(word==0xffffffffu,"limb carry pattern");
    mpz_add_ui(x.value,x.value,1);
    mustThrow([&] { acc::packWords(words.data(),bits,x.value); },"must reject truncation");
    mpz_set_si(x.value,-1);
    mustThrow([&] { acc::packWords(words.data(),bits,x.value); },"must reject negative exponent");
    mpz_set_ui(x.value,0); acc::packWords(words.data(),bits,x.value);
    require(acc::unpackWords(words.data(),bits)=="0","zero padding");
  }
  for(std::uint32_t low : {1u,3u,0x7fffffffu,0xfffffffdu,0xffffffffu})
    require(static_cast<std::uint32_t>(low*acc::montgomeryInverse(low))==0xffffffffu,
            "Montgomery low-word inverse");
  acc::BigInt q("4");
  mustThrow([&] { acc::storageBits(q.value); },"even modulus rejected");
  mpz_set_ui(q.value,1); mpz_mul_2exp(q.value,q.value,8192); mpz_add_ui(q.value,q.value,1);
  mustThrow([&] { acc::storageBits(q.value); },"oversized modulus rejected");
  mustThrow([] { acc::checkedProduct(std::numeric_limits<std::size_t>::max(),2); },"size overflow");
  std::cout<<"PASS packing: 256..8192 bits, zero, overflow, negative, Montgomery inverse\n";
}
void windowTests(bool hardware) {
  gmp_randstate_t random;
  gmp_randinit_mt(random); gmp_randseed_ui(random,7241);
  for(unsigned bits : {65u,257u,513u,1025u,1537u,2049u,3073u,4097u,6145u,8192u}) {
    acc::BigInt q;
    mpz_set_ui(q.value,1); mpz_mul_2exp(q.value,q.value,bits); mpz_sub_ui(q.value,q.value,17);
    const auto qString=acc::decimal(q.value);
    acc::Strings bases(7);
    acc::StringMatrix exponents(3,acc::Strings(7));
    for(std::size_t j=0;j<bases.size();++j) {
      acc::BigInt b; mpz_urandomm(b.value,random,q.value); bases[j]=acc::decimal(b.value);
    }
    // Exercise an unreduced base and bases near the modulus boundary.
    bases[0]=qString; acc::BigInt near(qString); mpz_sub_ui(near.value,near.value,1);
    bases[1]=acc::decimal(near.value);
    for(std::size_t i=0;i<exponents.size();++i) {
      for(auto &s:exponents[i]) {
        acc::BigInt x;
        if(i!=0) mpz_urandomb(x.value,random,bits);
        s=acc::decimal(x.value);
      }
      exponents[i][0]="0"; // zero^0 is one, matching GMP/NTL semantics
    }
    exponents[1][1]="255"; exponents[1][2]="256";
    exponents[1][3]="1"; exponents[1][4]="65536";
    for(std::size_t i=0;i<exponents.size();++i) {
      const auto windows=(bits+7)/8;
      HostWindowOps ops(qString,bases,exponents[i],windows);
      for(std::size_t w=0;w<windows;++w) {
        ops.active=w; acc::math::bucketWindow(ops,w);
      }
      acc::math::combineWindows(ops,windows);
      require(acc::decimal(ops.result.value)==independentCommit(qString,bases,exponents[i]),
              "host window/GMP disagreement at "+std::to_string(bits)+" bits");
    }
    if(hardware) {
      acc::Options o; o.gpuMemoryMiB=8; o.device=testDevice;
      acc::Strings gi(bases.begin(),bases.end()-1),rs,out(3);
      acc::StringMatrix ms=exponents;
      for(auto &row:ms) { rs.push_back(row.back()); row.pop_back(); }
      auto gpu=acc::makeCudaCommit(qString,bases.back(),gi,o);
      gpu->compute(ms,rs,out,0,ms.size());
      for(std::size_t i=0;i<out.size();++i)
        require(out[i]==independentCommit(qString,bases,exponents[i]),"CUDA commitment/GMP disagreement");
    }
    std::cout<<"PASS "<<(hardware?"CUDA + host":"host model")<<" commitment "<<bits<<"-bit modulus\n";
  }
  gmp_randclear(random);
}
void schedulerTests() {
  acc::Options o; o.mode=acc::Mode::Hybrid; o.cpuThreads=3;
  o.gpuChunk=3; o.gpuMaxChunk=7; o.verbose=false;
  constexpr std::size_t count=67;
  std::vector<std::atomic<unsigned>> writes(count);
  for(auto &v:writes) v.store(0);
  std::atomic<bool> cpuEntered(false),gpuEntered(false);
  auto stats=acc::schedule("mock overlap",count,o,true,"",[&](std::size_t i) {
    cpuEntered=true; while(!gpuEntered.load()) std::this_thread::yield(); ++writes[i];
  },[&](std::size_t first,std::size_t size) {
    gpuEntered=true; while(!cpuEntered.load()) std::this_thread::yield();
    for(std::size_t i=first;i<first+size;++i) ++writes[i]; return 0.25;
  });
  for(auto &v:writes) require(v.load()==1,"scheduler duplicate/missing row");
  require(stats.gpuTasks>0 && stats.cpuTasks>0,"CPU and GPU must overlap");
  std::vector<int> output(count,-1);
  stats=acc::schedule("mock device failure",count,o,true,"",[&](std::size_t i) {
    output[i]=static_cast<int>(i);
  },[&](std::size_t first,std::size_t)->double {
    output[first]=-999; throw std::runtime_error("injected device error");
  });
  for(std::size_t i=0;i<count;++i) require(output[i]==static_cast<int>(i),"partial batch fallback");
  require(stats.gpuTasks==0 && stats.fallbackTasks>=3,"fallback statistics");
  o.mode=acc::Mode::CUDA;
  mustThrow([&] { acc::schedule("strict",2,o,false,"no device",[](std::size_t){},
                    [](std::size_t,std::size_t){ return 0.0; }); },"strict CUDA unavailable");
  mustThrow([&] { acc::schedule("strict",2,o,true,"",[](std::size_t){},
                    [](std::size_t,std::size_t)->double { throw std::runtime_error("device error"); }); },
            "strict CUDA error must propagate");
  o.mode=acc::Mode::Hybrid; o.verifyGPU=true;
  mustThrow([&] { acc::schedule("mismatch",5,o,true,"",[](std::size_t){},
                    [](std::size_t,std::size_t){ return 0.0; },
                    [](std::size_t){ throw std::runtime_error("bad result"); }); },
            "arithmetic mismatch must not fall back silently");
  o.verifyGPU=false; o.mode=acc::Mode::CPU;
  mustThrow([&] { acc::schedule("cpu error",9,o,false,"",[](std::size_t) {
                  throw std::runtime_error("CPU failure"); },{}); },"CPU exception propagation");
  stats=acc::schedule("empty",0,o,false,"",{},{});
  require(stats.cpuTasks+stats.gpuTasks==0,"empty task set");
  std::cout<<"PASS scheduler: overlap, exactly-once, partial failure, strict mode, exceptions\n";
}

std::string transcript(acc::Mode mode,int threads,bool verifyGPU) {
  acc::Options o; o.mode=mode; o.cpuThreads=threads; o.verifyGPU=verifyGPU; o.verbose=false;
  o.device=testDevice;
  acc::configure(o);
  // Fixed seed is confined to tests. The benchmark never installs it.
  auto crypto=std::make_shared<PaillierEncryption>(8,binary_t{2,7,1,8,2,8});
  auto q=crypto->getGroupQ(),p=crypto->getGroupP();
  auto circuit=std::make_shared<CBatchEnc>(crypto,3,3,4,15);
  auto gi=crypto->genGenerators(circuit->estimateGeneratorsRequired());
  Vec<ZZ> messages; messages.SetLength(3);
  messages[0]=0; messages[1]=1; messages[2]=ZZ(1)<<32;
  circuit->encrypt(messages);
  auto Cm=circuit->Cm,Cm_=circuit->Cm_,CRj=circuit->CRj;
  auto ljir=circuit->calculateLjir(); auto lj=circuit->calculateLj(ljir);
  circuit->wireUp(ljir,lj); circuit->run(ljir,lj);
  auto prover=circuit->generateProverAndRelease(gi);
  Vec<ZZ_p> commits,pc,proof;
  prover->commit(commits); prover->zkp->setCommits(commits);
  auto y=prover->zkp->calculateY(); prover->polyCommit(y,pc);
  prover->zkp->setPolyCommits(pc); auto x=prover->zkp->calculateX(); prover->prove(y,x,proof);
  std::ostringstream out;
  out<<Cm<<Cm_<<CRj<<lj<<commits<<pc<<proof<<y<<x;
  auto vc=std::make_shared<CBatchEnc>(crypto,3,3,4,15);
  vc->setCipher(Cm,Cm_,CRj); auto vljir=vc->calculateLjir();
  require(ljir==vljir,"ciphertext challenge mismatch");
  vc->wireUp(vljir,lj); auto verifier=vc->generateVerifierAndRelease(gi);
  verifier->setCommits(commits); verifier->setPolyCommits(pc);
  auto vy=verifier->calculateY(),vx=verifier->calculateX();
  require(y==vy && x==vx,"transcript challenges mismatch");
  require(verifier->verify(proof,vy,vx),"full proof verification failed");
  { ZZ_pPush push(p); proof[0]+=1; }
  require(!verifier->verify(proof,vy,vx),"tampered proof was accepted");
  return out.str();
}
void commitIntegrationTests() {
  acc::Options o; o.mode=acc::Mode::CPU; o.cpuThreads=3; o.verbose=false; acc::configure(o);
  ZZ q(607),p(101); ZZ_p::init(q);
  Vec<ZZ_p> gi; gi.SetLength(4);
  for(long j=0;j<4;++j) gi[j]=conv<ZZ_p>(j+2);
  auto g=conv<ZZ_p>(8);
  PolynomialCommitment scheme(q,p,g,gi);
  ZZ_p::init(p);
  Mat<ZZ_p> matrix; matrix.SetDims(5,4); Vec<ZZ_p> randomness,ret;
  randomness.SetLength(5);
  for(long i=0;i<5;++i) {
    randomness[i]=i*17;
    for(long j=0;j<4;++j) matrix[i][j]=i*j*9;
  }
  scheme.commit(matrix,randomness,ret);
  require(ZZ_p::modulus()==p,"NTL modulus was not restored");
  for(long i=0;i<5;++i) {
    acc::Strings bases,exps;
    for(long j=0;j<4;++j) { bases.push_back(textZZ(rep(gi[j]))); exps.push_back(textZZ(rep(matrix[i][j]))); }
    bases.push_back("8"); exps.push_back(textZZ(rep(randomness[i])));
    require(textZZ(rep(ret[i]))==independentCommit("607",bases,exps),"matrix commitment oracle");
  }
  Vec<ZZ_p> empty;
  require(scheme.commit(empty,randomness[0])==conv<ZZ_p>(1),"empty vector commitment");
  matrix.SetDims(3,0); randomness.SetLength(3); scheme.commit(matrix,randomness,ret);
  require(ret.length()==3,"zero-column matrix length");
  matrix.SetDims(0,4); randomness.SetLength(0); scheme.commit(matrix,randomness,ret);
  require(ret.length()==0,"zero-row matrix length");
  randomness.SetLength(2);
  mustThrow([&] { scheme.commit(matrix,randomness,ret); },"dimension mismatch");
  std::cout<<"PASS matrix integration, independent powm oracle, empty inputs, NTL context\n";
}
void hardwareEncryptionTests() {
  for(unsigned bits : {64u,128u,256u,512u,768u,1024u,1536u,2048u,3072u,4096u}) {
    acc::BigInt n,n2;
    mpz_set_ui(n.value,1); mpz_mul_2exp(n.value,n.value,bits); mpz_sub_ui(n.value,n.value,59);
    mpz_mul(n2.value,n.value,n.value);
    acc::Strings ms={"0","1","-1",acc::decimal(n.value)},rs={"1","2","3","17"},out(ms.size());
    acc::Options o; o.gpuMemoryMiB=8; o.device=testDevice;
    auto gpu=acc::makeCudaEncrypt(acc::decimal(n.value),acc::decimal(n2.value),o);
    gpu->compute(ms,rs,out,0,ms.size());
    for(std::size_t i=0;i<ms.size();++i) {
      acc::BigInt m(ms[i]),r(rs[i]),left,right;
      mpz_mul(left.value,m.value,n.value); mpz_add_ui(left.value,left.value,1);
      mpz_powm(right.value,r.value,n.value,n2.value);
      mpz_mul(left.value,left.value,right.value); mpz_mod(left.value,left.value,n2.value);
      require(out[i]==acc::decimal(left.value),"CUDA Paillier/GMP disagreement");
    }
    std::cout<<"PASS CUDA Paillier "<<bits<<"-bit n\n";
  }
}
}
int main(int argc,char **argv) {
  try {
    bool hardware=false;
    auto parseDevice=[](const std::string &s) {
      if(s.empty() || s.find_first_not_of("0123456789")!=std::string::npos)
        throw std::invalid_argument("invalid CUDA device index");
      auto value=std::stoull(s);
      if(value>std::numeric_limits<int>::max()) throw std::invalid_argument("invalid CUDA device index");
      return static_cast<int>(value);
    };
    if(const char *s=std::getenv("ZKP_CUDA_DEVICE")) testDevice=parseDevice(s);
    for(int i=1;i<argc;++i) {
      const std::string flag=argv[i];
      if(flag=="--cuda") hardware=true;
      else if(flag=="--device" && i+1<argc) testDevice=parseDevice(argv[++i]);
      else throw std::invalid_argument("usage: acceleration_tests [--cuda] [--device N]");
    }
    if(hardware) {
      std::string device;
      if(!acc::cudaAvailable(testDevice,device)) { std::cout<<"SKIP CUDA: "<<device<<'\n'; return 77; }
      std::cout<<"CUDA device: "<<device<<'\n';
    }
    packingTests(); schedulerTests(); windowTests(hardware); commitIntegrationTests();
    const auto serial=transcript(acc::Mode::CPU,1,false);
    require(serial==transcript(acc::Mode::CPU,3,false),"CPU transcript changed with thread count");
    if(hardware) {
      hardwareEncryptionTests();
      require(serial==transcript(acc::Mode::CUDA,3,true),"CUDA transcript differs from CPU");
      require(serial==transcript(acc::Mode::Hybrid,3,true),"hybrid transcript differs from CPU");
    } else {
      acc::Options fallback; fallback.mode=acc::Mode::Auto; fallback.cpuThreads=3;
      fallback.verbose=false;
      const auto stats=acc::schedule("no GPU",7,fallback,false,"no device",
          [](std::size_t){},{});
      require(stats.cpuTasks==7 && stats.gpuTasks==0,"CPU fallback counts");
    }
    std::cout<<"PASS full proof, fixed-randomness transcript equality, tamper rejection\n";
    return 0;
  } catch(const std::exception &e) { std::cerr<<"FAIL: "<<e.what()<<'\n'; return 1; }
}
