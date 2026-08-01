# Changelog

All notable changes per release. Versions follow [semver](https://semver.org).

## v0.1.3 — 2026-08-01

Infrastructure only — no application code changed in this release.

- Split the CI surface: `pipeline.yml` keeps building and publishing the image,
  and everything that leaves this host now lives in `mirror-and-archive.yml`
  beside it.
- Every branch and tag push mirrors the repo to GitLab and Codeberg. Pull
  requests are turned off on both mirrors — they are force-pushed from GitHub,
  so anything merged there would be destroyed by the next sync; issues and
  forking stay enabled.
- Pushes to the default branch and to tags, plus a monthly schedule, archive the
  repo to the Wayback Machine, Software Heritage and archive.org, with outlinks
  captured.
- Added `issue-pull.yml`: issues opened on either mirror are pulled back into
  GitHub every six hours and closed here when the original closes. The schedule
  jitters up to ten minutes to avoid hammering both mirrors at once; a manual
  run does not.

## v0.1.2 — 2026-07-27

- Added a GitHub Actions CI status badge to the README.

## v0.1.1 — 2026-07-27

- Added self-hosted version and license badges plus a Docker Hub pulls badge; wired a badges job into pipeline.yml.

## v0.1.0 — 2026-07-27

First tagged release.

- Multi-user web chat where several users talk to the same self-hosted LLM
  (served via Ollama) at once, with optional document-knowledge (RAG) support.
