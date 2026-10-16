"""MAIC export engine. Only observes incoming classroom data; never advances lessons."""
from __future__ import annotations

import json
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BASE = "https://www.maic.tsinghua.edu.cn"
ROOT = Path(__file__).resolve().parent


class ExportError(Exception):
    pass


class Cancelled(ExportError):
    pass


def safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip(' .')[:72]
    if not value:
        value = '未命名'
    if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', value):
        value = '_' + value
    return value


def clean_error(exc: Exception) -> str:
    text = str(exc).split('Call log:')[0]
    text = re.sub(r'(?i)(token[=:\s]+|Bearer\s+)[^\s&"\']+', r'\1[已隐藏]', text)
    return text[:500] or type(exc).__name__


def atomic_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.part')
    try:
        temp.write_text(text, encoding='utf-8-sig')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def section_paths(folder: Path, module):
    chapter_number = f"{module['chapter_index']:0{module.get('chapter_width', 2)}d}"
    section_number = f"{module['module_index']:0{module.get('module_width', 2)}d}"
    chapter = folder / f"{chapter_number}-{safe_name(module['chapter_name'])}"
    stem = f"{chapter_number}-{section_number}-{safe_name(module['name'])}"
    return chapter / (stem + '.txt'), chapter / (stem + '.pdf')


def pdf_pages(path: Path, expected: int | None = None) -> int:
    from pypdf import PdfReader
    with path.open('rb') as stream:
        if stream.read(5) != b'%PDF-':
            raise ExportError('下载内容不是有效 PDF')
        stream.seek(0)
        reader = PdfReader(stream)
        if reader.is_encrypted:
            raise ExportError('PDF 已加密，无法合并')
        count = len(reader.pages)
        if count < 1 or (expected is not None and count != expected):
            raise ExportError(f'PDF 页数不符：应为 {expected}，实际 {count}')
        return count


def merge_pdfs(paths: list[Path], target: Path) -> int:
    from pypdf import PdfWriter
    if not paths:
        raise ExportError('没有可合并的 PDF')
    total = sum(pdf_pages(path) for path in paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + '.part')
    writer = PdfWriter()
    try:
        for path in paths:
            writer.append(str(path), import_outline=False)
        writer.write(str(temp))
        pdf_pages(temp, total)
        temp.replace(target)
    finally:
        writer.close()
        temp.unlink(missing_ok=True)
    return total


