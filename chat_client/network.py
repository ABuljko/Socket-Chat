import errno
import json
import socket
import threading
import time
from pathlib import Path

import tls


class ServerConnection:
    def __init__(self, host, port, cafile, events):
        if not Path(cafile).is_file():
            raise FileNotFoundError(errno.ENOENT, "certificate not found", str(cafile))
        context = tls.client_context(cafile)
        sock = socket.create_connection((host, port), timeout=5)
        self.sock = tls.LockedSocket(context.wrap_socket(sock, server_hostname=host))
        self.events = events
        # The reading thread answers pings, so sends come from two threads.
        self.send_lock = threading.Lock()
        self.last_seen = time.monotonic()

    def start(self):
        """Queue each message except pings as (self, message).

        When the connection ends, queue (self, {"type": "disconnected"}).
        """
        threading.Thread(target=self._read_loop, daemon=True).start()

    def send(self, type_, /, **fields):
        data = (json.dumps({"type": type_, **fields}) + "\n").encode()
        with self.send_lock:
            self.sock.sendall(data)

    def _read_loop(self):
        try:
            with self.sock.makefile() as reader:
                for line in reader:
                    self.last_seen = time.monotonic()
                    try:
                        message = json.loads(line)
                    except (ValueError, RecursionError):
                        continue
                    if not isinstance(message, dict):
                        continue
                    if message.get("type") == "ping":
                        self.send("pong")
                    else:
                        self.events.put((self, message))
        except (OSError, ValueError):
            pass
        self.events.put((self, {"type": "disconnected"}))

    def close(self):
        self.sock.close()
