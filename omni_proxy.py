import http.server
import socketserver
import sys
import urllib.request
import urllib.error

TARGET = 'http://127.0.0.1:3001'  # upstream OmniRoute
PORT = 3002
LOGFILE = 'omni_proxy.log'

class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def _log(self, msg):
        with open(LOGFILE, 'a', encoding='utf-8') as f:
            f.write(msg + '\n')
        print(msg)

    def _forward(self):
        path = self.path
        # Defensive fix: if client sent a provider path without /v1 (anomalous observed),
        # prefix /v1 for known API routes so upstream receives canonical paths.
        if not path.startswith('/v1') and ('/chat/completions' in path or path.startswith('/models') or '/models' in path):
            if path.startswith('/'):
                path = '/v1' + path
            else:
                path = '/v1/' + path

        url = TARGET + path

        # Copy headers but normalize/remove hop-by-hop and problematic headers
        headers = {k: v for k, v in self.headers.items()}
        # Remove Expect to avoid 100-continue issues upstream
        headers.pop('Expect', None)
        headers.pop('expect', None)
        # Ensure we set Host to the upstream host
        try:
            from urllib.parse import urlparse

            upstream_host = urlparse(TARGET).netloc
            headers['Host'] = upstream_host
        except Exception:
            pass
        length = int(self.headers.get('Content-Length', 0))
        body = None
        # If Content-Length is provided, read exactly that many bytes
        if length:
            body = self.rfile.read(length)
        else:
            te = self.headers.get('Transfer-Encoding', '') or ''
            if 'chunked' in te.lower():
                # read chunked body per RFC 7230
                chunks = []
                while True:
                    # read chunk-size line
                    line = self.rfile.readline()
                    if not line:
                        break
                    try:
                        size = int(line.strip().split(b";")[0], 16)
                    except Exception:
                        break
                    if size == 0:
                        # consume trailer and final CRLF
                        # read until an empty line
                        while True:
                            t = self.rfile.readline()
                            if not t or t in (b"\r\n", b"\n"):
                                break
                        break
                    data = self.rfile.read(size)
                    # consume CRLF
                    _ = self.rfile.read(2)
                    chunks.append(data)
                body = b"".join(chunks) if chunks else None
            else:
                # fallback: try to read what's available (non-blocking not available here), so don't block
                body = None
        self._log(f'===> {self.command} {path}')
        self._log(f'Headers: {dict(self.headers)}')
        if body:
            try:
                self._log('Body: ' + body.decode('utf-8', errors='replace'))
            except Exception:
                self._log('Body: <binary>')

        # If we have a body and the client didn't send a Content-Length (e.g., after chunking),
        # ensure Content-Length matches the body we forward.
        if body is not None:
            headers['Content-Length'] = str(len(body))

        # Use urllib to avoid external dependency on `requests`.
        try:
            req = urllib.request.Request(url, data=body, method=self.command)
            for k, v in headers.items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = resp.getcode()
                reason = getattr(resp, 'reason', '')
                resp_headers = dict(resp.getheaders())
                resp_body = resp.read()
        except urllib.error.HTTPError as e:
            status = e.code
            reason = getattr(e, 'reason', '')
            try:
                resp_body = e.read()
            except Exception:
                resp_body = b''
            resp_headers = dict(e.headers) if hasattr(e, 'headers') and e.headers else {}
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(str(e).encode('utf-8'))
            self._log('Upstream request failed: ' + str(e))
            return

        self._log(f'<=== {status} {reason}')
        self.send_response(status)
        for k, v in resp_headers.items():
            # Skip hop-by-hop headers
            if k.lower() in ('connection', 'keep-alive', 'transfer-encoding', 'content-encoding'):
                continue
            try:
                self.send_header(k, v)
            except Exception:
                pass
        self.end_headers()

        try:
            if resp_body:
                self.wfile.write(resp_body)
                self.wfile.flush()
        except Exception as e:
            self._log('Error streaming response: ' + str(e))

    # Forward common HTTP methods to the upstream
    def do_GET(self):
        self._forward()

    def do_POST(self):
        self._forward()

    def do_PUT(self):
        self._forward()

    def do_DELETE(self):
        self._forward()

    def do_PATCH(self):
        self._forward()

    def do_HEAD(self):
        self._forward()

    def do_OPTIONS(self):
        self._forward()


def run():
    with socketserver.ThreadingTCPServer(('127.0.0.1', PORT), ProxyHandler) as httpd:
        print(f'Proxy listening on http://127.0.0.1:{PORT} -> {TARGET}')
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('Proxy stopping')


if __name__ == '__main__':
    run()
