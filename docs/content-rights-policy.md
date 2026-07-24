# Content rights policy

Research Forge distinguishes open access, user upload, institutional access,
metadata-only and link-only records. A provider's availability does not imply a
right to download, persist, process, redistribute, share across users, or train
on its content.

When full text is unavailable, the correct result is `metadata_only`,
`abstract_only`, `link_only`, or `access_blocked`. Paywalls, CAPTCHA, SSO, MFA,
repository gates and download limits are never bypassed. Rights decisions are
stored separately from resource identity and may only become more permissive
through explicit evidence or user authorization.

Open-access acquisition is a separate, policy-authorized Gateway operation.
Resolver metadata alone is not treated as acquired full text. The acquisition
adapter accepts only public HTTPS targets, validates every redirect, rejects
private or loopback destinations, enforces the byte budget while streaming,
requires PDF content and a PDF file signature, and freezes the resulting bytes
before deterministic normalization.

On Windows systems using a local Clash/Mihomo-style fake-IP DNS proxy, public
publisher hosts can resolve into the benchmarking range `198.18.0.0/15`.
Research Forge permits this route only when the owner explicitly enables
`allow_proxy_fake_ip`, approval metadata is present, every resolved address is
inside that range, and the configured HTTPS proxy is loopback-local. Literal IP
targets and local/private hostnames remain blocked. Every acquisition records
the exception in the immutable audit ledger.

Institutional documents use project-private snapshots. They are never placed in
the public provider cache, never shared across users, and require an explicit
choice before model processing.
