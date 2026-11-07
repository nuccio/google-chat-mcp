"""
`python -m google_chat_mcp.settings`: the settings service as deployed on
Cloud Run. Configuration comes from the environment (see `config.py` and
`remote/policy.py`). Needs the `remote` extra.

`python -m google_chat_mcp.settings check` reports whether the GCP side is
configured and starts nothing (`remote/check.py`).

It does not start the development mock and never falls back to it: that is
`python -m google_chat_mcp.settings.mock`.
"""

from __future__ import annotations

import sys

EXTRA_MESSAGE = (
    "The settings service needs the 'remote' extra: pip install \"google-chat-mcp[remote]\""
)


def main() -> None:
    try:
        import google.cloud.firestore  # noqa: F401
        import nicegui  # noqa: F401
    except ImportError:
        raise SystemExit(EXTRA_MESSAGE) from None

    import logging

    from ..remote.firestore import create_client
    from ..remote.policy import AdminPolicy, PolicyConfigError
    from .config import ConfigError, SettingsConfig
    from .production import run

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = SettingsConfig.from_env()
        admin = AdminPolicy.from_env()
    except (ConfigError, PolicyConfigError) as e:
        raise SystemExit(f"Configuration error: {e}") from None
    run(config, admin, create_client())


if __name__ == "__main__":
    if sys.argv[1:2] == ["check"]:
        from ..remote.check import SETTINGS, run

        run(SETTINGS, sys.argv[2:])
    if len(sys.argv) > 1:
        raise SystemExit(
            "python -m google_chat_mcp.settings takes no arguments but `check`; "
            "use the environment"
        )
    main()
