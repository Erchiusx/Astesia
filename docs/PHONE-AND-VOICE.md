# iPhone and voice

## Shortcuts

A private `client-setup.json` has this shape. Use your real private value only outside the repository:

```json
{"url":"https://agent.example.com/personal-agent/briefing","headers":{"Authorization":"Bearer TOKEN"}}
```

The broker initializer creates this file with a placeholder domain. Edit the domain first. On a Mac, generate and sign the workflows:

```sh
python3.13 tools/build_shortcuts.py --setup /absolute/private/config/client-setup.json --output /absolute/private/shortcuts
```

The generator does not need installed workflow metadata or access to Shortcuts.sqlite. `--unsigned` is available for static inspection and CI. Signing uses the macOS Shortcuts CLI and can require network access. The generated files contain a private client credential and must never be published. This extraction verified static generation, not fresh iPhone imports of these renamed workflows.

Generated workflows:

- `问生活助手`: tap to end dictation, edit or cancel, then upload once. It does not wait for an answer or speak locally. Canceling before upload creates no agent request.
- `助手·同步健康摘要`: iPhone-only read/export of sleep, steps, active calories, exercise minutes, heart rate and resting heart rate. Run unlocked first and authorize the requested read categories. Samples are bounded, not guaranteed complete daily totals. Locked-device exports may be unavailable.
- `助手·睡眠计划信号`: pass text `wake` from a Sleep → Waking Up automation, or `wind_down` from Wind Down Begins. Choose immediate execution. Manual execution with no input is only a connectivity preview.

For event-based scheduling, personal automations must be configured on the iPhone. Alternatively configure a fixed `routine_daily_plan` on the broker as described in DEPLOYMENT.md; then no phone sleep signal is needed. Wake signals schedule a briefing 30 minutes later. Wind-down signals schedule bedtime minus 5 minutes only when `routine_wind_down_minutes` is configured accurately on the broker. Otherwise they do not schedule an evening briefing. A sleep-plan signal is not evidence of actual waking. Signal submission and Health export are separate so unavailable locked Health data does not block the routine event. Native Mac HealthKit access is not assumed.

## Zello

Use separate phone and agent accounts. Join a private channel, establish the necessary trust, and provision your own developer issuer/private key. Put these only in private `zello-config.json`; keep the key mode 0600. The agent receives only the configured sender on the configured channel. Do not start two transports with the same agent account.

Install Node with a built-in global WebSocket and opus-tools. `doctor` checks the actual WebSocket capability. Enable `zello_duplex_enabled`, set the fixed `voice_input_engine=qwen3_asr`, and configure the ASR interpreter/model paths. Merge the optional example settings you need, then rerun `doctor` and `install`. Start the generated voice plist explicitly.

`voice_auto_submit=false` keeps transcripts for review in the optional local viewer. Set true if you prefer immediate submission after the PTT release. The viewer is loopback-only; do not expose it publicly. To use it, create private viewer config containing `enable_viewer=true`, `client_setup_path` pointing at your private client setup, and optional `viewer_port` (default 18770), then run `src/conversation_viewer.py --config /absolute/private/viewer.json` with the same `PERSONAL_AGENT_CONFIG_DIR` and `PERSONAL_AGENT_DATA_DIR` environment as the worker.

Enable delivery on the broker after configuring the voice receiver:

```sh
python3.13 tools/client.py --setup /absolute/private/config/client-setup.json --enable-audio
```

A private `--text` option submits a test question; no question is sent by `doctor` or installation.

## Speech models

Core installation does not download models or create large environments. Create a separate Python 3.13 virtual environment for ASR and install `requirements-asr.txt`. This file pins the two direct packages used by the working adapter, not every transitive dependency. Supply a compatible local Qwen3-ASR model directory and edit private `qwen-asr.json`. Record the upstream revision and file hashes in your own model manifest. CPU inference, model formats, latency and memory behavior are not guaranteed by packaging tests. External storage can be guarded with `required_volume` and `volume_uuid`; internal storage can leave both null.

The default output engine is macOS `say`; choose an installed voice. GPT-SoVITS is optional: merge `config/tts.example.json` into private worker config and supply your separately installed upstream environment, compatible models and licensed reference audio/text. No character voice assets are bundled. The adapter currently uses the existing v2ProPlus CPU float32 setup and `all_zh`; it is not a general language/model compatibility layer.

PTT archival is **off** by default. To opt in, configure `speech_archive` with `enabled`, absolute `root`, mounted `required_volume` and Unix `started_at`. Only received PTT messages are archived, not ambient recording. Automatic transcripts remain unreviewed labels. Manage archive retention separately; the current archive adapter retains until operator deletion and is excluded from standard backups.