def allowed_url(url: str) -> bool:
    p = urllib.parse.urlsplit(url)
    return p.scheme == 'https' and p.hostname in {
        'file.maic.chat', 'www.maic.tsinghua.edu.cn'
    } and p.username is None and p.password is None and p.port in (None, 443)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise ExportError('资源重定向到了未允许的地址')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_pdf(url: str, target: Path, cancel: threading.Event):
    if not allowed_url(url):
        raise ExportError('课件地址不是支持的 MAIC HTTPS 文件地址')
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + '.part')
    opener = urllib.request.build_opener(SafeRedirect())
    for attempt in range(3):
        if cancel.is_set():
            raise Cancelled('已停止')
        try:
            request = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 MAIC-Helper/1.0', 'Referer': BASE + '/'
            })
            with opener.open(request, timeout=30) as response, temp.open('wb') as out:
                size = 0
                while True:
                    if cancel.is_set():
                        raise Cancelled('已停止')
                    block = response.read(128 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > 128 * 1024 * 1024:
                        raise ExportError('单页 PDF 超过 128 MB，已停止下载')
                    out.write(block)
            pdf_pages(temp, 1)
            temp.replace(target)
            return
        except Cancelled:
            raise
        except (OSError, ValueError, urllib.error.URLError, ExportError) as exc:
            if attempt == 2:
                # Do not echo signed resource URLs or headers in errors.
                raise ExportError(f'PDF 下载或校验失败（{type(exc).__name__}）') from None
            if cancel.wait(attempt + 1):
                raise Cancelled('已停止')
        finally:
            temp.unlink(missing_ok=True)


@dataclass
class Slide:
    position: int
    page_id: str
    url: str
    script: str
    rendered: str


class Capture:
    """Parse only incoming initialization/page frames, never user messages or tokens."""
    def __init__(self, module_id: str):
        self.module_id = module_id
        self.rows: list[dict] = []
        self.initialized = False
        self.last_update = 0.0

    def receive(self, frame):
        try:
            message = json.loads(frame)
            event = message.get('event')
            data = message.get('data', {})
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict):
                return
            if data.get('module_id') and data['module_id'] != self.module_id:
                return
            if event == 'EVENT_RES_INIT_DATA' and isinstance(data.get('rows'), list):
                self.rows = [r for r in data['rows'] if isinstance(r, dict)]
                self.initialized = True
            elif event in ('EVENT_MESSAGE_SCRIPT', 'EVENT_MESSAGE_QUESTION', 'EVENT_MESSAGE_SEND_PBL'):
                if not isinstance(data.get('content'), dict):
                    return
                key = data.get('page_id')
                if key and any(r.get('page_id') == key for r in self.rows):
                    self.rows = [data if r.get('page_id') == key else r for r in self.rows]
                else:
                    self.rows.append(data)
            else:
                return
            self.last_update = time.monotonic()
        except (ValueError, TypeError, AttributeError):
            return

    def slides(self) -> list[Slide]:
        result = []
        seen = set()
        for position, row in enumerate(self.rows, 1):
            content = row.get('content') or {}
            if not isinstance(content, dict) or not isinstance(content.get('slide'), dict):
                continue
            if row.get('page_type') not in (None, 'slide'):
                continue
            key = str(row.get('page_id') or row.get('id') or f'position:{position}')
            if key in seen:
                continue
            seen.add(key)
            value = content.get('script') or {}
            if isinstance(value, str):
                value = {'script': value}
            if not isinstance(value, dict):
                value = {}
            raw = value.get('script') or ''
            if not isinstance(raw, str):
                raw = ''
            segments = value.get('segments') or []
            display = ''.join(str(s.get('text') or '') for s in segments if isinstance(s, dict))
            result.append(Slide(position, key, content['slide'].get('url') or '', raw, display or raw))
        return result


API_JS = """async ({path, data}) => {
  const token = localStorage.getItem('token');
  if (!token) return {error: '请先在浏览器中登录'};
  let user; try { user = JSON.parse(localStorage.getItem('user') || '{}'); } catch {}
  if (!user?.id) return {error: '用户信息未就绪，请完成登录后重试'};
  const headers = {'Content-Type':'application/json', 'Authorization':'Bearer '+token,
    'x-trace-id':crypto.randomUUID()};
  const daue = localStorage.getItem('X-Daue'); if (daue) headers['X-Daue'] = daue;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 45000);
  try {
    const response = await fetch('/api/'+path, {method:'POST',headers,
      body:JSON.stringify({user_id:user.id,...data}),signal:controller.signal});
    const next = response.headers.get('x-daue'); if(next) localStorage.setItem('X-Daue',next);
    if(response.status===401) return {error:'登录已过期，请重新登录'};
    if(!response.ok) return {error:'平台请求失败：HTTP '+response.status};
    let body = await response.json(); body = body.content || body;
    if(!body.is_success) return {error:body.message || '平台未返回成功结果'};
    return {data:body.data};
  } finally { clearTimeout(timer); }
}"""

DOM_JS = """() => Array.from(document.querySelectorAll('.markdown-wrapper')).map(n=>({
  label:n.parentElement.querySelector('[data-page]')?.textContent.trim() || '',
  text:n.innerText
})).filter(x=>/^P\\d+页$/.test(x.label))"""


