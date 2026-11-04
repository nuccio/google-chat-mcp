"""
The `/settings` web UI of the remote connector (issue #51).

Built with NiceGUI (docs/remote/04-settings-server-design.md). The pages
follow docs/remote/03-settings-ui-flow.md. Everything the pages need from the
outside world (sign-in, the user's spaces, the ACL, the admin policy,
connector usage) goes through the small interfaces in `backends.py`, so the
real server (#52) can replace the in-memory implementations in `mock.py`.

Install the `remote` extra to use it: `pip install "google-chat-mcp[remote]"`.
"""
