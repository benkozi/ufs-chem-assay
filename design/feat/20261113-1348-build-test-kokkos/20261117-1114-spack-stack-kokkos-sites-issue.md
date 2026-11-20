# Issue draft: enable the Kokkos packages for the UFS weather-model environment on Gaea C7 and the other UFS sites (spack-stack 2.2.x)

A draft for [JCSDA/spack-stack](https://github.com/JCSDA/spack-stack/issues/new?template=install_request.yml), written 2026-11-17. It is not filed. It follows the repository's "Install/upgrade request" form, the one JCSDA/spack-stack#2081 used, so each heading below "The issue" is one field of that form. It belongs to `20261113-1349-build-test-kokkos.md` and came out of the first Gaea C7 run (`20261116-1257-gaea-c7-runbook.md`, "Kokkos is not in the environment").

# session

- **Session ID**: `5b8828d0-1bd9-4b6d-9259-4e5762769b1e`
- **Resume Command**: `agy --resume 5b8828d0-1bd9-4b6d-9259-4e5762769b1e`

---

## Before filing

- **The form's first field is an AI policy acknowledgment**, a required checkbox: "I confirm that this contribution was submitted by a real person. If AI tools or agents were used in its preparation, I have personally reviewed and take responsibility for all submitted content, code, and proposed changes." This draft was prepared with an AI assistant, so read it, correct it and make it yours before ticking that box.
- **Check the two things marked "confirm" in "What the draft rests on"** at the end: they are read from the repository's configs, not from the installs.
- **Decide the scope sentence.** The draft asks for Gaea C7 now and the remaining UFS sites in the next 2.2.x update, and offers the common-config alternative as a question. If you would rather ask for C7 only, delete the second half of "Installation timeframe" and the site table's last column.
- The `cc` line repeats the people copied on #2081; edit it.

## The issue

**Title:** `[INSTALL]: enable kokkos, kokkos-kernels and arborx for ufs-weather-model-env on Gaea C7 (and the other UFS sites)`

### Package name

kokkos kokkos-kernels arborx — through the existing `ufs-weather-model-env` variants `+kokkos +arborx`

### Package version/tag

The versions spack-stack 2.2.0 already sets in `configs/common/packages.yaml`: `kokkos@5.1.1`, `kokkos-kernels@5.1.1`, `arborx@2.1`

### Build options

current — `+openmp +serial` for kokkos and kokkos-kernels, `+mpi +openmp +serial` for arborx, as `configs/common/packages.yaml` has them. No CUDA on machines without GPUs (`~cuda`, as the Gaea C6 site config sets it).

### Installation timeframe

Gaea C7 first, and sooner than the next release if that is possible: please add the three packages to the existing 2.2.0 unified environment there (`/ncrc/proj/epic/spack-stack/c7/spack-stack-2.2.0/envs/ue-oneapi-2025.3.1`), or to an environment chained to it. I am testing CECE on C7 now and can test the install as soon as it is there.

For the other sites where the UFS weather model is supported and the variants are not yet on (table below), the next 2.2.x update is fine.

### Other information

This is a follow-up to #2081. That request was answered in #2088 and #2118: `ufs-weather-model-env` gained the `kokkos` and `arborx` variants, and the versions and build options went into `configs/common/packages.yaml`. Thank you, that part works well.

The variants default to off, though, and a site only gets the packages if its own `packages.yaml` requires them. At the `spack-stack-2.2.0` tag, and still on `release/2.2` and `develop`, two sites do:

| Site | `ufs-weather-model-env` in `configs/sites/tier1/<site>/packages.yaml` | Asked for here |
|---|---|---|
| ursa | `+kokkos +arborx` | nothing |
| gaea-c6 | `+kokkos` | `+arborx` |
| gaea-c7 | not set | `+kokkos +arborx` (first) |
| hercules | not set | `+kokkos +arborx` |
| orion | not set | `+kokkos +arborx` |
| the other tier-1 sites | not set | your call: wherever `ufs-weather-model-env` is installed for UFS users |

On Gaea C7 the result is what the config says. With the 2.2.0 environment loaded,

```
module reset
module swap oneapi/2026.0.0 oneapi/2025.3.1
module use /ncrc/proj/epic/spack-stack/c7/spack-stack-2.2.0/envs/ue-oneapi-2025.3.1/modules/Core
module load stack-intel-oneapi-compilers/2025.3.1
module load stack-cray-mpich/9.2.0
module avail kokkos kokkos-kernels arborx esmf mapl
```

lists `esmf` and `mapl` and no `kokkos`, `kokkos-kernels` or `arborx` (2026-11-17, from `gaea75`).

The change I am asking for on C7 is the block Ursa has, in `configs/sites/tier1/gaea-c7/packages.yaml`:

```yaml
  ufs-weather-model-env:
    require::
    - +kokkos
    - +arborx
```

Two details from the sites that already have it, in case they matter on C7: Ursa builds the three packages with `%cxx=gcc` even in its oneAPI environment, and Gaea C6 adds `~cuda` to `kokkos` and `kokkos-kernels`. C7's environment is oneAPI 2025.3.1 with cray-mpich 9.2.0; I do not know whether Kokkos 5.1.1 builds there with `icpx` or needs the same GCC requirement.

A question rather than a request: since two UFS-WM components now depend on these packages, would it be simpler to turn `+kokkos +arborx` on for `ufs-weather-model-env` once, in `configs/common/packages.yaml`, and let a site opt out or add its compiler requirement, rather than opting each site in? Every new site (C7 was added in #2161) would then get them without a follow-up like this one.

Why they are needed: two UFS-WM atmospheric composition components, CATChem and CECE, use Kokkos and kokkos-kernels as their execution engine, and CECE uses ArborX for spatial search. Both find them with `find_package` and are being prepared to build inside the weather model under the UFS modulefiles (the C7 port is ufs-community/ufs-weather-model#3432). Without the packages in the environment CECE falls back to fetching and building its own Kokkos, which works on a login node but not on compute nodes, and is not what the weather model build should rely on.

I am happy to test on Gaea C7 as soon as an install exists, and on Gaea C6 for ArborX.

Thank you!

cc @bbakernoaa @HendrikTolman-NOAA @JacobCarley-NOAA

---

## What the draft rests on

Read from JCSDA/spack-stack on 2026-11-17, at the tag `spack-stack-2.2.0` unless a branch is named:

- **The variants and their defaults**: `repos/spack_stack/spack_repo/spack_stack/packages/ufs_weather_model_env/package.py` — `variant("kokkos", default=False, …)`, `variant("arborx", default=False, …)`; `+arborx` requires `+kokkos`; the comment names #2081, CATChem and CECE.
- **The versions and build options**: `configs/common/packages.yaml` — `arborx` (`@2.1`, `+mpi`, `+openmp`, `+serial`), `kokkos` and `kokkos-kernels` (`@5.1.1`, `+openmp`, `+serial`).
- **The site table**: every `configs/sites/tier1/*/packages.yaml` was read at the tag, on `release/2.2` and on `develop`. Only `ursa` (`+kokkos`, `+arborx`) and `gaea-c6` (`+kokkos`) set `ufs-weather-model-env`; `gaea-c7`, `hercules`, `orion`, `derecho`, `hera`, `acorn`, `wcoss2` and the cloud and non-NOAA sites do not.
- **Ursa's compiler requirement** (`'%cxx=gcc'` for the three packages) and **C6's `~cuda`**: the same two site files.
- **C7's site config**: added by #2161 (2026-10-14); later C7 fixes went to `release/2.2` as #2205 and #2206, so a C7 change probably wants both branches.
- **The C7 observation**: the `module avail` output is in the runbook, "Results so far" and "Kokkos is not in the environment".

To confirm before filing:

- **confirm**: that the Ursa and Gaea C6 *installs* of 2.2.0 really carry the modules. The table is the site configs; nobody has run `module avail kokkos` on those two installs for this draft. (The harness's Ursa run config points at `…/spack-stack/envs/ue-oneapi-2025.3.1` and expects them.)
- **confirm**: the list of "other sites". Hercules and Orion are named because the weather model runs its regression tests there; whether Derecho, Hera or the cloud sites should be in the request is a judgment about who uses `ufs-weather-model-env` where. WCOSS2 installs are requested elsewhere (the form says so), so it is left out.

## Log

- **2026-11-17, first draft.** "draft an issue to enable the kokkos deps for spack-stack 2.2.x installed on c7 and other platforms. write the issue draft to design/feat/20261113-1348-build-test-kokkos" → this file. The form's fields and wording follow `.github/ISSUE_TEMPLATE/install_request.yml` and #2081. Scope chosen for the draft: C7 now (into the existing 2.2.0 environment), ArborX added on C6, Hercules and Orion in the next 2.2.x update, and the common-config default put as a question to the maintainers rather than a demand.
- **2026-11-20, installed on Gaea C7.** The C7 part of this request is done: the user reported `kokkos/5.1.1`, `kokkos-kernels/5.1.1` and `arborx/2.1` in `/ncrc/proj/epic/spack-stack/c7/spack-stack-2.2.0/envs/ue-oneapi-2025.3.1`. Whether this draft was filed, or the install came from the user reaching out directly, is not recorded here. If it is filed later, drop Gaea C7 from the title, the timeframe and the table; the Gaea C6 ArborX and the other sites remain.
