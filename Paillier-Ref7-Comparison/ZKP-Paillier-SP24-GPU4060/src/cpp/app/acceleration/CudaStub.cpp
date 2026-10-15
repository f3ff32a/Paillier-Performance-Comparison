#include "CudaBackend.hpp"
#include <stdexcept>
namespace polyu { namespace acceleration {
bool cudaAvailable(int, std::string &description) {
  description="this build has no CUDA backend (configure with -DZKP_ENABLE_CUDA=ON)";
  return false;
}
std::unique_ptr<CudaCommit> makeCudaCommit(const std::string &,const std::string &,
    const Strings &,const Options &) { throw std::runtime_error("CUDA was not compiled"); }
std::unique_ptr<CudaEncrypt> makeCudaEncrypt(const std::string &,const std::string &,
    const Options &) { throw std::runtime_error("CUDA was not compiled"); }
} }
