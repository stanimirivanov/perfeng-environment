INFRASTRUCTURE_ROOT ?= ../k8s-infrastructure
K8S_ROOT ?= ../perfeng-k8s
CONTROL_PLANE_ROOT ?= ../perfeng-control-plane
UV_RUN := uv run --locked --offline

.PHONY: help bootstrap doctor fmt fmt-check docs-check check test verify render-check native-gateway-check native-target-image-check release-selection-check supply-chain validate

help:
	@echo perfeng-environment engineering command surface
	@echo "  make bootstrap  Resolve the locked Python environment"
	@echo "  make doctor     Report the effective engineering toolchain"
	@echo "  make fmt        Format Python sources"
	@echo "  make fmt-check  Verify Python formatting without changes"
	@echo "  make docs-check Verify repository-local documentation and policy"
	@echo "  make check      Run static and repository-local configuration checks"
	@echo "  make test       Run the unit-test suite"
	@echo "  make verify     Run the network-independent inner loop"
	@echo "  make render-check  Validate Helm charts offline"
	@echo "  make native-gateway-check  Adapt the native HTTPS gateway with reviewed Caddy"
	@echo "  make native-target-image-check  Build and verify an isolated development candidate"
	@echo "  make release-selection-check  Validate exact adjacent release selections"
	@echo "  make supply-chain  Verify the lock and audit dependencies"
	@echo "  make validate   Run complete non-live acceptance"

bootstrap:
	uv sync --locked

doctor:
	@git --version
	@uv --version
	@$(UV_RUN) python --version
	@$(MAKE) --version

fmt:
	$(UV_RUN) ruff format .

fmt-check:
	$(UV_RUN) ruff format --check .

docs-check:
	$(UV_RUN) python scripts/repository_policy.py

check: fmt-check docs-check
	$(UV_RUN) ruff check .
	$(UV_RUN) ty check
	$(UV_RUN) python scripts/workspace.py validate
	$(UV_RUN) python scripts/gitops.py validate
	$(UV_RUN) python scripts/browser_host.py validate
	$(UV_RUN) python scripts/browser_host.py validate --profile profiles/browser/windows-four-core-4k.yaml
	$(UV_RUN) python scripts/native_target.py validate

test:
	$(UV_RUN) python -m unittest discover -s tests -v

verify: check test

render-check:
	$(UV_RUN) python scripts/validate_cluster.py

native-gateway-check:
	$(UV_RUN) python scripts/native_connectivity.py validate --execute

native-target-image-check:
	$(UV_RUN) python scripts/native_target.py build --execute

release-selection-check:
	$(UV_RUN) python scripts/infrastructure_release.py validate --infrastructure-root "$(INFRASTRUCTURE_ROOT)"
	$(UV_RUN) python scripts/infrastructure_runtime.py cluster ensure --infrastructure-root "$(INFRASTRUCTURE_ROOT)"
	$(UV_RUN) python scripts/infrastructure_runtime.py argocd validate --infrastructure-root "$(INFRASTRUCTURE_ROOT)"
	$(UV_RUN) python scripts/control_plane_release.py validate --k8s-root "$(K8S_ROOT)" --control-plane-root "$(CONTROL_PLANE_ROOT)"
	$(UV_RUN) python scripts/control_plane_runtime.py validate

supply-chain:
	uv lock --check --offline
	uv audit --locked

validate: verify render-check supply-chain
