# Releasing Nightloom

A release is a tag `vX.Y.Z` (or a release candidate `vX.Y.Z-rc.N`) on `main`. Pushing the tag runs [`.github/workflows/release.yml`](../.github/workflows/release.yml), which publishes the images to GitHub Container Registry and creates the GitHub Release. Nothing is published by hand.

Versions follow [Semantic Versioning](https://semver.org/). Before 1.0.0, a minor version (0.2.0) may contain breaking changes; patch versions (0.1.1) only contain fixes.

## What a release contains

- **One version for everything.** Every Python package (`packages/*`, `apps/api`, `apps/worker`, `shuttles/*`), `apps/web/package.json` and the `NIGHTLOOM_VERSION` example in `.env.example` carry the same version. [`scripts/check-versions.py`](../scripts/check-versions.py) checks this on every pull request (CI job *Versions*) and checks the tag in the release workflow. A Shuttle's own `ShuttleMeta.version` is separate: it versions what that Shuttle produces and only changes when its output does.
- **Images** for `linux/amd64` and `linux/arm64`, with an SBOM, max-mode build provenance and a GitHub build provenance attestation:
  - `ghcr.io/therealnightmare/nightloom-backend` ([`apps/api/Dockerfile`](../apps/api/Dockerfile)): the API, the worker and the `migrate` job;
  - `ghcr.io/therealnightmare/nightloom-web` ([`apps/web/Dockerfile`](../apps/web/Dockerfile)): the web UI behind nginx.

  Tags: `X.Y.Z` always; `X.Y` and `latest` only for a final release, never for a release candidate.
- **A GitHub Release** whose notes are the version's section of [`CHANGELOG.md`](../CHANGELOG.md), followed by the image tags and digests and a link to [DEPLOYMENT.md](DEPLOYMENT.md). Release candidates are marked as pre-releases.

## Cutting a release

1. **Update the changelog.** On a branch from an up-to-date `main` (`chore/release-X.Y.Z`), move the entries under `## [Unreleased]` into a new section `## [X.Y.Z] - YYYY-MM-DD` (today's date, UTC), leave `## [Unreleased]` empty above it, and update the compare links at the bottom:

   ```markdown
   [Unreleased]: https://github.com/TheRealNightmare/nightloom/compare/vX.Y.Z...HEAD
   [X.Y.Z]: https://github.com/TheRealNightmare/nightloom/compare/vPREVIOUS...vX.Y.Z
   ```

   Check what the release notes will say: `python3 scripts/changelog-section.py X.Y.Z`.
2. **Bump the version** in every manifest and lockfile with one command:

   ```sh
   make bump-version V=X.Y.Z      # or V=X.Y.Z-rc.N for a release candidate
   ```

   It edits each `pyproject.toml`, `apps/web/package.json` and `.env.example`, then runs `uv lock` and `pnpm install --lockfile-only`. Python manifests get the PEP 440 spelling of a release candidate (`X.Y.ZrcN`); the others keep `X.Y.Z-rc.N`.
3. **Check** with `make check-all` (and `make check-versions` on its own), then open a pull request. CI must pass, including *Versions* and *Production deploy*.
4. **Merge** the pull request into `main`.
5. **Tag the merge commit** with an annotated (and, if you have a signing key, signed) tag, and push only the tag:

   ```sh
   git switch main && git pull --ff-only
   git tag -a vX.Y.Z -m "Nightloom X.Y.Z"     # -s instead of -a to sign it
   git push origin vX.Y.Z
   ```

6. **Watch the workflow** under *Actions → Release*. Most of its time goes into building the arm64 backend image under emulation:
   - *Verify the tag* fails if a manifest does not match the tag, if the tagged commit is not on `main`, or if `CHANGELOG.md` has no section for the version. Fix the cause, delete the tag (`git push --delete origin vX.Y.Z && git tag -d vX.Y.Z`) and tag again; nothing has been published yet;
   - *Image backend* and *Image web* build, push and attest the images;
   - *GitHub Release* writes the release. Rerunning the workflow updates the existing release instead of failing.
7. **The first time only, make the packages public.** GHCR creates new packages as private. For each of `nightloom-backend` and `nightloom-web`: open the package from the repository's *Packages* list, then *Package settings → Danger Zone → Change visibility → Public*. While doing so, check under *Manage Actions access* that the repository has write access. Later releases reuse the packages and their visibility.
8. **Verify the published images** from a machine that is not logged in to GHCR, on both architectures:

   ```sh
   docker pull --platform linux/amd64 ghcr.io/therealnightmare/nightloom-backend:X.Y.Z
   docker pull --platform linux/arm64 ghcr.io/therealnightmare/nightloom-backend:X.Y.Z
   docker pull --platform linux/amd64 ghcr.io/therealnightmare/nightloom-web:X.Y.Z
   docker pull --platform linux/arm64 ghcr.io/therealnightmare/nightloom-web:X.Y.Z
   docker buildx imagetools inspect ghcr.io/therealnightmare/nightloom-backend:X.Y.Z   # both platforms listed
   docker run --rm ghcr.io/therealnightmare/nightloom-backend:X.Y.Z python -c "import nightloom_api; print(nightloom_api.__version__)"
   gh attestation verify oci://ghcr.io/therealnightmare/nightloom-backend:X.Y.Z --repo TheRealNightmare/nightloom
   gh attestation verify oci://ghcr.io/therealnightmare/nightloom-web:X.Y.Z --repo TheRealNightmare/nightloom
   ```

   On an arm64 machine (a Raspberry Pi, an Apple silicon Mac) pull without `--platform`. Then upgrade a test deployment as [DEPLOYMENT.md](DEPLOYMENT.md#upgrades) describes and check that the account menu shows the new version.

## Release candidates

Tag `vX.Y.Z-rc.N` after `make bump-version V=X.Y.Z-rc.N` and a `## [X.Y.Z-rc.N]` changelog section. The workflow publishes only the `X.Y.Z-rc.N` image tags and marks the GitHub Release as a pre-release, so `latest` and `X.Y` keep pointing at the last final release. For the final release, fold the candidates' sections into `## [X.Y.Z]`.

## If something goes wrong

- **A published release is broken:** do not move or reuse its tag (people may already have pulled the images). Fix it on `main` and release the next patch version.
- **The workflow failed after pushing images:** fix the cause and rerun the failed jobs; pushing the same tags again replaces them with identical content from the same commit.
- **Secrets:** the workflow uses only the automatic `GITHUB_TOKEN`, with the permissions each job declares. No personal tokens are needed or should be added.
