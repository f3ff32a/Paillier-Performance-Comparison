// Standalone conventional Paillier CUDA benchmark, adapted from cuhepy.
// Upstream: https://github.com/XTraceAI/cuhepy
// Revision: 6a8fc29419b2ff881d05171d7e40c6098dca4b96
// Source: src/cuhepy/paillier/_gpu_ext/paillier_GPU_client.cu
// Git blob: adc68b9d48ffae33398a26bf692d1bc629ff3390
// License: Apache-2.0; see the unchanged vendored upstream LICENSE.
// Changed for comparison: accept explicit integer m/r, share external n,
// remove Hamming padding and GPU random sampling, add strict checks/timing.
// The source's pow_mod_mem (lines 332-360) and encryption arithmetic
// (lines 522-546) are retained. This is an adapted operator benchmark,
// not the untouched author's Hamming/embedding application.

#include <cuda_runtime.h>
#include <gmp.h>
#include <cgbn/cgbn.h>

#include <chrono>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#ifndef KEY_BITS
#define KEY_BITS 1024
#endif
#ifndef CGBN_TPI
#define CGBN_TPI 32
#endif
#define PAILLIER_N_SQUARED_BITS (KEY_BITS * 4)
static constexpr int MODULUS_CAPACITY_BITS = KEY_BITS * 2;
static constexpr int LIMBS = (PAILLIER_N_SQUARED_BITS + 31) / 32;
static constexpr int INSTANCES_PER_BLOCK = 8;
static constexpr int THREADS_PER_BLOCK = INSTANCES_PER_BLOCK * CGBN_TPI;
static_assert(CGBN_TPI == 32, "retain upstream's default TPI=32");
static_assert(PAILLIER_N_SQUARED_BITS % 32 == 0, "whole limb capacity required");

using context_t = cgbn_context_t<CGBN_TPI>;
using env_t = cgbn_env_t<context_t, PAILLIER_N_SQUARED_BITS>;
using error_report_t = cgbn_error_report_t;
using mem_t = cgbn_mem_t<env_t::BITS>;
static_assert(sizeof(mem_t) == LIMBS * sizeof(uint32_t), "unexpected CGBN layout");
using Clock = std::chrono::steady_clock;

static void cuda_check(cudaError_t status, const char *where) {
  if (status != cudaSuccess)
    throw std::runtime_error(std::string(where) + ": " + cudaGetErrorName(status) +
                             " (" + cudaGetErrorString(status) + ")");
}
#define CUDA_CHECK(call) cuda_check((call), #call)

// Copied unchanged from upstream lines 332-360. In particular, the loop
// deliberately scans every storage bit, including leading zero bits.
static __device__ void pow_mod_mem(env_t &env,
                            typename env_t::cgbn_t &result,
                            const typename env_t::cgbn_t &base,
                            const mem_t *exp_mem,
                            const typename env_t::cgbn_t &mod_n2) {
  typename env_t::cgbn_t res, b;
  typename env_t::cgbn_wide_t wide;

  cgbn_set_ui32(env, res, 1);
  cgbn_set(env, b, base);

  const uint32_t *EW = reinterpret_cast<const uint32_t*>(exp_mem);

  // Iterate all LIMBS*32 bits, LSB to MSB
  for (int w = 0; w < LIMBS; ++w) {
    uint32_t x = EW[w];
    for (int k = 0; k < 32; ++k) {
      if (x & 1u) {
        cgbn_mul_wide(env, wide, res, b);
        cgbn_rem_wide(env, res, wide, mod_n2);
      }
      // b = b*b mod mod_n2
      cgbn_mul_wide(env, wide, b, b);
      cgbn_rem_wide(env, b, wide, mod_n2);
      x >>= 1;
    }
  }
  cgbn_set(env, result, res);
}

