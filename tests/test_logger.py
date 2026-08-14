"""Logging never blocks a run, and is never the thing that ends one.

File writes happen on a background thread so a log line cannot stall the event
loop, which makes the queue's overflow rule part of the contract rather than an
implementation detail: a full queue drops its oldest line instead of making the
caller wait. Every other case here is the same rule wearing a different hat — a
module that logs at import time before ``initialize`` ran, a message whose
``__str__`` raises, a ``shutdown`` with no file sink to stop. Session prefixing
is what makes the resulting file readable at all: concurrent sessions share one
log, and with no ``[session]`` tag their lines interleave indistinguishably.

The global ``logger`` singleton is deliberately untouched; ``a_logger()`` steps
around the Singleton metaclass so no test can poison another's.
"""

import logging
import time
from types import SimpleNamespace

import pytest

from agentevolver.logger.log import (
    LogLevel,
    Logger,
    get_session_id,
    set_session_id,
)


def a_logger(name="test-logger"):
    """A Logger outside the Singleton registry, so tests never share one."""
    instance = Logger.__new__(Logger)
    Logger.__init__(instance, name=name)
    return instance


@pytest.fixture
def log(tmp_path):
    instance = a_logger()
    instance.initialize(
        SimpleNamespace(log_path=str(tmp_path / "log" / "agent.log")),
        console_stream=open(tmp_path / "console.out", "w", encoding="utf-8"),
    )
    yield instance
    instance.shutdown()


@pytest.fixture(autouse=True)
def _clear_session():
    # The session id is a contextvar, so it outlives the test that set it and
    # would silently prefix the next one's lines.
    set_session_id(None)
    yield
    set_session_id(None)