class BrowserSession:
    def __init__(self, log, cancel: threading.Event):
        self.log, self.cancel = log, cancel
        self.context = self.page = self.playwright = None
        self.capture = None

    def check(self):
        if self.cancel.is_set():
            raise Cancelled('已停止；已保存的文件会保留')

    def open(self):
        if self.page and not self.page.is_closed():
            return
        if self.context or self.playwright:
            self.close()
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        for channel in ('msedge', 'chrome', None):
            try:
                self.context = self.playwright.chromium.launch_persistent_context(
                    str(ROOT / '.session'), channel=channel, headless=False,
                    viewport={'width': 1360, 'height': 900}, accept_downloads=False)
                break
            except Exception:
                continue
        if self.context is None:
            self.close()
            raise ExportError('未能打开浏览器。请安装 Microsoft Edge 或 Chrome，或运行：python -m playwright install chromium')
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_default_timeout(15000)
        self.page.on('websocket', self._socket)
        self.page.goto(BASE + '/', wait_until='domcontentloaded', timeout=60000)

    def _socket(self, socket):
        capture = self.capture
        if capture and '/conv/ws/' in socket.url:
            socket.on('framereceived', capture.receive)

    def wait_login(self):
        self.open()
        self.log('请在打开的浏览器中正常登录。登录完成后会自动读取已加入课程。')
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            self.check()
            if self.page.is_closed():
                raise ExportError('浏览器已关闭，请重新连接')
            if self.page.url.startswith(BASE + '/'):
                ready = self.page.evaluate("() => !!localStorage.getItem('token') && !!localStorage.getItem('user')")
                if ready:
                    return
            self.page.wait_for_timeout(500)
        raise ExportError('等待登录超时，请重新连接')

    def api(self, path, data):
        self.check()
        if not self.page or self.page.is_closed():
            raise ExportError('请先连接平台')
        if urllib.parse.urlsplit(self.page.url).netloc != 'www.maic.tsinghua.edu.cn':
            raise ExportError('请先完成登录并返回 MAIC 页面')
        result = self.page.evaluate(API_JS, {'path': path, 'data': data})
        if result.get('error'):
            raise ExportError(result['error'])
        return result.get('data')

    def courses(self):
        self.wait_login()
        result = []
        for current in range(1, 101):
            data = self.api('course/curriculum', {'current': current, 'pageSize': 50})
            rows = data.get('rows', [])
            result.extend(rows)
            if not rows or len(result) >= int(data.get('total', len(result))):
                return result
        raise ExportError('课程列表分页超过预期，请检查平台响应')

    def modules(self, course_id):
        data = self.api('course/module/list', {'course_id': course_id})
        if not isinstance(data, list):
            raise ExportError('课程目录格式发生变化')
        result = []
        chapter_width = max(2, len(str(len(data))))
        module_width = max(2, len(str(max((len(c.get('children') or []) for c in data), default=0))))
        for chapter_index, chapter in enumerate(data, 1):
            for module_index, module in enumerate(chapter.get('children') or [], 1):
                if module.get('type') != 'lecture':
                    continue
                result.append({**module, 'chapter_id': chapter['id'],
                    'chapter_name': chapter['name'], 'chapter_index': chapter_index,
                    'module_index': module_index, 'chapter_width': chapter_width,
                    'module_width': module_width})
        return result

    def collect(self, course_id, module, need_scripts=True):
        capture = Capture(module['id'])
        self.capture = capture
        route = '/classroom/' + '/'.join(urllib.parse.quote(str(x), safe='') for x in
                    (course_id, module['chapter_id'], module['id']))
        self.page.goto(BASE + route + '?from=courseList', wait_until='domcontentloaded', timeout=60000)
        deadline = time.monotonic() + 60
        while not capture.initialized and time.monotonic() < deadline:
            self.check()
            self.page.wait_for_timeout(250)
        if '/signin' in self.page.url:
            raise ExportError('登录已过期，请重新连接')
        if not capture.initialized:
            raise ExportError('未收到课堂初始化数据；请确认章节可访问后重试')
        # Never request next pages or mark a lesson as completed.
        expected = int(module.get('total') or 0)
        if expected and len(capture.rows) < expected:
            raise ExportError(f'当前课堂仅提供 {len(capture.rows)}/{expected} 页，未自动推进学习')
        dom = []
        if need_scripts:
            self.page.get_by_text('分页讲稿', exact=True).click(timeout=15000)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                self.check()
                dom = self.page.evaluate(DOM_JS)
                slides = capture.slides()
                if slides and len(dom) >= len(slides) and time.monotonic() - capture.last_update > 0.5:
                    break
                self.page.wait_for_timeout(250)
        slides = capture.slides()
        if not slides:
            raise ExportError('未提取到幻灯片，平台数据结构可能发生变化')
        if expected and len(capture.rows) != expected:
            raise ExportError(f'目录与课堂页数不一致：{expected} / {len(capture.rows)}，请检查后重试')
        dom_by_page = {int(re.search(r'\d+', r['label'])[0]): r['text'] for r in dom}
        for slide in slides:
            if slide.position in dom_by_page:
                slide.rendered = dom_by_page[slide.position]
        return slides

    def export(self, course, modules, output: Path, scripts: bool, pdfs: bool):
        from tqdm import tqdm
        self.check()
        if not modules or not (scripts or pdfs):
            raise ExportError('请选择章节和至少一种导出类型')
        folder = output / safe_name(course['course_name'])
        folder.mkdir(parents=True, exist_ok=True)
        succeeded = failed = 0
        self.log(f'保存位置：{folder}')
        # Selected subsets retain their original platform numbering and order.
        ordered = sorted(modules, key=lambda m: (m['chapter_index'], m['module_index']))
        with tqdm(total=len(ordered), desc='章节', unit='节', dynamic_ncols=True, position=0) as progress:
            for module in ordered:
                self.check()
                text_path, pdf_path = section_paths(folder, module)
                label = text_path.stem
                progress.set_postfix_str(label, refresh=True)
                errors = []
                try:
                    if module.get('locked'):
                        raise ExportError('章节已锁定，已跳过')
                    slides = self.collect(course['id'], module, need_scripts=scripts)
                    if scripts:
                        try:
                            empty = [s.position for s in slides if not s.rendered.strip()]
                            if empty:
                                raise ExportError(f'讲稿缺页：{empty}')
                            text = module['chapter_name'] + ' / ' + module['name'] + '\n\n'
                            text += '\n\n'.join(f'【P{s.position}】\n{s.rendered.strip()}' for s in slides)
                            atomic_text(text_path, text + '\n')
                        except Cancelled:
                            raise
                        except Exception as exc:
                            errors.append('讲稿：' + clean_error(exc))
                    if pdfs:
                        try:
                            pdf_path.parent.mkdir(parents=True, exist_ok=True)
                            # TemporaryDirectory cleans single-page downloads even on failure/interruption.
                            with tempfile.TemporaryDirectory(prefix='.maic-', dir=pdf_path.parent) as temp:
                                paths = []
                                with tqdm(total=len(slides), desc='PDF', unit='页', leave=False,
                                          dynamic_ncols=True, position=1) as pages:
                                    for number, slide in enumerate(slides, 1):
                                        self.check()
                                        target = Path(temp) / f'{number:03d}.pdf'
                                        download_pdf(slide.url, target, self.cancel)
                                        paths.append(target)
                                        pages.update(1)
                                self.check()
                                merge_pdfs(paths, pdf_path)
                        except Cancelled:
                            raise
                        except Exception as exc:
                            errors.append('PDF：' + clean_error(exc))
                except Cancelled:
                    raise
                except Exception as exc:
                    errors.append(clean_error(exc))
                finally:
                    self.capture = None
                if errors:
                    failed += 1
                    tqdm.write(f"{label}：{'；'.join(errors)}")
                else:
                    succeeded += 1
                progress.update(1)
        tqdm.write(f'完成：{succeeded} 节成功，{failed} 节失败。')
        return folder, failed == 0

    def close(self):
        if self.context:
            try:
                self.context.close()
            except Exception:
                pass
        if self.playwright:
            self.playwright.stop()
        self.context = self.page = self.playwright = None


