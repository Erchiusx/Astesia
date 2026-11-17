# Third-party components

The MIT license covers original project code. No model weights, reference voice recordings, personal prompts, authentication sessions, or upstream source trees are included.

Optional integrations use independently installed software: Codex CLI, MLX / mlx-audio, GPT-SoVITS, Node.js, opus-tools, Docker, and nginx. Their own licenses and service terms apply. Apple frameworks are supplied by macOS. Zello is a separate service and requires your own account and developer credentials.

GPT-SoVITS is an optional adapter, not a bundled or automatically installed environment. Choose an upstream revision and models you are permitted to use; record their revisions and checksums in your private deployment. You are responsible for permission to use any voice recordings or identity. The generic default uses macOS speech synthesis.

The Singapore weather adapter targets data.gov.sg and is disabled by default; enable it only for a deployment where that forecast is relevant.
