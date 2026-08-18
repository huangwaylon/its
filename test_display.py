#!/usr/bin/env python3
"""Tests for the split-panel TUI in display.py.

The one thing this display must guarantee is that the *newest* line is on
screen. It failed at that: `_visual_lines` estimated a message's height as
ceil(cell_len / width), Rich wraps on word boundaries and often needs one line
more, so `_render_panel` selected more messages than fit and Panel cropped the
excess off the bottom of its own content. The dashboard kept redrawing four
times a second and looked perfectly healthy while never showing the last line or
two.

Renders only — no server, no sockets, nothing touched on disk.

    .venv/bin/python test_display.py
"""
import io

from rich.console import Console
from rich.text import Text

from display import SplitDisplay, _URL_BAR_HEIGHT, _visual_lines

FAILURES = []

# 19-cell words in a 36-cell panel: 59 cells, so the old ceiling division said
# two lines, while word wrapping really needs three. This is the shape that made
# the newest messages disappear.
PATHOLOGICAL = ' '.join(['x' * 19] * 3)

# Lines this program actually logs, kept verbatim from book_hotels/main so the
# measurement is exercised on real inputs rather than on invented ones.
REAL_MESSAGES = [
    '12:34:56 [AUG] [412] no dates available (8 checked, 14 scans), waiting...',
    '12:34:56 [AUG 22]   Booking: ホテルハーヴェスト那須',
    '12:34:56 [AUG 22] Found 11 hotels: NAGU 勝浦, リソルの森, トスラブ箱根ビオーレ, 日光千姫物語',
    '12:34:56 [AUG 22]   Missing form params on booking page (status 200, '
    'missing: form_action, coma_s)',
    '12:34:56 [AUG 22]   Debug response saved: '
    '20260818_143022_0007_2026-08-22_step5_booking_form_status200 — location: (none)',
    '12:34:56 URL check: valid (200)',
    '12:34:56 New URL saved — service_category_id=1 verify_expires=+1h29m',
    '12:34:56 [AUG 22] 3 to book (priority first): NAGU 勝浦, リソルの森',
    '12:34:56   curl POST failed (503), retrying in 1.0s...',
    '\033[92m12:34:56 [AUG 22]   BOOKED: NAGU 勝浦\033[0m',
    PATHOLOGICAL,
]


def check(name, cond, detail=''):
    print(f'{"PASS" if cond else "FAIL"}  {name}' + (f'  — {detail}' if detail and not cond else ''))
    if not cond:
        FAILURES.append(name)


def _display(width, height):
    """A SplitDisplay rendering at a fixed size, independent of the real terminal."""
    d = SplitDisplay()
    d._console = Console(width=width, height=height, force_terminal=True,
                         color_system='truecolor')
    return d


def _panel_geometry(console):
    """The inner width and content height `_render_panel` works with."""
    return console.width // 2 - 4, console.height - 2 - _URL_BAR_HEIGHT


def _rendered_height(console, text, width):
    """The number of lines Rich really produces for `text` at `width`.

    Ground truth for `_visual_lines`, taken from the renderer rather than from a
    second guess: this is the call Panel makes on its child.
    """
    return len(console.render_lines(text, console.options.update(width=width),
                                    pad=False))


def _render(renderable, width, height):
    """Render to a throwaway console of exactly this size and return the text.

    `file=StringIO` keeps the rendered panels out of the test output; `record`
    still captures them for the assertions.
    """
    out = Console(width=width, height=height, force_terminal=True,
                  color_system='truecolor', record=True, file=io.StringIO())
    out.print(renderable)
    return out.export_text()


# ── Measurement ─────────────────────────────────────────────────────

def test_visual_lines_matches_what_rich_renders():
    """The whole bug in one assertion: measure exactly, or crop silently."""
    for total_width in (60, 80, 100, 120, 160, 200):
        console = Console(width=total_width, color_system=None)
        width, _ = _panel_geometry(Console(width=total_width, height=24))
        for msg in REAL_MESSAGES:
            text = Text.from_ansi(msg)
            measured = _visual_lines(console, text, width)
            actual = _rendered_height(console, text, width)
            check(f'measure: w={width} {msg[9:38]!r}', measured == actual,
                  f'measured {measured}, renders {actual}')


def test_visual_lines_counts_the_pathological_case():
    """Three 19-cell words in 36 cells: the old ceiling division said 2."""
    console = Console(width=80, color_system=None)
    text = Text(PATHOLOGICAL)
    check('measure: word wrap needs the third line',
          _visual_lines(console, text, 36) == 3, str(_visual_lines(console, text, 36)))


def test_visual_lines_edge_cases():
    console = Console(width=80, color_system=None)
    check('measure: zero width is one line, not a crash',
          _visual_lines(console, Text('anything'), 0) == 1)
    check('measure: negative width is one line',
          _visual_lines(console, Text('anything'), -5) == 1)
    check('measure: empty message still occupies a line',
          _visual_lines(console, Text(''), 36) == 1)
    check('measure: embedded newlines counted',
          _visual_lines(console, Text('one\ntwo\nthree'), 36) == 3)
    check('measure: an unbreakable token folds',
          _visual_lines(console, Text('t' * 100), 36) == 3)
    # A double-width run: 20 CJK characters are 40 cells, so they cannot fit in 36.
    check('measure: full-width characters counted by cell, not by character',
          _visual_lines(console, Text('週' * 20), 36) == 2)


# ── The panel ───────────────────────────────────────────────────────

