import json
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout

from pypdf import PdfReader
from maic import Capture, ExportError, Cancelled, BrowserSession, allowed_url, download_pdf, merge_pdfs, safe_name, select_sections, choose_course, main, section_paths, atomic_text
from tests.support import make_pdf


def slide(number, text='原始讲稿', segments=None):
    return {'page_id': f'p{number}', 'page_type': 'slide', 'content': {
        'slide': {'url': f'https://file.maic.chat/data/sample_{number}.pdf'},
        'script': {'script': text, 'segments': segments or []}}}


class CaptureTests(unittest.TestCase):
    def test_initial_order_question_and_segment_priority(self):
        capture = Capture('module')
        capture.receive(json.dumps({'event': 'EVENT_RES_INIT_DATA', 'data': {'rows': [
            slide(1, segments=[{'text': '分段一'}, {'text': '分段二'}]),
            {'page_id': 'q1', 'page_type': 'question', 'content': {'question': '题目'}},
            slide(3)]}}))
        self.assertTrue(capture.initialized)
        result = capture.slides()
        self.assertEqual([s.position for s in result], [1, 3])
        self.assertEqual(result[0].rendered, '分段一分段二')
        self.assertEqual(result[0].script, '原始讲稿')

    def test_unrelated_and_malformed_frames_are_ignored(self):
        capture = Capture('module')
        for frame in ('invalid', '[]', '{}', json.dumps({'event': 'EVENT_RES_INIT_DATA',
                       'data': {'module_id': 'other', 'rows': [slide(1)]}})):
            capture.receive(frame)
        self.assertFalse(capture.initialized)
        self.assertEqual(capture.slides(), [])

    def test_replayed_page_is_updated_without_duplication(self):
        capture = Capture('module')
        capture.receive(json.dumps({'event': 'EVENT_RES_INIT_DATA', 'data': {'rows': [slide(1)]}}))
        capture.receive(json.dumps({'event': 'EVENT_MESSAGE_SCRIPT', 'data': slide(1, '更新')}))
        self.assertEqual(len(capture.slides()), 1)
        self.assertEqual(capture.slides()[0].script, '更新')

    def test_resource_hosts_and_windows_filenames(self):
        self.assertTrue(allowed_url('https://file.maic.chat/a.pdf'))
        for url in ('http://file.maic.chat/a.pdf', 'https://file.maic.chat.evil.test/a.pdf',
                    'https://127.0.0.1/a.pdf', 'https://user:pass@file.maic.chat/a.pdf'):
            self.assertFalse(allowed_url(url))
        self.assertEqual(safe_name('CON'), '_CON')
        self.assertNotIn('/', safe_name('../a/b'))


