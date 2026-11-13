// Smoke test for the spack-stack-provided Kokkos and kokkos-kernels
// (JCSDA/spack-stack#2081): initialize Kokkos, print the version and
// enabled backends, run a trivial parallel_for, and exercise one
// kokkos-kernels routine (BLAS1 axpby).

#include <Kokkos_Core.hpp>
#include <KokkosBlas1_axpby.hpp>

#include <cmath>
#include <iostream>

int main(int argc, char* argv[]) {
  Kokkos::initialize(argc, argv);
  int failures = 0;
  {
    std::cout << "Kokkos version: " << KOKKOS_VERSION_MAJOR << "."
              << KOKKOS_VERSION_MINOR << "." << KOKKOS_VERSION_PATCH << "\n";
    std::cout << "Default execution space: "
              << Kokkos::DefaultExecutionSpace::name() << "\n";
    std::cout << "== Kokkos configuration ==\n";
    Kokkos::print_configuration(std::cout, true);

    constexpr int n = 1000;
    Kokkos::View<double*> x("x", n);
    Kokkos::View<double*> y("y", n);
    Kokkos::parallel_for(
        "smoke_fill", n, KOKKOS_LAMBDA(const int i) {
          x(i) = 1.0;
          y(i) = 2.0;
        });

    // y = 3*x + 1*y -> every entry becomes 5.0
    KokkosBlas::axpby(3.0, x, 1.0, y);

    double sum = 0.0;
    Kokkos::parallel_reduce(
        "smoke_check", n,
        KOKKOS_LAMBDA(const int i, double& acc) { acc += y(i); }, sum);

    const double expected = 5.0 * n;
    std::cout << "axpby checksum: " << sum << " (expected " << expected
              << ")\n";
    if (std::abs(sum - expected) > 1e-10) {
      std::cerr << "SMOKE TEST FAILED\n";
      failures = 1;
    } else {
      std::cout << "SMOKE TEST PASSED\n";
    }
  }
  Kokkos::finalize();
  return failures;
}
