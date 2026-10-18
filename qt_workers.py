"""Qt-owned jobs and a bounded adapter for the existing process event queue."""

from __future__ import annotations

import queue
import threading

from PySide6.QtCore import QThread, Signal


class Job(QThread):
    def __init__(self, function, done=None, parent=None):
        super().__init__(parent)
        self.function, self.done = function, done
        self.result, self.error = None, None

    def run(self):
        try:
            self.result = self.function()
        except Exception as error:
            self.error = error


class ProcessBridge(QThread):
    batch = Signal(object)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.stopping = threading.Event()
        # Backpressure: at most one batch awaits the GUI, even under log floods.
        self.acknowledged = threading.Event()
        self.acknowledged.set()

    def run(self):
        previous = None
        while not self.stopping.is_set():
            if self.acknowledged.wait(0.1):
                events = []
                for _ in range(100):
                    try:
                        events.append(self.controller.events.get_nowait())
                    except queue.Empty:
                        break
                state = (
                    self.controller.state,
                    self.controller.mode,
                    dict(self.controller.status),
                )
                if events or state != previous:
                    previous = state
                    self.acknowledged.clear()
                    self.batch.emit((events, state))
            self.stopping.wait(0.1)

    def stop(self):
        self.stopping.set()
        self.acknowledged.set()