def select_sections(modules, expression=None):
    """Use 1-based directory positions; omitted expression selects completed sections."""
    if expression is None:
        selected = [m for m in modules if not m.get('locked') and int(m.get('total') or 0) > 0
                    and int(m.get('learned') or 0) >= int(m.get('total') or 0)]
    elif expression.strip().lower() == 'all':
        selected = [m for m in modules if not m.get('locked')]
    else:
        positions = set()
        for piece in expression.split(','):
            match = re.fullmatch(r'\s*(\d+)(?:\s*-\s*(\d+))?\s*', piece)
            if not match:
                raise ExportError('章节格式应为 1,3-5，或 all')
            start, end = int(match[1]), int(match[2] or match[1])
            if not 1 <= start <= end <= len(modules):
                raise ExportError(f'章节序号超出范围：{piece}（共 {len(modules)} 节）')
            positions.update(range(start, end + 1))
        selected = [m for i, m in enumerate(modules, 1) if i in positions]
        if any(m.get('locked') for m in selected):
            raise ExportError('所选章节包含已锁定内容，请调整章节序号')
    if not selected:
        raise ExportError('没有可导出的已学完章节。可先用 chapters 查看目录，再用 --chapters 指定章节。')
    return selected


def choose_course(courses, query=None):
    if not courses:
        raise ExportError('没有找到已加入课程')
    if query:
        exact = [c for c in courses if c['id'] == query or c['course_name'] == query]
        matches = exact or [c for c in courses if query.casefold() in c['course_name'].casefold()]
        if len(matches) != 1:
            raise ExportError('课程不存在或名称匹配到多门课程，请用 courses 查看并指定完整名称或 ID')
        return matches[0]
    for i, course in enumerate(courses, 1):
        print(f"{i:>3}. {course['course_name']}")
    try:
        index = int(input('选择课程序号：').strip())
    except ValueError:
        raise ExportError('请输入有效的课程序号') from None
    if not 1 <= index <= len(courses):
        raise ExportError('课程序号超出范围')
    return courses[index - 1]


