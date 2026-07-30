# Clean-room Drive validation

Version 0.1 is released only after a person or account that has never used the
private MeetingIntel setup completes this workflow with synthetic audio.

## Boundaries

- Use a spare Google account and a disposable Drive folder containing only
  generated tones or silence.
- Share the folder with that account as **Viewer**. Do not allow public,
  editor, or upload access.
- Create a new rclone remote in the clean account. Do not copy an existing
  rclone configuration, application state, staging folder, or ledger.
- Never paste OAuth tokens, remote IDs, checksums, filenames, or local paths
  into an issue or validation report.
- Delete the disposable folder, revoke the test account's access, and remove
  the test rclone remote after the gate.

Google's Drive OAuth grant is account-wide. Folder restriction is enforced by
the configured remote path and by the operator's use of a dedicated account;
it is not a folder-scoped OAuth permission.

## Fresh installation

```sh
git clone https://github.com/sharantulsiani-ui/meetingintel-phone-ingest.git
cd meetingintel-phone-ingest
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install .
mi-phone --help
```

Record the operating system and `python --version`. Do not include usernames or
home-directory paths.

## Configure a disposable root

Choose empty local staging and canonical directories. Configure the new rclone
remote and the exact disposable folder path:

```sh
mi-phone configure \
  --remote "clean-room:DisposablePhoneRecordings" \
  --timezone "Asia/Kolkata" \
  --staging-root "$HOME/mi-phone-clean-room/staging" \
  --canonical-root "$HOME/mi-phone-clean-room/canonical"
```

## Required observations

1. First inspect without writes:

   ```sh
   mi-phone fetch --dry-run
   ```

   It should report the synthetic candidates without creating staged audio.

2. Fetch:

   ```sh
   mi-phone fetch
   mi-phone status
   ```

   New files should be `pending_settlement`.

3. Wait at least ten minutes without modifying the Drive files, then fetch
   again:

   ```sh
   mi-phone fetch
   mi-phone status
   ```

   The unchanged files should become `settled_ready`.

4. Preview grouping without normalization:

   ```sh
   mi-phone review --dry-run
   ```

5. Run guided review. Confirm the expected synthetic partition, then verify
   that every canonical folder contains `audio.m4a` and
   `meeting_source.json`, while staged originals remain present:

   ```sh
   mi-phone review
   mi-phone status
   ```

6. Repeat `mi-phone fetch` and `mi-phone review --dry-run`. Nothing should be
   downloaded or normalized a second time.

## Pass criteria

- fresh Python 3.11+ installation and CLI smoke pass;
- dry-run performs no download or normalization;
- first fetch downloads only the disposable synthetic files;
- settlement requires the second unchanged observation after ten minutes;
- guided review shows the expected timing and grouping evidence;
- canonical audio and provenance are created once;
- originals remain staged;
- repeat fetch/review is idempotent;
- no credentials, private recordings, private paths, or MeetingIntel state are
  used.

Report only:

```text
Platform:
Python:
Install: pass/fail
Dry-run: pass/fail
First fetch: pass/fail
Settlement: pass/fail
Review/normalization: pass/fail
Repeat/idempotence: pass/fail
Cleanup/revocation: pass/fail
Sanitized note:
```

Any failure leaves version 0.1 unreleased until it is understood or explicitly
documented as an unsupported boundary.

## Reference validation

The release gate passed on 2026-07-30:

```text
Platform: macOS
Python: 3.11
Install: pass
Dry-run: pass
First fetch: pass
Settlement: pass
Review/normalization: pass
Repeat/idempotence: pass
Cleanup/revocation: pass
Sanitized note: Three speech-free fixtures produced one canonical recording
with provenance while retaining all staged originals. An initial pair of
byte-identical silence fixtures was rejected before normalization; unique
speech-free replacements then passed the complete gate.
```

The disposable Drive folder, its Viewer access, the isolated rclone
configuration, generated fixtures, staging, canonical output, and local ledgers
were removed after validation. No private MeetingIntel configuration, account
identifier, credential, recording, transcript, or model was used.
