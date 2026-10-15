// Operator adapter for the supplied ZKP-Paillier-SP24-GPU4060 project.
// Original sources remain unchanged. The CPU task below extracts the private
// paillierEncryptGMP function in src/cpp/app/CBatchEnc.cpp, lines 33-67.
#include "Backend.hpp"
#include "CudaBackend.hpp"
#include "GmpSupport.hpp"
#ifdef ZKP_HAS_CUDA
#include <cuda_runtime.h>
#endif
#include <chrono>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>

namespace acc = polyu::acceleration;
using Clock = std::chrono::steady_clock;

namespace {
double seconds(Clock::time_point start) {
  return std::chrono::duration<double>(Clock::now() - start).count();
}
std::size_t number(const std::string &s, const std::string &flag) {
  if (s.empty() || s.find_first_not_of("0123456789") != std::string::npos)
    throw std::invalid_argument(flag + " requires an unsigned integer");
  const auto n = std::stoull(s);
  if (n > std::numeric_limits<std::size_t>::max())
    throw std::out_of_range(flag + " is too large");
  return static_cast<std::size_t>(n);
}
bool decimalToken(const std::string &s) {
  return !s.empty() && s.find_first_not_of("0123456789") == std::string::npos;
}
struct Fixture {
  std::string n, n2;
  acc::Strings messages, randomness, oracle;
  std::size_t nBits = 0;
  unsigned storageBits = 0;
};

// Independent oracle uses the general g^m expression rather than the CPU/GPU
// operator's (1+n*m) shortcut. Fixture generation and this work are never timed.
std::string oracleCipher(mpz_srcptr n, mpz_srcptr n2, mpz_srcptr m, mpz_srcptr r) {
  acc::BigInt g, left, right, out;
  mpz_add_ui(g.value, n, 1);
  mpz_powm(left.value, g.value, m, n2);
  mpz_powm(right.value, r, n, n2);
  mpz_mul(out.value, left.value, right.value);
  mpz_mod(out.value, out.value, n2);
  return acc::decimal(out.value);
}
Fixture readFixture(const std::string &path) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("cannot open fixture: " + path);
  Fixture f;
  std::string line, token, extra;
  if (!std::getline(input, line)) throw std::invalid_argument("empty fixture");
  std::istringstream header(line);
  if (!(header >> token) || (header >> extra) || !decimalToken(token))
    throw std::invalid_argument("fixture first line must contain only decimal n");
  acc::BigInt n(token), n2;
  if (mpz_cmp_ui(n.value, 1) <= 0 || mpz_even_p(n.value))
    throw std::invalid_argument("Paillier n must be odd and greater than 1");
  mpz_mul(n2.value, n.value, n.value);
  f.n = acc::decimal(n.value);
  f.n2 = acc::decimal(n2.value);
  f.nBits = mpz_sizeinbase(n.value, 2);
  f.storageBits = acc::storageBits(n2.value);
  std::size_t lineNo = 1;
  while (std::getline(input, line)) {
    ++lineNo;
    std::istringstream pair(line);
    std::string mToken, rToken;
    if (!(pair >> mToken)) continue;
    if (!(pair >> rToken) || (pair >> extra) ||
        !decimalToken(mToken) || !decimalToken(rToken))
      throw std::invalid_argument("fixture line " + std::to_string(lineNo) +
                                  " must contain decimal m and r");
    acc::BigInt m(mToken), r(rToken), gcd;
    if (mpz_cmp(m.value, n.value) >= 0)
      throw std::invalid_argument("message outside [0,n) at fixture line " +
                                  std::to_string(lineNo));
    mpz_gcd(gcd.value, r.value, n.value);
    if (mpz_cmp_ui(r.value, 1) < 0 || mpz_cmp(r.value, n.value) >= 0 ||
        mpz_cmp_ui(gcd.value, 1) != 0)
      throw std::invalid_argument("r must satisfy 0<r<n and gcd(r,n)=1 at line " +
                                  std::to_string(lineNo));
    f.messages.push_back(acc::decimal(m.value));
    f.randomness.push_back(acc::decimal(r.value));
    f.oracle.push_back(oracleCipher(n.value, n2.value, m.value, r.value));
  }
  if (input.bad()) throw std::runtime_error("failed while reading fixture");
  if (f.messages.empty()) throw std::invalid_argument("fixture has no messages");
  return f;
}

