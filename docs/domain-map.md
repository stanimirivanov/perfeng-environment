# Perfeng domain map

## TL;DR

- Contracts defines interchange meaning. The owning component enforces decisions and authority at its boundary.
- Control Plane accepts a Run, delegates capture, binds exact evidence, and exposes authorized historical reads.
- Analysis evaluates verified measurements. Deployment and operator verification have separate ownership and lifecycles.
- Read the [shared glossary](https://github.com/stanimirivanov/perfeng-contracts/blob/main/GLOSSARY.md) and the relevant component glossary before interpreting a handoff.

## Ownership and modules

| Repository | Domain responsibility | Code or configuration to inspect |
| --- | --- | --- |
| [perfeng-contracts](https://github.com/stanimirivanov/perfeng-contracts/blob/main/GLOSSARY.md) | Shared interchange contracts and compatibility | `contracts/source/` authors data shapes. `api/` carries Run, Native Worker and Integration HTTP interfaces. |
| [perfeng-control-plane](https://github.com/stanimirivanov/perfeng-control-plane/blob/main/GLOSSARY.md) | Admission, lifecycle, leases, baseline approval, evidence registration and service-scoped reads | `internal/run`, `internal/registry`, `internal/baseline`, `internal/reconcile`, `internal/nativeworker`, `internal/runcomparison`, `internal/runrerun` and `internal/serviceaccess`. |
| [perfeng-analysis](https://github.com/stanimirivanov/perfeng-analysis/blob/main/GLOSSARY.md) | Normalize verified observations and compute measurement quality, SLO, regression and report outcomes | `src/perfeng_analysis/` contains tool-specific normalizers, evaluation and report assembly. |
| [perfeng-k6](https://github.com/stanimirivanov/perfeng-k6/blob/main/GLOSSARY.md) | Versioned API loads, native summary and sample points, raw manifest assembly | `definitions/`, `workloads/` and `scripts/`. |
| [perfeng-playwright](https://github.com/stanimirivanov/perfeng-playwright/blob/main/GLOSSARY.md) | Browser journeys, semantic measurement, diagnostics and one-shot native worker execution | `src/interaction`, `src/journey`, `src/journeys`, diagnostic collectors and `src/native-worker`. |
| [perfeng-k8s](https://github.com/stanimirivanov/perfeng-k8s/blob/main/GLOSSARY.md) | Immutable deployment packaging, Job templates and reviewed runtime inputs | `bundles/`, `charts/`, `execution/` and `packages/runtime-inputs/`. |
| [perfeng-environment](../GLOSSARY.md) | Workspace and deployment composition, GitOps selection and release evidence | `workspace.json`, `deployments/`, `gitops/` and `scripts/`. |
| [perfeng-console](https://github.com/stanimirivanov/perfeng-console/blob/main/GLOSSARY.md) | Operator Run journey, BFF and separately authorized Scenario Lab | `apps/console/` and `src/bff/` for the product. `src/scenarios/` and `src/simulator/` declare intended evaluator and runner boundaries. |

## Run and evidence flow

1. Control Plane resolves the exact reviewed test, workload, candidate, environment and policy selections, checks current authority, and records an accepted Run. Integration admission retains the original issuer-scoped request and its association with the native Run.
2. A reviewed Kubernetes Job or leased Native Worker attempt produces raw evidence. k6 carries forward original summary and sample points. Playwright records semantic timings and separate browser diagnostics, then uses lease-scoped transfer and registration where enabled.
3. An Artifact Reference identifies exact bytes. The raw manifest describes the measurement window and raw evidence. Analysis receives verified bytes, normalizes the selected evidence and evaluates the configured policy using any caller-selected approved baseline.
4. Control Plane retains the exact report-time binding, registers the report and can retain a request-linked Analysis Publication. Run Comparison projects that historical evidence under current read authority. Run completion, evidence availability and scientific verdict are independent.
5. Console reads the authorized Run and evidence through the BFF. The approved Scenario Lab design separates frozen expectations and independent Findings from the performance report verdict. Staged or proposed features must retain their availability status in operator documentation.

Sources include the [Run API](https://github.com/stanimirivanov/perfeng-contracts/blob/main/docs/run-management-api.md), [artifact transport](https://github.com/stanimirivanov/perfeng-contracts/blob/main/docs/artifact-and-result-transport.md), [Analysis Publication](https://github.com/stanimirivanov/perfeng-contracts/blob/main/docs/performance-analysis.md) and [Console ownership](https://github.com/stanimirivanov/perfeng-console/blob/main/docs/adr/0001-own-operator-console-and-bff.md).

## Authority and terminology

- A Native Worker lease can be replaced while the Run ID stays the same. A retry repeats the same operation within its original identity, receipt recovery retrieves an existing acceptance, and a rerun requests a distinct Run from a frozen selection. A fresh Integration attempt has fresh request and attempt identities.
- An AVAILABLE Run Comparison can carry an INCONCLUSIVE report. A subsequently retired baseline can still appear in authorized historical evidence. Current eligibility controls new execution.
- The SUT environment describes the system under test. Browser environment and comparison cohort describe qualified measurement conditions. Browser baseline mode describes instrumentation, while a Baseline Version identifies candidate reference evidence and its approval history.
- Policy identity is independent of Test ID. The reviewed registry binds applicability. Analysis currently requires equal names, so a differently named reviewed pair still fails evaluation until a separate compatibility change lands. [Policy applicability ADR](https://github.com/stanimirivanov/perfeng-control-plane/blob/main/docs/adr/0049-bind-policy-applicability-by-reviewed-test-selection.md)

## Configuration and deployment flow

Service and component owners author their configuration. Environment owns reviewed proposal routes and selects published inputs. Kubernetes packaging assembles immutable Runtime Registry Bundles, while the Control Plane’s loader enforces its runtime admission contract. A source-checkout branch is a development default. A Deployment Lock pins artifacts for a target environment. Argo CD reconciles desired state, and staged release workflows handle finite operational steps. The Control Plane owns performance Runs independently of release orchestration. [Proposal routing ADR](adr/0076-route-configuration-proposals-to-source-owners.md) · [Release orchestration ADR](adr/0014-separate-release-and-run-orchestration.md)
