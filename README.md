# MeetingIntel Phone Ingest

## A note from Sharan

I use recordings from my phone as part of a private meeting-intelligence
system. My recording app splits long meetings into 15-minute files and sends
them to Drive. That sounds simple. It wasn't.

Files could arrive late, look ready before the upload had finished, or sit
close enough together that the computer had to decide whether they were one
meeting or two. I did not want that decision hidden inside an automated
pipeline. I wanted the system to check the files, show me what it knew, and ask
me when it wasn't sure.

This repository is the part that gets those recordings safely onto a computer
and ready for whichever transcription tool comes next. I designed it around
the problems I actually ran into. Codex built it and tested it with me.

I'm sharing it because other people may have the same dull but important
problem. If your recorder behaves differently, or this misses an edge case,
I'd like to hear about it.

## Why this may be useful to you

This could help if you:

- record long interviews, research sessions or in-person meetings that arrive
  as several files;
- collect voice notes or field recordings and transcribe them later;
- use a local transcription tool but still sort and verify its input by hand;
- are building a private knowledge or meeting-notes workflow and need a clear
  record of where each audio file came from;
- are testing a speech-to-text system and want repeatable input instead of a
  folder full of uncertain recordings.

It can sit before tools built with
[Whisper](https://github.com/openai/whisper), or before applications that import
local audio, such as [Muesli](https://github.com/Muesli-HQ/muesli). It does not
replace those tools. It prepares the recording they receive. If your
transcription engine needs a different audio format, convert the canonical
file after this intake step.

## What Codex built

`mi-phone` retrieves recordings through an existing
[rclone](https://rclone.org/) remote, verifies them, waits for two unchanged
observations, shows exact start/end/duration/gap evidence, and asks the operator
whether to join, separate, or discard recorder chunks. It produces canonical
audio folders that can feed any transcription system.

It does **not** transcribe, summarize, identify speakers, or call an AI model.

In practical terms, it handles the awkward step before transcription: getting
segmented phone recordings onto a computer without accepting partial uploads,
creating duplicates, or guessing which chunks belong together.

## What can go wrong

Phone recorder workflows become unreliable at the boundary between recorder,
cloud storage, and local processing:

- an upload can be visible before it is stable;
- long recordings may be split into fixed-size chunks;
- adjacent chunks may be one meeting or deliberate stop/restarts;
- repeated syncs can create duplicates;
- automation can hide failures;
- downstream transcription should never guess through ambiguity.

This project treats those as explicit states rather than filename folklore.

```text
phone recorder
    ↓
rclone remote
    ↓
atomic verified staging
    ↓
settlement gate: unchanged twice over ≥10 minutes
    ↓
operator review: join / separate / discard
    ↓
canonical audio.m4a + provenance
    ↓
your transcription pipeline
```

## Status

Version 0.1 is an alpha release extracted from a private system used for real
in-person meeting recordings.

- macOS: supported and tested, including a fresh-account Drive clean-room gate
- Linux: deterministic workflow is validated in CI; real Drive field validation
  remains pending
- Windows: not currently supported
- scheduler: deliberately omitted from 0.1; invoke `mi-phone fetch` from your
  scheduler of choice after the manual workflow is proven

The independent Drive field gate is defined in
[CLEAN_ROOM_VALIDATION.md](CLEAN_ROOM_VALIDATION.md). The reference macOS
validation passed before the v0.1.0 tag.

The filename parser currently expects recorder names beginning with:

```text
YYYY_MM_DD_HH_MM_SS
```

For example: `2026_07_24_14_30_00.m4a`.

## Privacy

Everything after the configured rclone remote is local. There is no telemetry.
Runtime state contains filenames, remote object IDs, timestamps, checksums, and
local paths, so it should still be treated as private.

Only record people with their knowledge and consent. Recording laws differ by
location and may depend on where every participant is located.

See [PRIVACY.md](PRIVACY.md).

## Requirements

- Python 3.11 or newer
- `rclone`
- `ffprobe`
- `ffmpeg` when joining multiple segments

On macOS with Homebrew:

```sh
brew install rclone ffmpeg
```

Configure and authorize an rclone remote separately. `mi-phone` does not manage
your cloud credentials.

## Install from a checkout

```sh
git clone https://github.com/sharantulsiani-ui/meetingintel-phone-ingest.git
cd meetingintel-phone-ingest
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

If your `python3 --version` already reports Python 3.11 or newer, `python3` may
be used instead. Do not rely on the macOS system Python without checking its
version.

## Configure

Use a narrow remote folder that contains only recorder output:

```sh
mi-phone configure \
  --remote "my-drive:PhoneRecordings" \
  --timezone "Asia/Kolkata" \
  --staging-root "$HOME/MeetingAudio/staging" \
  --canonical-root "$HOME/MeetingAudio/canonical"
```

Configuration and ledgers default to:

```text
~/.local/share/mi-phone/
```

No Drive access occurs during `configure`.

## Fetch and settlement

Inspect first:

```sh
mi-phone fetch --dry-run
```

Fetch:

```sh
mi-phone fetch
```

New or changed recordings are downloaded atomically and remain
`pending_settlement`. Run fetch again at least ten minutes later:

```sh
mi-phone fetch
mi-phone status
```

A recording becomes `settled_ready` only when its immutable remote ID and full
metadata signature remain unchanged for the settlement interval and its local
size/checksum still match.

If the same remote object ID appears at a new path, processing stops for an
operator decision. The tool never silently treats a move as a new recording.

## Review chunks

Preview the latest unresolved day:

```sh
mi-phone review --dry-run
```

Start guided review:

```sh
mi-phone review
```

Example display:

```text
PHONE RECORDING REVIEW — 2026-07-24
3 settled segments · originals retained

 #  Filename                         Start        End*         Duration       Gap
 1  2026_07_24_12_00_00.m4a         12:00:00     12:15:00     15m 00.000s    —
 2  2026_07_24_12_15_01.m4a         12:15:01     12:30:01     15m 00.000s    +1.000s
 3  2026_07_24_12_30_02.m4a         12:30:02     12:32:02     2m 00.000s     +1.000s

Suggested meetings:
  Meeting 1: segments 1, 2, 3
```

You can accept the suggestion, use `1,2;3` to define two meetings, keep every
segment separate, or discard selected segments from future review. Discard
records an operator decision; it does not delete the staged source file.

The deliberately conservative rule is:

- two or more continuous approximately-900-second chunks may be grouped,
  including a final tail;
- one full chunk followed by one continuous tail is ambiguous and is never
  auto-accepted;
- gaps outside -1 to +5 seconds are not recorder continuity evidence.

## Canonical output

Each confirmed meeting receives its own deterministic folder:

```text
canonical/
└── phone_2026-07-24_12-00-00_<fingerprint>/
    ├── audio.m4a
    └── meeting_source.json
```

The provenance file records source fingerprints, relative paths, start time,
segment count, canonical checksum, and creation time. Original staged files are
retained.

## Safety properties

- immutable remote IDs prevent path moves from becoming duplicates;
- downloads use temporary files followed by atomic replacement;
- remote hashes are checked when supplied, with local SHA-256 always recorded;
- two stable observations gate downstream review;
- a single-user lock prevents overlapping fetches;
- malformed paths, symlinks, untracked destinations, and changed settled
  evidence fail closed;
- normalization uses a complete, explicit partition of every reviewed segment;
- all ledgers are private, atomic JSON files;
- dry-run and status do not download or normalize audio.

## Development

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

All committed fixtures are synthetic. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Help build the reliability layer

This project is deliberately smaller than a meeting assistant. The current
contributor roadmap focuses on the unreliable boundary between a recorder and
downstream transcription: recorder filename formats, transfer diagnostics,
portable field validation, machine-readable status, and additional audio
containers.

Start with a labelled
[`good first issue`](https://github.com/sharantulsiani-ui/meetingintel-phone-ingest/labels/good%20first%20issue)
or [`help wanted`](https://github.com/sharantulsiani-ui/meetingintel-phone-ingest/labels/help%20wanted)
task. Every contributor issue should include acceptance criteria, non-goals,
synthetic-test expectations, and an exact verification command. See the
[contributor roadmap](docs/CONTRIBUTOR_ROADMAP.md) for the planned extension
surfaces and boundaries.

Useful field reports are also welcome even when you do not plan to write code.
Please never attach real recordings, transcripts, credentials, names, account
identifiers, or private paths.

## Relationship to MeetingIntel

This is a standalone extraction from the phone-ingestion boundary of
[Meeting Intelligence System](https://github.com/sharantulsiani-ui/meeting-intelligence-system),
a private-data local meeting workflow built through AI-directed engineering.

The private system and its Git history are not published here. This repository
contains no meeting prompts, transcripts, voiceprints, model configuration,
production ledger, personal context, or account-specific cloud configuration.

## Built through AI-directed engineering

Sharan Tulsiani defined the problem, operating boundaries, failure policy,
operator workflow, and behavioural gates. OpenAI Codex implemented the code and
tests under those controls. This project is explicit about that division rather
than presenting AI-generated implementation as hand-written code.

## Licence

Apache License 2.0. See [LICENSE](LICENSE).
