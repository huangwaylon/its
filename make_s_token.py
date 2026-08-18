#!/usr/bin/env python3
"""Build an `s=` token with verify_expires 1.5 hours out, showing each layer.

The site's encoding, outward from the plaintext:

    payload  -> base64 -> reverse -> base64 -> token

Nothing signs the payload, so this is a plain reversible transform. Printing the
layers is the point of this script; see `book_hotels.token_summary()` for the
redacted form used in the log.
"""

import base64
import time

EXPIRES_IN = 1.5 * 3600  # seconds from now


def make_token(expires_at):
    """Return (payload, inner, reversed, token) for a verify_expires value."""
    payload = f'service_category_id=1&verify_expires={int(expires_at)}'
    inner = base64.b64encode(payload.encode()).decode()
    reversed_inner = inner[::-1]
    token = base64.b64encode(reversed_inner.encode()).decode()
    return payload, inner, reversed_inner, token


def main():
    payload, inner, reversed_inner, token = make_token(time.time() + EXPIRES_IN)
    for name, value in (('payload', payload), ('inner', inner),
                        ('reverse', reversed_inner), ('token', token)):
        print(f'{name:8}({len(value):>3}): {value}')


if __name__ == '__main__':
    main()
