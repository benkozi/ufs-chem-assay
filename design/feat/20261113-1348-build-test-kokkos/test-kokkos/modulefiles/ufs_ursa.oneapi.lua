-- Verbatim copy of ufs_ursa.oneapi.lua from ufs-weather-model
-- feature/ss2.x (spack-stack 2.1.x style):
-- https://github.com/gspetro-NOAA/ufs-weather-model/blob/feature/ss2.x/modulefiles/ufs_ursa.oneapi.lua
--
-- Local changes, kept deliberately minimal so this models a real UFS build:
--   1. MODULEPATH points at the custom kokkos test env from
--      https://github.com/JCSDA/spack-stack/issues/2081#issuecomment-5268519924
--      instead of the /contrib production install.
--   2. kokkos + kokkos-kernels loads appended at the end — the packages
--      under test, i.e. what the UFS platform file gains once #2081 lands.
--   3. cmake and nccmp version pins removed (load the env default): a
--      2026-08-14 Ursa run showed the env does not carry cmake/3.31.8, and
--      exact tool versions are not what this test validates. Override via
--      cmake_ver / nccmp_ver if ever needed.
--   4. MODULEPATH is driven by the TEST_KOKKOS_ENV_ROOT env var, exported
--      by build-test-kokkos.sh from the platform's env_root config, so the
--      script's env selection is real rather than display-only. Unset (e.g.
--      standalone `module load`), it falls back to the plain kokkos env.
--   5. arborx loaded tolerantly at the end (pin via arborx_ver, else
--      try_load): present in the -cuda-arborx env, absent in the plain one —
--      consumers' find_package falls back gracefully when absent.

help([[
loads UFS Model prerequisites for Ursa/Intel LLVM
]])

local env_root = os.getenv("TEST_KOKKOS_ENV_ROOT")
  or "/scratch4/NCEPDEV/nems/Richard.Grubin/spack-stack/envs/ue-oneapi-2025.3.1"
prepend_path("MODULEPATH", pathJoin(env_root, "modules/Core"))

stack_oneapi_ver=os.getenv("stack_oneapi_ver") or "2025.3.1"
load(pathJoin("stack-intel-oneapi-compilers", stack_oneapi_ver))

stack_impi_ver=os.getenv("stack_impi_ver") or "2021.17"
load(pathJoin("stack-intel-oneapi-mpi", stack_impi_ver))

cmake_ver=os.getenv("cmake_ver")
if cmake_ver then
  load(pathJoin("cmake", cmake_ver))
else
  load("cmake")
end

load("ufs_common")

nccmp_ver=os.getenv("nccmp_ver")
if nccmp_ver then
  load(pathJoin("nccmp", nccmp_ver))
else
  load("nccmp")
end

setenv("CC", "mpiicx")
setenv("CXX", "mpiicpx")
setenv("FC", "mpiifx")
setenv("I_MPI_CC", "icx")
setenv("I_MPI_CXX", "icpx")
setenv("I_MPI_FC", "ifx")

setenv("CMAKE_Platform", "ursa.oneapi")

whatis("Description: UFS build environment")

-- Local addition (see header): the packages under test in
-- JCSDA/spack-stack#2081. TODO(ursa): pin versions once known
-- (`module avail kokkos kokkos-kernels`); unversioned loads take the
-- environment's default version.
kokkos_ver=os.getenv("kokkos_ver")
if kokkos_ver then
  load(pathJoin("kokkos", kokkos_ver))
else
  load("kokkos")
end

kokkos_kernels_ver=os.getenv("kokkos_kernels_ver")
if kokkos_kernels_ver then
  load(pathJoin("kokkos-kernels", kokkos_kernels_ver))
else
  load("kokkos-kernels")
end

-- Local addition (see header): arborx, tolerantly — only some envs carry it
-- (e.g. ue-oneapi-2025.3.1-cuda-arborx). try_load is a silent no-op when the
-- module doesn't exist, letting consumers' find_package(ArborX) fall back.
arborx_ver=os.getenv("arborx_ver")
if arborx_ver then
  load(pathJoin("arborx", arborx_ver))
else
  try_load("arborx")
end
