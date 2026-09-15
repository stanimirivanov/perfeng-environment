# Architecture decisions

Architecture decisions explain why the environment uses particular tools and
where their responsibility ends. They are historical records: supersede an
accepted decision with a new ADR rather than rewriting its conclusion.

| ADR | Status | Decision |
| --- | --- | --- |
| [0001](0001-gitops-environment-composition.md) | Accepted | Adopt an Argo CD-centred GitOps target architecture through a reversible spike and incremental migration. |
| [0002](0002-stateful-gitops-ownership.md) | Accepted | Preserve StatefulSet, PVC, PV and credential identities through staged GitOps adoption and rollback. |
