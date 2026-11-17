# Extraction validation — 2026-11-07

Local environment: macOS, Apple Silicon, Python 3.13, installed Node runtime.

Passed:

- 63 Python tests covering broker authentication/queues, memory, calendar snapshots, task-scoped PIM guards, routine deadlines, Health relay, deployment release selection and migration validation.
- A separate rerun of 6 deployment/migration/shortcut tests after closing SQLite backup connections explicitly.
- Fresh broker and worker subprocesses completing a synthetic question through a fake execution binary. No real model/account request was made.
- 2 Node tests for Zello Opus/Ogg framing and truncated input rejection, without a Zello login.
- Objective-C syntax compilation of Calendar and PIM bridge sources. No apps were installed or privacy permissions requested by this validation.
- Docker Compose configuration validation. No image was built and no container deployment was started.
- Publication allowlist/common-secret-pattern guard, staged whitespace checks, and an in-memory comparison against credential values in selected existing private config files: no matching public file.

Not verified by this extraction: GitHub-hosted CI, fresh Linux runtime deployment, actual HTTPS provisioning, a fresh Mac's Codex authentication, native Apple permission flows, iPhone imports/execution, live Zello delivery, real ASR/TTS environment installation/inference, or a cutover from the existing private service. No GitHub remote was created and no existing service was replaced.

The public-tree guard and private-value comparison are limited checks, not an exhaustive security audit. Public release still needs the staged-file/history review described in PUBLISHING.md.

## Publication rerun — 2026-11-17

- Public-tree guard passed for 80 text files; staged whitespace check passed.
- 87 Python offline tests passed on Python 3.13.9 with loopback access for the local test server. Tests did not contact real accounts.
- 2 Node Zello framing tests passed.
- The repository has no existing commits or remote at this point. The staged tree contains only the publication allowlist; generated Python caches remain ignored.

## Tool-based worker update — 2026-11-09

78 offline Python tests passed after the speech queue, plain-text completion, memory adapter, scoped history lookup and session reuse changes. A real Codex CLI 0.167.0 integration against a local stub broker verified `speak(text)` acknowledgement followed by continued computation, developer-role instruction placement, and a second resumed turn using prior conversational context. No real phone speech was emitted by that probe. Existing live services were subsequently switched with backups; health checks confirmed worker and voice processes online. The authoritative memory row/audit counts were unchanged by deployment.
