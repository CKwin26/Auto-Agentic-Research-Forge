# Security policy

## Reporting a vulnerability

Please do not open a public issue for credential exposure, sandbox escape,
SSRF, unsafe artifact handling or authorization bypass. Use the repository's
[private vulnerability reporting](https://github.com/CKwin26/Auto-Agentic-Research-Forge/security/advisories/new).

Include the affected version, reproduction steps, impact and any suggested
mitigation. Do not include real credentials, private datasets or unpublished
project contents.

## Security boundary

- The default retrieval policy is offline.
- Provider credentials are resolved only inside provider adapters.
- Queries are sanitized before external transmission.
- External responses are untrusted data and have no instruction authority.
- Local execution is not an operating-system sandbox; use the controlled
  container runtime for untrusted experiment code.
- Institutional authentication remains in the institution's own browser page.
  Research Forge must not capture passwords, cookies or MFA responses.

Only the latest release and the current default branch receive security fixes.
