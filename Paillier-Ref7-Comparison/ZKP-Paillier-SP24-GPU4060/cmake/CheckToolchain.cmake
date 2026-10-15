# Detect incompatible NTL / libstdc++ combinations before the long CUDA build.
function(zkp_check_ntl_abi)
  include(CheckCXXSourceCompiles)
  include(CheckCXXSourceRuns)
  set(CMAKE_REQUIRED_INCLUDES "${NTL_INCLUDE_DIR};${GMP_INCLUDE_DIR}")
  set(CMAKE_REQUIRED_LIBRARIES "${NTL_LIBRARY};${GMP_LIBRARY};Threads::Threads")
  set(probe [=[
    #include <NTL/ZZ.h>
    #include <sstream>
    int main() {
      NTL::ZZ a(3), e(5), q(17);
      std::ostringstream out;
      out << NTL::PowerMod(a, e, q);
      return out.str() == "5" ? 0 : 1;
    }
  ]=])
  unset(ZKP_NTL_LINKS CACHE)
  check_cxx_source_compiles("${probe}" ZKP_NTL_LINKS)
  if(NOT ZKP_NTL_LINKS)
    message(FATAL_ERROR
      "NTL/GMP failed to link with ${CMAKE_CXX_COMPILER}.\n"
      "If the log mentions __cxa_call_terminate@CXXABI_1.3.15, GLIBCXX or CXXABI, "
      "the selected libstdc++ is incompatible with NTL.\n"
      "Ubuntu 26.04: use /usr/bin/g++ for both C++ and CUDA, with CUDA 13.4. "
      "Do not force an old GCC 13 or replace system library symlinks.\n"
      "Check CXX, LIBRARY_PATH, LD_LIBRARY_PATH and CMake's cached library paths. "
      "Reconfigure in a new build directory after correcting the toolchain.\n"
      "NTL=${NTL_LIBRARY}; GMP=${GMP_LIBRARY}; see docs/UBUNTU_26_RTX4060.md.")
  endif()
  if(NOT CMAKE_CROSSCOMPILING)
    unset(ZKP_NTL_RUNS CACHE)
    check_cxx_source_runs("${probe}" ZKP_NTL_RUNS)
    if(NOT ZKP_NTL_RUNS)
      message(FATAL_ERROR
        "NTL linked, but the NTL/GMP runtime probe failed. Check LD_LIBRARY_PATH "
        "and the actual libstdc++/libntl/libgmp loaded by the program. "
        "Conda or a previous compiler installation may shadow the system runtime. "
        "See docs/UBUNTU_26_RTX4060.md and the CMake configure log.")
    endif()
  endif()
endfunction()

function(zkp_select_cuda_host)
  if(NOT CMAKE_CUDA_HOST_COMPILER)
    set(CMAKE_CUDA_HOST_COMPILER "${CMAKE_CXX_COMPILER}" CACHE FILEPATH
        "Use the same host compiler for C++ and CUDA" FORCE)
  endif()
  get_filename_component(cxx_real "${CMAKE_CXX_COMPILER}" REALPATH)
  get_filename_component(cuda_host_real "${CMAKE_CUDA_HOST_COMPILER}" REALPATH)
  if(NOT cxx_real STREQUAL cuda_host_real)
    message(FATAL_ERROR
      "C++ and CUDA must use the same host compiler in this project.\n"
      "CXX=${CMAKE_CXX_COMPILER}; CUDA host=${CMAKE_CUDA_HOST_COMPILER}.\n"
      "Select the same g++ for both in a new build directory; "
      "see scripts/build.py and docs/UBUNTU_26_RTX4060.md.")
  endif()
endfunction()
