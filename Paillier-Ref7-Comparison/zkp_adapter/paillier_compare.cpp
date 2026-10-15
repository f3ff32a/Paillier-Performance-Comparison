// Common ordinary-Paillier encryption benchmark; no proof/protocol changes.
#include "app/acceleration/Backend.hpp"
#include "app/acceleration/CudaBackend.hpp"
#include "app/acceleration/GmpSupport.hpp"
#include "vendor/json/json.hpp"
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <iterator>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <utility>

namespace {
using Json = nlohmann::json;
using namespace polyu::acceleration;
using Clock = std::chrono::steady_clock;
struct Dataset {
  std::string id, n, n2;
  Strings messages, randomness, expected;
  std::size_t bits = 0, n2Bits = 0;
};
struct Arguments {
  std::string dataset, output, results;
  Mode backend = Mode::CPU;
  std::size_t repeat = 5, warmup = 1;
  int threads = 1;
  bool checkOnly = false;
};
struct Measurement {
  Strings ciphertexts;
  Statistics statistics;
  double hostMs = 0;
};
std::size_t unsignedNumber(const std::string &s, const std::string &name) {
  if (s.empty() || s.find_first_not_of("0123456789") != std::string::npos)
    throw std::invalid_argument(name + " must be an unsigned integer");
  const auto value = std::stoull(s);
  if (value > std::numeric_limits<std::size_t>::max())
    throw std::invalid_argument(name + " is too large");
  return static_cast<std::size_t>(value);
}
void usage() {
  std::cout << "Ordinary Paillier common-operator benchmark (no ZKP stages)\n"
      "  --dataset PATH                paillier-comparison-v1 JSON\n"
      "  --backend cpu|cuda|hybrid      default cpu; no successful fallback\n"
      "  --threads N                   positive total worker budget, default 1\n"
      "  --repeat N                    measured runs, default 5\n"
      "  --warmup N                    extra runs, default 1 (may be zero)\n"
      "  --output PATH                 new CSV file, required\n"
      "  --results-output PATH         optional new final-ciphertexts JSON\n"
      "  --check-only                  validate dataset; no output/backend execution\n"
      "Each run includes fresh session creation/free, conversion, transfer and\n"
      "scheduling; excludes file I/O, key/random generation and oracle checks.\n";
}
Arguments parse(int argc, char **argv) {
  Arguments a;
  std::vector<std::string> seen;
  for (int i = 1; i < argc; ++i) {
    const std::string flag(argv[i]);
    if (flag == "--help") { usage(); std::exit(0); }
    if (std::find(seen.begin(), seen.end(), flag) != seen.end())
      throw std::invalid_argument("duplicate option: " + flag);
    seen.push_back(flag);
    if (flag == "--check-only") { a.checkOnly = true; continue; }
    if (i + 1 == argc) throw std::invalid_argument("missing value for " + flag);
    const std::string value(argv[++i]);
    if (flag == "--dataset") a.dataset = value;
    else if (flag == "--output") a.output = value;
    else if (flag == "--results-output") a.results = value;
    else if (flag == "--backend") {
      if (value != "cpu" && value != "cuda" && value != "hybrid")
        throw std::invalid_argument("backend must be cpu, cuda or hybrid");
      a.backend = parseMode(value);
    } else if (flag == "--threads" || flag == "--repeat" || flag == "--warmup") {
      const auto v = unsignedNumber(value, flag);
      if (flag == "--threads") {
        if (v == 0 || v > 4096) throw std::invalid_argument("threads must be 1..4096");
        a.threads = static_cast<int>(v);
      } else if (flag == "--repeat") {
        if (v == 0 || v > 1000000) throw std::invalid_argument("repeat must be 1..1000000");
        a.repeat = v;
      } else {
        if (v > 1000000) throw std::invalid_argument("warmup must be 0..1000000");
        a.warmup = v;
      }
    } else throw std::invalid_argument("unknown option: " + flag);
  }
  if (a.dataset.empty() || (!a.checkOnly && a.output.empty()))
    throw std::invalid_argument("--dataset and --output are required");
  if (a.output == a.dataset || (!a.results.empty() &&
      (a.results == a.dataset || a.results == a.output)))
    throw std::invalid_argument("dataset, CSV and results paths must differ");
  return a;
}
std::string decimalString(const Json &value, const std::string &name) {
  if (!value.is_string()) throw std::invalid_argument(name + " must be a decimal string");
  const auto s = value.get<std::string>();
  if (s.empty() || s.size() > 20000 || s.find_first_not_of("0123456789") != std::string::npos ||
      (s.size() > 1 && s[0] == '0'))
    throw std::invalid_argument(name + " must be a canonical nonnegative decimal string");
  return s;
}
Strings decimalArray(const Json &value, const std::string &name) {
  if (!value.is_array() || value.empty() || value.size() > 1000000)
    throw std::invalid_argument(name + " must be a nonempty array of at most 1000000 items");
  Strings result;
  result.reserve(value.size());
  for (std::size_t i = 0; i < value.size(); ++i)
    result.push_back(decimalString(value.at(i), name + "[" + std::to_string(i) + "]"));
  return result;
}
std::string encryptGmp(const std::string &nString, const std::string &n2String,
    const std::string &message, const std::string &randomness) {
  // Same ordinary-encryption expression as the user's CBatchEnc.cpp GMP path.
  // GMP values are task-local; no NTL modulus state enters OpenMP workers.
  BigInt n(nString), n2(n2String), m(message), r(randomness), left, right, c;
  mpz_mul(left.value, n.value, m.value);
  mpz_add_ui(left.value, left.value, 1);
  mpz_mod(left.value, left.value, n2.value);
  mpz_mod(r.value, r.value, n2.value);
  mpz_powm(right.value, r.value, n.value, n2.value);
  mpz_mul(c.value, left.value, right.value);
  mpz_mod(c.value, c.value, n2.value);
  return decimal(c.value);
}
Dataset readDataset(const std::string &path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) throw std::runtime_error("cannot read dataset: " + path);
  input.seekg(0, std::ios::end);
  const auto size = input.tellg();
  if (size < 0 || size > 512LL * 1024 * 1024)
    throw std::invalid_argument("dataset must be at most 512 MiB");
  input.seekg(0);
  const std::string raw((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
  std::set<std::string> rootKeys;
  const auto j = Json::parse(raw,
      [&](int depth, Json::parse_event_t event, Json &value) {
        if (depth == 1 && event == Json::parse_event_t::key &&
            !rootKeys.insert(value.get<std::string>()).second)
          throw std::invalid_argument("duplicate root dataset key: " + value.get<std::string>());
        return true;
      }); // Reject trailing material and duplicate root keys.
  if (!j.is_object() || !j.at("schema").is_string() ||
      j.at("schema").get<std::string>() != "paillier-comparison-v1")
    throw std::invalid_argument("unsupported dataset schema");
  Dataset d;
  if (!j.at("dataset_id").is_string()) throw std::invalid_argument("dataset_id must be a string");
  d.id = j.at("dataset_id").get<std::string>();
  if (d.id.size() != 64 || d.id.find_first_not_of("0123456789abcdefABCDEF") != std::string::npos)
    throw std::invalid_argument("dataset_id must be a 64-character SHA-256 hex string");
  if (!(j.at("bits").is_number_integer() || j.at("bits").is_number_unsigned()))
    throw std::invalid_argument("bits must be an integer");
  const auto bits = j.at("bits").get<std::int64_t>();
  if (bits < 2 || bits > 32768) throw std::invalid_argument("bits must be 2..32768");
  d.bits = static_cast<std::size_t>(bits);
  d.n = decimalString(j.at("n"), "n");
  d.n2 = decimalString(j.at("n2"), "n2");
  d.messages = decimalArray(j.at("messages"), "messages");
  d.randomness = decimalArray(j.at("randomness"), "randomness");
  d.expected = decimalArray(j.at("expected"), "expected");
  if (d.messages.size() != d.randomness.size() || d.messages.size() != d.expected.size())
    throw std::invalid_argument("messages, randomness and expected lengths differ");
  BigInt n(d.n), n2(d.n2), squared, gcd;
  if (mpz_cmp_ui(n.value, 2) <= 0 || mpz_even_p(n.value))
    throw std::invalid_argument("n must be odd and greater than 2");
  if (mpz_sizeinbase(n.value, 2) != d.bits)
    throw std::invalid_argument("bits must equal the actual n bit length");
  mpz_mul(squared.value, n.value, n.value);
  if (mpz_cmp(squared.value, n2.value) != 0) throw std::invalid_argument("n2 must equal n*n");
  d.n2Bits = mpz_sizeinbase(n2.value, 2);
  for (std::size_t i = 0; i < d.messages.size(); ++i) {
    BigInt m(d.messages[i]), r(d.randomness[i]), expected(d.expected[i]);
    if (mpz_cmp(m.value, n.value) >= 0)
      throw std::invalid_argument("message is outside [0,n) at index " + std::to_string(i));
    if (mpz_sgn(r.value) <= 0 || mpz_cmp(r.value, n.value) >= 0)
      throw std::invalid_argument("randomness is outside (0,n) at index " + std::to_string(i));
    mpz_gcd(gcd.value, r.value, n.value);
    if (mpz_cmp_ui(gcd.value, 1) != 0)
      throw std::invalid_argument("randomness is not coprime to n at index " + std::to_string(i));
    if (mpz_sgn(expected.value) <= 0 || mpz_cmp(expected.value, n2.value) >= 0)
      throw std::invalid_argument("expected ciphertext is outside (0,n2) at index " + std::to_string(i));
    if (encryptGmp(d.n, d.n2, d.messages[i], d.randomness[i]) != d.expected[i])
      throw std::invalid_argument("expected ciphertext does not match GMP at index " + std::to_string(i));
  }
  return d;
}
void requireNew(const std::string &path) {
  if (path.empty()) return;
  std::ifstream existing(path, std::ios::binary);
  if (existing.good()) throw std::invalid_argument("refusing to overwrite existing output: " + path);
}
Measurement measure(const Dataset &d, const Options &options, bool available,
    const std::string &device) {
  Measurement result;
  const auto started = Clock::now();
  result.ciphertexts.resize(d.messages.size()); // allocation is deliberately timed
  {
    std::unique_ptr<CudaEncrypt> gpu;
    result.statistics = schedule("CompareEncrypt", d.messages.size(), options, available, device,
        [&](std::size_t i) {
          result.ciphertexts[i] = encryptGmp(d.n, d.n2, d.messages[i], d.randomness[i]);
        },
        [&](std::size_t first, std::size_t count) {
          if (!gpu) gpu = makeCudaEncrypt(d.n, d.n2, options);
          return gpu->compute(d.messages, d.randomness, result.ciphertexts, first, count);
        });
  } // Complete session destruction/free is included in hostMs.
  result.hostMs = std::chrono::duration<double, std::milli>(Clock::now() - started).count();
  // Correctness/fallback checks are outside the timing scope.
  const auto &s = result.statistics;
  if (s.fallbackTasks != 0 || !s.fallbackReason.empty())
    throw std::runtime_error("GPU fallback invalidates this comparison: " + s.fallbackReason);
  if (options.mode == Mode::CUDA && (s.gpuTasks != d.messages.size() || s.cpuTasks != 0))
    throw std::runtime_error("cuda must complete all tasks on the GPU");
  if (options.mode == Mode::Hybrid && s.gpuTasks == 0)
    throw std::runtime_error("hybrid must actually complete GPU tasks");
  if (result.ciphertexts != d.expected) {
    for (std::size_t i = 0; i < d.expected.size(); ++i)
      if (result.ciphertexts[i] != d.expected[i])
        throw std::runtime_error("ciphertext mismatch at index " + std::to_string(i));
  }
  return result;
}
void writeRow(std::ofstream &csv, const Dataset &d, const Arguments &a,
    const Measurement &m, std::size_t run, bool warmup) {
  csv << d.id << ",zkp-paillier," << modeName(a.backend) << ",encryption," << d.bits
      << ',' << d.messages.size() << ',' << d.messages.size() << ',' << run
      << ',' << (warmup ? 1 : 0) << ',' << std::setprecision(17) << m.hostMs
      << ',' << m.statistics.kernelMs << ",true,none," << a.threads
      << ',' << m.statistics.cpuTasks << ',' << m.statistics.gpuTasks
      << ',' << m.statistics.fallbackTasks << ',' << d.bits << ',' << d.n2Bits
      << ",host-inclusive-session-per-run\n";
  csv.flush();
  if (!csv) throw std::runtime_error("failed to write benchmark CSV");
}
} // namespace
int main(int argc, char **argv) {
  try {
    const auto a = parse(argc, argv);
    requireNew(a.output);
    requireNew(a.results);
    const auto d = readDataset(a.dataset); // reading and independent GMP oracle excluded from timer
    if (a.checkOnly) {
      std::cout << "DATASET=PASS dataset_id=" << d.id << " actual_n_bits=" << d.bits
          << " actual_n2_bits=" << d.n2Bits << " count=" << d.messages.size()
          << " dataset_id_recomputed_by_cpp=false" << std::endl;
      return 0;
    }
    auto options = optionsFromEnvironment();
    options.mode = a.backend;
    options.cpuThreads = a.threads;
    options.verifyGPU = false; // never time the CPU oracle, including inherited environment settings
    options.verbose = false;
    validate(options);
    bool available = false;
    std::string device;
    if (a.backend != Mode::CPU) {
      available = cudaAvailable(options.device, device);
      if (!available) throw std::runtime_error("GPU required for this backend: " + device);
    }
    std::ofstream csv(a.output, std::ios::binary);
    if (!csv) throw std::runtime_error("cannot create CSV: " + a.output);
    csv << "dataset_id,implementation,backend,mode,modulus_bits,logical_count,ciphertext_count,"
        "run,warmup,host_ms,kernel_ms,verified,packing,threads,cpu_tasks,gpu_tasks,fallback_tasks,"
        "actual_n_bits,actual_n2_bits,timing_scope\n";
    Measurement final;
    for (std::size_t i = 0; i < a.warmup + a.repeat; ++i) {
      const bool warmup = i < a.warmup;
      const std::size_t run = warmup ? i + 1 : i - a.warmup + 1;
      auto measurement = measure(d, options, available, device);
      writeRow(csv, d, a, measurement, run, warmup);
      std::cout << "VERIFICATION=PASS dataset_id=" << d.id << " backend=" << modeName(a.backend)
          << " run=" << run << " warmup=" << (warmup ? 1 : 0)
          << " host_ms=" << std::setprecision(17) << measurement.hostMs
          << " kernel_ms=" << measurement.statistics.kernelMs
          << " cpu_tasks=" << measurement.statistics.cpuTasks
          << " gpu_tasks=" << measurement.statistics.gpuTasks << std::endl;
      final = std::move(measurement);
    }
    if (!a.results.empty()) {
      // The CSV now exists; checking again also rejects equivalent path aliases
      // such as --output a.csv --results-output ./a.csv without C++17 filesystem.
      requireNew(a.results);
      Json j;
      j["schema"] = "paillier-comparison-results-v1";
      j["dataset_id"] = d.id;
      j["dataset_id_recomputed_by_cpp"] = false;
      j["implementation"] = "zkp-paillier";
      j["backend"] = modeName(a.backend);
      j["threads"] = a.threads;
      j["mode"] = "encryption";
      j["packing"] = "none";
      j["actual_n_bits"] = d.bits;
      j["actual_n2_bits"] = d.n2Bits;
      j["logical_count"] = d.messages.size();
      j["ciphertext_count"] = d.messages.size();
      j["timing_scope"] = "host-inclusive-session-per-run";
      j["cpu_tasks"] = final.statistics.cpuTasks;
      j["gpu_tasks"] = final.statistics.gpuTasks;
      j["fallback_tasks"] = final.statistics.fallbackTasks;
      j["verified"] = true;
      j["ciphertexts"] = final.ciphertexts;
      std::ofstream out(a.results, std::ios::binary);
      if (!out) throw std::runtime_error("cannot create results JSON: " + a.results);
      out << j.dump(2) << '\n';
      out.flush();
      if (!out) throw std::runtime_error("failed to write results JSON");
    }
    std::cout << "PASS implementation=zkp-paillier operation=encryption packing=none" << std::endl;
    return 0;
  } catch (const std::exception &e) {
    std::cerr << "ERROR: " << e.what() << std::endl;
    return 1;
  }
}
