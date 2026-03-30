"""Side-by-side terminal display using Rich Live + Layout."""
import threading
from collections import deque

from rich.cells import cell_len
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

_URL_BAR_HEIGHT = 3  # top border + 1 content line + bottom border


def _visual_lines(plain, width):
    """Count visual lines a message occupies at the given panel width."""
    if width <= 0:
        return 1
    total = 0
    for line in plain.split('\n'):
        total += max(1, -(-cell_len(line) // width))  # ceiling division
    return total


class SplitDisplay:
    """Thread-safe two-panel terminal display with a URL status bar."""

    def __init__(self, max_lines=200):
        self._left = deque(maxlen=max_lines)
        self._right = deque(maxlen=max_lines)
        self._url = ""  # GIL-atomic str; safe across threads without a lock
        self._console = Console()

    def add_left(self, formatted_msg):
        self._left.append(formatted_msg)

    def add_right(self, formatted_msg):
        self._right.append(formatted_msg)

    def set_url(self, url):
        self._url = url or ""

    def _render_panel(self, buffer, title, border_style):
        height = self._console.height - 2 - _URL_BAR_HEIGHT  # 2 = panel top/bottom borders
        width = self._console.width // 2 - 4  # inner width (panel borders + padding)

        items = list(buffer)
        selected = []
        used = 0
        for msg in reversed(items):
            text = Text.from_ansi(msg)
            vis = _visual_lines(text.plain, width)
            if used + vis > height and selected:
                break
            selected.append(text)
            used += vis

        selected.reverse()
        content = Group(*selected) if selected else Text("(waiting...)", style="dim")
        return Panel(content, title=title, border_style=border_style, expand=True)

    def _make_layout(self):
        url = self._url
        if url:
            url_content = Text(url, style="cyan", no_wrap=True, overflow="ellipsis")
        else:
            url_content = Text("(no URL)", style="dim yellow")
        url_bar = Panel(url_content, title="Current URL", border_style="blue")

        layout = Layout()
        layout.split_column(
            Layout(url_bar, name="url", size=_URL_BAR_HEIGHT),
            Layout(name="body"),
        )
        layout["body"].split_row(
            Layout(self._render_panel(self._left, "URL Monitor / CAPTCHA", "cyan"), name="left"),
            Layout(self._render_panel(self._right, "Booking Threads", "green"), name="right"),
        )
        return layout

    def run(self):
        """Start the Live display. Blocks until KeyboardInterrupt."""
        with Live(
            self._make_layout(),
            console=self._console,
            refresh_per_second=4,
            screen=True,
        ) as live:
            live.get_renderable = self._make_layout
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                pass
