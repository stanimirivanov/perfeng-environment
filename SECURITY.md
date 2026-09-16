# Security policy

## TL;DR

- Do not disclose suspected vulnerabilities in public issues, discussions or
  pull requests.
- Use GitHub private vulnerability reporting when the repository exposes it.
- If no private channel is available, request one without sensitive details.
- The project is pre-release; the current default branch is the only supported
  line.
- Test only systems, repositories, accounts and data you own or are explicitly
  authorized to assess.

## Supported versions

The performance engineering platform has no supported production release.
Security fixes target the current default branch. When releases begin, this
section MUST be replaced with an explicit supported-version table and
disclosure policy.

## Reporting a vulnerability

Use the repository Security page's **Report a vulnerability** action when it is
available. This is the preferred private channel.

If private vulnerability reporting is unavailable, do not publish the report.
Create a minimal issue asking the repository owner to establish a private
security contact, without naming the vulnerable component, exploit, affected
data or reproduction details. You MAY also use a private contact method listed
on the repository owner's GitHub profile.

Include, where applicable:

- the affected revision, component and configuration;
- impact and conditions required to reproduce it;
- minimal reproduction steps or a proof of concept;
- relevant logs with credentials, tokens and personal data removed;
- known mitigations; and
- whether and where the issue has already been disclosed.

Do not retain, alter or disclose accessed data beyond what is necessary to
demonstrate the issue safely.

## Triage and disclosure

Maintainers SHOULD acknowledge a private report, validate severity and scope,
and agree on a communication path before public disclosure. Response times are
best-effort until a staffed security process is established.

Reporter and maintainers SHOULD coordinate disclosure after a fix or effective
mitigation is available. Maintainers MAY publish a GitHub security advisory
that credits the reporter unless the reporter asks not to be named. Reports
made in good faith under this policy SHOULD be handled respectfully.

## Security expectations for contributions

Contributors MUST follow the trust-boundary, secret-handling, external-operation
and verification requirements in [AGENTS.md](AGENTS.md),
[CONTRIBUTING.md](CONTRIBUTING.md) and the
[engineering standards](docs/development/engineering-standards.md). A change
affecting authentication, authorization, credentials, persistent data,
execution of untrusted content or cluster access MUST document its threat and
failure model and receive focused security review.
