#include "app/EndToEnd.hpp"
#include "app/acceleration/Backend.hpp"
#include <chrono>
#include <limits>

namespace {
std::size_t number(const std::string &s) {
  if (s.empty() || s.find_first_not_of("0123456789")!=std::string::npos)
    throw std::invalid_argument("expected an unsigned integer: "+s);
  auto v=std::stoull(s);
  if (v>std::numeric_limits<std::size_t>::max()) throw std::out_of_range(s);
  return static_cast<std::size_t>(v);
}
void usage() {
  std::cout<<"ZKP-Paillier-SP24-GPU2\n"
    <<"  --backend cpu|auto|cuda|hybrid   (default: ZKP_BACKEND or auto)\n"
    <<"  --bits N                       Paillier modulus bits, multiple of 8 (default 512)\n"
    <<"  --messages N                   message count (default 10)\n"
    <<"  --range-proofs N               range proof count (default 128)\n"
    <<"  --slot-bytes N                 slot width in bytes (default 4)\n"
    <<"  --batch N                      messages per batch (default 15)\n"
    <<"  --threads N                    CPU worker budget (default OMP_NUM_THREADS)\n"
    <<"  --device N                     CUDA device index (default 0)\n"
    <<"  --gpu-chunk N                  first GPU batch size (default 1)\n"
    <<"  --gpu-max-chunk N              adaptive batch limit (default 32)\n"
    <<"  --gpu-memory-mib N             explicit GPU buffer budget (default 256)\n"
    <<"  --verify-gpu                   compare all GPU results with GMP\n"
    <<"  --repeat N                     protocol runs (default 1)\n"
    <<"  --output PATH                  write legacy benchmark CSV (new file only)\n"
    <<"  --quiet                        suppress acceleration batch summaries\n"
    <<"  --help\n"
    <<"64-bit parameters are for functional tests only. No deterministic key seed is set.\n";
}
const char *csvHeader=
  "message count,message/batch,batch count,byte length(bit),byte length(byte),"
  "message size(byte),slot size(byte),slot/message,range proof count,cipher size,"
  "proof size,proof size/message,single encrypt circuit's N,single encrypt circuit's Q,"
  "batch encrypt circuit's N,batch encrypt circuit's Q,batch encrypt circuit's matrix m,"
  "batch encrypt circuit's matrix n,encryption time,circuit create time,value assign time,"
  "commit time,prove time,verify time,attach time-V(s),phase1 time-P(s),phase2 time-V(s),"
  "phase3 time-P(ms) - simple,phase3 time-P(ns) - simple,phase3 time-P(ms) - complex,"
  "phase3 time-P(ns) - complex,amo. cmt time (ms),amo. totalPrf time (ms),"
  "amo. vrf. time (ms),amo. proof cost (bits)";
}
int main(int argc,char **argv) {
  try {
    using namespace polyu::acceleration;
    auto config=optionsFromEnvironment();
    std::size_t bits=512,messages=10,ranges=128,slot=4,batch=15,repeat=1;
    std::string output;
    for(int i=1;i<argc;++i) {
      std::string flag=argv[i];
      if(flag=="--help") { usage(); return 0; }
      if(flag=="--verify-gpu") { config.verifyGPU=true; continue; }
      if(flag=="--quiet") { config.verbose=false; continue; }
      if(i+1==argc) throw std::invalid_argument("missing value for "+flag);
      std::string value=argv[++i];
      if(flag=="--backend") config.mode=parseMode(value);
      else if(flag=="--output") output=value;
      else {
        const auto v=number(value);
        if(flag=="--bits") bits=v;
        else if(flag=="--messages") messages=v;
        else if(flag=="--range-proofs") ranges=v;
        else if(flag=="--slot-bytes") slot=v;
        else if(flag=="--batch") batch=v;
        else if(flag=="--repeat") repeat=v;
        else if(flag=="--gpu-chunk") config.gpuChunk=v;
        else if(flag=="--gpu-max-chunk") config.gpuMaxChunk=v;
        else if(flag=="--gpu-memory-mib") config.gpuMemoryMiB=v;
        else if(flag=="--threads" || flag=="--device") {
          if(v>std::numeric_limits<int>::max()) throw std::out_of_range(flag);
          if(flag=="--threads") config.cpuThreads=static_cast<int>(v);
          else config.device=static_cast<int>(v);
        } else throw std::invalid_argument("unknown option: "+flag);
      }
    }
    validate(config);
    if(bits<64 || bits%8 || bits>32768 || messages==0 || ranges==0 ||
       batch==0 || repeat==0 || slot==0 || slot>bits/8 ||
       messages>1000000 || ranges>1000000 || batch>1000000 || repeat>1000000)
      throw std::invalid_argument("invalid benchmark parameters; see --help");
    configure(config);
    std::string device;
    const bool available=config.mode!=Mode::CPU && cudaAvailable(config.device,device);
    std::cout<<"project=ZKP-Paillier-SP24-GPU2 backend="<<modeName(config.mode)
             <<" bits="<<bits<<" messages="<<messages<<" verify_gpu="<<config.verifyGPU<<std::endl;
    if(config.mode!=Mode::CPU) std::cout<<"CUDA: "<<device<<std::endl;
    if(config.mode==Mode::CUDA && !available)
      throw std::runtime_error("CUDA was explicitly requested but is unavailable: "+device);
    std::ofstream fs;
    if(!output.empty()) {
      std::ifstream existing(output);
      if(existing.good()) throw std::invalid_argument("output file already exists: "+output);
      fs.open(output);
      if(!fs) throw std::runtime_error("cannot open output: "+output);
      fs<<csvHeader<<'\n';
    }
    auto crypto=std::make_shared<PaillierEncryption>(bits/8);
    for(std::size_t i=0;i<repeat;++i) {
      const auto start=std::chrono::steady_clock::now();
      polyu::end_to_end(crypto,messages,ranges,slot,batch,fs);
      const double seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
      std::cout<<"VERIFICATION=PASS run="<<i+1<<" WALL_SECONDS="<<seconds<<std::endl;
    }
    if(fs.is_open()) { fs.flush(); if(!fs) throw std::runtime_error("failed to write output CSV"); }
    return 0;
  } catch(const std::exception &e) {
    std::cerr<<"ERROR: "<<e.what()<<std::endl; return 1;
  }
}
