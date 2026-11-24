-- Copy of ufs_gaeac6.oneapi.lua from ufs-weather-model feature/ss2.x
-- (spack-stack 2.1.x style):
-- https://github.com/gspetro-NOAA/ufs-weather-model/blob/feature/ss2.x/modulefiles/ufs_gaeac6.oneapi.lua
--
-- Local changes, kept deliberately minimal so this models a real UFS build
-- (same recipe as ufs_ursa.oneapi.lua):
--   1. No environment root of its own. Upstream prepends its production
--      spack-stack install to MODULEPATH; here the caller names the
--      environment, one of two ways: `module use <env>/modules/Core` before
--      loading this file (ufs-chem-assay: a full path in the run config's
--      applications.<name>.modulepath), or ASSAY_SPACK_ENV_ROOT=<env> in
--      the environment (applications.<name>.module_env), which this file
--      turns into the same MODULEPATH entry. With neither, the first stack
--      load below fails with Lmod's own unknown-module error.
--   2. kokkos + kokkos-kernels loads appended at the end — the packages
--      under test (JCSDA/spack-stack#2081), i.e. what the UFS platform file
--      gains once they are part of the stack.
--   3. cmake and nccmp version pins removed (load the env default),
--      mirroring the Ursa recipe — exact tool versions are not what this
--      validates. Override via cmake_ver / nccmp_ver if ever needed.
--   4. arborx loaded tolerantly at the end (pin via arborx_ver, else
--      try_load): a silent no-op in an environment without it — consumers'
--      find_package falls back gracefully.
-- Everything else — the stack pins (the toolchain under test), the
-- darshan-runtime/cray-libsci unloads, the Cray cc/CC/ftn wrappers, and
-- even upstream's stale "Intel-2023.2.0" help text — is verbatim. Load it
-- after `module reset`, never `module purge` (purge strips the Cray
-- programming environment the wrappers need).
-- Every <name>_ver variable is read from the environment (an empty value
-- counts as unset: pin, below), so a run config
-- pins a version through applications.<name>.module_env.

help([[
  This module loads libraries required for building and running UFS Weather Model
  on the NOAA RDHPC machine Gaea C6 using Intel-2023.2.0.
]])

-- Local addition (see header): a version pin read from the environment.
-- Unset and empty both mean "no pin": a run config that extends another can
-- only clear a pin its base sets by setting it to the empty string.
local function pin(name)
  local value = os.getenv(name)
  if value ~= nil and value ~= "" then
    return value
  end
  return nil
end

whatis([===[Loads libraries needed for building the UFS Weather Model on Gaea C6]===])

-- Optional (see header): unset or empty, the caller has already
-- `module use`d the environment's modules/Core.
local env_root = os.getenv("ASSAY_SPACK_ENV_ROOT")
if env_root and env_root ~= "" then
  prepend_path("MODULEPATH", pathJoin(env_root, "modules/Core"))
end

local stack_intel_ver = pin("stack_intel_ver") or "2025.2.1"
load(pathJoin("stack-intel-oneapi-compilers", stack_intel_ver))

local stack_cray_mpich_ver = pin("stack_cray_mpich_ver") or "8.1.32"
load(pathJoin("stack-cray-mpich", stack_cray_mpich_ver))

local cmake_ver = pin("cmake_ver")
if cmake_ver then
  load(pathJoin("cmake", cmake_ver))
else
  load("cmake")
end

load("ufs_common")

local nccmp_ver = pin("nccmp_ver")
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
local kokkos_ver = pin("kokkos_ver")
if kokkos_ver then
  load(pathJoin("kokkos", kokkos_ver))
else
  load("kokkos")
end

local kokkos_kernels_ver = pin("kokkos_kernels_ver")
if kokkos_kernels_ver then
  load(pathJoin("kokkos-kernels", kokkos_kernels_ver))
else
  load("kokkos-kernels")
end

-- Local addition (see header): arborx, tolerantly — absent in this env
-- today, so try_load is a silent no-op and find_package falls back.
local arborx_ver = pin("arborx_ver")
if arborx_ver then
  load(pathJoin("arborx", arborx_ver))
else
  try_load("arborx")
end
