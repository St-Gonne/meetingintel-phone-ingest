# Contributing

Bug reports, portability results, documentation fixes, and focused pull
requests are welcome.

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

Please open an issue before proposing a large architectural change. Test
fixtures must be synthetic and must not contain real voices or meeting data.
