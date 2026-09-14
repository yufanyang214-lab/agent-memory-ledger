# Building a matching release

Version 0.1.1 is an Alpha prerelease. Do not overwrite the
historical v0.1.0 tag or replace its binaries with newer source under the same
version number.

From the intended checkout:

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m compileall -q src tests scripts integrations
python scripts/clean_room_smoke.py
python -m pip install "setuptools>=61" wheel
python scripts/build_release.py
```

The release builder verifies that package metadata, the core version, and both
plugin manifests agree, then creates a wheel and sdist with SHA-256 checksums
under `dist/<version>/`. Runtime dependencies remain empty. Test the sdist's
wheel too before release, so source packaging omissions cannot be hidden by a
working-tree build.

The explicit build-dependency installation is required in fresh Python
environments: pip's isolated editable-install build does not install setuptools
into the calling interpreter. These dependencies are only needed by builders.

After the reviewed changes are merged and CI passes, create a new matching tag
and release from that commit, attach the generated wheel, sdist, and checksum
file, and mark the release as a prerelease while the project is Alpha. Use the
changelog as the release notes. Repository visibility is a separate action.

## GitHub Actions publishing

The `Alpha release` workflow publishes when a maintainer creates a
`release/v<version>` branch at a commit already merged into `main`:

```bash
git fetch origin main
git push origin origin/main:refs/heads/release/v0.1.1
```

Creating this branch is a release action. The workflow reuses the full Linux
and Windows test matrix, downloads packages from that same workflow run,
verifies checksums and wheel source contents, and tests installation/recovery
from the extracted sdist. It then creates the matching tag and Alpha prerelease
with wheel, sdist, and checksums. Existing tags are never replaced. A failed run
can be retried from Actions before its release is created; inspect any draft
left by an interrupted upload before retrying. The workflow uses GitHub's
short-lived repository token and needs no personal access token.

Repository visibility must be changed separately in the repository settings
using an account or connection with repository administration access.

Preserve the dates and limitations of existing runtime E2E reports. In
particular, Kimi model-driven E2E remains unverified by the current release
checks. Never include personal memory workspaces, local configuration, or
recovery backups in release artifacts.
