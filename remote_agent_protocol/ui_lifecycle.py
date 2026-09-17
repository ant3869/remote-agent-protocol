"""Track explicit browser departures without treating a suspended tab as closed."""

import threading
import time


class UITabs:
    """Allow reloads and multiple tabs before shutting down the desktop session."""

    def __init__(self, grace_seconds: float = 15.0) -> None:
        """Keep the session alive during a brief browser reload."""
        self._lock = threading.Lock()
        self._tabs: dict[str, tuple[int, bool]] = {}
        self._deadline: float | None = None
        self._grace = grace_seconds

    def update(self, tab: str, sequence: int, opened: bool) -> None:
        """Ignore reordered delivery of a tab's open/close notifications."""
        with self._lock:
            previous = self._tabs.get(tab)
            if previous is not None and sequence <= previous[0]:
                return
            self._tabs[tab] = (sequence, opened)
            if any(active for _, active in self._tabs.values()):
                self._deadline = None
            elif self._deadline is None:
                self._deadline = time.monotonic() + self._grace

    def should_stop(self) -> bool:
        """No deadline exists until a browser explicitly reports its departure."""
        with self._lock:
            return self._deadline is not None and time.monotonic() >= self._deadline
