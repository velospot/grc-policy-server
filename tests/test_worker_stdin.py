import os
import sys

from grc_policy_server import worker


def test_worker_ensure_stdin_open_recovers_from_closed_stdin(monkeypatch):
    closed = open(os.devnull)
    closed.close()
    monkeypatch.setattr(sys, "stdin", closed, raising=False)

    worker._ensure_stdin_open()

    assert sys.stdin is not None
    assert not getattr(sys.stdin, "closed", False)
    # Most importantly: this must not raise ValueError.
    sys.stdin.isatty()


def test_worker_process_init_does_not_close_stdin_in_main_process(monkeypatch):
    stdin = open(os.devnull)
    monkeypatch.setattr(sys, "stdin", stdin, raising=False)

    worker._close_inherited_stdin()

    assert not stdin.closed
    stdin.close()

