#include "Backend.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <thread>
#ifdef _OPENMP
#include <omp.h>
#endif

namespace polyu { namespace acceleration {
namespace {
using Clock = std::chrono::steady_clock;
double milliseconds(Clock::time_point start) {
  return std::chrono::duration<double, std::milli>(Clock::now()-start).count();
}
std::mutex configMutex;
Options &configured() { static Options value = optionsFromEnvironment(); return value; }
thread_local Statistics previous;
std::size_t integerEnv(const char *name, std::size_t fallback) {
  const char *value = std::getenv(name);
  if (!value) return fallback;
  std::string s(value);
  if (s.empty() || s.find_first_not_of("0123456789") != std::string::npos)
    throw std::invalid_argument(std::string(name)+" must be a nonnegative integer");
  const auto n = std::stoull(s);
  if (n > std::numeric_limits<std::size_t>::max()) throw std::out_of_range(name);
  return static_cast<std::size_t>(n);
}
bool boolEnv(const char *name, bool fallback) {
  const auto n = integerEnv(name, fallback ? 1 : 0);
  if (n > 1) throw std::invalid_argument(std::string(name)+" must be 0 or 1");
  return n != 0;
}
}
Mode parseMode(const std::string &s) {
  if (s=="cpu") return Mode::CPU;
  if (s=="auto") return Mode::Auto;
  if (s=="cuda" || s=="gpu") return Mode::CUDA;
  if (s=="hybrid") return Mode::Hybrid;
  throw std::invalid_argument("backend must be cpu, auto, cuda or hybrid");
}
const char *modeName(Mode m) {
  switch(m) { case Mode::CPU:return "cpu"; case Mode::CUDA:return "cuda";
              case Mode::Hybrid:return "hybrid"; default:return "auto"; }
}
void validate(const Options &o) {
  if (o.device<0 || o.cpuThreads<0 || o.cpuThreads>4096 || o.gpuChunk==0 ||
      o.gpuMaxChunk<o.gpuChunk || o.gpuMaxChunk>65536 ||
      o.gpuMemoryMiB<8 || o.gpuMemoryMiB>65536 ||
      !std::isfinite(o.targetBatchMs) || o.targetBatchMs<=0)
    throw std::invalid_argument("invalid acceleration configuration");
}
Options optionsFromEnvironment() {
  Options o;
  if (const char *v=std::getenv("ZKP_BACKEND")) o.mode=parseMode(v);
  auto device=integerEnv("ZKP_CUDA_DEVICE",0), threads=integerEnv("ZKP_CPU_THREADS",0);
  if (device>std::numeric_limits<int>::max() || threads>4096)
    throw std::invalid_argument("device or CPU thread count is out of range");
  o.device=static_cast<int>(device); o.cpuThreads=static_cast<int>(threads);
  o.gpuChunk=integerEnv("ZKP_GPU_CHUNK",1);
  o.gpuMaxChunk=integerEnv("ZKP_GPU_MAX_CHUNK",32);
  o.gpuMemoryMiB=integerEnv("ZKP_GPU_MEMORY_MIB",256);
  o.verifyGPU=boolEnv("ZKP_GPU_VERIFY",false);
  o.verbose=boolEnv("ZKP_ACCEL_VERBOSE",true);
  validate(o); return o;
}
Options options() { std::lock_guard<std::mutex> lock(configMutex); return configured(); }
void configure(const Options &o) {
  validate(o); std::lock_guard<std::mutex> lock(configMutex); configured()=o;
}
Statistics lastStatistics() { return previous; }

Statistics schedule(const std::string &stage, std::size_t count,
    const Options &o, bool available, const std::string &reason,
    const CpuTask &cpu, const GpuBatch &gpu, const CheckTask &check) {
  validate(o);
  Statistics result;
  const auto start=Clock::now();
  const bool useGPU=o.mode!=Mode::CPU && available && count>0;
  if (!available && o.mode==Mode::CUDA && count>0)
    throw std::runtime_error(stage+": CUDA required: "+reason);
  if (o.verifyGPU && useGPU && !check)
    throw std::invalid_argument("GPU verification requested without an oracle");
  if (!available && o.mode!=Mode::CPU) result.fallbackReason=reason;

  std::atomic<std::size_t> next(0), cpuCount(0);
  std::atomic<bool> stop(false);
  std::exception_ptr failure;
  std::mutex failureMutex;
  auto fail=[&]() {
    std::lock_guard<std::mutex> lock(failureMutex);
    if (!failure) failure=std::current_exception();
    stop.store(true);
  };
  auto claim=[&](std::size_t wanted, std::size_t &first, std::size_t &size) {
    first=next.load();
    while (first<count && !stop.load()) {
      size=std::min(wanted,count-first);
      if (next.compare_exchange_weak(first,first+size)) return true;
    }
    return false;
  };
  auto cpuOne=[&](std::size_t i) { cpu(i); ++cpuCount; };
  std::size_t initialFirst=0, initialCount=0;
  if (useGPU) claim(o.gpuChunk, initialFirst, initialCount);
  std::thread gpuWorker;
  if (useGPU) {
    gpuWorker=std::thread([&]() {
      std::size_t first=initialFirst, size=initialCount, chunk=o.gpuChunk;
      bool gpuFailed=false;
      try {
        do {
          if (stop.load()) break;
          auto t=Clock::now();
          if (!gpuFailed) {
            try { result.kernelMs+=gpu(first,size); }
            catch (const std::exception &e) {
              if (o.mode==Mode::CUDA) throw;
              gpuFailed=true; result.fallbackReason=e.what();
            }
            const double elapsed=milliseconds(t);
            result.gpuMs+=elapsed;
            if (!gpuFailed) {
              result.gpuTasks+=size;
              // A check failure is fatal in every mode; never disguise bad arithmetic
              // as a successful CPU fallback.
              if (o.verifyGPU) {
                t=Clock::now();
                for (std::size_t i=first;i<first+size;++i) check(i);
                result.checkMs+=milliseconds(t);
              }
              const double estimate=size*o.targetBatchMs/std::max(elapsed,0.001);
              chunk=static_cast<std::size_t>(std::max(1.0,
                  std::min(static_cast<double>(o.gpuMaxChunk),estimate)));
            }
          }
          if (gpuFailed) {
            // Recompute the entire claimed batch, including any partial GPU output,
            // with the original inputs/randomness. No other worker owns these rows.
            for (std::size_t i=first;i<first+size;++i) cpuOne(i);
            result.fallbackTasks+=size;
            chunk=1;
          }
        } while (claim(chunk,first,size));
      } catch (...) { fail(); }
    });
  }
  int threads=o.cpuThreads;
  if (threads==0) {
#ifdef _OPENMP
    threads=omp_get_max_threads();
#else
    threads=1;
#endif
  }
  // Reserve one host worker for CUDA in hybrid/auto. With a budget of one,
  // GPU and the calling CPU thread still collaborate.
  if (useGPU) threads=std::max(1,threads-1);
  if (!useGPU || o.mode!=Mode::CUDA) {
#pragma omp parallel num_threads(threads)
    {
      std::size_t first=0,size=0;
      try { while (claim(1,first,size)) cpuOne(first); }
      catch (...) { fail(); }
    }
  }
  if (gpuWorker.joinable()) gpuWorker.join();
  if (failure) std::rethrow_exception(failure);
  result.cpuTasks=cpuCount.load();
  result.wallMs=milliseconds(start);
  if (result.cpuTasks+result.gpuTasks!=count)
    throw std::logic_error("acceleration scheduler lost a task");
  previous=result;
  if (o.verbose) {
    std::cout<<"["<<stage<<"] requested="<<modeName(o.mode)
      <<" cpu="<<result.cpuTasks<<" gpu="<<result.gpuTasks
      <<" fallback="<<result.fallbackTasks<<" wall_ms="<<result.wallMs
      <<" gpu_batch_ms="<<result.gpuMs<<" kernel_ms="<<result.kernelMs
      <<" check_ms="<<result.checkMs;
    if (!result.fallbackReason.empty()) std::cout<<" reason="<<result.fallbackReason;
    std::cout<<std::endl;
  }
  return result;
}
} }
