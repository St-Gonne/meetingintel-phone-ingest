# Privacy boundary

`mi-phone` is an ingestion utility. It retrieves configured audio files,
records local integrity metadata, asks the operator how segmented recordings
should be grouped, and creates canonical local audio.

It does not:

- transcribe recordings;
- identify speakers;
- call an AI model;
- upload telemetry;
- inspect calendar, contacts, or meeting content;
- delete original staged recordings.

Runtime ledgers contain filenames, remote object identifiers, timestamps,
checksums, sizes, and local relative paths. Treat the runtime directory as
private even though it contains no transcript text.

Only record people with their knowledge and consent, and follow the laws that
apply where every participant is located.
