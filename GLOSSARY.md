# Environment composition language

Terms for selecting and operating an environment built from reviewed component releases. Shared terms are defined in the [Perfeng glossary](https://github.com/stanimirivanov/perfeng-contracts/blob/main/GLOSSARY.md).

## Language

**Workspace Membership**
The set of source checkouts included for joint development. Its branch values are initial clone defaults.

**Environment Deployment**
The selected artifact and configuration identities for a particular environment.

**Deployment Lock**
Reviewed immutable references to deployment inputs such as chart versions, image digests and runtime packages.

**Release Request**
Exact reviewed release intent bound to a target environment and immutable inputs.

**Release Receipt**
Retained evidence of a release operation or verification outcome.

**Release Adoption**
Explicit selection of a verified release as the current environment identity.

**Maintenance Checkpoint**
Evidence of the reviewed paused state and required quiescence before a bounded release operation.

**Proposal Route**
Reviewed mapping from a configuration proposal type to the owning Git repository and path, including the associated publisher.
