# Personal Agent

A self-hosted personal assistant project with a Linux task broker, a Mac worker, voice adapters, and an iPhone Shortcuts generator. The source includes local memory and optional macOS Calendar, Reminders, and Notes bridges.

The repository includes offline tests. A complete fresh Linux + Mac + iPhone deployment has not yet been validated.

## Components

| Source | Responsibility |
| --- | --- |
| `src/server.py` | Authenticated queue, history, leases, routine scheduling and Health relay |
| `src/worker.py` | Codex execution, local memory and task-scoped tools |
| `src/voice_core.py`, `src/zello_bridge.mjs` | Speech processing and Zello transport |
| `tools/build_shortcuts.py` | Generate iPhone Shortcuts for questions, Health exports and sleep-plan signals |

Core Python services use the standard library and require Python 3.11+. Voice adapters need Apple Silicon, a separate MLX environment, Node with built-in WebSocket, and opus-tools. Native Apple bridges target macOS 14+.

## Start here

```sh
python3.13 manage.py --home "$HOME/.local/share/personal-agent-oss" init --role mac --timezone UTC
```

Edit the generated private configuration, then follow [deployment](docs/DEPLOYMENT.md). `init` does not start services, copy accounts, grant permissions, or download models. `doctor` checks local requirements without signing in or making network requests.

- [Deployment and rollback](docs/DEPLOYMENT.md)
- [Private data migration](docs/MIGRATION.md)
- [iPhone and voice setup](docs/PHONE-AND-VOICE.md)
- [Optional recent-speech Notes mirror](docs/SPEECH-NOTES.md)
- [Tool-based speech, instructions and memory](docs/TOOL-BASED-AGENT.md)
- [Architecture, boundaries and known gaps](docs/ARCHITECTURE.md)
- [Publication checklist](docs/PUBLISHING.md)

## Development

```sh
python3.13 tools/check_public_tree.py
PYTHONPATH=src:tests python3.13 -m unittest discover -s tests
node --test tests/zello.test.mjs
```

CI checks core tests on Linux and native bridge compilation on macOS. No live accounts or model downloads are needed for tests. MIT applies to original code; see [third-party components](NOTICE.md).
