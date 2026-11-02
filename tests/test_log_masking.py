"""
Tests for the masking of email addresses in the logs (google_chat_mcp/log_masking.py).

Verifies:
- that the formatter masks addresses in messages, logged arguments and tracebacks,
  plain and URL-encoded, and leaves the text around them unchanged
- that text that is not an address is left alone
- that fastmcp's records are masked before its handlers render them, with
  messages, chained exceptions, notes and groups kept readable
- that on a real server start, fastmcp keeps its Rich output on stderr, masked,
  and its records stay out of the log file
"""

import io
import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from rich.console import Console
from rich.logging import RichHandler

from google_chat_mcp.log_masking import EmailMaskingFormatter, _mask_record

REPO_ROOT = Path(__file__).resolve().parent.parent


def _format(msg, *args, exc_info=None):
    record = logging.LogRecord("test", logging.INFO, __file__, 1, msg, args, exc_info)
    return EmailMaskingFormatter("%(levelname)s %(message)s").format(record)


@pytest.mark.parametrize(
    "address",
    [
        "someone@example.com",
        "first.last+tag@mail.example.co.uk",
        "someone%40example.com",
        "first%2Blast%40example.com",
        "josé@exämple.com",
    ],
)
def test_masks_address_and_keeps_surrounding_text(address):
    assert _format(f"before <{address}>, after") == "INFO before <[email]>, after"


def test_masks_address_in_logged_argument():
    line = _format("tool=x args=%s", {"space_name": "users/someone@example.com", "n": 1})
    assert line == "INFO tool=x args={'space_name': 'users/[email]', 'n': 1}"


def test_masks_every_address_in_a_line():
    assert _format("a@example.com, b@example.org.") == "INFO [email], [email]."


def test_masks_address_in_traceback():
    try:
        raise ValueError("denied for someone@example.com")
    except ValueError:
        line = _format("failed", exc_info=sys.exc_info())
    assert "someone@example.com" not in line
    assert "ValueError: denied for [email]" in line
    assert line.startswith("INFO failed\nTraceback")


@pytest.mark.parametrize(
    "text",
    [
        "spaces/AAABBBCCC",
        "@mcp.tool(annotations=_READ_ONLY)",
        "fastmcp@4.1.3",
        "user@localhost",
        "100%40 of nothing",
    ],
)
def test_leaves_other_text_unchanged(text):
    assert _format("%s", text) == f"INFO {text}"


def _chained_exception():
    """An exception with a chained cause and a note, each holding an address.

    The addresses are built at runtime because tracebacks also show source lines.
    """
    domain = "example.com"
    try:
        try:
            raise ValueError(f"cause for cause@{domain}")
        except ValueError as cause:
            raise RuntimeError(f"failed for main@{domain}") from cause
    except RuntimeError as e:
        e.add_note(f"note for note@{domain}")
        return e


def _exception_group():
    domain = "example.com"
    try:
        raise ExceptionGroup(f"group for group@{domain}", [_chained_exception()])
    except ExceptionGroup as group:
        return group


def _masked_record(exc):
    record = logging.LogRecord(
        "fastmcp.test", logging.ERROR, __file__, 1, "call by %s, 100%% failed",
        ("caller@example.com",), (type(exc), exc, exc.__traceback__),
    )
    assert _mask_record(record) is True
    return record


def _render_rich(record):
    stream = io.StringIO()
    handler = RichHandler(console=Console(file=stream, width=200), rich_tracebacks=True)
    handler.emit(record)
    return stream.getvalue()


def _assert_masked_chain(output):
    assert "@example.com" not in output
    assert "ValueError" in output and "cause for [email]" in output
    assert "RuntimeError" in output and "failed for [email]" in output
    assert "note for [email]" in output
    assert "_chained_exception" in output


def test_mask_record_masks_message():
    record = _masked_record(_chained_exception())
    assert record.getMessage() == "call by [email], 100% failed"


def test_mask_record_with_plain_formatter():
    output = logging.Formatter().format(_masked_record(_chained_exception()))
    _assert_masked_chain(output)


def test_mask_record_group_with_plain_formatter():
    output = logging.Formatter().format(_masked_record(_exception_group()))
    _assert_masked_chain(output)
    assert "group for [email]" in output


def test_mask_record_with_rich_handler():
    _assert_masked_chain(_render_rich(_masked_record(_chained_exception())))


def test_mask_record_group_with_rich_handler():
    """Rich does not show the cause of an exception inside a group."""
    output = _render_rich(_masked_record(_exception_group()))
    assert "@example.com" not in output
    assert "group for [email]" in output
    assert "RuntimeError" in output and "failed for [email]" in output
    assert "note for [email]" in output


def test_mask_record_handles_exception_with_broken_str():
    class Broken(Exception):
        def __str__(self):
            raise RuntimeError("no text")

    exc = Broken()
    record = logging.LogRecord(
        "fastmcp.test", logging.ERROR, __file__, 1, "failed", None,
        (Broken, exc, None),
    )
    assert _mask_record(record) is True
    assert str(record.exc_info[1]) == "<exception str() failed>"


def test_mask_record_handles_chain_cycle():
    first, second = ValueError("a@example.com"), ValueError("b@example.com")
    first.__context__, second.__context__ = second, first
    record = logging.LogRecord(
        "fastmcp.test", logging.ERROR, __file__, 1, "failed", None,
        (ValueError, first, None),
    )
    assert _mask_record(record) is True
    assert str(record.exc_info[1].__context__) == "[email]"


def test_real_start_masks_fastmcp_and_keeps_its_own_output(tmp_path):
    """A failed tool call is logged by fastmcp, not by this project's loggers."""
    script = textwrap.dedent(
        """
        import asyncio
        import sys
        from types import SimpleNamespace

        from fastmcp import Client, FastMCP
        from fastmcp.exceptions import ToolError
        from google_chat_mcp import __main__ as cli, server


        def fail(_):
            raise RuntimeError("failed for someone@example.com")


        async def call():
            async with Client(server.mcp) as client:
                try:
                    await client.call_tool("get_space", {"space_name": "spaces/A"})
                except ToolError:
                    pass


        def run(self, transport):
            server._chat = SimpleNamespace(spaces=SimpleNamespace(get=fail))
            asyncio.run(call())


        FastMCP.run = run
        sys.argv = ["google-chat-mcp", "--space", "spaces/A:r"]
        cli.main()
        """
    )
    # Wide enough that Rich does not wrap, which could hide an address from the checks.
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PYTHONPATH": str(REPO_ROOT),
        "COLUMNS": "300",
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr

    # stderr keeps fastmcp's Rich output, masked
    assert "Traceback (most recent call last)" in result.stderr
    assert "╭" in result.stderr
    assert "Error calling tool 'get_space'" in result.stderr
    assert "RuntimeError: failed for [email]" in result.stderr
    assert "someone@example.com" not in result.stderr

    # fastmcp's records stay out of the log file
    log = (tmp_path / ".config" / "google-chat-mcp" / "server.log").read_text()
    assert "tool=get_space" in log
    assert "Error calling tool" not in log
    assert "someone@example.com" not in log
