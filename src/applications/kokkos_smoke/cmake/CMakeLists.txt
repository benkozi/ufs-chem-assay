# Standalone smoke test for the spack-stack-provided Kokkos and
# kokkos-kernels (JCSDA/spack-stack#2081). Deliberately no FetchContent
# fallback: a find_package failure means the environment is missing or
# broken, which is exactly what this project exists to detect.
cmake_minimum_required(VERSION 3.21)
project(kokkos_spack_smoke LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Kokkos REQUIRED)
find_package(KokkosKernels REQUIRED)

message(STATUS "Found Kokkos ${Kokkos_VERSION} at ${Kokkos_DIR}")
message(STATUS "Found KokkosKernels ${KokkosKernels_VERSION} at ${KokkosKernels_DIR}")

add_executable(smoke smoke.cpp)
target_link_libraries(smoke PRIVATE Kokkos::kokkos Kokkos::kokkoskernels)
