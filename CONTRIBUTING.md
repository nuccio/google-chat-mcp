# Contributing

Thanks for helping with google-chat-mcp. This page covers how a change gets from an idea to `stable`. The project is written in English: code, comments, commit messages, issues and pull requests.

## Before you start

- Look for an existing issue, or open one describing the problem or the feature. For anything larger than a small fix, agree on the direction in the issue before writing code.
- Security problems (for example a way around the space permissions) should not be reported in a public issue. Use **Security → Report a vulnerability** on GitHub, which reaches the maintainer privately.

## Setup

```bash
git clone https://github.com/nuccio/google-chat-mcp
cd google-chat-mcp
uv pip install -e ".[dev]"
pytest
```

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

## From `main` to `stable`

After the merge, the tests run again on `main` and, if they pass, the `latest` tag moves to the new commit. The maintainer tries `latest` in a real client ([docs/manual-testing.md](docs/manual-testing.md)), often after several changes have been merged, and then promotes it to `stable`. The promotion also deletes the branches of the pull requests it releases.

The full policy is in [Release and branch policy](README.md#release-and-branch-policy).
