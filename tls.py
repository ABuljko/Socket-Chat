import io
import select
import socket
import ssl
import threading

HANDSHAKE_TIMEOUT = 10
POLL_INTERVAL = 0.5
CHUNK = 16 * 1024


def server_context(cert, key):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context


def client_context(cafile):
    return ssl.create_default_context(cafile=cafile)


class LockedSocket:
    def __init__(self, sock):
        sock.setblocking(False)
        self.sock = sock
        # OpenSSL can't handle two threads at once.
        self.lock = threading.Lock()
        self.closed = False

    def _wait(self, for_write):
        if self.closed:
            return False
        try:
            # select() fails on Linux above fd 1023.
            if hasattr(select, "poll"):
                poller = select.poll()
                poller.register(self.sock, select.POLLOUT if for_write else select.POLLIN)
                poller.poll(POLL_INTERVAL * 1000)
            elif for_write:
                select.select([], [self.sock], [], POLL_INTERVAL)
            else:
                select.select([self.sock], [], [], POLL_INTERVAL)
        except (OSError, ValueError):
            return False
        return not self.closed

    def recv_into(self, buffer):
        while True:
            with self.lock:
                if self.closed:
                    return 0
                try:
                    return self.sock.recv_into(buffer)
                except ssl.SSLWantReadError:
                    for_write = False
                except ssl.SSLWantWriteError:
                    for_write = True
            if not self._wait(for_write):
                return 0

    def sendall(self, data):
        view = memoryview(data)
        while view:
            # Retry with the same bytes.
            chunk = view[:CHUNK]
            sent = 0
            with self.lock:
                if self.closed:
                    raise ConnectionAbortedError("connection was shut down")
                try:
                    sent = self.sock.send(chunk)
                except ssl.SSLWantWriteError:
                    for_write = True
                except ssl.SSLWantReadError:
                    for_write = False
            if sent:
                view = view[sent:]
            elif not self._wait(for_write):
                raise ConnectionAbortedError("connection was shut down")

    def makefile(self, encoding="utf-8", errors="replace"):
        return io.TextIOWrapper(io.BufferedReader(_Reader(self)), encoding=encoding, errors=errors, newline="\n")

    def shutdown(self):
        with self.lock:
            self.closed = True
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def close(self):
        self.shutdown()
        with self.lock:
            self.sock.close()


class _Reader(io.RawIOBase):
    def __init__(self, sock):
        self._sock = sock

    def readable(self):
        return True

    def readinto(self, buffer):
        return self._sock.recv_into(buffer)
