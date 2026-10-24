"""Offline protocol regressions; real authorization is proved in disposable CI."""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_release_workflows_integration import BASH, FIXTURE

HARNESS = r"""
set -euo pipefail
fixture=$1
results=$fixture/results
mkdir -p "$results"
touch "$fixture/owned"
export KUBECONFIG=$fixture/kubeconfig
mode=boundaries
cluster_uid=owned-cluster
source_dir=$2
k() {
  printf '%s\0' "$@" >> "$fixture/calls"
  printf '\n' >> "$fixture/calls"
  if [[ $1 == get ]]; then printf '%s' "$cluster_uid"; return 0; fi
  if [[ $* == *'auth can-i'* ]]; then
    if [[ $CASE == review-error ]]; then echo yes; echo private-server-value >&2; return 1; fi
    if [[ $* == *'--subresource=exec'* ]]; then
      if [[ $CASE == exec-allowed ]]; then echo yes; return 0; fi
      echo no; return 1
    fi
    echo yes; return 0
  fi
  local args=("$@") ns= verb= resource= group= identity=system:serviceaccount:argo-workflows:release-adoption
  for ((i=0; i<${#args[@]}; i++)); do
    if [[ ${args[i]} == -n ]]; then ns=${args[i+1]}; fi
    case ${args[i]} in patch|delete|create|get) verb=${args[i]}; resource=${args[i+1]};
      if [[ $resource == -f ]]; then
        case ${args[i+2]} in
          */authority-configmap.yaml) resource=configmap ;;
          */authority-secret.yaml) resource=secret ;;
          */authority-job.yaml) resource=job ;;
          *) return 3 ;;
        esac
      fi
      break ;; esac
  done
  case $resource in
    configmap) resource=configmaps ;; secret|secrets) resource=secrets ;;
    job) resource=jobs; group=batch ;; deployment) resource=deployments; group=apps ;;
    application) resource=applications; group=argoproj.io ;;
    *) return 3 ;;
  esac
  if [[ $verb == get ]]; then verb=list; fi
  case $CASE in
    allowed) echo private-server-value; return 0 ;;
    transport) echo 'private-server-value: timeout' >&2; return 1 ;;
    notfound) echo 'Error from server (NotFound): private-server-value' >&2; return 1 ;;
    wrong-user) identity=other-user ;; wrong-verb) verb=watch ;;
    wrong-resource) resource=pods ;; wrong-namespace) ns=other ;;
    wrong-group) group=other ;;
  esac
  local qualified=$resource name=''
  [[ -z $group ]] || qualified="$resource.$group"
  [[ $CASE != named ]] || name=' "ci-adoption-denied"'
  local prefix="Error from server (Forbidden): $qualified$name is forbidden:"
  if [[ $CASE == wrong-wrapper && $verb == create ]]; then
    prefix='error: unrelated client error:'
  fi
  printf '%s User "%s" cannot %s resource "%s" in API group "%s" in the namespace "%s"\n' \
    "$prefix" \
    "$identity" "$verb" "$resource" "$group" "$ns" >&2
  if [[ $CASE == timeout ]]; then return 124; fi
  return 1
}
snapshot() {
  if [[ $CASE == changed && -f $fixture/sampled ]]; then echo changed > "$1"; else echo original > "$1"; fi
  touch "$fixture/sampled"
}
source "$2/adoption-authority.sh"
if [[ ${SMOKE:-} == yes ]]; then verify_adoption_create_authority; else
verify_adoption_authority "receipt.$(printf 'a%.0s' {1..32}).$(printf 'b%.0s' {1..32})" snapshot
fi
"""