def test_newest_message_is_always_visible():
    """Across terminal sizes, the last line logged must be on screen."""
    sizes = ((80, 20), (80, 45), (100, 24), (132, 40), (160, 30), (60, 15))
    for total_width, total_height in sizes:
        for shape, body in (('pathological', PATHOLOGICAL), ('real', None)):
            d = _display(total_width, total_height)
            msgs = [f'{body or REAL_MESSAGES[i % len(REAL_MESSAGES)]} #{i:02d}'
                    for i in range(60)]
            for m in msgs:
                d.add_left(m)
                d.add_right(m)
            txt = _render(d._make_layout(), total_width, total_height)
            where = f'{total_width}x{total_height} ({shape})'
            check(f'panel: newest visible at {where}', '#59' in txt, txt)
            check(f'panel: fits the terminal at {where}',
                  len(txt.splitlines()) <= total_height, str(len(txt.splitlines())))
            # Cropping eats from the bottom, so whenever the last two messages do
            # fit, both must be on screen — not just the older of the two.
            width, height = _panel_geometry(d._console)
            if sum(_visual_lines(d._console, Text.from_ansi(m), width)
                   for m in msgs[-2:]) <= height:
                check(f'panel: second-newest visible at {where}', '#58' in txt, txt)


def test_oldest_messages_are_the_ones_dropped():
    """A full panel scrolls: oldest off the top, newest kept at the bottom."""
    d = _display(100, 24)
    for i in range(40):
        d.add_left(f'12:00:00 line number {i:02d}')
    txt = _render(d._make_layout(), 100, 24)
    check('panel: oldest dropped', 'line number 00' not in txt, txt)
    check('panel: newest kept', 'line number 39' in txt, txt)
    lines = [l for l in txt.splitlines() if 'line number' in l]
    numbers = [int(l.split('line number ')[1][:2]) for l in lines]
    check('panel: shown in order, oldest first', numbers == sorted(numbers), str(numbers))
    check('panel: contiguous run', numbers == list(range(numbers[0], numbers[-1] + 1)),
          str(numbers))


def test_panel_survives_a_message_taller_than_the_panel():
    """One huge message must still render rather than selecting nothing."""
    d = _display(80, 12)
    d.add_left('12:00:00 ' + ' '.join(['word'] * 400))
    txt = _render(d._make_layout(), 80, 12)
    check('panel: oversized message still shown', 'word' in txt, txt)
    check('panel: oversized message does not overflow the terminal',
          len(txt.splitlines()) <= 12, str(len(txt.splitlines())))


def test_empty_panels_and_url_bar():
    d = _display(100, 24)
    txt = _render(d._make_layout(), 100, 24)
    check('layout: empty panel shows a placeholder', '(waiting...)' in txt, txt)
    check('layout: no URL shows a placeholder', '(no URL)' in txt, txt)
    check('layout: both panel titles present',
          'URL Monitor' in txt and 'Booking Threads' in txt, txt)

    d.set_url('https://as.its-kenpo.or.jp/calendar_apply/calendar_select?s=TOKEN')
    txt = _render(d._make_layout(), 100, 24)
    check('layout: URL shown once set', 'calendar_select' in txt, txt)
    d.set_url(None)
    check('layout: URL can be cleared',
          '(no URL)' in _render(d._make_layout(), 100, 24))


def test_panels_are_independent():
    d = _display(100, 24)
    d.add_left('12:00:00 left-only message')
    d.add_right('12:00:00 right-only message')
    txt = _render(d._make_layout(), 100, 24)
    check('layout: left message on the left panel', 'left-only' in txt, txt)
    check('layout: right message on the right panel', 'right-only' in txt, txt)
    left, right = txt.splitlines()[4].split('││')
    check('layout: messages land in their own panel',
          'left-only' in left and 'right-only' in right, txt.splitlines()[4])


def test_buffer_is_bounded():
    """The deques are the only thing bounding memory over a multi-week run."""
    d = SplitDisplay(max_lines=10)
    for i in range(50):
        d.add_left(f'msg {i}')
    check('buffer: capped at max_lines', len(d._left) == 10, str(len(d._left)))
    check('buffer: keeps the newest', 'msg 49' in d._left[-1], d._left[-1])


def test_ansi_colour_is_rendered_not_printed():
    d = _display(100, 24)
    d.add_left('\033[92m12:00:00 BOOKED: NAGU 勝浦\033[0m')
    txt = _render(d._make_layout(), 100, 24)
    check('ansi: message text kept', 'BOOKED: NAGU' in txt, txt)
    check('ansi: escape codes not shown literally', '\\033[92m' not in txt and '[92m' not in txt, txt)


TESTS = (
    'test_visual_lines_matches_what_rich_renders',
    'test_visual_lines_counts_the_pathological_case',
    'test_visual_lines_edge_cases',
    'test_newest_message_is_always_visible',
    'test_oldest_messages_are_the_ones_dropped',
    'test_panel_survives_a_message_taller_than_the_panel',
    'test_empty_panels_and_url_bar',
    'test_panels_are_independent',
    'test_buffer_is_bounded',
    'test_ansi_colour_is_rendered_not_printed',
)


def main_(argv=()):
    """Run the suite. Args select tests by substring."""
    names = [a for a in argv if not a.startswith('-')]
    for name in TESTS:
        if not names or any(k in name for k in names):
            globals()[name]()

    print()
    if FAILURES:
        print(f'{len(FAILURES)} FAILED:')
        for f in FAILURES:
            print(f'  - {f}')
        raise SystemExit(1)
    print('all checks passed')


if __name__ == '__main__':
    import sys
    main_(sys.argv[1:])
