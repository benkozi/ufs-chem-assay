-- Copy of ufs_gaeac7.oneapi.lua from ufs-weather-model's Gaea C7 port
-- (spack-stack 2.x style), at commit 8eaee8e of
-- https://github.com/ufs-community/ufs-weather-model/pull/3432
--
-- Local changes, kept deliberately minimal so this models a real UFS build
-- (same recipe as ufs_ursa.oneapi.lua and ufs_gaeac6.oneapi.lua):
--   1. No environment root of its own. Upstream prepends its production
--      spack-stack install to MODULEPATH; here the caller names the
--      environment, one of two ways: `module use <env>/modules/Core` before
--      loading this file (ufs-chem-assay: a full path in the run config's
--      applications.<name>.modulepath), or ASSAY_SPACK_ENV_ROOT=<env> in
--      the environment (applications.<name>.module_env), which this file
--      turns into the same MODULEPATH entry. With neither, the first stack
--      load below fails with Lmod's own unknown-module error.
--   2. The site's default oneAPI makes way for the one the environment was
--      built with. Upstream leaves that to the caller (`module swap
--      oneapi/2026.0.0 oneapi/2025.3.1` before the load); here it is part of
--      the file, so `module reset` then `module load` is the whole recipe.
--   3. cmake and nccmp version pins removed (load the default), mirroring
--      the Ursa recipe: exact tool versions are not what this validates.
--      Override via cmake_ver / nccmp_ver if ever needed.
--   4. kokkos + kokkos-kernels loads appended at the end — the packages
--      under test (JCSDA/spack-stack#2081), installed in the C7 environment
--      since 2026-11-20 (kokkos/5.1.1, kokkos-kernels/5.1.1) — then arborx
--      tolerantly (pin via arborx_ver, else try_load), as in the Ursa and
--      C6 files.
--   5. yaml-cpp loaded (pin via yaml_cpp_ver): CECE takes an installed
--      yaml-cpp over its bundled submodule when find_package sees one, and
--      the environment has yaml-cpp/0.8.0. The weather model does not load
--      it.
--   6. The cray-mpich unload names no version. Upstream unloads
--      cray-mpich/9.2.0, the site default when it was written; Lmod unloads
--      only the version named, so a newer default would stay loaded beside
--      the environment's.
-- Everything else is verbatim: the stack pins (the
-- toolchain under test) and the compilers, which are Intel's own icx / icpx
-- / ifx (C7 has no Cray cc / CC / ftn wrappers). Load it after
-- `module reset`, never `module purge`.
-- Every <name>_ver variable is read from the environment (an empty value
-- counts as unset: pin, below), so a run config
-- pins a version through applications.<name>.module_env. The environment's
-- default esmf is a later beta than the one its mapl is built against: pin
-- both (esmf_ver, mapl_ver), as config/gaea-c7.yaml does.

help([[
  This module loads libraries required for building and running UFS Weather Model
  on the NOAA RDHPC machine Gaea C7 using Intel-2025.3.1.
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

whatis([===[Loads libraries needed for building the UFS Weather Model on Gaea C7]===])

-- Local change (see header): unversioned, so it still unloads the site's
-- default once that is no longer 9.2.0.
unload("cray-mpich")

-- Local addition (see header): the site default (2026.0.0 at the time of
-- writing) is not the compiler the environment was built with.
local oneapi_ver = pin("oneapi_ver") or "2025.3.1"
unload("oneapi")
load(pathJoin("oneapi", oneapi_ver))

-- Optional (see header): unset or empty, the caller has already
-- `module use`d the environment's modules/Core.
local env_root = os.getenv("ASSAY_SPACK_ENV_ROOT")
if env_root and env_root ~= "" then
  prepend_path("MODULEPATH", pathJoin(env_root, "modules/Core"))
end

local stack_intel_ver = pin("stack_intel_ver") or "2025.3.1"
load(pathJoin("stack-intel-oneapi-compilers", stack_intel_ver))

local stack_cray_mpich_ver = pin("stack_cray_mpich_ver") or "9.2.0"
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

setenv("I_MPI_CC", "icx")
setenv("I_MPI_CXX", "icpx")
setenv("I_MPI_FC", "ifx")

setenv("CC","icx")
setenv("CXX","icpx")
setenv("FC","ifx")
setenv("CMAKE_Platform","gaeac7.oneapi")

-- Local addition (see header): the packages under test in
-- JCSDA/spack-stack#2081. Unversioned loads take the environment's default.
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

-- Local addition (see header): the environment's yaml-cpp, for CECE.
local yaml_cpp_ver = pin("yaml_cpp_ver")
if yaml_cpp_ver then
  load(pathJoin("yaml-cpp", yaml_cpp_ver))
else
  load("yaml-cpp")
end

-- Local addition (see header): arborx, tolerantly (arborx/2.1 in the C7
-- environment). try_load is a silent no-op when the module does not exist,
-- letting consumers' find_package(ArborX) fall back.
local arborx_ver = pin("arborx_ver")
if arborx_ver then
  load(pathJoin("arborx", arborx_ver))
else
  try_load("arborx")
end
