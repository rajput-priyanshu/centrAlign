"""Explicit per-task scratchpad memory.

This is deliberately separate from the LLM conversation history: it is what the
agent *chooses* to write down as a fact worth keeping, and it is what the dashboard
and final report show under "Facts remembered during execution". A real deployment
would want this queryable across tasks too (e.g. "what did we pay vendor X last
time"); for this prototype it is scoped to a single task run.
"""
import json
import os
import threading


class Memory:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._data = {}
        if os.path.exists(path):
            with open(path) as f:
                self._data = json.load(f)

    def remember(self, key, value):
        with self._lock:
            self._data[key] = value
            self._flush()

    def recall(self, key=None):
        with self._lock:
            if key is None:
                return dict(self._data)
            return self._data.get(key)

    def all(self):
        with self._lock:
            return dict(self._data)

    def _flush(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)
