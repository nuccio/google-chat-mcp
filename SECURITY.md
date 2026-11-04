# Security policy

## Reporting a vulnerability

Do not open a public issue. Use **Security → Report a vulnerability** on GitHub, which reaches the maintainer privately. This includes any way around the space permissions or the send confirmation.

## Supported versions

Fixes go to `main`, then to the `latest` and `stable` tags. Older commits are not patched.

## What the server can reach

The server runs on your machine with your user's permissions, and it reads your OAuth token (`~/.config/google-chat-mcp/token.json`). Any code it runs, including its dependencies, can read that token and anything else your user can read. A compromised release or dependency is therefore the main risk of the install path.

## What protects your install

| Control | What it covers | What it does not cover |
|---|---|---|
| `uv.lock` with hashes | a clone installed with `uv sync --frozen` | `uvx`, which does not read it |
| Daily `pip-audit` in CI | known advisories, in `uv.lock` and in a fresh resolution | malicious releases with no advisory yet |
| Dependabot with a 7-day cooldown | what the maintainer merges | what `uvx` resolves on your machine |
| Actions pinned to commit SHAs | the workflows that test and move the tags | your install |

`uvx` from git resolves the dependencies itself, within the ranges in `pyproject.toml`. How often that happens, and how to limit it, depends on how you install: see [Pinning a version](docs/claude-desktop.md#pinning-a-version).
