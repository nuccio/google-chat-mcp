"""
Masking of email addresses in the logs.

A safety net, not a guarantee: it covers the common forms of an address, plain
and URL-encoded (%40, %2540), and will not catch every possible one. Not masked:
- a domain with no alphabetic top-level domain, so that "fastmcp@4.1.3" stays
  readable ("user@localhost", "user@192.168.1.10");
- deliberately obfuscated forms ("user[at]example.com", "user&#64;example.com");
- the part before a "/" or "=" in the local part: they separate resource names
  and query strings, so "users/someone@example.com" keeps its "users/" prefix.
"""

import logging
import re

EMAIL_MARKER = "[email]"
_UNFORMATTABLE = "<log message could not be formatted>"

_LOCAL_CHARS = r"[\w.%+!#$&*^~-]"
# The lookbehinds make a match start only where a run of local-part characters
# starts: without them, long text with no address takes quadratic time.
# An apostrophe counts only inside the local part (o'brien), so that the quote
# opening a logged value ('someone@example.com') stays out of the match.
_EMAIL = re.compile(
    "(?:"
    rf"(?<!{_LOCAL_CHARS})(?<!{_LOCAL_CHARS}'){_LOCAL_CHARS}+(?:'{_LOCAL_CHARS}+)*"
    r'|"[^"\\\n]{1,64}"'
    ")"
    r"(?:[@＠﹫]|%(?:25)*40)"
    r"(?:[\w-]+\.)+(?:xn--[\w-]+|[^\W\d_]{2,})"
)


def mask_emails(text: str) -> str:
    return _EMAIL.sub(EMAIL_MARKER, text)


class EmailMaskingFormatter(logging.Formatter):
    """Masks addresses in the finished line, tracebacks included."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            return mask_emails(super().format(record))
        except Exception:
            # Raising would send the record to logging's handleError, which
            # prints its message and arguments to stderr unmasked.
            return f"{record.levelname} {record.name}: {_UNFORMATTABLE}"


def _masked_exception(exc: BaseException | None, seen: dict | None = None) -> BaseException | None:
    """A stand-in for exc that renders the same, with addresses masked.

    Keeps what a traceback shows: class name, frames, notes, chained exceptions
    and the exceptions of a group.
    """
    if exc is None:
        return None
    seen = {} if seen is None else seen
    if id(exc) in seen:
        return seen[id(exc)]

    if isinstance(exc, BaseExceptionGroup):
        masked = ExceptionGroup(
            mask_emails(exc.message),
            [_masked_exception(sub, seen) for sub in exc.exceptions],
        )
    else:
        try:
            text = mask_emails(str(exc))
        except Exception:
            text = "<exception str() failed>"
        cls = type(exc)
        masked = type(
            cls.__name__,
            (Exception,),
            {
                "__str__": lambda self: text,
                "__module__": cls.__module__,
                "__qualname__": cls.__qualname__,
            },
        )()
    seen[id(exc)] = masked

    masked.__traceback__ = exc.__traceback__
    masked.__cause__ = _masked_exception(exc.__cause__, seen)
    masked.__context__ = _masked_exception(exc.__context__, seen)
    masked.__suppress_context__ = exc.__suppress_context__
    for note in getattr(exc, "__notes__", ()):
        masked.add_note(mask_emails(note))
    return masked


def _mask_record(record: logging.LogRecord) -> bool:
    try:
        record.msg = mask_emails(record.getMessage())
        if record.exc_info and record.exc_info[1] is not None:
            masked = _masked_exception(record.exc_info[1])
            record.exc_info = (type(masked), masked, record.exc_info[2])
        if record.stack_info:
            record.stack_info = mask_emails(record.stack_info)
    except Exception:
        # An exception raised by a filter reaches the code that logged, so a
        # malformed log call would make it fail. Drop the content instead.
        record.msg = _UNFORMATTABLE
        record.exc_info = None
        record.stack_info = None
    record.args = None
    record.exc_text = None
    return True


def mask_fastmcp_logging() -> None:
    """Masks addresses in what fastmcp logs. Call it after importing fastmcp.

    fastmcp has its own Rich handlers on stderr and does not propagate to the
    root logger, so its records never reach EmailMaskingFormatter. Masking must
    happen on the record: Rich wraps lines to fit its panels, which can split an
    address in the finished output.

    fastmcp replaces its handlers if run() is given a log_level: don't pass one.
    """
    for handler in logging.getLogger("fastmcp").handlers:
        handler.addFilter(_mask_record)
