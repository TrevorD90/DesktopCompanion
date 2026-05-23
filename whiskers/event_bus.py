"""Thread-safe pub/sub for SSE subscribers.

Backend threads (voice input, tutor brain, session) publish events; the Flask
SSE generator pulls from a per-subscriber queue. Events are dicts with a
required 'type' key and arbitrary JSON-serializable payload.
"""

import queue
import threading
import time


class EventBus:
    def __init__(self, queue_maxsize=256):
        self._lock = threading.Lock()
        self._subscribers = []  # list of queue.Queue
        self._queue_maxsize = queue_maxsize

    def subscribe(self):
        q = queue.Queue(maxsize=self._queue_maxsize)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def publish(self, event_type, **payload):
        event = {'type': event_type, 'ts': time.time(), **payload}
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                # Drop the oldest event for slow subscribers — keeps publishers
                # non-blocking. SSE will skip a beat but the client will catch
                # up on the next event.
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except queue.Empty:
                    pass


bus = EventBus()
