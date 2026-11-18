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
--   4. kokkos, kokkos-kernels and arborx loaded tolerantly at the end (pin
--      via kokkos_ver / kokkos_kernels_ver / arborx_ver, else try_load).
--      TODO(gaea-c7): the spack-stack 2.2.0 environment on C7 carries none
--      of the three (its site config does not ask for them), so a consumer's
--      find_package falls back to whatever it does without them. Once they
--      are installed, kokkos and kokkos-kernels become plain loads, as in
--      the Ursa and C6 files.
-- Everything else is verbatim: the cray-mpich unload, the stack pins (the
-- toolchain under test) and the compilers, which are Intel's own icx / icpx
-- / ifx (C7 has no Cray cc / CC / ftn wrappers). Load it after
-- `module reset`, never `module purge`.
-- Every <name>_ver variable is read from the environment, so a run config
-- pins a version through applications.<name>.module_env. The environment's
-- default esmf is a later beta than the one its mapl is built against: pin
-- both (esmf_ver, mapl_ver), as config/gaea-c7.yaml does.

help([[
  This module loads libraries required for building and running UFS Weather Model
  on the NOAA RDHPC machine Gaea C7 using Intel-2025.3.1.
]])

whatis([===[Loads libraries needed for building the UFS Weather Model on Gaea C7]===])

unload("cray-mpich/9.2.0")

-- Local addition (see header): the site default (2026.0.0 at the time of
-- writing) is not the compiler the environment was built with.
oneapi_ver=os.getenv("oneapi_ver") or "2025.3.1"
unload("oneapi")
load(pathJoin("oneapi", oneapi_ver))

-- Optional (see header): unset or empty, the caller has already
-- `module use`d the environment's modules/Core.
local env_root = os.getenv("ASSAY_SPACK_ENV_ROOT")
if env_root and env_root ~= "" then
  prepend_path("MODULEPATH", pathJoin(env_root, "modules/Core"))
end

stack_intel_ver=os.getenv("stack_intel_ver") or "2025.3.1"
load(pathJoin("stack-intel-oneapi-compilers", stack_intel_ver))

stack_cray_mpich_ver=os.getenv("stack_cray_mpich_ver") or "9.2.0"
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

setenv("I_MPI_CC", "icx")
setenv("I_MPI_CXX", "icpx")
setenv("I_MPI_FC", "ifx")

setenv("CC","icx")
setenv("CXX","icpx")
setenv("FC","ifx")
setenv("CMAKE_Platform","gaeac7.oneapi")

-- Local addition (see header): the Kokkos packages, tolerantly. try_load is
-- a silent no-op when the module does not exist.
kokkos_ver=os.getenv("kokkos_ver")
if kokkos_ver then
  load(pathJoin("kokkos", kokkos_ver))
else
  try_load("kokkos")
end

kokkos_kernels_ver=os.getenv("kokkos_kernels_ver")
if kokkos_kernels_ver then
  load(pathJoin("kokkos-kernels", kokkos_kernels_ver))
else
  try_load("kokkos-kernels")
end

arborx_ver=os.getenv("arborx_ver")
if arborx_ver then
  load(pathJoin("arborx", arborx_ver))
else
  try_load("arborx")
end