// Same decimal parse, per-task GMP temporaries and arithmetic as the supplied
// project's private CPU function. No key, message, or r generation occurs here.
std::string userCpuCipher(const std::string &nDec, const std::string &n2Dec,
                          const std::string &mDec, const std::string &rDec) {
  mpz_t n, n2, m, r, left, right, c;
  mpz_inits(n, n2, m, r, left, right, c, NULL);
  mpz_set_str(n, nDec.c_str(), 10);
  mpz_set_str(n2, n2Dec.c_str(), 10);
  mpz_set_str(m, mDec.c_str(), 10);
  mpz_set_str(r, rDec.c_str(), 10);
  mpz_mul(left, n, m);
  mpz_add_ui(left, left, 1);
  mpz_mod(left, left, n2);
  mpz_mod(r, r, n2);
  mpz_powm(right, r, n, n2);
  mpz_mul(c, left, right);
  mpz_mod(c, c, n2);
  char *raw = mpz_get_str(NULL, 10, c);
  std::string result(raw);
  free(raw);
  mpz_clears(n, n2, m, r, left, right, c, NULL);
  return result;
}
void selectDevice(int device) {
#ifdef ZKP_HAS_CUDA
  const auto status = cudaSetDevice(device);
  if (status != cudaSuccess)
    throw std::runtime_error(std::string("cudaSetDevice: ") + cudaGetErrorString(status));
#else
  (void)device;
  throw std::runtime_error("CUDA was not compiled");
#endif
}
void verify(const Fixture &f, const acc::Strings &out, const acc::Statistics &s,
            acc::Mode mode) {
  if (s.cpuTasks + s.gpuTasks != f.messages.size())
    throw std::runtime_error("operator task count mismatch");
  if (mode != acc::Mode::CPU &&
      (s.gpuTasks == 0 || s.fallbackTasks != 0 || !s.fallbackReason.empty()))
    throw std::runtime_error("refusing GPU measurement: no GPU work or fallback: " +
                             s.fallbackReason);
  if (mode == acc::Mode::CUDA &&
      (s.cpuTasks != 0 || s.gpuTasks != f.messages.size()))
    throw std::runtime_error("strict CUDA did not complete all ciphertexts");
  for (std::size_t i = 0; i < out.size(); ++i)
    if (out[i] != f.oracle[i])
      throw std::runtime_error("GMP ciphertext mismatch at index " + std::to_string(i));
}
void usage() {
  std::cout << "user_bench --input fixture.txt --output ciphertexts.txt "
            << "--backend cpu|cuda|hybrid [--repeat N] [--warmup N] [--device D] "
            << "[--threads T] [--gpu-chunk N] [--gpu-max-chunk N]\n"
            << "Fixture: decimal n on line 1, then decimal m r pairs. "
            << "A separate cold diagnostic call precedes warmup and measurements.\n";
}
} // namespace