def print_sections(modules):
    for i, m in enumerate(modules, 1):
        learned, total = int(m.get('learned') or 0), int(m.get('total') or 0)
        state = '锁定' if m.get('locked') else ('已学完' if total > 0 and learned >= total else f'{learned}/{total}')
        print(f"{i:>3}. [{state}] {m['chapter_name']} / {m['name']}")


def main(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(description='MAIC：提取分页讲稿文本，下载并合并 PDF。省略命令可交互选择。')
    commands = parser.add_subparsers(dest='command')
    commands.add_parser('courses', help='列出已加入课程及 ID')
    chapters = commands.add_parser('chapters', help='列出指定课程的章节序号')
    chapters.add_argument('--course', required=True, help='课程名称、唯一名称片段或课程 ID')
    export = commands.add_parser('export', help='导出指定课程')
    export.add_argument('--course', required=True, help='课程名称、唯一名称片段或课程 ID')
    export.add_argument('--chapters', help='章节序号，例如 1,3-5；all 为所有可访问章节；默认仅已学完章节')
    export.add_argument('--type', choices=['text', 'pdf', 'both'], default='both', help='默认 both')
    export.add_argument('--output', type=Path, default=Path('exports'), help='输出目录，默认当前目录下 exports')
    args = parser.parse_args(argv)
    cancel = threading.Event()
    session = BrowserSession(lambda text: print(text, flush=True), cancel)
    try:
        courses = session.courses()
        if args.command == 'courses':
            for c in courses:
                print(f"{c['id']}  {c['course_name']}")
            return 0
        course = choose_course(courses, getattr(args, 'course', None))
        modules = session.modules(course['id'])
        print_sections(modules)
        if args.command == 'chapters':
            return 0
        if args.command is None:
            expression = input('导出哪些章节？回车选全部已学完；也可输入 1,3-5 或 all：').strip() or None
            kind = input('导出类型：text 讲稿 / pdf 课件 / both 两者（回车默认）：').strip().lower() or 'both'
            if kind not in ('text', 'pdf', 'both'):
                raise ExportError('导出类型应为 text、pdf 或 both')
            output = Path('exports')
        else:
            expression, kind, output = args.chapters, args.type, args.output
        selected = select_sections(modules, expression)
        folder, complete = session.export(course, selected, output.expanduser().resolve(),
                                          scripts=kind in ('text', 'both'), pdfs=kind in ('pdf', 'both'))
        print(('导出完成：' if complete else '部分导出失败，详情见上方提示。保存位置：') + str(folder))
        return 0 if complete else 1
    except (KeyboardInterrupt, EOFError):
        cancel.set()
        print('\n已中止，已保存的文件会保留。', file=sys.stderr)
        return 130
    except ModuleNotFoundError as exc:
        print(f'缺少依赖 {exc.name}。请在你自己的 Python 环境中执行：python -m pip install -r requirements.txt', file=sys.stderr)
        return 2
    except Exception as exc:
        print('错误：' + clean_error(exc), file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == '__main__':
    raise SystemExit(main())
