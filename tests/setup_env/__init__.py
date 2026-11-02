"""
Builds the integration test environment described in tests/test_env.toml.

Run by hand from a repository checkout:
    python -m tests.setup_env

This package needs permissions the MCP server must not have, so it is kept out
of it: it is not in the wheel, google_chat_mcp/ never imports it, and it has
its own scopes and token (see auth.py).
"""
