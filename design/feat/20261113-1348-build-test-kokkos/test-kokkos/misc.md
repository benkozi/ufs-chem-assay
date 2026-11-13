# ursa

rm -rf /scratch3/NCEPDEV/stmp/Benjamin.Koziol/my_stmp/test-kokkos && git pull && bash build-test-kokkos-suite.sh cece --upload-logs

-----

git pull && bash build-test-kokkos-suite.sh cece
git pull && bash build-test-kokkos-suite.sh all
bash upload-logs.sh

rm -rf /scratch3/NCEPDEV/stmp/Benjamin.Koziol/my_stmp/test-kokkos && git pull && bash build-test-kokkos.sh cece --srun-build

rm -rf /scratch3/NCEPDEV/stmp/Benjamin.Koziol/my_stmp/test-kokkos && git pull && bash build-test-kokkos.sh cece --srun-build --compiler gcc

rm -rf /scratch3/NCEPDEV/stmp/Benjamin.Koziol/my_stmp/test-kokkos && git pull && bash build-test-kokkos.sh cece --build-only && \
  git pull && bash build-test-kokkos.sh cece --direct-launch --test-only

rm -rf /scratch3/NCEPDEV/stmp/Benjamin.Koziol/my_stmp/test-kokkos && git pull && bash build-test-kokkos.sh cece --build-only --compiler gcc && \
  git pull && bash build-test-kokkos.sh cece --direct-launch --test-only


rm -rf /scratch3/NCEPDEV/stmp/Benjamin.Koziol/my_stmp/test-kokkos && git pull && bash build-test-kokkos.sh cece
rm -rf /scratch3/NCEPDEV/stmp/Benjamin.Koziol/my_stmp/test-kokkos && git pull && bash build-test-kokkos.sh cece --compiler gcc

git pull && bash ./build-test-kokkos.sh --test-only --test-filter '^nuopc_contract_valid$' catchem
git pull && bash ./build-test-kokkos.sh --test-only --test-filter 'nuopc' catchem

cp /scratch3/NCEPDEV/stmp/Benjamin.Koziol/my_stmp/test-kokkos/logs/* ../../tasks/artifacts/20260813-1605-kokkos-spack-stack/&& git add ../../tasks/artifacts/20260813-1605-kokkos-spack-stack/ && git commit -m "add log" && git push

# gaea-c6

rm -rf /gpfs/f6/bil-fire3/scratch/Benjamin.Koziol/stmp/test-kokkos && git pull && bash build-test-kokkos.sh catchem --platform gaea-c6

cp /gpfs/f6/bil-fire3/scratch/Benjamin.Koziol/stmp/test-kokkos/logs/* ../../tasks/artifacts/20260814-1243-kokkos-spack-stack-gaeac6 && git add ../../tasks/artifacts/20260814-1243-kokkos-spack-stack-gaeac6 && git commit -m "add log" && git push