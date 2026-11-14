INFRASTRUCTURE_ROOT ?= ../k8s-infrastructure
K8S_ROOT ?= ../perfeng-k8s
CONTROL_PLANE_ROOT ?= ../perfeng-control-plane
CONTRACTS_ROOT ?= ../perfeng-contracts
NATIVE_TARGET_K8S_ROOT ?= $(K8S_ROOT)
UV_RUN := uv run --locked --offline

.PHONY: argus-campaign-check argus-perfeng-fixture-check help bootstrap doctor fmt fmt-check docs-check check test verify render-check workflow-lint release-workflows-integration native-gateway-check native-target-image-check release-selection-check supply-chain validate

help:
	@echo perfeng-environment engineering command surface
	@echo "  make contracts-inspect-check  Verify offline contract artifact inspection"
	@echo "  make contracts-materializer-check  Verify atomic local projection activation"
	@echo "  make contracts-shadow  Compare the signed tracer with the local contract offline"
	@echo "  make contracts-restore-check  Test cold restoration from retained signed assets"
	@echo "  make contracts-update-check  Verify the signed benchmark schema update path"
	@echo "  make contracts-tool-release-check  Verify materializer release boundaries"
	@echo "  make keycloak-qualification  Run the opt-in disposable Keycloak machine fixture"
	@echo "  make openbao-qualification  Qualify pinned OpenBao with synthetic identities"
	@echo "  make argus-campaign-check  Verify offline shadow campaign aggregation"
	@echo "  make argus-perfeng-fixture-check  Validate disposable joint inputs and safety offline"
	@echo "  make bootstrap  Resolve the locked Python environment"
	@echo "  make doctor     Report the effective engineering toolchain"
	@echo "  make fmt        Format Python sources"
	@echo "  make fmt-check  Verify Python formatting without changes"
	@echo "  make docs-check Verify repository-local documentation and policy"
	@echo "  make check      Run static and repository-local configuration checks"
	@echo "  make test       Run the unit-test suite"
	@echo "  make verify     Run the network-independent inner loop"
	@echo "  make render-check  Validate Helm charts offline"
	@echo "  make workflow-lint  Lint the staged release WorkflowTemplate offline with Argo v4.1.5"
	@echo "  make release-workflows-integration  Exercise release boundaries on isolated GitHub-hosted kind"
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
	$(UV_RUN) python scripts/browser_host.py validate --profile profiles/browser/linux-four-core-4k.yaml
	$(UV_RUN) python scripts/native_target.py validate
	$(UV_RUN) python -m scripts.kubernetes_benchmark validate

test:
	$(UV_RUN) python -m unittest discover -s tests -v

verify: check test

render-check:
	$(UV_RUN) python scripts/validate_cluster.py

workflow-lint:
	argo lint --offline --kinds=workflowtemplates gitops/release-workflows

# Separate live CI tier; refuses local/self-hosted runners and ambient kubeconfigs.
release-workflows-integration:
	bash tests/integration/release-workflows/run.sh

native-gateway-check:
	$(UV_RUN) python scripts/native_connectivity.py validate --execute

native-target-image-check:
	$(UV_RUN) python scripts/native_target.py build --execute

release-selection-check:
	$(UV_RUN) python -m scripts.kubernetes_benchmark validate --contracts-root "$(CONTRACTS_ROOT)"
	$(UV_RUN) python scripts/infrastructure_release.py validate --infrastructure-root "$(INFRASTRUCTURE_ROOT)"
	$(UV_RUN) python scripts/infrastructure_runtime.py cluster ensure --infrastructure-root "$(INFRASTRUCTURE_ROOT)"
	$(UV_RUN) python scripts/infrastructure_runtime.py argocd validate --infrastructure-root "$(INFRASTRUCTURE_ROOT)"
	$(UV_RUN) python scripts/resource_metrics_release.py validate --infrastructure-root "$(INFRASTRUCTURE_ROOT)"
	$(UV_RUN) python scripts/infrastructure_runtime.py metrics health --infrastructure-root "$(INFRASTRUCTURE_ROOT)"
	$(UV_RUN) python scripts/control_plane_release.py validate --k8s-root "$(K8S_ROOT)" --control-plane-root "$(CONTROL_PLANE_ROOT)"
	$(UV_RUN) python scripts/control_plane_runtime.py validate
	$(UV_RUN) python scripts/native_target_release.py validate --k8s-root "$(NATIVE_TARGET_K8S_ROOT)"