int main(int argc, char **argv) {
  try {
    std::string inputPath, outputPath, backend = "cpu";
    std::size_t repeat = 3, warmup = 1;
    acc::Options config;
    config.mode = acc::Mode::CPU;
    config.cpuThreads = 1;
    config.verbose = false;
    config.verifyGPU = false;
    for (int i = 1; i < argc; ++i) {
      const std::string flag = argv[i];
      if (flag == "--help") { usage(); return 0; }
      if (i + 1 == argc) throw std::invalid_argument("missing value for " + flag);
      const std::string value = argv[++i];
      if (flag == "--input") inputPath = value;
      else if (flag == "--output") outputPath = value;
      else if (flag == "--backend") {
        if (value != "cpu" && value != "cuda" && value != "hybrid")
          throw std::invalid_argument("backend must be cpu, cuda or hybrid");
        backend = value;
        config.mode = acc::parseMode(value);
      } else {
        const auto n = number(value, flag);
        if (flag == "--repeat") repeat = n;
        else if (flag == "--warmup") warmup = n;
        else if (flag == "--gpu-chunk") config.gpuChunk = n;
        else if (flag == "--gpu-max-chunk") config.gpuMaxChunk = n;
        else if (flag == "--threads" || flag == "--device") {
          if (n > static_cast<std::size_t>(std::numeric_limits<int>::max()))
            throw std::out_of_range(flag + " is too large");
          if (flag == "--threads") config.cpuThreads = static_cast<int>(n);
          else config.device = static_cast<int>(n);
        } else throw std::invalid_argument("unknown option: " + flag);
      }
    }
    if (inputPath.empty() || outputPath.empty() || repeat == 0 || repeat > 1000000 ||
        warmup > 1000000 || config.cpuThreads == 0)
      throw std::invalid_argument("input/output, positive repeat and positive threads are required");
    acc::validate(config);
    // Refuse an existing destination; fixture and original source cannot be overwritten.
    if (std::filesystem::exists(outputPath) || std::filesystem::is_symlink(outputPath))
      throw std::invalid_argument("output file already exists: " + outputPath);
    const auto f = readFixture(inputPath);
    acc::Strings out(f.messages.size());
    std::string description;
    const bool available = config.mode != acc::Mode::CPU &&
                           acc::cudaAvailable(config.device, description);
    if (config.mode != acc::Mode::CPU && !available)
      throw std::runtime_error("CUDA required for " + backend + ": " + description);
    std::unique_ptr<acc::CudaEncrypt> gpu;
    double setupSeconds = 0;
    if (available) {
      // Session/context creation, constant upload and initial allocation happen
      // before any operator timer. Create/use on a GPU worker as required by API.
      const auto start = Clock::now();
      std::exception_ptr failure;
      std::thread preparer([&] {
        try { gpu = acc::makeCudaEncrypt(f.n, f.n2, config); }
        catch (...) { failure = std::current_exception(); }
      });
      preparer.join();
      setupSeconds = seconds(start);
      if (failure) std::rethrow_exception(failure);
    }
    auto compute = [&] {
      return acc::schedule("Encrypt", f.messages.size(), config, available, description,
        [&](std::size_t i) {
          out[i] = userCpuCipher(f.n, f.n2, f.messages[i], f.randomness[i]);
        },
        [&](std::size_t first, std::size_t count) {
          // schedule creates a fresh worker for each call; CUDA device state is
          // thread local. This selects the session's existing device/context.
          selectDevice(config.device);
          return gpu->compute(f.messages, f.randomness, out, first, count);
        });
    };
    const auto coldStart = Clock::now();
    auto stats = compute();
    const auto coldFirstCallSeconds = seconds(coldStart);
    verify(f, out, stats, config.mode);
    for (std::size_t i = 0; i < warmup; ++i) {
      stats = compute();
      verify(f, out, stats, config.mode);
    }
    std::cout << std::setprecision(17);
    for (std::size_t i = 0; i < repeat; ++i) {
      const auto start = Clock::now();
      stats = compute();
      const auto wallSeconds = seconds(start);
      // Oracle work, validation, JSON, and file I/O are outside the timed area.
      verify(f, out, stats, config.mode);
      std::cout << "{\"backend\":\"user_" << backend << "\",\"repeat\":" << i
        << ",\"wall_seconds\":" << wallSeconds << ",\"kernel_ms\":" << stats.kernelMs
        << ",\"count\":" << f.messages.size() << ",\"n_bits\":" << f.nBits
        << ",\"storage_bits\":" << f.storageBits << ",\"gpu_tasks\":" << stats.gpuTasks
        << ",\"cpu_tasks\":" << stats.cpuTasks << ",\"fallback_tasks\":" << stats.fallbackTasks
        << ",\"setup_seconds\":" << setupSeconds
        << ",\"cold_first_call_seconds\":" << coldFirstCallSeconds
        << ",\"verified\":true,\"verification\":\"PASS\"}\n";
      if (!std::cout) throw std::runtime_error("cannot write JSON benchmark record");
    }
    std::ofstream output(outputPath);
    if (!output) throw std::runtime_error("cannot create ciphertext output: " + outputPath);
    for (const auto &cipher : out) output << cipher << '\n';
    output.flush();
    if (!output) throw std::runtime_error("failed to write ciphertext output");
    return 0;
  } catch (const std::exception &e) {
    std::cerr << "ERROR: " << e.what() << '\n';
    return 1;
  }
}
