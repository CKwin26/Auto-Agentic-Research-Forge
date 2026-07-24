# Institutional access security

Institutional access is a local user handoff, not a credential proxy. Research
Forge never accepts password, Duo, MFA, recovery-code, cookie, secret, or token
fields. The user authenticates on the institution's official HTTPS page.

Session metadata is bound to one user, Project and Study. Cross-user loading is
rejected; revoked sessions cannot be reauthenticated. Subscription documents
never enter a shared cache and are not redistributable or training data.

The broker can open an isolated visible Edge or Chrome profile. Research Forge
does not inspect the page, browser profile, cookies, password fields or MFA
interaction. Authentication becomes active only after an explicit user
confirmation, has a bounded local expiry, and can be revoked or returned to a
reauthentication state.

An active user-bound session may register one PDF that the user explicitly made
available to the Study. The broker verifies the file signature, freezes a
project-private copy and hash, records an institutional AccessDecision, creates
a full-text Snapshot and a new Study binding, and deliberately omits the source
machine path from persisted metadata.

This implementation does not claim institutional readiness merely because the
browser controller exists. `INSTITUTIONAL_ACCESS_READY` additionally requires a
recent user-authenticated, hash-bound single-document handoff on the active
installation. Until that occurs, readiness remains degraded or not ready.