@unittest.skipUnless(BASH, "Bash is required for actual fixture protocol tests")
class AdoptionAuthorityTests(unittest.TestCase):
    def run_case(self, case, environment=None):
        assert BASH is not None
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result = subprocess.run(
                [BASH, "-c", HARNESS, "authority-test", directory.as_posix(), FIXTURE.as_posix()],
                env={
                    **os.environ,
                    "CASE": case,
                    "GITHUB_ACTIONS": "true",
                    "RUNNER_ENVIRONMENT": "github-hosted",
                    "RUNNER_OS": "Linux",
                    **(environment or {}),
                },
                capture_output=True,
                text=True,
                # Git Bash command substitutions incur Windows process startup cost.
                timeout=120,
            )
            self.assertTrue((directory / "calls").exists(), result.stderr)
            calls = [
                [part.decode() for part in row.split(b"\0") if part]
                for row in (directory / "calls").read_bytes().splitlines()
            ]
            report = directory / (
                "results/adoption-authority-smoke.tsv"
                if (environment or {}).get("SMOKE") == "yes"
                else "results/adoption-authority.tsv"
            )
            evidence = report.read_text() if report.exists() else ""
        self.assertNotIn("private-server-value", result.stdout + result.stderr + evidence)
        return result, calls, evidence

    def test_denials_use_full_identity_server_dry_run_and_preserve_selected_ledger(self):
        result, calls, evidence = self.run_case("denied")
        self.assertEqual(result.returncode, 0, result.stderr)
        probes = [args for args in calls if args[0].startswith("--as=")]
        self.assertEqual(len(probes), 14)
        for args in probes:
            self.assertEqual(
                args[:4],
                [
                    "--as=system:serviceaccount:argo-workflows:release-adoption",
                    "--as-group=system:serviceaccounts",
                    "--as-group=system:serviceaccounts:argo-workflows",
                    "--as-group=system:authenticated",
                ],
            )
            if "auth" in args:
                continue
            if "get" in args:
                self.assertIn("secrets", args)
                self.assertEqual(args[-2:], ["-o", "name"])
            else:
                self.assertIn("--dry-run=server", args)
                self.assertNotIn("--dry-run=client", args)
                self.assertNotIn("exec", args)
        self.assertEqual(evidence.count("\tforbidden\t"), 11)
        self.assertTrue(evidence.endswith("suite\tverified\n"))
        self.assertEqual(sum(args[0] == "get" for args in calls), 2)

    def test_unexpected_permission_and_unrelated_failures_never_pass_or_leak_output(self):
        for case in (
            "allowed",
            "transport",
            "notfound",
            "wrong-user",
            "wrong-verb",
            "wrong-resource",
            "wrong-namespace",
            "wrong-group",
            "wrong-wrapper",
            "timeout",
            "review-error",
            "exec-allowed",
            "changed",
        ):
            with self.subTest(case=case):
                result, calls, evidence = self.run_case(case)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(evidence.endswith("suite\tverified\n"))
                if case == "allowed":
                    self.assertEqual(sum("--dry-run=server" in args for args in calls), 10)

    def test_both_fixtures_share_authority_verification_before_destructive_faults(self):
        for file, call, fault in (
            (
                "ledger.sh",
                'verify_adoption_authority "$receipt_name" snapshot',
                "\nrecovery_refusals",
            ),
            (
                "adoption-parent.sh",
                'verify_adoption_authority "$adoption_receipt" adoption_records',
                "# Owner-approved destructive faults",
            ),
        ):
            source = (FIXTURE / file).read_text()
            self.assertIn('source "$source_dir/adoption-authority.sh"', source)
            self.assertLess(source.index(call), source.index(fault))

    def test_named_and_collection_denials_share_the_same_authorization_contract(self):
        result, _, evidence = self.run_case("named")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(evidence.count("\tforbidden\t"), 11)

    def test_early_smoke_needs_no_ledger_records_and_reports_every_mismatch(self):
        result, calls, evidence = self.run_case("wrong-wrapper", {"SMOKE": "yes"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 4)
        self.assertEqual(evidence.count("\trefusal-mismatch\t"), 3)
        self.assertEqual(evidence.count("\tforbidden\t"), 1)
        self.assertTrue(evidence.endswith("suite\tfailed\n"))
        self.assertIn("matches=011111", result.stderr)

    def test_create_probes_are_single_object_manifests_and_precede_the_controllers(self):
        import yaml

        for kind, namespace in (
            ("ConfigMap", "argo-workflows"),
            ("Secret", "argo-workflows"),
            ("Job", "perf-platform"),
        ):
            file = FIXTURE / ("authority-" + kind.lower() + ".yaml")
            documents = list(yaml.safe_load_all(file.read_text()))
            self.assertEqual(len(documents), 1)
            self.assertEqual(documents[0]["kind"], kind)
            self.assertEqual(
                documents[0]["metadata"], {"name": "ci-adoption-denied", "namespace": namespace}
            )
        source = (FIXTURE / "run.sh").read_text()
        self.assertLess(
            source.index("verify_adoption_create_authority\n"),
            source.index('"$fixture/namespace-install.yaml"'),
        )

    def test_stage_runner_preserves_worker_failure_and_never_marks_it_successful(self):
        assert BASH is not None
        source = (FIXTURE / "run.sh").read_text()
        functions = "\n".join(
            re.findall(r"^(?:start_stage|complete_stage|run_stage)\(\).*", source, re.M)
        )
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = (
                "set -euo pipefail\nresults=$1\n"
                + functions
                + "\nworker() { return 7; }\nrun_stage protocol worker\necho unsafe-success\n"
            )
            result = subprocess.run(
                [BASH, "-c", script, "stage-test", directory.as_posix()],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 7)
            self.assertNotIn("unsafe-success", result.stdout)
            self.assertEqual((directory / "stages.tsv").read_text(), "protocol\tstarted\t0\n")


if __name__ == "__main__":
    unittest.main()
