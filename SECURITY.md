# Security

Please do not include recordings, transcripts, credentials, remote names,
account identifiers, or private filesystem paths in a public issue.

For a suspected vulnerability, contact the maintainer privately through the
security-reporting channel configured on the GitHub repository.

The project invokes `rclone`, `ffprobe`, and `ffmpeg` as local subprocesses. It
does not upload telemetry or send recordings to a model or transcription API.
