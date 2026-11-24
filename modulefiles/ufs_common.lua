-- Based on ufs_common.lua from ufs-weather-model feature/ss2.x:
-- https://github.com/gspetro-NOAA/ufs-weather-model/blob/feature/ss2.x/modulefiles/ufs_common.lua
--
-- Local change: the upstream version pins are removed — each module loads
-- the environment's default version. A 2026-08-14 run on Ursa showed the
-- custom kokkos test env (JCSDA/spack-stack#2081) carries different
-- versions of jasper, libpng, and gftl-shared than upstream pins, and the
-- exact versions of these libraries are not what this validates.
-- Versions can still be forced via the usual <name>_ver environment
-- variables (e.g. esmf_ver; an empty one counts as unset) without editing this file — from a run
-- config, applications.<name>.module_env.

whatis("Description: UFS build environment common libraries")

help([[Load UFS Model common libraries]])

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

local ufs_modules = {
  "jasper",
  "libpng",
  "hdf5",
  "netcdf-c",
  "netcdf-fortran",
  "parallelio",
  "esmf",
  "fms",
  "bacio",
  "crtm",
  "g2",
  "g2tmpl",
  "ip",
  "w3emc",
  "gftl-shared",
  "mapl",
  "scotch",
}

for i = 1, #ufs_modules do
  local name = ufs_modules[i]
  local env_version_name = string.gsub(name, "-", "_") .. "_ver"
  local ver = pin(env_version_name)
  if ver then
    load(pathJoin(name, ver))
  else
    load(name)
  end
end
