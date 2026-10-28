"""Verify the environment: Python version, packages, config, seeded RNG."""
import sys

import numpy as np
import pandas as pd

from mfgsched.config import GeneratorConfig
from mfgsched.ids import machine_id, product_id


def main() -> None:
    assert sys.version_info >= (3, 10), "Python 3.10+ is required"
    print(f"Python  : {sys.version.split()[0]}")
    print(f"pandas  : {pd.__version__}")
    print(f"numpy   : {np.__version__}")

    cfg = GeneratorConfig.full()
    print(f"\nFull config   : {cfg.n_skus} SKUs, {cfg.n_machines} machines, "
          f"{cfg.n_orders} orders, {cfg.n_operations} operations, "
          f"{cfg.n_materials} materials (seed {cfg.seed})")
    st = GeneratorConfig.starter()
    print(f"Starter config: {st.n_skus} SKUs, {st.n_machines} machines, "
          f"{st.n_orders} orders")

    print(f"\nSample IDs: {product_id(1)}, {machine_id(1)}")

    # Reproducibility smoke test: same seed -> identical numbers
    a = np.random.default_rng(cfg.seed).integers(0, 1000, 5)
    b = np.random.default_rng(cfg.seed).integers(0, 1000, 5)
    assert (a == b).all(), "Seeded RNG is not reproducible!"
    print(f"Seeded RNG  : {a.tolist()} (identical on both runs)")

    print("\nEnvironment OK")


if __name__ == "__main__":
    main()