"""One Chrome at a time, across both of this program's browser users.

Two places drive real Chrome: `captcha_solver` re-mints the `s=` session, and
`browser_apply` submits the applicant form when curl is refused. They must not run
at once, for one blunt reason — `captcha_solver._kill_stray_chrome()` reaps by
`pgrep -f remote-debugging-port`, which matches *every* Chrome either of them
launched. A CAPTCHA solve that times out while an application is being filed would
SIGKILL the browser holding a 30-minute room hold, somewhere between 申込する and
確認, with no way to find out which side of the commit it died on.

Serialising them removes the hazard rather than papering over it: the reaper can
only run while its own side owns the lock, at which point no other Chrome of ours
exists and anything `pgrep` finds really is an orphan.

`acquire()` always takes a timeout. The CAPTCHA solve happens in the one thread
that can re-mint a session and the browser submit happens inside a booking thread
against a hold that expires, so neither may block on the other indefinitely.
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
