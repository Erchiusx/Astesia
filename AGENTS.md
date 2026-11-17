# Project development

This is a public source tree. Never copy production configs, prompts, credentials, health exports, recordings, model weights, or private run logs into it. Keep runtime files outside this repository. Do not auto-discover existing installations or credentials.

Run `python tools/check_public_tree.py` before staging. Add files explicitly from the publication allowlist. New migrations need round-trip and invalid-input tests. Core tests must remain offline and use synthetic data. Tests must never log into Zello or call a real Codex account.

Deployment changes are staged here. Do not restart or replace a user's existing services while working on packaging. Installer commands should generate separate OSS identities and require explicit activation. Do not edit macOS privacy databases or automatically grant permissions.
