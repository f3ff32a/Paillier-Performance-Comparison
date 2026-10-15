#include "app/EndToEnd.hpp"
#include <NTL/BasicThreadPool.h>
#include <NTL/version.h>
#include <chrono>
#include <algorithm>
#include <filesystem>
#include <iomanip>
#include <limits>
#include <cstdlib>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <unistd.h>

#ifdef _OPENMP
#error "This is a serial baseline: compiling with OpenMP is prohibited."
#endif

namespace {
size_t number(const std::string &s) {
  if (s.empty() || s.find_first_not_of("0123456789") != std::string::npos)
    throw std::invalid_argument("expected an unsigned integer: " + s);
  const auto v = std::stoull(s);
  if (v > std::numeric_limits<size_t>::max()) throw std::out_of_range(s);
  return static_cast<size_t>(v);
}
void usage() {
  std::cout << "ZKP-Paillier-SP24-Serial-Ubuntu26\n"
    "Original serial algorithm; OpenMP=OFF CUDA=OFF NTL threads=1.\n"
    "  --bits N          modulus size in BITS, multiple of 8 (default 512)\n"
    "  --mc N            messages (alias --messages; default 1)\n"
    "  --range N         range proofs (alias --range-proofs; default 128)\n"
    "  --slot N          slot bytes (alias --slot-bytes; default 4)\n"
    "  --batch N         messages per batch (default 15)\n"
    "  --repeat N        protocol repetitions using one generated key (default 1)\n"
    "  --csv PATH        new CSV file (alias --output; never overwrites)\n"
    "  --seed DECIMAL    optional deterministic experimental seed\n"
    "  --threads 1       compatibility option; any other value is rejected\n"
    "  --backend serial  compatibility option; also accepts cpu\n"
    "  --help\n"
    "WALL_SECONDS wraps end_to_end, excludes key generation.\n"
    "64-bit configurations and deterministic seeds are for research tests only.\n";
}
const char *header =
  "message count,message/batch,batch count,byte length(bit),byte length(byte),"
  "message size(byte),slot size(byte),slot/message,range proof count,cipher size,"
  "proof size,proof size/message,single encrypt circuit's N,single encrypt circuit's Q,"
  "batch encrypt circuit's N,batch encrypt circuit's Q,batch encrypt circuit's matrix m,"
  "batch encrypt circuit's matrix n,encryption time,circuit create time,value assign time,"
  "commit time,prove time,verify time,attach time-V(s),phase1 time-P(s),phase2 time-V(s),"
  "phase3 time-P(ms) - simple,phase3 time-P(ns) - simple,phase3 time-P(ms) - complex,"
  "phase3 time-P(ns) - complex,amo. cmt time (ms),amo. totalPrf time (ms),"
  "amo. vrf. time (ms),amo. proof cost (bits),wall_seconds,verification,run_index,"
  "keygen_seconds,seed,execution_backend,ntl_threads";
}

