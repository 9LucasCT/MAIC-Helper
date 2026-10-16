"""Offline browser integration check: no MAIC requests or credentials."""
import base64
import hashlib
import json
import shutil
import struct
import threading
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from pypdf import PdfReader
import maic
from tests.support import make_pdf

transcripts = [f'This is synthetic lecture script {i}.' for i in range(1, 4)]
rows = [{'page_id':f'p{i}', 'page_type':'slide', 'content':{
    'slide':{'url':f'https://file.maic.chat/check/{i}.pdf'},
    'script':{'script':transcripts[i-1]}}} for i in range(1, 4)]
HTML = b'''<!doctype html><meta charset="utf-8"><button>\xe5\x88\x86\xe9\xa1\xb5\xe8\xae\xb2\xe7\xa8\xbf</button><main></main>
<script>let rows=[]; const ws=new WebSocket('ws://'+location.host+'/api/student/v8/conv/ws/inclass');
ws.onmessage=e=>{rows=JSON.parse(e.data).data.rows;};
document.querySelector('button').onclick=()=>{const main=document.querySelector('main');main.innerHTML='';
rows.forEach((r,i)=>{const box=document.createElement('section');
const text=document.createElement('div');text.className='markdown-wrapper';text.innerText=r.content.script.script;
const label=document.createElement('div');label.setAttribute('data-page','true');label.innerText='P'+(i+1)+'\\u9875';
box.append(text,label);main.append(box);});};</script>'''
stop = threading.Event()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.headers.get('Upgrade', '').lower() == 'websocket':
            key = self.headers['Sec-WebSocket-Key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
            accept = base64.b64encode(hashlib.sha1(key.encode()).digest()).decode()
            self.send_response(101)
            self.send_header('Upgrade','websocket')
            self.send_header('Connection','Upgrade')
            self.send_header('Sec-WebSocket-Accept',accept)
            self.end_headers()
            payload = json.dumps({'event':'EVENT_RES_INIT_DATA','data':{'rows':rows}}).encode()
            header = bytes([0x81,126]) + struct.pack('!H',len(payload))
            self.wfile.write(header + payload)
            self.wfile.flush()
            stop.wait(30)
        else:
            self.send_response(200)
            self.send_header('Content-Type','text/html;charset=utf-8')
            self.send_header('Content-Length',str(len(HTML)))
            self.end_headers()
            self.wfile.write(HTML)


def main():
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    fixtures = tempfile.TemporaryDirectory()
    sources = [Path(fixtures.name)/f'page-{i}.pdf' for i in range(1, 4)]
    for i, path in enumerate(sources, 1):
        make_pdf(path, f'Synthetic page {i}')
    def offline_download(url, target, cancel):
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(sources[int(url.rsplit('/',1)[1].split('.')[0])-1],target)
    session = maic.BrowserSession(print,threading.Event())
    try:
        with sync_playwright() as p:
            browser = None
            for channel in ('msedge','chrome',None):
                try:
                    browser = p.chromium.launch(channel=channel,headless=True)
                    break
                except Exception:
                    continue
            if browser is None:
                raise RuntimeError('No local browser available for integration check')
            try:
                session.page = browser.new_page()
                session.page.on('websocket',session._socket)
                module = dict(id='module',name='本地模拟章节',chapter_id='chapter',chapter_name='模拟讲',
                              chapter_index=1,module_index=1,total=3,learned=3)
                with patch.object(maic,'BASE',f'http://127.0.0.1:{server.server_port}'), patch.object(maic,'download_pdf',offline_download):
                    captured = session.collect('course', module, need_scripts=False)
                    assert len(captured) == 3
                    assert session.page.locator('.markdown-wrapper').count() == 0
                    with tempfile.TemporaryDirectory() as temp:
                        folder, complete = session.export({'id':'course','course_name':'本地流程验证'},[module],
                            Path(temp),True,True)
                        assert complete
                        text_path,pdf_path = maic.section_paths(folder,module)
                        assert len(PdfReader(pdf_path).pages) == 3
                        text = text_path.read_text(encoding='utf-8-sig')
                        assert all(script in text for script in transcripts)
                        assert {f for f in folder.rglob('*') if f.is_file()} == {text_path,pdf_path}
                        # Re-export replaces the same two files without creating a run directory.
                        repeated, complete = session.export({'id':'course','course_name':'本地流程验证'},[module],
                            Path(temp),True,True)
                        assert repeated == folder and complete
                        assert {f for f in folder.rglob('*') if f.is_file()} == {text_path,pdf_path}
                print('PASS: browser capture -> section TXT/PDF only -> repeat export replaces same paths')
            finally:
                browser.close()
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        fixtures.cleanup()


if __name__ == '__main__':
    main()
