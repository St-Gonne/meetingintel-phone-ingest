# Contributing

Bug reports, portability results, documentation fixes, and focused pull
requests are welcome. You do not need to be an audio or AI specialist.

## Find a task

The best starting points are issues labelled `good first issue` or
`help wanted`. A contributor issue should tell you:

- the user-visible problem;
- the expected behaviour and acceptance criteria;
- the files or extension surface likely to be involved;
- the privacy and scope boundaries;
- the exact verification command.

Please comment on the issue before starting substantial work so two people do
not unknowingly solve the same problem. For a large architectural proposal,
open a feature request first.

Before opening an issue:

1. Remove recordings, names, credentials, account identifiers, and private
   paths.
2. Include the `mi-phone status` summary and platform/version information.
3. Explain whether the failure occurred during fetch, settlement, review, or
   normalization.

Before opening a pull request:

```sh
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

## Pull-request shape

Keep each pull request focused on one issue. In the description, include:

- the issue it addresses;
- what changed and why;
- tests added or changed;
- the commands you ran;
- platform-specific limitations or evidence;
- a privacy confirmation that no real recording or private identifier was
  added.

Small documentation-only pull requests do not need a prior issue. Please open
an issue before proposing a large architectural change.

## Synthetic evidence only

Test fixtures must be generated or synthetic and must not contain real voices,
meeting content, names, credentials, cloud identifiers, or private filesystem
paths. Silence, generated tones, and programmatically constructed container
fixtures are preferred. Do not paste unsanitized runtime output into issues or
pull requests.

## Design boundaries

Changes must preserve the project's fail-closed behaviour:

- no silent grouping through ambiguous chunks;
- no destructive deletion of staged originals;
- no cloud credential management;
- no transcription, summarization, speaker identification, or model calls;
- no telemetry;
- no acceptance of a partial or unverifiable download.

The extension roadmap is documented in
[docs/CONTRIBUTOR_ROADMAP.md](docs/CONTRIBUTOR_ROADMAP.md).

## Review expectations

Maintainers will prioritise small, tested changes with explicit non-goals.
Review may ask for a synthetic regression test or narrower scope before code is
merged. AI-assisted contributions are welcome, but the contributor remains
responsible for understanding, testing, and accurately describing the change.
