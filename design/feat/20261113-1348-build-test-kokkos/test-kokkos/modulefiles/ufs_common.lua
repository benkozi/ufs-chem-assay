-- Based on ufs_common.lua from ufs-weather-model feature/ss2.x:
-- https://github.com/gspetro-NOAA/ufs-weather-model/blob/feature/ss2.x/modulefiles/ufs_common.lua
--
-- Local change: the upstream version pins are removed — each module loads
-- the environment's default version. A 2026-08-14 run on Ursa showed the
-- custom kokkos test env (JCSDA/spack-stack#2081) carries different
-- versions of jasper, libpng, and gftl-shared than upstream pins, and the
-- exact versions of these libraries are not what this test validates.
-- Versions can still be forced via the usual <name>_ver environment
-- variables (e.g. esmf_ver) without editing this file.

whatis("Description: UFS build environment common libraries")

help([[Load UFS Model common libraries]])

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
  local ver = os.getenv(env_version_name)
  if ver then
    load(pathJoin(name, ver))
  else
    load(name)
  end
end
