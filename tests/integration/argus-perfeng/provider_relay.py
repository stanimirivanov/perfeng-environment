"""Forward the Argus pod's loopback HTTP connection to the fixture provider."""

import selectors
import socket
import socketserver


class Relay(socketserver.BaseRequestHandler):
    def handle(self):
        with (
            socket.create_connection(
                ("perfeng.perf-platform.svc.cluster.local", 8080), 15
            ) as upstream,
            selectors.DefaultSelector() as sockets,
        ):
            sockets.register(self.request, selectors.EVENT_READ, upstream)
            sockets.register(upstream, selectors.EVENT_READ, self.request)
            while events := sockets.select(timeout=60):
                for source, _ in events:
                    data = (upstream if source.fileobj is upstream else self.request).recv(65536)
                    if not data:
                        return
                    source.data.sendall(data)


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


if __name__ == "__main__":
    with Server(("127.0.0.1", 8081), Relay) as server:
        server.serve_forever()