supply-chain:
	uv lock --check --offline
	uv audit --locked

validate: verify render-check supply-chain

# Offline only; live preparation requires the documented --execute path.
argus-perfeng-fixture-check:
	$(UV_RUN) python tests/integration/argus-perfeng/fixture.py
	$(UV_RUN) python -m unittest tests.test_argus_fixture tests.test_argus_serving tests.test_argus_flow tests.test_argus_recovery tests.test_argus_shadow -v

argus-campaign-check:
	$(UV_RUN) python -m unittest tests.test_argus_campaign -v

# The verifier is independently prepared and never downloaded by this check.
.PHONY: contracts-tools-check
contracts-tools-check:
	$(UV_RUN) python -m scripts.contracts_distribution_tools --verifier "$(CONTRACTS_VERIFIER)"

.PHONY: contracts-inspect-check
contracts-inspect-check:
	$(UV_RUN) python -m unittest tests.test_contract_inspect -v

.PHONY: contracts-materializer-check
contracts-materializer-check:
	$(UV_RUN) python -m unittest discover -s tests -p 'test_contract_materialize.py' -v

.PHONY: contracts-tool-release-check
contracts-tool-release-check:
	$(UV_RUN) python -m unittest discover -s tests -p test_contract_tool_release.py -v

.PHONY: openbao-qualification
openbao-qualification:
	$(UV_RUN) python -m unittest discover -s tests -p test_openbao_qualification.py -v
	$(UV_RUN) python -m scripts.openbao_qualification --bao "$(OPENBAO_BIN)" --execute

.PHONY: contracts-shadow
contracts-shadow:
	$(UV_RUN) python -m scripts.contracts_shadow --assets "$(CONTRACTS_SHADOW_ASSETS)" --verifier "$(CONTRACTS_VERIFIER)"

.PHONY: contracts-restore-check
contracts-restore-check:
	$(UV_RUN) python -m unittest discover -s tests -p test_contract_restore.py -v
ifneq ($(strip $(CONTRACTS_RETAINED_ASSETS)),)
	$(UV_RUN) python -m scripts.contracts_restore --retained "$(CONTRACTS_RETAINED_ASSETS)" --verifier "$(CONTRACTS_VERIFIER)"
endif

.PHONY: contracts-update-check
contracts-update-check:
	$(UV_RUN) python -m unittest discover -s tests -p test_contract_update.py -v
ifneq ($(strip $(CONTRACTS_UPDATE_ASSETS)),)
	$(UV_RUN) python -m scripts.contracts_update --assets "$(CONTRACTS_UPDATE_ASSETS)" --verifier "$(CONTRACTS_VERIFIER)"
endif

.PHONY: contracts-campaign-check
contracts-campaign-check:
	$(UV_RUN) python -m unittest discover -s tests -p test_contract_campaign.py -v
ifneq ($(strip $(CONTRACTS_CAMPAIGN_MANIFEST)),)
	@$(UV_RUN) python -m scripts.contracts_campaign --manifest "$(CONTRACTS_CAMPAIGN_MANIFEST)" $(CONTRACTS_CAMPAIGN_ARGS)
endif

.PHONY: keycloak-qualification
keycloak-qualification:
	$(UV_RUN) python -m unittest discover -s tests -p test_keycloak_qualification.py -v
	$(UV_RUN) python -m scripts.keycloak_qualification --execute
