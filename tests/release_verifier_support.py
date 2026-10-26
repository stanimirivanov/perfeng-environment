"""Load the exact installed verifier package for isolated unit tests."""

import importlib.util
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "gitops/release-workflows/verifiers-v3"


def load(name):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, PACKAGE / (name + ".py"))
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


policy = load("release_policy_v3")
runtime = load("release_runtime_v3")