// Upstream encrypt_kernel with application packing/random sampling
// replaced by direct loads of externally supplied, validated m and r.
__global__ void encrypt_fixture_kernel(error_report_t *report,
                                      mem_t *M, mem_t *R,
                                      int count, mem_t *N,
                                      mem_t *N2, mem_t *out_ct) {
  int thread = blockIdx.x * blockDim.x + threadIdx.x;
  int instance = thread / CGBN_TPI;
  if (instance >= count) return;

  context_t context(cgbn_report_monitor, report, instance);
  env_t env(context);
  typename env_t::cgbn_t n, n2, m;
  cgbn_load(env, n, &N[0]);
  cgbn_load(env, n2, &N2[0]);
  cgbn_load(env, m, &M[instance]);

  // Retained from upstream lines 522-546: g=n+1 identity, r^n, product.
  typename env_t::cgbn_t a, prod;
  typename env_t::cgbn_wide_t wide;
  cgbn_mul_wide(env, wide, m, n);
  cgbn_rem_wide(env, prod, wide, n2);
  cgbn_add_ui32(env, a, prod, 1);
  if (cgbn_compare(env, a, n2) >= 0) {
    cgbn_sub(env, a, a, n2);
  }

  typename env_t::cgbn_t r, b;
  cgbn_load(env, r, &R[instance]);
  pow_mod_mem(env, b, r, N, n2);

  typename env_t::cgbn_t c;
  cgbn_mul_wide(env, wide, a, b);
  cgbn_rem_wide(env, c, wide, n2);
  cgbn_store(env, &out_ct[instance], c);
}

class Big {
public:
  mpz_t value;
  Big() { mpz_init(value); }
  Big(const Big &other) { mpz_init_set(value, other.value); }
  Big(Big &&other) noexcept { mpz_init(value); mpz_swap(value, other.value); }
  Big &operator=(const Big &other) { if (this != &other) mpz_set(value, other.value); return *this; }
  Big &operator=(Big &&other) noexcept { if (this != &other) mpz_swap(value, other.value); return *this; }
  ~Big() { mpz_clear(value); }
  static Big parse(const std::string &text, const std::string &label) {
    if (text.empty() || text.find_first_not_of("0123456789") != std::string::npos)
      throw std::runtime_error(label + ": expected unsigned decimal integer");
    Big out;
    if (mpz_set_str(out.value, text.c_str(), 10) != 0)
      throw std::runtime_error(label + ": invalid decimal integer");
    return out;
  }
  std::string decimal() const {
    // Caller owns the buffer, avoiding assumptions about GMP's allocator.
    std::vector<char> buffer(mpz_sizeinbase(value, 10) + 2);
    mpz_get_str(buffer.data(), 10, value);
    return std::string(buffer.data());
  }
};

struct Fixture {
  Big n, n2;
  std::vector<std::string> m, r;
  std::vector<Big> oracle;
  int n_bits = 0;
};

