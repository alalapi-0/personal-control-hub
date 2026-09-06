"""Temporary, explicit Figma capture of a sanitized static DOM; no product API writes."""
import argparse
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from fixture_server import load_routes, handler_for

parser=argparse.ArgumentParser()
parser.add_argument('--source-dir',type=Path,required=True)
args=parser.parse_args()
routes,_=load_routes(args.source_dir)
path=args.source_dir/'capture.html'
assert path.is_file() and not path.is_symlink()
html=path.read_bytes()
assert html.count(b'<script')==1 and b'https://mcp.figma.com/mcp/html-to-design/capture.js' in html
routes['/capture.html']=('text/html; charset=utf-8',html)
Base=handler_for(routes)
class CaptureHandler(Base):
    def send_header(self,key,value):
        if key.lower()=='content-security-policy':
            value=value.replace("script-src 'self' 'unsafe-inline'", "script-src 'self' 'unsafe-inline' https://mcp.figma.com")
            value=value.replace("connect-src 'self'", "connect-src 'self' https://mcp.figma.com")
        super().send_header(key,value)
server=ThreadingHTTPServer(('127.0.0.1',0),CaptureHandler)
print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}/capture.html','purpose':'authorized sanitized Figma capture only'}),flush=True)
try:server.serve_forever()
except KeyboardInterrupt:pass
finally:server.server_close()
