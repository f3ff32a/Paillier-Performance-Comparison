#pragma once
#include "Backend.hpp"
#include <memory>
#include <string>
#include <vector>

namespace polyu { namespace acceleration {
using Strings = std::vector<std::string>;
using StringMatrix = std::vector<Strings>;
struct CudaCommit {
  virtual ~CudaCommit() = default;
  virtual double compute(const StringMatrix &messages, const Strings &randomness,
      Strings &output, std::size_t first, std::size_t count) = 0;
};
struct CudaEncrypt {
  virtual ~CudaEncrypt() = default;
  virtual double compute(const Strings &messages, const Strings &randomness,
      Strings &output, std::size_t first, std::size_t count) = 0;
};
// Create/use on the GPU worker. Destructors select the owning CUDA device,
// allowing cleanup after the worker has joined on the calling thread.
std::unique_ptr<CudaCommit> makeCudaCommit(const std::string &q,
    const std::string &g, const Strings &generators, const Options &options);
std::unique_ptr<CudaEncrypt> makeCudaEncrypt(const std::string &n,
    const std::string &n2, const Options &options);
} }
