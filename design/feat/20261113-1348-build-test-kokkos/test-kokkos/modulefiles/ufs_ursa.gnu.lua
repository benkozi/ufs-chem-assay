-- Verbatim copy of ufs_ursa.gnu.lua from ufs-weather-model
-- feature/ss2.x (spack-stack 2.1.x style):
-- https://github.com/gspetro-NOAA/ufs-weather-model/blob/feature/ss2.x/modulefiles/ufs_ursa.gnu.lua
--
-- Local changes, kept deliberately minimal so this models a real UFS build
-- (same recipe as ufs_ursa.oneapi.lua):
--   1. MODULEPATH is driven by the TEST_KOKKOS_ENV_ROOT env var, exported
--      by build-test-kokkos.sh from the platform's gcc env_root config.
--      Unset (e.g. standalone `module load`), it falls back to the custom
--      GCC kokkos/arborx test env instead of upstream's
--      /contrib/spack-stack/spack-stack-2.1.0/envs/ue-gcc-12.4.0 install
--      (same env-name family — pure path substitution).
--   2. kokkos + kokkos-kernels loads appended at the end — the packages
--      under test in JCSDA/spack-stack#2081.
--   3. arborx loaded tolerantly (pin via arborx_ver, else try_load).
--   4. cmake and nccmp version pins removed (load the env default),
--      mirroring the oneapi recipe — exact tool versions are not what this
--      test validates. Override via cmake_ver / nccmp_ver if ever needed.
-- Everything else is verbatim — the stack-gcc/stack-openmpi pins (the
-- toolchain under test) and the MPI_CC/MPI_CXX/MPI_FC/FC setenvs. Note
-- upstream sets no CC/CXX (unlike the oneapi file): CMake picks gcc/g++
-- from the loaded stack and FindMPI supplies MPI — that is the UFS-gnu
-- reality this models; do not "fix" it here without a recorded reason.

help([[
loads UFS Model prerequisites for Ursa/GNU
]])

local env_root = os.getenv("TEST_KOKKOS_ENV_ROOT")
  or "/scratch4/NCEPDEV/nems/Richard.Grubin/spack-stack/envs/ue-gcc-12.4.0-arborx"
prepend_path("MODULEPATH", pathJoin(env_root, "modules/Core"))

stack_gnu_ver=os.getenv("stack_gnu_ver") or "12.4.0"
load(pathJoin("stack-gcc", stack_gnu_ver))

stack_openmpi_ver=os.getenv("stack_openmpi_ver") or "4.1.6"
load(pathJoin("stack-openmpi", stack_openmpi_ver))

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

setenv("MPI_CC", "mpicc")
setenv("MPI_CXX", "mpic++")
setenv("MPI_FC", "mpifort")
setenv("FC", "mpifort")

setenv("CMAKE_Platform", "ursa.gnu")

whatis("Description: UFS build environment")

-- Local addition (see header): the packages under test in
-- JCSDA/spack-stack#2081. Unversioned loads take the environment's default
-- version.
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

-- Local addition (see header): arborx, tolerantly — try_load is a silent
-- no-op if an env without arborx is ever selected.
arborx_ver=os.getenv("arborx_ver")
if arborx_ver then
  load(pathJoin("arborx", arborx_ver))
else
  try_load("arborx")
end
