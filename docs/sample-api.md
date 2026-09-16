# Local sample API

## TL;DR

- Argo CD is the only supported deployer for the local sample SUT.
- Validate the chart offline, then use `gitops.py health --execute` after merge.
- Recover a bad deployment by reverting reviewed Git state; do not run a second
  Helm reconciler.
- The fixture proves platform wiring and failure handling, not realistic
  application performance.

The sample-sut chart now serves a stateless API matching the existing checkout,
search, and account k6 scenarios. It replaces the earlier HTTP echo response.
It tests platform wiring and failure handling, not realistic application latency,
capacity, payment processing, authentication, or database persistence.

## Endpoints

| Request | Normal response |
| --- | --- |
| GET /healthz | 200, status ok |
| GET / | 200, fixture name/version/mode |
| GET /api/v1/cart | 200, cartId example-cart |
| POST /api/v1/checkout | 201, orderId fixture-order |
| GET /api/v1/search?q=anything | 200, fixed results |
| GET /api/v1/users/any-id | 200, fixed fixture profile |
| PUT /api/v1/users/any-id/preferences | 200, updated true |

Checkout requires a JSON object containing cartId equal to example-cart.
Preferences accepts a JSON object but stores no changes. Unknown paths/versions
return 404. Malformed/non-object bodies return 400; bodies over 16 KiB return
413. Chunked request bodies are intentionally unsupported. Connections have a
five-second socket timeout and close after the response.

The fixture does not log request paths, headers, or bodies and does not reflect
arbitrary input in responses. It has no external calls or production credentials.
Python's threaded standard-library HTTP server is used only as a local functional
fixture; this is not a production server or performance reference.

## Validate and deploy

The GitOps-managed local environment deploys this chart through the
`perfeng-local-sample-sut` Application. After the GitOps change is merged, wait
for the child and workload health:

~~~sh
uv run --locked python scripts/gitops.py health --execute
~~~

~~~sh
uv run --locked python -m unittest discover -s tests
uv run --locked python scripts/validate_cluster.py
uv run --locked python scripts/check_sample_api.py --runner-repo ../perfeng-k6
~~~

The optional k6 check requires the sibling runner and k6 2.2.0. It runs three
success cases and five deliberate failures against an ephemeral loopback server.
It uses one iteration per case with explicit functional thresholds, not the
published performance profiles. Success must exit zero with no transaction
errors; deliberate failures must exit 99 with a transaction error rate of one.
It clears inherited k6 settings, authorization tokens, and proxy overrides.
Temporary summaries are removed after checking; these are not retained run
artifacts. It does not modify the runner repository or Kubernetes.

GitOps deployment keeps the existing Service name, port, namespace and SUT-node
placement. It does not touch PostgreSQL or SeaweedFS.
The Python 3.12.14-slim-bookworm runtime is digest-pinned. API code is owned here
under charts/sample-sut/files/server.py and mounted read-only through a ConfigMap.
Its SHA-256 is included in the pod template so source changes trigger rollout.
This is local chart composition, not a separately published application image.

Port-forward as described in the local Kubernetes guide, then request
http://127.0.0.1:8080/healthz. The root now returns JSON rather than echo text.

## Negative-test modes

The default fixtureMode is ok. Allowed alternatives are cart-failure,
search-failure, preferences-failure, missing-order, and malformed-order.
The first three return HTTP 500 on their respective endpoint. Missing-order
returns a successful checkout status without orderId; malformed-order returns
invalid JSON with a successful checkout status. Health remains available in all
modes: business failure must not look like Kubernetes readiness failure.

Modes are startup configuration only, never controlled by request headers/query
parameters. For a deliberate local negative deployment, change `fixtureMode` in
reviewed Git state, merge it, observe the behavior and revert the change. Do not
introduce a manual Helm override beside the Argo CD reconciler. The default chart
mode is `ok`; verify the root response after reconciliation.

## Ownership and next step

Endpoint behavior is aligned with the fixture and scenarios in perfeng-k6;
neither those sources nor their workload definitions are copied or changed.
The environment owns this disposable integration target. Real SUT implementations
belong to their owning projects.

The next runner integration still needs immutable runner-image identity,
Kubernetes Jobs, raw output collection, least-privilege S3 upload, and contract
provenance. Passing these fixture checks does not establish those capabilities.
