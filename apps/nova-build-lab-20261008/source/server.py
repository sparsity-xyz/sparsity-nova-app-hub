"""Synthetic app. Only its fixed receipt route may call the private Capsule API."""
import json
import os
import re
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = json.loads(Path(__file__).with_name('version.json').read_text())


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, value):
        raw = json.dumps(value, sort_keys=True).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path in ('/health', '/version'):
            return self.reply(200, dict(VERSION, status='ok', source_commit=os.environ.get('SOURCE_COMMIT', 'local')))
        self.reply(404, {'error': 'route not found'})

    def do_POST(self):
        if self.path != '/receipt':
            return self.reply(404, {'error': 'route not found'})
        try:
            if self.headers.get('Transfer-Encoding'):
                raise ValueError('chunked requests are not supported')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 2048:
                raise ValueError('body must be 1..2048 bytes')
            self.connection.settimeout(5)
            body = json.loads(self.rfile.read(size))
            if set(body) != {'challenge', 'client_public_key'}:
                raise ValueError('expected challenge and client_public_key')
            if not re.fullmatch('[0-9a-f]{64}', body['challenge']):
                raise ValueError('challenge must be 32 bytes of lowercase hex')
            if not re.fullmatch('[0-9a-f]{100,512}', body['client_public_key']):
                raise ValueError('invalid client key encoding')
            # Encrypt only this fixed synthetic receipt. No general decrypt/sign proxy.
            receipt = dict(VERSION, challenge=body['challenge'], source_commit=os.environ.get('SOURCE_COMMIT', 'local'))
            request = urllib.request.Request('http://127.0.0.1:18000/v1/encryption/encrypt',
                data=json.dumps({'plaintext': json.dumps(receipt, sort_keys=True),
                                 'client_public_key': body['client_public_key']}).encode(),
                headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=10) as response:
                encrypted = json.load(response)
            self.reply(200, encrypted)
        except (ValueError, TypeError, KeyError):
            self.reply(400, {'error': 'invalid receipt request'})
        except Exception:
            self.reply(502, {'error': 'Capsule receipt encryption unavailable'})


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), Handler).serve_forever()
