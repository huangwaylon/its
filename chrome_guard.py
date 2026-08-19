"""One Chrome at a time, across both of this program's browser users.

`captcha_solver._kill_stray_chrome()` reaps by `pgrep -f remote-debugging-port`,
which matches `browser_apply`'s application-filing browser as readily as a solving
one: a solve that timed out mid-application would SIGKILL it somewhere between
申込する and 確認, with no way to learn which side of the commit it died on.
Serialised, the reaper only runs while its own side holds the lock, so anything
`pgrep` finds really is an orphan. Both sides take the lock with a timeout — one is
the only thread that can re-mint a session, the other holds a room that expires.
"""
import contextlib
import threading

# Long enough to sit through a CAPTCHA solve (CAPTCHA_TIMEOUT, 180s) plus teardown,
# short enough that a wedged holder cannot cost a whole booking.
DEFAULT_WAIT = 210

_lock = threading.Lock()


@contextlib.contextmanager
def chrome(timeout=None):
    """Own the browser for the duration of the block.

    Yields True when the lock was taken and False when it was not — callers decide
    what to do with that, because the right answer differs: a CAPTCHA solve can wait
    for the next cycle, while a booking sitting on a hold may have seconds left.

    `timeout=None` means `DEFAULT_WAIT`, read at call time rather than bound as a
    default argument, so a test can shorten it without waiting three minutes to find
    out that a busy Chrome defers a solve.
    """
    wait = DEFAULT_WAIT if timeout is None else timeout
    acquired = _lock.acquire(timeout=wait)
    try:
        yield acquired
    finally:
        if acquired:
            _lock.release()


def in_use():
    """True when some thread currently owns Chrome. Advisory only."""
    if _lock.acquire(blocking=False):
        _lock.release()
        return False
    return True