def written(log, timeout=3.0):
    """The file sink is asynchronous — wait for the writer to catch up."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            text = open(log._log_path, encoding="utf-8").read()
        except FileNotFoundError:
            text = ""
        if text.strip():
            return text
        time.sleep(0.02)
    return ""


# --------------------------------------------------------------------------- #
# Telling one session's lines from another's
# --------------------------------------------------------------------------- #
def test_the_session_id_starts_unset():
    assert get_session_id() is None


def test_a_set_session_id_reads_back():
    set_session_id("sess-1")
    assert get_session_id() == "sess-1"


def test_messages_are_prefixed_with_the_current_session():
    """Concurrent sessions share one log; without this their lines interleave
    indistinguishably."""
    instance = a_logger()
    set_session_id("sess-1")
    assert instance._prefix_msg("hello") == "[sess-1] hello"


def test_nothing_is_prefixed_when_no_session_is_bound():
    assert a_logger()._prefix_msg("hello") == "hello"


def test_a_rich_object_is_not_prefixed():
    """The prefix is string surgery; applying it to a renderable would break it."""
    from rich.table import Table

    set_session_id("sess-1")
    table = Table()
    assert a_logger()._prefix_msg(table) is table


# --------------------------------------------------------------------------- #
# Levels
# --------------------------------------------------------------------------- #
def test_the_level_enum_matches_the_standard_library():
    """These values are handed straight to ``setLevel`` and to ``getattr(logging, …)``.

    An enum that merely ordered its members correctly would read fine and filter
    wrongly — a handler set to a home-grown ``ERROR`` would silently swallow
    everything or nothing, with no error to trace it back from.
    """
    assert LogLevel.INFO == logging.INFO
    assert LogLevel.ERROR == logging.ERROR
    assert LogLevel.CRITICAL == logging.CRITICAL


# --------------------------------------------------------------------------- #
# The file sink
# --------------------------------------------------------------------------- #
def test_a_message_reaches_the_log_file(log):
    """The call returning without raising proves nothing: the write happens on
    another thread, so the file is the only place the outcome is visible."""
    log.info("hello from the test")
    assert "hello from the test" in written(log)


@pytest.mark.parametrize("level", ["info", "warning", "error", "critical"])
def test_every_level_reaches_the_file(log, level):
    """Each level is a separately hand-written method, not one shared path.

    ``info``/``warning``/``error``/``critical`` each prefix the message and
    enqueue it themselves, so a level can be added — or edited — without the
    enqueue, and the console keeps showing it while the file quietly loses it.
    """
    getattr(log, level)(f"message at {level}")
    assert f"message at {level}" in written(log)


def test_the_session_prefix_reaches_the_file(log):
    """The prefix is applied by the level method, before the queue, not by the
    writer thread — so a method that skips it produces a file whose lines cannot
    be attributed even though the console output looked right."""
    set_session_id("sess-7")
    log.info("prefixed line")
    assert "[sess-7] prefixed line" in written(log)


def test_the_log_directory_is_created_if_absent(tmp_path):
    """The configured path points into a session tree that may not exist yet."""
    instance = a_logger()
    path = tmp_path / "deep" / "nested" / "agent.log"
    instance.initialize(SimpleNamespace(log_path=str(path)),
                        console_stream=open(tmp_path / "c.out", "w"))
    try:
        assert path.parent.is_dir()
    finally:
        instance.shutdown()


def test_a_host_can_start_without_creating_a_log_file(tmp_path):
    """The Gateway starts before any session exists; a tag-level file would be
    an orphan."""
    instance = a_logger()
    path = tmp_path / "log" / "agent.log"
    instance.initialize(SimpleNamespace(log_path=str(path)),
                        console_stream=open(tmp_path / "c.out", "w"),
                        file_logging=False)
    try:
        instance.info("before any session")
        assert not path.exists()
    finally:
        instance.shutdown()


# --------------------------------------------------------------------------- #
# Nothing here may take the run down
# --------------------------------------------------------------------------- #
def test_logging_before_initialize_is_dropped_not_raised():
    """A module logging at import time must not crash the process."""
    a_logger()._enqueue_log("info", "too early")  # must not raise


def test_a_full_queue_drops_the_oldest_line_rather_than_blocking(log):
    """Blocking here would stall the event loop the async sink exists to protect."""
    from queue import Queue

    # Two slots and five lines: small enough that the overflow branch is the
    # only one exercised, and the surviving pair identifies which end was cut.
    log._log_queue = Queue(maxsize=2)
    for n in range(5):
        log._enqueue_log("info", f"line {n}")
    assert log._log_queue.qsize() == 2
    remaining = [log._log_queue.get_nowait() for _ in range(2)]
    assert "line 4" in remaining[-1]
    assert "line 0" not in "".join(remaining)


def test_an_unformattable_message_does_not_raise(log):
    """Agents log arbitrary objects, and some of them raise when rendered.

    A log call is not a place a caller checks for errors, so an exception
    escaping from here surfaces as a failure of whatever was being logged about.
    """

    class Explodes:
        def __str__(self):
            raise RuntimeError("cannot render")

    log._enqueue_log("info", "%s", Explodes())  # must not raise


# --------------------------------------------------------------------------- #
# Moving the file sink under a session
# --------------------------------------------------------------------------- #
def test_rebinding_moves_later_lines_to_the_new_file(log, tmp_path):
    """A long-lived host initialises once, then binds a session."""
    log.info("before rebind")
    assert "before rebind" in written(log)
    first_path = log._log_path

    second_path = str(tmp_path / "session" / "log" / "agent.log")
    log.rebind(second_path)
    log.info("after rebind")

    assert "after rebind" in written(log)
    assert log._log_path == second_path
    assert "after rebind" not in open(first_path, encoding="utf-8").read()


def test_rebinding_to_the_same_path_keeps_the_existing_sink(log):
    """Rebinding runs on every task, and most of them bind the session already bound.

    Tearing the sink down and building it again would stop the writer thread
    with lines still queued behind it — so the cost of treating this as a normal
    rebind is dropped log lines on the busiest path there is.
    """
    handler = log._file_handler
    thread = log._log_thread
    log.rebind(log._log_path)
    assert log._file_handler is handler
    assert log._log_thread is thread


def test_the_console_sink_survives_a_rebind(log, tmp_path):
    """Nothing may be lost while the file sink is being switched."""
    before = len(log.handlers)
    log.rebind(str(tmp_path / "other" / "agent.log"))
    assert len(log.handlers) == before


# --------------------------------------------------------------------------- #
# Shutdown
# --------------------------------------------------------------------------- #
def test_shutdown_is_safe_before_any_file_sink_exists():
    a_logger().shutdown()  # must not raise


def test_shutdown_stops_the_writer_thread(log):
    """The writer is a daemon thread holding an open file handle. It would not keep
    the process alive, but a shutdown that leaves it running leaves the last lines
    unflushed and the handle open across a rebind."""
    log.info("last line")
    log.shutdown()
    assert not log._log_thread.is_alive()
