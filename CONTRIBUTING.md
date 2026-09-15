# Contributing to Voice Studio

Thank you for helping improve Voice Studio. Keep changes small, testable, and compatible with existing
projects and local data.

## Before changing code

1. Read `AGENTS.md` and `docs/architecture/README.md`.
2. Check the current Git status and do not overwrite unrelated work.
3. Confirm the real API, service, provider and storage path affected by the change.
4. Add regression coverage for behavior that already works before changing its implementation.

## Development checks

Use the repository environment and keep all test data outside the user's Voice Studio directory:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
pnpm --dir frontend test
pnpm --dir frontend check
pnpm --dir frontend build
.venv/bin/python scripts/verify_open_source_release.py
```

Web-visible changes also need a browser check against a real service instance using an isolated
temporary `VOICE_STUDIO_DATA_DIR`.

## Data and model safety

Never commit model weights, generated media, voices, databases, caches, logs, credentials, private
paths, or `local-overrides/`. Do not add runtime downloads without a pinned source/revision, integrity
verification, a managed destination, and verified model terms.

## Licensing and provenance

By submitting a contribution, you represent that you have the right to submit it under the applicable
repository terms. Identify copied or adapted code in the pull request and preserve its copyright and
license notices. Do not copy model code or weights merely because they are publicly downloadable.
Read `THIRD_PARTY_NOTICES.md`; changes under `mlx_indextts/` require explicit provenance review until
the documented IndexTTS licensing question is resolved.

## Pull request scope

Explain the problem, compatibility impact, tests run, and any remaining limitation. Avoid unrelated
formatting, mass renames, or dependency upgrades in the same change.
