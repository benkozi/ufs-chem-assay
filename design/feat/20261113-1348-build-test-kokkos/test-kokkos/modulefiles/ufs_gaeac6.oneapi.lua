-- Verbatim copy of ufs_gaeac6.oneapi.lua from ufs-weather-model
-- feature/ss2.x (spack-stack 2.1.x style):
-- https://github.com/gspetro-NOAA/ufs-weather-model/blob/feature/ss2.x/modulefiles/ufs_gaeac6.oneapi.lua
--
-- Local changes, kept deliberately minimal so this models a real UFS build
-- (same three-change recipe as ufs_ursa.oneapi.lua):
--   1. MODULEPATH points at the custom kokkos test env from
--      https://github.com/JCSDA/spack-stack/issues/2081 (rickgrubin's
--      2026-08-13 Gaea-C6 comment) instead of the /ncrc/proj/epic
--      production install. Same env name (ue-oneapi-2025.2.1) — pure path
--      substitution.
--   2. kokkos + kokkos-kernels loads appended at the end — the packages
--      under test, i.e. what the UFS platform file gains once #2081 lands.
--   3. cmake and nccmp version pins removed (load the env default),
--      mirroring the Ursa recipe — exact tool versions are not what this
--      test validates. Override via cmake_ver / nccmp_ver if ever needed.
--   4. MODULEPATH is driven by the TEST_KOKKOS_ENV_ROOT env var, exported
--      by build-test-kokkos.sh from the platform's env_root config, so the
--      script's env selection is real rather than display-only. Unset (e.g.
--      standalone `module load`), it falls back to the custom kokkos env.
--   5. arborx loaded tolerantly at the end (pin via arborx_ver, else
--      try_load): the C6 env carries no arborx today, so this is a silent
--      no-op there — consumers' find_package falls back gracefully.
-- Everything else — the stack pins (the toolchain under test), the
-- darshan-runtime/cray-libsci unloads, the Cray cc/CC/ftn wrappers, and
-- even upstream's stale "Intel-2023.2.0" help text — is verbatim.

help([[
  This module loads libraries required for building and running UFS Weather Model 
  on the NOAA RDHPC machine Gaea C6 using Intel-2023.2.0.
]])

whatis([===[Loads libraries needed for building the UFS Weather Model on Gaea C6]===])

local env_root = os.getenv("TEST_KOKKOS_ENV_ROOT")
  or "/gpfs/f6/epic/scratch/Richard.Grubin/spack-stack/envs/ue-oneapi-2025.2.1"
prepend_path("MODULEPATH", pathJoin(env_root, "modules/Core"))

stack_intel_ver=os.getenv("stack_intel_ver") or "2025.2.1"
load(pathJoin("stack-intel-oneapi-compilers", stack_intel_ver))

stack_cray_mpich_ver=os.getenv("stack_cray_mpich_ver") or "8.1.32"
load(pathJoin("stack-cray-mpich", stack_cray_mpich_ver))

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

unload("darshan-runtime")
unload("cray-libsci")

setenv("I_MPI_CC", "icx")
setenv("I_MPI_CXX", "icpx")
setenv("I_MPI_FC", "ifx")
setenv("CC","cc")
setenv("CXX","CC")
setenv("FC","ftn")

setenv("CMAKE_Platform","gaeac6.oneapi")

-- Local addition (see header): the packages under test in
-- JCSDA/spack-stack#2081. TODO(gaea-c6): pin versions once known
-- (`module avail kokkos kokkos-kernels` with the stack loaded); unversioned
-- loads take the environment's default version.
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

-- Local addition (see header): arborx, tolerantly — absent in this env
-- today, so try_load is a silent no-op and find_package falls back.
arborx_ver=os.getenv("arborx_ver")
if arborx_ver then
  load(pathJoin("arborx", arborx_ver))
else
  try_load("arborx")
end