static Fixture read_fixture(const std::string &path) {
  std::ifstream stream(path);
  if (!stream) throw std::runtime_error("cannot open fixture: " + path);
  std::string line, ntext, extra;
  if (!std::getline(stream, line)) throw std::runtime_error("empty fixture");
  std::istringstream first(line);
  if (!(first >> ntext) || (first >> extra))
    throw std::runtime_error("fixture first line must contain only n");
  Fixture f;
  f.n = Big::parse(ntext, "n");
  if (mpz_cmp_ui(f.n.value, 3) < 0 || mpz_even_p(f.n.value))
    throw std::runtime_error("n must be an odd integer >= 3");
  f.n_bits = static_cast<int>(mpz_sizeinbase(f.n.value, 2));
  if (f.n_bits != MODULUS_CAPACITY_BITS && f.n_bits != MODULUS_CAPACITY_BITS - 1)
    throw std::runtime_error("fixture n has " + std::to_string(f.n_bits) +
      " bits; this build accepts " + std::to_string(MODULUS_CAPACITY_BITS - 1) +
      " or " + std::to_string(MODULUS_CAPACITY_BITS) + "-bit moduli");
  mpz_mul(f.n2.value, f.n.value, f.n.value);
  if (mpz_sizeinbase(f.n2.value, 2) > PAILLIER_N_SQUARED_BITS)
    throw std::runtime_error("n squared exceeds compile-time storage capacity");
  // Independent oracle uses general g^m, not the GPU's g=n+1 identity.
  Big divisor, g, a, b;
  mpz_add_ui(g.value, f.n.value, 1);
  std::size_t lineno = 1;
  while (std::getline(stream, line)) {
    ++lineno;
    std::istringstream row(line);
    std::string mtext, rtext;
    if (!(row >> mtext)) continue;
    if (!(row >> rtext) || (row >> extra))
      throw std::runtime_error("fixture line " + std::to_string(lineno) + " must contain m r");
    Big m = Big::parse(mtext, "m at line " + std::to_string(lineno));
    Big r = Big::parse(rtext, "r at line " + std::to_string(lineno));
    if (mpz_cmp(m.value, f.n.value) >= 0)
      throw std::runtime_error("fixture m must satisfy 0 <= m < n");
    if (mpz_cmp_ui(r.value, 1) < 0 || mpz_cmp(r.value, f.n.value) >= 0)
      throw std::runtime_error("fixture r must satisfy 1 <= r < n");
    mpz_gcd(divisor.value, r.value, f.n.value);
    if (mpz_cmp_ui(divisor.value, 1) != 0)
      throw std::runtime_error("fixture r must be coprime to n");
    // Canonical decimal strings form the common batch-input interface.
    // They will be parsed anew inside every timed operator invocation.
    f.m.push_back(m.decimal());
    f.r.push_back(r.decimal());
    Big oracle;
    mpz_powm(a.value, g.value, m.value, f.n2.value);
    mpz_powm(b.value, r.value, f.n.value, f.n2.value);
    mpz_mul(oracle.value, a.value, b.value);
    mpz_mod(oracle.value, oracle.value, f.n2.value);
    f.oracle.push_back(std::move(oracle));
    if (f.m.size() > static_cast<std::size_t>(std::numeric_limits<int>::max() / CGBN_TPI - 8))
      throw std::runtime_error("fixture too large for upstream int kernel indexing");
  }
  if (stream.bad()) throw std::runtime_error("error reading fixture: " + path);
  if (f.m.empty()) throw std::runtime_error("fixture contains no m/r rows");
  return f;
}

static mem_t pack(const Big &number) {
  mem_t out{};
  std::size_t words = 0;
  // f validates range/capacity before timed calls; no truncation is allowed.
  mpz_export(&out, &words, -1, sizeof(uint32_t), 0, 0, number.value);
  return out;
}

template <class T> class DeviceBuffer {
public:
  T *data = nullptr;
  explicit DeviceBuffer(std::size_t count) {
    if (count > std::numeric_limits<std::size_t>::max() / sizeof(T))
      throw std::runtime_error("CUDA allocation size overflow");
    CUDA_CHECK(cudaMalloc(reinterpret_cast<void **>(&data), count * sizeof(T)));
  }
  ~DeviceBuffer() { if (data) cudaFree(data); }
  void release() {
    T *pointer = data;
    data = nullptr;
    CUDA_CHECK(cudaFree(pointer));
  }
  DeviceBuffer(const DeviceBuffer &) = delete;
  DeviceBuffer &operator=(const DeviceBuffer &) = delete;
};

class Event {
public:
  cudaEvent_t data{};
  Event() { CUDA_CHECK(cudaEventCreate(&data)); }
  ~Event() { if (data) cudaEventDestroy(data); }
  Event(const Event &) = delete;
  Event &operator=(const Event &) = delete;
};

struct Result {
  std::vector<Big> ciphertext;
  std::vector<std::string> decimal;
  double wall_seconds = 0;
  float kernel_ms = 0;
};

