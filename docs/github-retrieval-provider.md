# GitHub retrieval provider

The provider uses GitHub's official REST API and never scrapes HTML. Public
search works without a user token at the public rate limit; an optional
server-managed token is resolved only inside the adapter.

Repository discoveries are pinned to a full 40-character commit SHA. The
adapter can read repository metadata, commits, README, LICENSE, CITATION.cff,
tags, releases, trees, workflow files, dependency files, code search, and
issues. Stars, forks, and watchers are stored as adoption signals, never as
scientific quality scores.
