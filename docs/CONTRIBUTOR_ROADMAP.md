# Contributor roadmap

## A note from Sharan

I want this repository to be useful on its own, not something people can only
look at. You should not need to understand my private system before improving a
small part of this one.

If it works with your recorder, tell us. If it does not, a focused test or fix
is more useful than a broad rewrite. The issues are written to make that kind
of contribution easier.

## What Codex is maintaining

This roadmap identifies useful, independently testable work. It is not a
promise that every item will ship, and an issue's acceptance criteria remain
the authority for a specific pull request.

## Project focus

`mi-phone` is the reliability layer before transcription. It retrieves
recorder output, verifies and settles it, asks a human to resolve ambiguous
chunk boundaries, and produces canonical audio plus provenance.

The project does not aim to become a meeting bot, transcription engine,
speaker-identification system, or summarizer.

## Contribution surfaces

### Recorder profiles

The first release recognizes filenames beginning with
`YYYY_MM_DD_HH_MM_SS`. Future recorder profiles should parse timestamps and
supported extensions through an explicit interface, with deterministic
fixtures and no content inspection.

Good contributions include common recorder filename formats, timezone edge
cases, and clear unsupported-format diagnostics.

### Diagnostics and machine-readable output

Operators should be able to determine whether configuration, dependencies,
settlement, or local evidence is blocking progress without exposing private
data. Planned surfaces include a read-only doctor command and opt-in JSON output
for status and review previews.

Diagnostics must remain allowlisted and sanitized. They must never print cloud
credentials, remote object identifiers, filenames, recording content, or
private absolute paths by default.

### Portable field validation

The deterministic workflow runs on macOS and Linux CI. Real storage-backend
field reports should use disposable synthetic files and a dedicated test
folder. Linux validation, rclone backend reports, installation documentation,
and packaging improvements are welcome.

### Audio containers

Additional containers or codecs may be accepted only when stream compatibility,
duration, deterministic joining, and provenance behaviour are explicitly
tested. Renaming an unsupported file extension is not support.

### Safe downstream handoff

Canonical folders are intended to feed independent transcription systems. A
future handoff mechanism must be opt-in, pass structured arguments without a
shell string, preserve canonical evidence, and never imply that downstream
processing succeeded.

## Later research

A recorder-neutral capture-manifest validator may eventually check finalized
segments, checksums, decodability, audio-stream presence, interrupted sessions,
and source-loss evidence. That work requires a separate public design and
privacy review before implementation.

## Explicit non-goals

- publishing private MeetingIntel code or Git history;
- recordings, transcripts, prompts, voiceprints, or model configuration;
- speaker naming or identity inference;
- autonomous decisions about ambiguous recordings;
- managing OAuth credentials or broad cloud mirroring;
- background scheduling in the core package;
- claiming untested platform or recorder support.

## How work is selected

Maintainer-created contributor issues include acceptance criteria and non-goals.
Small fixes can proceed directly. New dependencies, external services, schema
changes, destructive behaviour, or broader product scope require design
discussion before implementation.
