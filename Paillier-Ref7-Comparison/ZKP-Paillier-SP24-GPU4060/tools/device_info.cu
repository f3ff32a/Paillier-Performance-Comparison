#include <cuda_runtime.h>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
void check(cudaError_t code,const char *operation) {
  if(code!=cudaSuccess)
    throw std::runtime_error(std::string(operation)+": "+cudaGetErrorName(code)+
                             " ("+std::to_string(static_cast<int>(code))+") "+cudaGetErrorString(code));
}
__global__ void probe(unsigned *values) {
  unsigned i=blockIdx.x*blockDim.x+threadIdx.x;
  if(i<256) values[i]=(i*17u)^0x12345678u;
}
std::string version(int v) { return std::to_string(v/1000)+"."+std::to_string(v%1000/10); }
int index(const std::string &s) {
  if(s.empty() || s.find_first_not_of("0123456789")!=std::string::npos)
    throw std::invalid_argument("invalid device index");
  const auto v=std::stoull(s);
  if(v>std::numeric_limits<int>::max()) throw std::invalid_argument("invalid device index");
  return static_cast<int>(v);
}
}
int main(int argc,char **argv) {
  unsigned *buffer=nullptr;
  try {
    if(argc==2 && std::string(argv[1])=="--help") {
      std::cout<<"zkp_device_info [--device N]: query CUDA and execute a real kernel\n";
      return 0;
    }
    int device=0;
    if(const char *v=std::getenv("ZKP_CUDA_DEVICE")) device=index(v);
    if(argc==3 && std::string(argv[1])=="--device") device=index(argv[2]);
    else if(argc!=1) throw std::invalid_argument("usage: zkp_device_info [--device N]");
    std::cout<<"project=ZKP-Paillier-SP24-GPU2\ncompiled_cuda="<<version(CUDART_VERSION)<<std::endl;
    int driver=0,runtime=0,count=0;
    check(cudaDriverGetVersion(&driver),"cudaDriverGetVersion");
    check(cudaRuntimeGetVersion(&runtime),"cudaRuntimeGetVersion");
    std::cout<<"driver_api="<<version(driver)<<" runtime="<<version(runtime)<<std::endl;
    check(cudaGetDeviceCount(&count),"cudaGetDeviceCount");
    if(device<0 || device>=count) throw std::runtime_error("selected CUDA device is unavailable");
    check(cudaSetDevice(device),"cudaSetDevice");
    cudaDeviceProp p{};
    check(cudaGetDeviceProperties(&p,device),"cudaGetDeviceProperties");
    std::cout<<"device="<<device<<" name="<<p.name<<" sm="<<p.major<<p.minor
             <<" memory_mib="<<p.totalGlobalMem/(1024*1024)
             <<" multiprocessors="<<p.multiProcessorCount<<std::endl;
    cudaFuncAttributes attributes{};
    check(cudaFuncGetAttributes(&attributes,probe),"cudaFuncGetAttributes (compiled architecture)");
    std::cout<<"kernel_binary_arch="<<attributes.binaryVersion<<std::endl;
    check(cudaMalloc(reinterpret_cast<void **>(&buffer),256*sizeof(unsigned)),"cudaMalloc");
    probe<<<2,128>>>(buffer);
    check(cudaGetLastError(),"probe launch");
    check(cudaDeviceSynchronize(),"probe execution");
    std::vector<unsigned> result(256);
    check(cudaMemcpy(result.data(),buffer,256*sizeof(unsigned),cudaMemcpyDeviceToHost),"probe result");
    for(unsigned i=0;i<256;++i)
      if(result[i]!=((i*17u)^0x12345678u)) throw std::runtime_error("probe result mismatch");
    check(cudaFree(buffer),"cudaFree"); buffer=nullptr;
    std::cout<<"CUDA_PROBE=PASS\n";
    return 0;
  } catch(const std::exception &e) {
    if(buffer) cudaFree(buffer);
    std::cerr<<"CUDA_PROBE=FAIL "<<e.what()<<"\n"
             <<"See docs/UBUNTU_26_RTX4060.md. Check the driver, selected GPU, and native sm_89 build.\n";
    return 1;
  }
}
