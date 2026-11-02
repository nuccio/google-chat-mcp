"""
Masking of email addresses in the logs.

A safety net, not a guarantee: it covers the common forms of an address, plain
and URL-encoded (%40), and will not catch every possible one.
"""

import logging
import re

EMAIL_MARKER = "[email]"

# The lookbehind makes a match start only where a run of local-part characters
# starts: without it, long text with no address takes quadratic time.
_EMAIL = re.compile(r"(?<![\w.%+-])[\w.%+-]+(?:@|%40)(?:[\w-]+\.)+[^\W\d_]{2,}")


def mask_emails(text: str) -> str:
    return _EMAIL.sub(EMAIL_MARKER, text)


class EmailMaskingFormatter(logging.Formatter):
    """Masks addresses in the finished line, tracebacks included."""

    def format(self, record: logging.LogRecord) -> str:
        return mask_emails(super().format(record))


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
    record.msg = mask_emails(record.getMessage())
    record.args = None
    if record.exc_info and record.exc_info[1] is not None:
        masked = _masked_exception(record.exc_info[1])
        record.exc_info = (type(masked), masked, record.exc_info[2])
    record.exc_text = None
    if record.stack_info:
        record.stack_info = mask_emails(record.stack_info)
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
