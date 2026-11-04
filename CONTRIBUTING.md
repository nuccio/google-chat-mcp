# Contributing

Thanks for helping with google-chat-mcp. This page covers how a change gets from an idea to `stable`. The project is written in English: code, comments, commit messages, issues and pull requests.

## Before you start

- Look for an existing issue, or open one describing the problem or the feature. For anything larger than a small fix, agree on the direction in the issue before writing code.
- Security problems (for example a way around the space permissions) should not be reported in a public issue. Use **Security → Report a vulnerability** on GitHub, which reaches the maintainer privately.

## Setup

```bash
git clone https://github.com/nuccio/google-chat-mcp
```

```bash
cd google-chat-mcp
```

```bash
uv sync --extra dev
```

```bash
uv run pytest
```

`uv sync` creates `.venv` in the checkout and installs exactly what `uv.lock` lists. Run the project's commands through `uv run` (e.g. `uv run google-chat-mcp spaces`), or activate `.venv` first.

Integration tests run against real Google Chat spaces and need your own OAuth token. They are optional for contributors: see [tests/README.md](tests/README.md).

## Making a change

1. Create a branch from `main`, one per issue, e.g. `fix/token-file-permissions` or `issue-14-display-names`.
2. Add or update tests with the change. The unit tests must pass on Python 3.11–3.13.
3. Update the docs (`README.md`, `docs/`) when behaviour or configuration changes.
4. If you change the dependencies in `pyproject.toml`, run `uv lock` and commit `uv.lock`. Declare what the code imports directly, even when another package already brings it in.
5. Open a pull request against `main`. Put `Fixes #N` in the description, so the issue is closed when the pull request is merged.

## Signed commits

`main` only accepts commits whose signature GitHub can verify. Sign your commits with GPG or SSH. With an SSH key:

```bash
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub
git config --global commit.gpgsign true
```

Then add the same public key on GitHub as a **Signing Key**: Settings → SSH and GPG keys → New SSH key → Key type: **Signing Key**. A key added only as an Authentication Key lets you push, but your commits still show up as unverified. A key used for both needs two entries, one of each type.

## Review and merge

A pull request can be merged when:

- the **Tests** workflow passes (`pytest (Python 3.11)`, `3.12`, `3.13` are required checks);
- it has one approving review;
- the branch is up to date with `main`.

Pull requests are merged with **squash and merge**. GitHub signs the squash commit, so it stays verified on `main`. With rebase, GitHub rewrites the commits without signing them.

Don't delete your branch after the merge. Branches are deleted when the change is promoted to `stable` (see below), so the change can still be looked at on its branch until then.

## Release and branch policy

Two tags separate the version people use every day from the one being tried:

| Tag | Points to | How it moves |
|---|---|---|
| `latest` | the most recent commit on `main` whose automated tests passed | automatically, when the tests on `main` pass |
| `stable` | the last version tried in a real client and known to work | by hand, after trying `latest` |

Users install `stable`. A change reaches them only after someone has tried it in a real client, because some problems only show up there: for example, whether the client supports a given MCP feature.

### Flow of a change

1. The change is developed on its own branch, usually one per issue, and proposed with a pull request.
2. The automated tests run on every pull request (the **Tests** workflow, [`.github/workflows/tests.yml`](.github/workflows/tests.yml), on Python 3.11–3.13) and must pass before merging. Integration tests need a real OAuth token and are run by hand (see [tests/README.md](tests/README.md)).
3. The pull request is merged into `main` with squash and merge (see [Review and merge](#review-and-merge)). The tests run again on `main`, and if they pass `latest` moves to the new commit. The branch is kept until step 5.
4. The maintainer tries `latest` in Claude Desktop, following [docs/manual-testing.md](docs/manual-testing.md), often after several changes have been merged.
5. If it works, `latest` is promoted to `stable`, and the branches of the pull requests released by the promotion are deleted. If a bug turns up, `stable` stays where it is, and the fix goes through the same flow.

### Promoting to `stable`

On GitHub, **Actions → Release tags → Run workflow**. Leave "Commit" empty to promote the commit `latest` points to, or enter the SHA of an earlier commit on `main` (e.g. to roll `stable` back). Commits that are not on `main` are refused.

The workflow then deletes the branch of every pull request merged into `main` whose merge is now part of `stable`, unless something was pushed to the branch after the merge or an open pull request still uses it. The workflow is in [`.github/workflows/release-tags.yml`](.github/workflows/release-tags.yml).

The branch protection of `main` (Settings → Branches) requires the three `pytest` jobs of the **Tests** workflow, one approving review and verified signatures.
