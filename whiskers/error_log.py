"""Thread-safe ring buffer of recent errors for the UI to display.

Server-side and client-side errors both push here so the user can see what
went wrong even after the toast scrolled past in the transcript.
"""

import threading
import time
from collections import deque

_LOCK = threading.Lock()
_BUF = deque(maxlen=200)


def log(source: str, message, level: str = 'error'):
    """Record an entry. Also prints to stdout for the server log."""
    msg = str(message)
    if len(msg) > 2000:
        msg = msg[:2000] + '…'
    entry = {
        'ts': time.time(),
        'source': source,
        'message': msg,
        'level': level,
    }
    with _LOCK:
        _BUF.append(entry)
    print(f'[{level.upper()}] {source}: {msg}')


def entries():
    with _LOCK:
        return list(_BUF)


def count():
    with _LOCK:
        return len(_BUF)


def clear():
    with _LOCK:
        _BUF.clear()
