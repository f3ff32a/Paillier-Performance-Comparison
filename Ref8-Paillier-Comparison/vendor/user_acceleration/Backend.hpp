#pragma once

#include <cstddef>
#include <functional>
#include <string>

namespace polyu { namespace acceleration {

enum class Mode { CPU, Auto, CUDA, Hybrid };
struct Options {
  Mode mode = Mode::Auto;
  int device = 0;
  int cpuThreads = 0; // 0 uses OMP_NUM_THREADS / OpenMP's default
  std::size_t gpuChunk = 1;
  std::size_t gpuMaxChunk = 32;
  std::size_t gpuMemoryMiB = 256;
  double targetBatchMs = 50.0;
  bool verifyGPU = false;
  bool verbose = true;
};
struct Statistics {
  std::size_t cpuTasks = 0, gpuTasks = 0, fallbackTasks = 0;
  double wallMs = 0, gpuMs = 0, kernelMs = 0, checkMs = 0;
  std::string fallbackReason;
};

Mode parseMode(const std::string &name);
const char *modeName(Mode mode);
Options optionsFromEnvironment();
Options options();
// Configure on the calling thread, before starting a protocol execution.
void configure(const Options &options);
void validate(const Options &options);
bool cudaAvailable(int device, std::string &description);
Statistics lastStatistics();

using CpuTask = std::function<void(std::size_t)>;
// Returns kernel-only milliseconds. H2D/D2H/setup remain in measured gpuMs.
using GpuBatch = std::function<double(std::size_t, std::size_t)>;
using CheckTask = std::function<void(std::size_t)>;

// Inputs and preallocated outputs must stay alive for the complete call.
// cpu and gpu must write only their claimed, disjoint output indices.
// check compares an already-computed GPU result with an independent CPU oracle.
Statistics schedule(const std::string &stage, std::size_t count,
                    const Options &config, bool available,
                    const std::string &unavailableReason,
                    const CpuTask &cpu, const GpuBatch &gpu,
                    const CheckTask &check = CheckTask());

} }