int main(int argc, char **argv) {
  try {
    size_t bits=512, mc=1, ranges=128, slot=4, batch=15, repeat=1;
    std::string csv, seed;
    for (int i=1; i<argc; ++i) {
      const std::string flag=argv[i];
      if (flag=="--help" || flag=="-h") { usage(); return 0; }
      if (i+1==argc) throw std::invalid_argument("missing value for " + flag);
      const std::string value=argv[++i];
      if (flag=="--csv" || flag=="--output") csv=value;
      else if (flag=="--seed") {
        if (value.empty() || value.find_first_not_of("0123456789")!=std::string::npos || value.size()>4096)
          throw std::invalid_argument("seed must be a nonnegative decimal integer (up to 4096 digits)");
        seed=value;
      } else if (flag=="--backend") {
        if (value!="serial" && value!="cpu") throw std::invalid_argument("only the serial CPU backend is available");
      } else {
        const size_t n=number(value);
        if (flag=="--bits") bits=n;
        else if (flag=="--mc" || flag=="--messages") mc=n;
        else if (flag=="--range" || flag=="--range-proofs") ranges=n;
        else if (flag=="--slot" || flag=="--slot-bytes") slot=n;
        else if (flag=="--batch") batch=n;
        else if (flag=="--repeat") repeat=n;
        else if (flag=="--threads") {
          if (n!=1) throw std::invalid_argument("serial baseline requires --threads 1");
        } else throw std::invalid_argument("unknown option: " + flag);
      }
    }
    if (bits<64 || bits>32768 || bits%8 || !mc || !ranges || !slot ||
        slot>bits/8 || !batch || !repeat || mc>1000000 || ranges>1000000 ||
        batch>1000000 || repeat>1000000)
      throw std::invalid_argument("invalid benchmark parameters; see --help");
    if ((NTL::power(NTL::ZZ(2), static_cast<long>(slot*8))-1)/NTL::conv<NTL::ZZ>(mc) == 0)
      throw std::invalid_argument("message count exceeds the weight capacity of slot bytes");

    // NTL can use its own thread pool even without application-level OpenMP.
    NTL::SetNumThreads(1);
    if (NTL::AvailableThreads()!=1) throw std::runtime_error("NTL is not running with one available thread");
    std::cout << std::setprecision(12)
      << "project=ZKP-Paillier-SP24-Serial-Ubuntu26 backend=serial CPU_THREADS=1"
      << " OpenMP=OFF CUDA=OFF NTL_THREADS=" << NTL::AvailableThreads()
      << " NTL_VERSION=" << NTL_VERSION << " bits=" << bits << " messages=" << mc
      << " repeat=" << repeat << " seed=" << (seed.empty()?"random":seed) << std::endl;

    std::ofstream fs;
    if (!csv.empty()) {
      const std::filesystem::path path(csv);
      if (path.has_parent_path()) std::filesystem::create_directories(path.parent_path());
      const int fd=::open(csv.c_str(),O_WRONLY|O_CREAT|O_EXCL,0644);
      if (fd<0) throw std::runtime_error("cannot create new CSV " + csv + ": " + std::strerror(errno));
      ::close(fd);
      fs.open(csv, std::ios::app);
      if (!fs) throw std::runtime_error("cannot open CSV: " + csv);
      fs << std::setprecision(17) << header << '\n'; fs.flush();
      if (!fs) throw std::runtime_error("failed to write CSV header");
    }
    if (!seed.empty()) NTL::SetSeed(NTL::conv<NTL::ZZ>(seed.c_str()));
    const auto key_start=std::chrono::steady_clock::now();
    auto crypto=std::make_shared<PaillierEncryption>(bits/8);
    const double key_seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-key_start).count();
    std::cout << "KEYGEN_SECONDS=" << key_seconds << " KEYGEN_INCLUDED_IN_WALL=0" << std::endl;

    for (size_t run=1; run<=repeat; ++run) {
      std::ostringstream row;
      row << std::setprecision(17);
      const auto start=std::chrono::steady_clock::now();
      // Verification is checked before EndToEnd writes any measurements.
      // Writing/flushing the original 35-column prefix stays inside its wall
      // boundary, as in the older CPU/GPU benchmark. Only the new metadata
      // tail and PASS line are appended outside the measurement.
      std::ostream &measurements=fs.is_open()?static_cast<std::ostream &>(fs):static_cast<std::ostream &>(row);
      polyu::end_to_end(crypto,mc,ranges,slot,batch,measurements,false);
      const double seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
      if (!fs.is_open()) {
        const std::string text=row.str();
        if (std::count(text.begin(),text.end(),',')!=34)
          throw std::runtime_error("unexpected legacy benchmark row schema");
      }
      if (fs.is_open()) {
        fs << ',' << std::setprecision(17) << seconds << ",PASS," << run
           << ',' << key_seconds << ',' << seed << ",serial,1\n";
        fs.flush();
        if (!fs) throw std::runtime_error("failed to write benchmark CSV");
      }
      std::cout << "VERIFICATION=PASS run=" << run << " WALL_SECONDS=" << seconds << std::endl;
    }
    return 0;
  } catch (const std::bad_alloc &) {
    std::cerr << "VERIFICATION=FAIL ERROR: insufficient memory for this configuration" << std::endl;
    return 2;
  } catch (const std::exception &e) {
    std::cerr << "VERIFICATION=FAIL ERROR: " << e.what() << std::endl;
    return 1;
  }
}
