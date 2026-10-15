#pragma once

#include "../namespace.hpp"

#include <map>
#include <vector>

#if defined(__linux__) || defined(__GLIBC__)
#include <malloc.h>
#endif

namespace polyu
{

/**
 * @brief Ask the C runtime allocator to return unused heap pages to the OS.
 *
 * This is useful for long benchmark processes that run several large parameter
 * sets one after another.  Destructors release C++ objects, but glibc may keep
 * the freed arenas inside the process unless malloc_trim is called explicitly.
 */
inline void trimProcessMemory()
{
#if defined(__linux__) || defined(__GLIBC__)
  malloc_trim(0);
#endif
}

template <typename T>
inline void releaseStdVector(vector<T> &v)
{
  vector<T>().swap(v);
}

template <typename K, typename V>
inline void releaseStdMap(map<K, V> &m)
{
  map<K, V>().swap(m);
}

template <typename T>
inline void releaseNtlVec(Vec<T> &v)
{
  v.kill();
}

template <typename T>
inline void releaseNtlMat(Mat<T> &m)
{
  m.kill();
}

} // namespace polyu