class ExportTests(unittest.TestCase):
    def test_merge_preserves_page_order_and_text(self):
        with tempfile.TemporaryDirectory() as temp:
            sources = [Path(temp)/f'source-{i}.pdf' for i in (1,2,3)]
            for i, path in enumerate(sources, 1):
                make_pdf(path, f'Synthetic page {i}')
            target = Path(temp)/'merged.pdf'
            self.assertEqual(merge_pdfs(sources, target), 3)
            actual = PdfReader(target)
            self.assertEqual([p.extract_text() for p in actual.pages],
                             [PdfReader(p).pages[0].extract_text() for p in sources])

    def test_html_error_cannot_be_merged(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp)/'bad.pdf', Path(temp)/'merged.pdf'
            source.write_text('<html>login</html>')
            with self.assertRaises(ExportError):
                merge_pdfs([source], target)
            self.assertFalse(target.exists())

    def test_cancelled_download_creates_no_final_file(self):
        cancel = threading.Event()
        cancel.set()
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/'page.pdf'
            with self.assertRaises(ExportError):
                download_pdf('https://file.maic.chat/a.pdf', target, cancel)
            self.assertFalse(target.exists())

    def test_partial_export_only_keeps_section_text_and_reports_failure_in_terminal(self):
        session = BrowserSession(lambda _: None, threading.Event())
        modules = [dict(id='a',name='第一节',chapter_name='第一讲',chapter_id='c',chapter_index=1,module_index=1),
                   dict(id='b',name='第二节',chapter_name='第一讲',chapter_id='c',chapter_index=1,module_index=2)]
        cap = Capture('a')
        cap.receive(json.dumps({'event':'EVENT_RES_INIT_DATA','data':{'rows':[slide(1)]}}))
        terminal = io.StringIO()
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(terminal), patch.object(session, 'collect',
                side_effect=[cap.slides(), ExportError('当前课堂仅提供部分页面')]):
            folder, complete = session.export({'id':'course','course_name':'测试课程'}, modules, Path(temp), True, False)
            self.assertFalse(complete)
            self.assertEqual(folder, Path(temp)/'测试课程')
            self.assertEqual({p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()},
                             {'01-第一讲/01-01-第一节.txt'})
            self.assertIn('当前课堂仅提供部分页面', terminal.getvalue())
            self.assertIn('1 节成功，1 节失败', terminal.getvalue())

    def test_original_numbering_and_padding(self):
        module = dict(chapter_index=2,module_index=7,chapter_name='大模型基础',name='神经网络')
        text, pdf = section_paths(Path('out'), module)
        self.assertEqual(text, Path('out/02-大模型基础/02-07-神经网络.txt'))
        self.assertEqual(pdf, text.with_suffix('.pdf'))
        # Widths come from the entire course, not the selected subset.
        module.update(chapter_width=3, module_width=3)
        self.assertEqual(section_paths(Path('out'), module)[0].name, '002-007-神经网络.txt')

    def test_pdf_failure_or_cancellation_preserves_previous_file_and_cleans_downloads(self):
        session = BrowserSession(lambda _: None, threading.Event())
        module = dict(id='a',name='节',chapter_name='讲',chapter_id='c',chapter_index=1,module_index=2)
        cap = Capture('a')
        cap.receive(json.dumps({'event':'EVENT_RES_INIT_DATA','data':{'rows':[slide(1),slide(2)]}}))
        for error in (ExportError('下载失败'), Cancelled('停止')):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as temp:
                folder = Path(temp)/'课程'
                _, target = section_paths(folder,module)
                target.parent.mkdir(parents=True)
                make_pdf(target, 'Previous successful export')
                original = target.read_bytes()
                def failing_download(url, path, cancel):
                    make_pdf(path, 'Incomplete replacement')
                    if url.endswith('_2.pdf'):
                        raise error
                with patch.object(session,'collect',return_value=cap.slides()), patch('maic.download_pdf',side_effect=failing_download):
                    if isinstance(error,Cancelled):
                        with self.assertRaises(Cancelled):
                            session.export({'id':'c','course_name':'课程'},[module],Path(temp),False,True)
                    else:
                        self.assertFalse(session.export({'id':'c','course_name':'课程'},[module],Path(temp),False,True)[1])
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(set(folder.rglob('*')), {target.parent,target})

    def test_failed_text_replacement_keeps_previous_text_and_removes_part(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/'讲稿.txt'
            atomic_text(target,'旧讲稿')
            with patch.object(Path,'replace',side_effect=OSError('模拟替换失败')):
                with self.assertRaises(OSError):
                    atomic_text(target,'新讲稿')
            self.assertEqual(target.read_text(encoding='utf-8-sig'),'旧讲稿')
            self.assertEqual(list(Path(temp).iterdir()),[target])


class CommandTests(unittest.TestCase):
    def test_default_and_explicit_section_selection(self):
        modules = [dict(id=str(i), learned=6 if i != 2 else 1, total=6, locked=i == 3) for i in range(1,5)]
        self.assertEqual([m['id'] for m in select_sections(modules)], ['1', '4'])
        self.assertEqual([m['id'] for m in select_sections(modules, '4,1-2,1')], ['1','2','4'])
        for expression in ('0', '5', '3', '4-2', 'abc'):
            with self.assertRaises(ExportError):
                select_sections(modules, expression)

    def test_ambiguous_course_is_rejected(self):
        courses = [{'id':'a','course_name':'课程秋'}, {'id':'b','course_name':'课程春'}]
        self.assertEqual(choose_course(courses, 'a')['id'], 'a')
        with self.assertRaises(ExportError):
            choose_course(courses, '课程')

    def test_cli_pdf_only_and_failure_exit_status(self):
        with patch('maic.BrowserSession') as cls:
            session = cls.return_value
            session.courses.return_value = [{'id':'c','course_name':'测试课'}]
            session.modules.return_value = [dict(id='m',name='节',chapter_name='讲',learned=6,total=6)]
            session.export.return_value = (Path('out'), False)
            with patch('builtins.print'):
                code = main(['export','--course','c','--type','pdf','--chapters','1'])
            self.assertEqual(code, 1)
            self.assertFalse(session.export.call_args.kwargs['scripts'])
            self.assertTrue(session.export.call_args.kwargs['pdfs'])
            session.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