class Benchmark {
  const Fixture &fixture;
  DeviceBuffer<mem_t> n, n2;
  Event start_event, stop_event;
public:
  explicit Benchmark(const Fixture &f)
    : fixture(f), n(1), n2(1) {
    const mem_t h_n = pack(f.n), h_n2 = pack(f.n2);
    CUDA_CHECK(cudaMemcpy(n.data, &h_n, sizeof(mem_t), cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(n2.data, &h_n2, sizeof(mem_t), cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaDeviceSynchronize());
  }
  Result run() {
    const auto begin = Clock::now();
    const std::size_t count = fixture.m.size(), bytes = count * sizeof(mem_t);
    Result result;
    {
      std::vector<mem_t> host_m(count), host_r(count), host_ct(count);
      for (std::size_t i = 0; i < count; ++i) {
        // Both adapters receive decimal batch inputs. Account for decimal
        // parsing/GMP temporaries as well as fixed-limb conversion here.
        host_m[i] = pack(Big::parse(fixture.m[i], "m"));
        host_r[i] = pack(Big::parse(fixture.r[i], "r"));
      }
      // The author's original encrypt() allocates/frees batch buffers in
      // each invocation. Retain this allocation policy rather than caching.
      DeviceBuffer<mem_t> m(count), r(count), ciphertext(count);
      // Match upstream's ordinary-device report allocation, rather than
      // introducing managed-memory page migrations to its kernel timing.
      DeviceBuffer<error_report_t> report(1);
      CUDA_CHECK(cudaMemset(report.data, 0, sizeof(error_report_t)));
      CUDA_CHECK(cudaMemcpy(m.data, host_m.data(), bytes, cudaMemcpyHostToDevice));
      CUDA_CHECK(cudaMemcpy(r.data, host_r.data(), bytes, cudaMemcpyHostToDevice));
      CUDA_CHECK(cudaEventRecord(start_event.data));
      const int blocks = (static_cast<int>(count) + INSTANCES_PER_BLOCK - 1) / INSTANCES_PER_BLOCK;
      encrypt_fixture_kernel<<<blocks, THREADS_PER_BLOCK>>>(report.data, m.data,
        r.data, static_cast<int>(count), n.data, n2.data, ciphertext.data);
      CUDA_CHECK(cudaPeekAtLastError());
      CUDA_CHECK(cudaEventRecord(stop_event.data));
      CUDA_CHECK(cudaDeviceSynchronize());
      CUDA_CHECK(cudaMemcpy(host_ct.data(), ciphertext.data, bytes, cudaMemcpyDeviceToHost));
      error_report_t host_report;
      CUDA_CHECK(cudaMemcpy(&host_report, report.data, sizeof(host_report), cudaMemcpyDeviceToHost));
      if (cgbn_error_report_check(&host_report)) {
        const char *description = cgbn_error_string(&host_report);
        throw std::runtime_error("CGBN instance " + std::to_string(host_report._instance) +
          ": " + (description ? description : "unknown error"));
      }
      // Device and host batch buffers are freed before ending wall timing.
      m.release();
      r.release();
      ciphertext.release();
      report.release();
      result.ciphertext.resize(count);
      result.decimal.resize(count);
      for (std::size_t i = 0; i < count; ++i) {
        mpz_import(result.ciphertext[i].value, LIMBS, -1, sizeof(uint32_t), 0, 0, &host_ct[i]);
        result.decimal[i] = result.ciphertext[i].decimal();
      }
    }
    result.wall_seconds = std::chrono::duration<double>(Clock::now() - begin).count();
    CUDA_CHECK(cudaEventElapsedTime(&result.kernel_ms, start_event.data, stop_event.data));
    for (std::size_t i = 0; i < count; ++i) {
      if (mpz_cmp(result.ciphertext[i].value, fixture.oracle[i].value) != 0)
        throw std::runtime_error("ciphertext mismatch at index " + std::to_string(i));
    }
    return result;
  }
};

struct Options { std::string input, output; int repeat = 5, warmup = 1, device = 0; };
static int parse_int(const std::string &text, const std::string &option) {
  std::size_t consumed = 0;
  int value;
  try { value = std::stoi(text, &consumed); }
  catch (const std::exception &) { throw std::runtime_error(option + ": invalid integer"); }
  if (consumed != text.size()) throw std::runtime_error(option + ": invalid integer");
  return value;
}
static Options parse_options(int argc, char **argv) {
  Options options;
  for (int i = 1; i < argc; ++i) {
    const std::string arg(argv[i]);
    if (i + 1 >= argc) throw std::runtime_error("missing value for " + arg);
    const std::string value(argv[++i]);
    if (arg == "--input") options.input = value;
    else if (arg == "--output") options.output = value;
    else if (arg == "--repeat") options.repeat = parse_int(value, arg);
    else if (arg == "--warmup") options.warmup = parse_int(value, arg);
    else if (arg == "--device") options.device = parse_int(value, arg);
    else throw std::runtime_error("unknown option: " + arg);
  }
  if (options.input.empty() || options.output.empty())
    throw std::runtime_error("--input and --output are required");
  if (options.repeat < 1 || options.repeat > 1000000 || options.warmup < 0 || options.warmup > 1000000)
    throw std::runtime_error("repeat must be in [1,1000000]; warmup in [0,1000000]");
  if (options.device < 0) throw std::runtime_error("device must be >= 0");
  if (options.input == options.output) throw std::runtime_error("input and output paths must differ");
  return options;
}

int main(int argc, char **argv) {
  try {
    if (argc == 2 && std::string(argv[1]) == "--help") {
      std::cout << "ref8_bench_" << MODULUS_CAPACITY_BITS
        << " --input fixture.txt --output ciphertexts.txt [--repeat 5] [--warmup 1] [--device 0]\n";
      return 0;
    }
    const Options options = parse_options(argc, argv);
    // Refusing any existing path also protects aliases/symlinks to input
    // or user source. Do this before fixture reads or CUDA work.
    if (std::filesystem::exists(options.output) || std::filesystem::is_symlink(options.output))
      throw std::runtime_error("output path already exists: " + options.output);
    const Fixture fixture = read_fixture(options.input);
    const auto setup_begin = Clock::now();
    CUDA_CHECK(cudaSetDevice(options.device));
    // Force context startup outside operator timing even with warmup=0.
    CUDA_CHECK(cudaFree(nullptr));
    Benchmark benchmark(fixture);
    const double setup_seconds = std::chrono::duration<double>(Clock::now() - setup_begin).count();
    // Separate first-call diagnostic precedes the explicit warmup count.
    // run() verifies it exactly as it verifies warmups/measured repeats.
    const double cold_first_call_seconds = benchmark.run().wall_seconds;
    for (int i = 0; i < options.warmup; ++i) benchmark.run();
    std::vector<std::string> last_output;
    for (int i = 0; i < options.repeat; ++i) {
      Result result = benchmark.run();
      last_output = std::move(result.decimal);
      std::cout << std::setprecision(12)
        << "{\"backend\":\"ref8_cuda\",\"repeat\":" << i
        << ",\"wall_seconds\":" << result.wall_seconds
        << ",\"kernel_ms\":" << result.kernel_ms
        << ",\"count\":" << fixture.m.size()
        << ",\"n_bits\":" << fixture.n_bits
        << ",\"storage_bits\":" << PAILLIER_N_SQUARED_BITS
        << ",\"gpu_tasks\":" << fixture.m.size()
        << ",\"cpu_tasks\":0,\"fallback_tasks\":0,\"verified\":true"
        << ",\"setup_seconds\":" << setup_seconds
        << ",\"cold_first_call_seconds\":" << cold_first_call_seconds << "}\n";
      if (!std::cout) throw std::runtime_error("cannot write JSON benchmark record");
    }
    std::ofstream output(options.output, std::ios::trunc);
    if (!output) throw std::runtime_error("cannot open output: " + options.output);
    for (const std::string &decimal : last_output) output << decimal << '\n';
    output.close();
    if (!output) throw std::runtime_error("error writing ciphertext output");
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "ref8_bench: " << error.what() << '\n';
    return 1;
  }
}
