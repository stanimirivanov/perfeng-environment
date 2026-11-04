"""Prove post-deadline evidence recovery across real Argus pod replacement."""

import importlib
import json
import time
from datetime import UTC, datetime
from uuid import UUID

acceptance = importlib.import_module("tests.integration.argus-perfeng.acceptance")
serving = acceptance.serving


def instant(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("recovery timestamps require a time zone")
    return result


def publication_lateness(published, deadline, recovered):
    publication, limit, observation = map(instant, (published, deadline, recovered))
    if observation <= limit or observation < publication:
        raise ValueError("recovery must occur after publication and the request deadline")
    return publication >= limit


class Recovery(acceptance.Acceptance):
    def argus_pod(self, paths):
        self.argus_manifest = super().argus_pod(paths)
        return self.argus_manifest

    def pod_uid(self):
        return str(
            UUID(
                self.k(
                    "-n",
                    serving.PLATFORM,
                    "get",
                    "pod/argus",
                    "-o",
                    "jsonpath={.metadata.uid}",
                ).strip()
            )
        )

    def stop_argus(self):
        uid = self.pod_uid()
        self.k(
            "delete",
            "--raw",
            f"/api/v1/namespaces/{serving.PLATFORM}/pods/argus",
            "-f",
            "-",
            data=json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "DeleteOptions",
                    "gracePeriodSeconds": 1,
                    "preconditions": {"uid": uid},
                }
            ),
        )
        self.k(
            "-n",
            serving.PLATFORM,
            "wait",
            "--for=delete",
            "pod/argus",
            "--timeout=30s",
            timeout=35,
        )
        return uid

    def start_argus(self, previous):
        self.apply(self.argus_manifest)
        self.wait("argus", serving.PLATFORM)
        uid = self.pod_uid()
        if uid == previous:
            raise ValueError("Argus pod was not replaced")
        return uid

    def analysis_rows(self):
        return self.db("""
SELECT coalesce(json_agg(json_build_object('position', position, 'event', event_id,
    'reportSha256', encode(sha256(report), 'hex'), 'fingerprint', fingerprint,
    'recordedAt', recorded_at) ORDER BY position, event_id), '[]'::json)
FROM argus_catalog.performance_analysis_events;
""")

    def before_review(self, publication, admitted):
        print("Interrupting Argus after durable publication and before consumption", flush=True)
        original = self.stop_argus()
        if json.loads(self.analysis_rows()):
            raise ValueError("analysis was already retained before interruption")
        context = publication["data"]["context"]
        deadline = instant(context["deadline"])
        remaining = (deadline - datetime.now(UTC)).total_seconds()
        if remaining > 65:
            raise ValueError("fixture recovery deadline exceeds the allowed window")
        time.sleep(max(0, remaining) + 0.2)
        replacement = self.start_argus(original)
        recovered = datetime.now(UTC).isoformat()
        self.recovery_receipt = {
            "initialPodUid": original,
            "recoveryPodUid": replacement,
            "deadline": context["deadline"],
            "publishedAt": publication["time"],
            "recoveryReadyAt": recovered,
            "publicationLate": publication_lateness(
                publication["time"], context["deadline"], recovered
            ),
            "eventId": publication["id"],
        }

    def export(self, admitted, run, report_sha):
        before = self.analysis_rows()
        [row] = json.loads(before)
        if row["event"] != self.recovery_receipt["eventId"] or row["reportSha256"] != report_sha:
            raise ValueError("recovered report identity changed")
        if (
            publication_lateness(
                self.recovery_receipt["publishedAt"],
                self.recovery_receipt["deadline"],
                row["recordedAt"],
            )
            is not self.recovery_receipt["publicationLate"]
        ):
            raise ValueError("retained analysis has an inconsistent publication time")
        projection = super().export(admitted, run, report_sha)
        original = (self.root / "authorized-projection.json").read_bytes()
        print("Restarting Argus to replay the same publication", flush=True)
        previous = self.stop_argus()
        if previous != self.recovery_receipt["recoveryPodUid"]:
            raise ValueError("unexpected Argus pod before replay")
        replay = self.start_argus(previous)

        def replay_observed():
            logs = self.k("-n", serving.PLATFORM, "logs", "pod/argus", "-c", "argus", "--tail=40")
            return any(
                json.loads(line).get("msg") == "performance analysis retained"
                for line in logs.splitlines()
            )

        self.wait_for(replay_observed, "Argus analysis replay", 30)
        if self.analysis_rows() != before:
            raise ValueError("replay changed retained analysis")
        if (
            self.admission(
                admitted["event"]["data"]["context"],
                admitted["requestSha256"],
                admitted["intentFingerprint"],
            )
            != admitted
        ):
            raise ValueError("replay changed the retained admission")
        replay_projection = self.read_projection(admitted["position"]).stdout.encode()
        if replay_projection != original:
            raise ValueError("replay changed the authorized projection bytes")
        document = json.loads(original)
        [decision] = document["analysis"]["decisions"]
        if (
            decision["eventId"] != row["event"]
            or decision["late"] is not self.recovery_receipt["publicationLate"]
        ):
            raise ValueError("authorized projection changed publication lateness or identity")
        self.fixture.evidence["recovery"] = self.recovery_receipt | {
            "replayPodUid": replay,
            "retainedAnalysisEvents": 1,
            "recordedAt": row["recordedAt"],
        }
        return projection


if __name__ == "__main__":
    serving.main(Recovery, description=__doc__)
