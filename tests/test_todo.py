import contextlib
import errno
import io
import json
import re
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import todo


class TodoTests(unittest.TestCase):
    def setUp(self):
        # Keep test data inside the project, including all subprocess writes.
        self.temp = tempfile.TemporaryDirectory(prefix='.test-', dir=APP)
        self.root = Path(self.temp.name)
        self.data = self.root / '.todo'
        # Exercise defaults without ever touching the real application's data.
        self.script = self.root / 'todo.py'
        shutil.copyfile(APP / 'todo.py', self.script)

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args, input=None, code=0):
        result = subprocess.run([sys.executable, '-B', str(self.script), *args],
                                cwd=self.root, input=input, text=True,
                                encoding='utf-8', capture_output=True)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def state(self):
        return json.loads((self.data / 'tasks.json').read_text(encoding='utf-8'))

    def archive(self):
        return json.loads((self.data / 'archive.json').read_text(encoding='utf-8'))

    def local_cli(self, *args, code=0, answer='n'):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with patch('builtins.input', return_value=answer):
                result = todo.main(['--data-dir', str(self.data), *args])
        self.assertEqual(result, code, output.getvalue())
        return output.getvalue()

    def seed_batch(self, count=3):
        for number in range(count):
            self.local_cli('add', f'一括操作のタスク{number + 1}')

    def test_short_commands_lifecycle(self):
        self.cli('a', 'h:省略形のタスク')
        self.assertIn('省略形のタスク', self.cli('l', '--priority', 'high'))
        self.cli('e', '1', '--name', '編集済み')
        self.cli('c', '1')
        self.assertIn('編集済み', self.cli('l', '--completed'))
        self.cli('uc', '1')
        self.assertFalse(self.state()['tasks'][0]['completed'])
        self.cli('c', '1')
        self.cli('ar', '1', '-y')
        self.assertEqual(self.cli('ar', 'l'), self.cli('archive', 'list'))
        self.cli('r', '1')
        self.cli('d', '1', '-y')
        self.assertEqual(self.state()['tasks'], [])
        self.cli('u')
        self.assertEqual(self.state()['tasks'][0]['name'], '編集済み')
        self.cli('ar', 'l', '1', code=1)
        self.assertEqual(len(self.state()['tasks']), 1)

    def test_bulk_check_uncheck_and_undo(self):
        self.seed_batch()
        self.local_cli('check', '1')
        before = self.state()['tasks']
        self.local_cli('check', '1', '2', '2', '3')
        self.assertTrue(all(t['completed'] for t in self.state()['tasks']))
        self.assertEqual(self.state()['tasks'][0], before[0])
        self.local_cli('undo')
        self.assertEqual(self.state()['tasks'], before)
        self.local_cli('check', '1', '2', '3')
        before = self.state()['tasks']
        self.local_cli('uncheck', '1', '3')
        self.assertEqual([t['completed'] for t in self.state()['tasks']], [False, True, False])
        self.local_cli('undo')
        self.assertEqual(self.state()['tasks'], before)
        self.local_cli('uncheck', '1', '2', '3')
        unchanged = (self.data / 'tasks.json').read_bytes()
        self.local_cli('uncheck', '1', '2', '3')
        self.assertEqual((self.data / 'tasks.json').read_bytes(), unchanged)

    def test_bulk_delete_confirmation_duplicates_and_undo(self):
        self.seed_batch()
        before = self.state()['tasks']
        unchanged = (self.data / 'tasks.json').read_bytes()
        self.local_cli('delete', '1', '2')
        self.assertEqual((self.data / 'tasks.json').read_bytes(), unchanged)
        self.local_cli('delete', '1', '2', '1', answer='yes')
        self.assertEqual([t['id'] for t in self.state()['tasks']], [3])
        self.local_cli('undo')
        self.assertEqual(self.state()['tasks'], before)
        self.local_cli('delete', '1', '2', '-y')
        self.assertEqual([t['id'] for t in self.state()['tasks']], [3])

    def test_bulk_archive_restore_and_undo(self):
        self.seed_batch(4)
        self.local_cli('check', '1', '2', '3')
        before = self.state()['tasks']
        self.local_cli('archive', '1', '2')
        self.assertEqual(self.state()['tasks'], before)
        self.assertEqual(self.archive()['tasks'], [])
        self.local_cli('archive', '1', '2', '2', answer='y')
        self.assertEqual([t['id'] for t in self.state()['tasks']], [3, 4])
        self.assertEqual([t['id'] for t in self.archive()['tasks']], [1, 2])
        self.local_cli('undo')
        self.assertEqual(self.state()['tasks'], before)
        self.assertEqual(self.archive()['tasks'], [])
        self.local_cli('archive', '1', '2', '-y')
        active, archived = self.state()['tasks'], self.archive()['tasks']
        self.local_cli('restore', '1', '2', '2')
        self.assertEqual(self.archive()['tasks'], [])
        restored = [t for t in self.state()['tasks'] if t['id'] in (1, 2)]
        self.assertEqual(len(restored), 2)
        self.assertTrue(all(t['completed'] for t in restored))
        self.local_cli('undo')
        self.assertEqual(self.state()['tasks'], active)
        self.assertEqual(self.archive()['tasks'], archived)

    def test_bulk_invalid_selection_never_partially_changes_data(self):
        self.seed_batch()
        self.local_cli('check', '1', '2')

        def files():
            return [(self.data / name).read_bytes() for name in ('tasks.json', 'archive.json')]

        before = files()
        for args in [('check', '3', '999'), ('uncheck', '1', '999'),
                     ('delete', '1', '999', '-y'), ('archive', '1', '999', '-y'),
                     ('archive', '1', '3', '-y'), ('archive', 'list', '1')]:
            with self.subTest(args=args):
                self.local_cli(*args, code=1)
                self.assertEqual(files(), before)
        self.local_cli('archive', '1', '2', '-y')
        before = files()
        self.local_cli('restore', '1', '999', code=1)
        self.assertEqual(files(), before)

    def test_bulk_invalid_id_arguments(self):
        self.seed_batch()
        before = (self.data / 'tasks.json').read_bytes()
        for args in [('check', '1', '0'), ('uncheck', '1', '-2'),
                     ('delete', '1', 'abc', '-y'), ('restore', '1', '0'),
                     ('archive', '1', 'abc'), ('archive', '0'), ('check',)]:
            with self.subTest(args=args):
                self.cli(*args, code=2)
                self.assertEqual((self.data / 'tasks.json').read_bytes(), before)

    def test_lifecycle_japanese_edit_check_archive_restore_delete(self):
        self.cli('add', '資料を 確認する', '--priority', 'high')
        task = self.state()['tasks'][0]
        self.assertEqual(task['name'], '資料を 確認する')
        self.assertTrue(task['created_at'])
        self.assertIn('資料を 確認する', (self.data / 'tasks.json').read_text(encoding='utf-8'))
        self.cli('edit', '1', '--name', '資料を送る', '--priority', 'low')
        self.assertEqual(self.state()['tasks'][0]['priority'], 'low')
        self.cli('check', '1')
        self.assertTrue(self.state()['tasks'][0]['completed_at'])
        self.assertNotIn('資料を送る', self.cli('list'))
        self.assertIn('資料を送る', self.cli('list', '--all'))
        self.assertIn('[x]', self.cli('list', '--completed'))
        self.cli('uncheck', '1')
        self.assertIsNone(self.state()['tasks'][0]['completed_at'])
        self.cli('check', '1')
        self.cli('archive', '-y')
        self.assertEqual(self.state()['tasks'], [])
        self.assertEqual(len(self.archive()['tasks']), 1)
        self.assertIn('資料を送る', self.cli('archive', 'list'))
        self.cli('restore', '1')
        self.assertEqual(self.archive()['tasks'], [])
        self.assertTrue(self.state()['tasks'][0]['completed'])
        self.cli('delete', '1', '-y')
        self.assertEqual(self.state()['tasks'], [])
        self.cli('undo')
        self.assertEqual(self.state()['tasks'][0]['id'], 1)

    def test_filters_and_sorting(self):
        self.cli('add', '低優先度', '--priority', 'low')
        self.cli('add', 'APIの確認')
        self.cli('add', '高優先度', '--priority', 'high')
        output = self.cli('list')
        self.assertLess(output.index('高優先度'), output.index('APIの確認'))
        self.assertLess(output.index('APIの確認'), output.index('低優先度'))
        self.assertIn('APIの確認', self.cli('list', '--search', 'api', '--priority', 'middle'))
        self.assertNotIn('高優先度', self.cli('list', '--priority', 'middle'))
        self.cli('check', '2')
        self.assertNotIn('APIの確認', self.cli('list', '--search', 'api'))
        self.assertIn('APIの確認', self.cli('list', '--all', '--search', 'api'))
        self.assertNotIn('低優先度', self.cli('list', '--completed'))

    @unittest.skipUnless(hasattr(time, 'tzset'), 'requires timezone switching via tzset')
    def test_completion_dates_use_local_timezone_without_changing_storage(self):
        self.local_cli('add', '早朝に完了')
        self.local_cli('add', '未完了のまま')
        self.local_cli('check', '1')
        state = self.state()
        state['tasks'][0]['completed_at'] = '2026-11-10T23:00:00+00:00'
        path = self.data / 'tasks.json'
        path.write_text(json.dumps(state), encoding='utf-8')
        before = path.read_bytes()
        try:
            for zone, expected in [('JST-9', '2026-11-11'),
                                   ('UTC0', '2026-11-10'),
                                   ('EST5', '2026-11-10')]:
                with self.subTest(zone=zone), patch.dict(todo.os.environ, {'TZ': zone}):
                    time.tzset()
                    output = self.local_cli('list', '--all')
                    self.assertIn(expected, output)
                    pending_row = next(line for line in output.splitlines() if '未完了のまま' in line)
                    self.assertTrue(pending_row.endswith('-'))
                    self.assertEqual(path.read_bytes(), before)
            self.local_cli('archive', '1', '-y')
            archive_before = (self.data / 'archive.json').read_bytes()
            with patch.dict(todo.os.environ, {'TZ': 'JST-9'}):
                time.tzset()
                self.assertIn('2026-11-11', self.local_cli('archive', 'list'))
            self.assertEqual((self.data / 'archive.json').read_bytes(), archive_before)
            self.assertEqual(self.archive()['tasks'][0]['completed_at'],
                             '2026-11-10T23:00:00+00:00')
        finally:
            time.tzset()

    def test_priority_prefixes_and_explicit_override(self):
        self.cli('add', 'h:急ぎの 作業')
        self.cli('add', 'm:通常の作業')
        self.cli('add', 'l: 後で行う作業 ')
        self.cli('add', 'H:大文字も利用可能')
        self.cli('add', 'h:明示指定を優先', '--priority', 'middle')
        self.assertEqual([(t['name'], t['priority']) for t in self.state()['tasks']], [
            ('急ぎの 作業', 'high'), ('通常の作業', 'middle'),
            ('後で行う作業', 'low'), ('大文字も利用可能', 'high'),
            ('明示指定を優先', 'middle')])

    def test_empty_prefixed_names_are_rejected_without_writes(self):
        for name in ('h:', 'm:   ', 'l:　'):
            self.cli('add', name, code=2)
        self.assertFalse(self.data.exists())
        self.cli('add', '残すタスク')
        before = (self.data / 'tasks.json').read_bytes()
        self.cli('add', 'h:', '--priority', 'low', code=2)
        self.assertEqual((self.data / 'tasks.json').read_bytes(), before)

    def test_colons_in_normal_names_and_edits_are_preserved(self):
        for name in ('https://example.com', '打合せ: 準備', 'x:通常の名前'):
            self.cli('add', name)
        self.assertEqual([t['name'] for t in self.state()['tasks']],
                         ['https://example.com', '打合せ: 準備', 'x:通常の名前'])
        self.assertTrue(all(t['priority'] == 'middle' for t in self.state()['tasks']))
        self.cli('edit', '1', '--name', 'h:編集では通常の名前')
        self.assertEqual(self.state()['tasks'][0]['name'], 'h:編集では通常の名前')
        self.assertEqual(self.state()['tasks'][0]['priority'], 'middle')

    def test_muted_colors_for_terminal_and_archive(self):
        self.cli('add', 'h:高優先度')
        self.cli('add', 'm:通常優先度')
        self.cli('add', 'l:低優先度')

        def render(command, tty=True, term='xterm-256color', no_color=''):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), patch.object(output, 'isatty', return_value=tty):
                with patch.dict(todo.os.environ, {'TERM': term, 'NO_COLOR': no_color}):
                    self.assertEqual(todo.main(['--data-dir', str(self.data), *command]), 0)
            return output.getvalue()

        for command in (['list'], ['archive', 'list']):
            with self.subTest(command=command):
                colored = render(command)
                plain = render(command, tty=False)
                self.assertIn('\033[38;5;174m', colored)
                self.assertIn('\033[38;5;110m', colored)
                self.assertNotIn('\033[', plain)
                self.assertEqual(re.sub(r'\x1b\[[0-9;]*m', '', colored), plain)
                low_row = next(line for line in colored.splitlines() if '低優先度' in line)
                self.assertNotIn('\033[', low_row)
                self.assertEqual(render(command, no_color='1'), plain)
                self.assertEqual(render(command, term='dumb'), plain)
            if command == ['list']:
                for ident in ('1', '2', '3'):
                    self.cli('check', ident)
                self.cli('archive', '-y')

    def test_confirmation_cancel_and_accept(self):
        self.cli('add', '残すタスク')
        self.cli('delete', '1', input='n\n')
        self.cli('delete', '1', input='')
        self.assertEqual(len(self.state()['tasks']), 1)
        self.cli('check', '1')
        self.cli('archive', input='\n')
        self.assertEqual(self.archive()['tasks'], [])
        self.cli('archive', input='yes\n')
        self.assertEqual(len(self.archive()['tasks']), 1)
        self.cli('restore', '1')
        self.cli('delete', '1', input='y\n')
        self.assertEqual(self.state()['tasks'], [])

    def test_undo_archive_and_monotonic_ids(self):
        self.cli('add', '最初')
        self.cli('check', '1')
        self.cli('archive', '-y')
        self.cli('undo')
        self.assertEqual(len(self.state()['tasks']), 1)
        self.assertEqual(self.archive()['tasks'], [])
        self.cli('undo', code=1)
        self.cli('add', '取り消す追加')
        self.cli('undo')
        self.cli('add', '次の追加')
        self.assertEqual([t['id'] for t in self.state()['tasks']], [1, 3])
        self.cli('delete', '3', '-y')
        self.cli('add', '削除後')
        self.assertEqual(self.state()['tasks'][-1]['id'], 4)

    def test_noop_preserves_undo(self):
        self.cli('add', 'タスク')
        self.cli('uncheck', '1')
        self.cli('edit', '1', '--priority', 'middle')
        self.cli('archive', '-y')
        self.cli('list')
        self.cli('undo')
        self.assertEqual(self.state()['tasks'], [])

    def test_invalid_arguments_and_missing_id(self):
        for args in [('add', '  '), ('add', '改行\nタスク'), ('add', 'x', '--priority', 'urgent'),
                     ('check', '0'), ('delete', 'abc'), ('list', '--all', '--completed')]:
            self.cli(*args, code=2)
        self.cli('add', '正常')
        before = (self.data / 'tasks.json').read_bytes()
        self.cli('edit', '1', code=1)
        self.cli('check', '999', code=1)
        self.assertEqual((self.data / 'tasks.json').read_bytes(), before)

    def test_corrupt_data_is_preserved(self):
        self.cli('add', '正常')
        file = self.data / 'tasks.json'
        for content in ['{broken', json.dumps({'version': 99}),
                        json.dumps({**self.state(), 'next_id': 0})]:
            file.write_text(content, encoding='utf-8')
            self.cli('add', '書き込まない', code=1)
            self.assertEqual(file.read_text(encoding='utf-8'), content)

    def test_invalid_task_type_is_reported(self):
        self.cli('add', '正常')
        state = self.state()
        state['tasks'][0]['name'] = 42
        (self.data / 'tasks.json').write_text(json.dumps(state))
        output = self.cli('list', code=1)
        self.assertNotIn('Traceback', output)

    def test_missing_archive_is_not_silently_recreated(self):
        self.cli('add', '正常')
        (self.data / 'archive.json').unlink()
        self.cli('add', '書き込まない', code=1)
        self.assertEqual(len(self.state()['tasks']), 1)

    def test_recovery_after_interrupted_archive_write(self):
        self.cli('add', '保存途中')
        self.cli('add', '保存途中の2件目')
        self.cli('check', '1', '2')
        original = todo.Store.atomic_write

        def fail_archive(store, name, value):
            if name == 'archive.json':
                raise OSError('simulated interruption')
            return original(store, name, value)

        with patch.object(todo.Store, 'atomic_write', fail_archive):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = todo.main(['--data-dir', str(self.data), 'archive', '1', '2', '-y'])
        self.assertEqual(result, 1)
        self.assertTrue((self.data / 'pending.json').exists())
        output = self.cli('archive', 'list')
        self.assertIn('保存途中', output)
        self.assertEqual(self.state()['tasks'], [])
        self.assertEqual(len(self.archive()['tasks']), 2)
        self.assertFalse((self.data / 'pending.json').exists())

    def test_concurrent_additions_do_not_lose_data(self):
        processes = [subprocess.Popen([sys.executable, '-B', str(self.script),
                                      'add', f'並行タスク{i}'], cwd=self.root,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for i in range(16)]
        for process in processes:
            out, err = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, (out, err))
        tasks = self.state()['tasks']
        self.assertEqual(len(tasks), 16)
        self.assertEqual({t['id'] for t in tasks}, set(range(1, 17)))
        self.assertEqual(len({t['name'] for t in tasks}), 16)

    def test_data_dir_and_symlink_restrictions(self):
        self.cli('--data-dir', 'custom', 'add', '別の保存先')
        self.assertTrue((self.root / 'custom' / 'tasks.json').exists())
        # Parent directories are outside this copy of the application's boundary.
        self.cli('--data-dir', '..', 'list', code=1)
        self.cli('add', '保護')
        target = self.root / 'target.json'
        target.write_text('unchanged')
        (self.data / 'tasks.json').unlink()
        try:
            (self.data / 'tasks.json').symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest('Creating symlinks is not permitted on this system')
        self.cli('list', code=1)
        self.assertEqual(target.read_text(encoding='utf-8'), 'unchanged')

    def test_shared_storage_from_different_working_directories(self):
        first = self.root / 'first'
        second = self.root / 'second'
        first.mkdir()
        second.mkdir()

        def invoke(cwd, *args):
            result = subprocess.run([sys.executable, '-B', str(self.script), *args],
                                    cwd=cwd, text=True, encoding='utf-8', capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

        invoke(first, 'add', 'どこからでも共通')
        self.assertIn('どこからでも共通', invoke(second, 'list'))
        invoke(second, 'check', '1')
        self.assertIn('[x]', invoke(first, 'list', '--all'))
        invoke(first, 'archive', '-y')
        self.assertIn('どこからでも共通', invoke(second, 'archive', 'list'))
        self.assertFalse((first / '.todo').exists())
        self.assertFalse((second / '.todo').exists())
        invoke(second, '--data-dir', 'custom', 'add', '指定先も共通')
        self.assertIn('指定先も共通', invoke(first, '--data-dir', 'custom', 'list'))
        self.assertTrue((self.root / 'custom' / 'tasks.json').exists())
        self.assertFalse((second / 'custom').exists())

    @unittest.skipUnless(sys.platform != 'win32' and shutil.which('bash'),
                         'requires bash on macOS or Linux')
    def test_bash_function_from_another_directory(self):
        # Include spaces in both the script and interpreter paths.
        install = self.root / 'app with spaces'
        install.mkdir()
        shutil.copyfile(self.script, install / 'todo.py')
        python_link = install / 'python with spaces'
        python_link.symlink_to(sys.executable)
        caller = self.root / 'caller'
        caller.mkdir()
        script = r'''
set -e
source "$1"
TODO_CLI_SCRIPT="$2"
TODO_CLI_PYTHON="$3"
export PYTHONDONTWRITEBYTECODE=1
t a "h:日本語と 空白のタスク"
todo add "l:一括チェックの2件目"
todo list
t c 1 2
t l --all
todo edit 1 --priority low
todo --help
t --help
if t c 999; then
    exit 40
else
    test "$?" -eq 1
fi
'''
        result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script,
                                 'shell-test', str(APP / 'shell/todo.bash-zsh.sh'),
                                 str(install / 'todo.py'), str(python_link)],
                                cwd=caller, text=True, encoding='utf-8', capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('[x]', result.stdout)
        tasks = json.loads((install / '.todo/tasks.json').read_text(encoding='utf-8'))['tasks']
        self.assertEqual(tasks[0]['name'], '日本語と 空白のタスク')
        self.assertEqual(tasks[0]['priority'], 'low')
        self.assertTrue(tasks[0]['completed'])
        self.assertTrue(tasks[1]['completed'])
        self.assertFalse((caller / '.todo').exists())

    def test_empty_list_and_help(self):
        self.assertIn('0件', self.cli('list'))
        self.assertIn('0件', self.cli('archive', 'list'))
        self.assertIn('undo', self.cli('--help'))
        self.cli('undo', code=1)

    def test_windows_lock_retries_and_releases_on_error(self):
        backend = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=0, locking=Mock())
        offsets = []

        def locking(fd, operation, length):
            offsets.append(todo.os.lseek(fd, 0, todo.os.SEEK_CUR))
            if len(offsets) == 1:
                raise OSError(errno.EACCES, 'another process holds the lock')

        backend.locking.side_effect = locking
        lock_path = self.root / '.lock'
        with patch.object(todo, 'WINDOWS', True), patch.object(todo, 'msvcrt', backend):
            with patch.object(todo.time, 'sleep') as sleep:
                with self.assertRaisesRegex(RuntimeError, 'operation failed'):
                    with todo.exclusive_lock(lock_path):
                        raise RuntimeError('operation failed')
                sleep.assert_called_once_with(0.05)
        self.assertEqual(offsets, [0, 0, 0])
        self.assertEqual([c.args[1:] for c in backend.locking.call_args_list],
                         [(1, 1), (1, 1), (0, 1)])
        self.assertEqual(lock_path.read_bytes(), b'\0')

    def test_windows_lock_fatal_error_does_not_retry(self):
        backend = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=0,
                                  locking=Mock(side_effect=OSError(errno.EBADF, 'bad descriptor')))
        with patch.object(todo, 'WINDOWS', True), patch.object(todo, 'msvcrt', backend):
            with patch.object(todo.time, 'sleep') as sleep:
                with self.assertRaises(OSError) as caught:
                    with todo.exclusive_lock(self.root / '.lock'):
                        self.fail('Must not enter the protected operation')
                self.assertEqual(caught.exception.errno, errno.EBADF)
                sleep.assert_not_called()
                self.assertEqual(backend.locking.call_count, 1)

    def test_windows_storage_flushes_files_without_directory_fsync(self):
        backend = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=0, locking=Mock())
        with patch.object(todo, 'WINDOWS', True), patch.object(todo, 'msvcrt', backend):
            with patch.object(todo.os, 'fsync', wraps=todo.os.fsync) as fsync:
                self.local_cli('add', 'h:Windowsのタスク')
                # Journal, active file and archive file; directories cannot be fsynced here.
                self.assertEqual(fsync.call_count, 3)
            self.local_cli('check', '1')
            self.local_cli('archive', '1', '-y')
            self.assertEqual(self.state()['tasks'], [])
            self.assertEqual(self.archive()['tasks'][0]['name'], 'Windowsのタスク')
            self.local_cli('undo')
            self.assertTrue(self.state()['tasks'][0]['completed'])

    @unittest.skipUnless(shutil.which('pwsh') or shutil.which('powershell'),
                         'PowerShell is not installed')
    def test_powershell_uses_platform_venv_from_another_directory(self):
        install = self.root / 'PowerShell app with spaces'
        install.mkdir()
        shutil.copyfile(self.script, install / 'todo.py')
        subprocess.run([sys.executable, '-B', '-m', 'venv', '--without-pip', str(install / '.venv')],
                       check=True, capture_output=True, timeout=60)
        profile = self.root / 'todo-profile.ps1'
        template = (APP / 'shell/todo.powershell.ps1').read_text(encoding='utf-8')
        profile.write_text(template.replace('/absolute/path/to/todo-cli',
                                            install.as_posix().replace("'", "''")),
                           encoding='utf-8-sig')
        runner = self.root / 'run.ps1'
        runner.write_text('''param([string]$TodoProfile)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
. $TodoProfile
t a 'h:日本語と 空白のタスク'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
todo add 'l:2件目'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
t c 1 2
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
t l --all
exit $LASTEXITCODE
''', encoding='utf-8-sig')
        caller = self.root / 'caller'
        caller.mkdir()
        result = subprocess.run([shutil.which('pwsh') or shutil.which('powershell'),
                                 '-NoProfile', '-NonInteractive', '-File', str(runner), str(profile)],
                                cwd=caller, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        tasks = json.loads((install / '.todo/tasks.json').read_text(encoding='utf-8'))['tasks']
        self.assertEqual(tasks[0]['name'], '日本語と 空白のタスク')
        self.assertTrue(all(t['completed'] for t in tasks))
        self.assertFalse((caller / '.todo').exists())


if __name__ == '__main__':
    unittest.main()
