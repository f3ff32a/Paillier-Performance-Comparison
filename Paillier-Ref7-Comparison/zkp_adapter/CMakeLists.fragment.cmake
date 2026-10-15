# Included from the existing ZKP-Paillier project's root CMakeLists.txt.
# The installer copies the adapter source into tools/paillier_compare.cpp.
if(NOT TARGET zkp_paillier)
  message(FATAL_ERROR "Paillier comparison adapter needs the existing zkp_paillier target")
endif()
add_executable(paillier_compare tools/paillier_compare.cpp)
target_link_libraries(paillier_compare PRIVATE zkp_paillier)
