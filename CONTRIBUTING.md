# Contributing to Nightloom

Thanks for your interest in Nightloom. The project is in early development, so the codebase is still taking shape — issues, design feedback and documentation fixes are especially welcome.

Before contributing, read the [Code of Conduct](CODE_OF_CONDUCT.md) and [ETHICS.md](ETHICS.md). Contributions that enable prohibited uses will not be accepted.

## Workflow

1. Open or find an issue describing the change (small fixes can skip this).
2. Fork the repository, or create a branch if you have write access. Name it after the change, e.g. `feat/dns-shuttle` or `docs/fix-typo`.
3. Make your changes in small, focused commits.
4. Open a pull request against `main` and fill in the template.

`main` is protected: **no direct pushes**. Every change goes through a pull request.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>(optional scope): <short summary>
```

| Type | Use for |
| --- | --- |
| `feat` | A new feature |
| `fix` | A bug fix |
| `docs` | Documentation only |
| `chore` | Maintenance, tooling, repo hygiene |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `test` | Adding or updating tests |
| `ci` | CI configuration |
| `build` | Build system or dependencies |

Example: `feat(shuttles): add passive DNS Shuttle`

## Developer Certificate of Origin (DCO)

Every commit must be signed off to certify that you have the right to submit it under the project's license. Sign off with `-s`:

```sh
git commit -s -m "fix: handle empty DNS answers"
```

This adds a line like the following to the commit message:

```text
Signed-off-by: Your Name <your.address@example.com>
```

By signing off you agree to the [Developer Certificate of Origin 1.1](https://developercertificate.org/). In summary, you certify that:

1. The contribution was created in whole or in part by you and you have the right to submit it under the project's open-source license; or
2. It is based on previous work that, to the best of your knowledge, is covered by a compatible open-source license and you have the right to submit it with modifications; or
3. It was provided to you by someone who certified (1) or (2), and you have not modified it; and
4. You understand that the project and your contribution are public, and that a record of the contribution (including your sign-off) is kept indefinitely and may be redistributed.

Forgot to sign off? Amend the last commit with `git commit --amend -s --no-edit`, or sign off a range with `git rebase --signoff main`.

## Development setup

The Python code lives in a [uv](https://docs.astral.sh/uv/) workspace (Python 3.12). You need `uv` and `make`; uv downloads the right Python version for you.

1. Install uv by following the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).
2. Install the workspace packages and dev tools:

   ```sh
   make install
   ```

3. Run the linters, type checker and tests (the same checks a pull request must pass):

   ```sh
   make check
   ```

4. Start the API dev server (auto-reloads on changes):

   ```sh
   make api
   ```

   Then open the interactive API docs at <http://127.0.0.1:8000/api/docs>.

5. Install the Git pre-commit hook so formatting, linting and type checks run before each commit:

   ```sh
   uv run pre-commit install
   ```

Run `make` (or `make help`) to list all targets; `make fmt` formats code and applies lint fixes.

## Continuous integration

Every pull request and every push to `main` runs the [CI workflow](.github/workflows/ci.yml) on GitHub Actions:

| Job | What it checks |
| --- | --- |
| `lint` | All pre-commit hooks except mypy (Ruff lint and format, YAML/TOML checks, whitespace) |
| `typecheck` | `make typecheck` (mypy, strict) |
| `test` | `make test` (pytest with at least 90% coverage); the coverage report is uploaded as an artifact |
| `dco` | Pull requests only: every non-merge commit has a `Signed-off-by:` trailer (see [DCO](#developer-certificate-of-origin-dco)) |

Each job installs dependencies with `uv sync --locked`, so CI fails if `uv.lock` is out of date. Run `uv lock` and commit the result whenever you change dependencies.

A pull request can only be merged when all jobs are green and every commit is signed off. Run `make check` locally before pushing to catch most failures early.

Coming later:

- [pnpm](https://pnpm.io/) for the web frontend
- [Docker Compose](https://docs.docker.com/compose/) for Neo4j, PostgreSQL, Redis and MinIO

## Writing a Shuttle

Shuttles are Nightloom's plugins: each one queries a single data source and returns entities and relationships. The Shuttle SDK (`packages/sdk`) is coming soon; the planned contract is described in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#shuttle-contract).

To propose a new data source now, open a **Shuttle request** issue.
