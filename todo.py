#!/usr/bin/env python3
"""A UTF-8, file-backed TODO CLI for Python 3.11+, Windows, macOS and Linux."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unicodedata

WINDOWS = sys.platform == 'win32'
if WINDOWS:
    import msvcrt
    fcntl = None
else:
    import fcntl
    msvcrt = None

PRIORITIES = {'high': 0, 'middle': 1, 'low': 2}
PRIORITY_PREFIXES = {'h': 'high', 'm': 'middle', 'l': 'low'}
# Muted rose and blue from the standard 256-color palette.
PRIORITY_COLORS = {'high': '\033[38;5;174m', 'middle': '\033[38;5;110m'}


class TodoError(Exception):
    pass


@contextmanager
def exclusive_lock(path):
    with path.open('a+b') as lock:
        if not WINDOWS:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
            return

        # Windows locks a byte range. All instances use byte zero of the same file.
        if os.fstat(lock.fileno()).st_size == 0:
            lock.write(b'\0')
            lock.flush()
        while True:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                # Keep waiting even when another invocation is at a confirmation prompt.
                time.sleep(0.05)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def task_name(value):
    if not isinstance(value, str):
        raise argparse.ArgumentTypeError('タスク名は文字列で指定してください。')
    value = value.strip()
    if not value or any(unicodedata.category(c).startswith('C') for c in value):
        raise argparse.ArgumentTypeError('タスク名は空白だけにせず、改行や制御文字を含めないでください。')
    return value


def new_task(value):
    value = task_name(value)
    prefix, separator, name = value.partition(':')
    priority = PRIORITY_PREFIXES.get(prefix.lower())
    if separator and priority:
        return task_name(name), priority
    return value, None


def positive_id(value):
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('IDは正の整数で指定してください。')
    if result < 1:
        raise argparse.ArgumentTypeError('IDは正の整数で指定してください。')
    return result


def archive_target(value):
    return value if value == 'list' else positive_id(value)


def validate_tasks(items):
    if not isinstance(items, list):
        raise TodoError('タスクデータの形式が不正です。')
    ids = set()
    for task in items:
        if not isinstance(task, dict):
            raise TodoError('タスクデータの形式が不正です。')
        ident = task.get('id')
        if type(ident) is not int or ident < 1 or ident in ids:
            raise TodoError('タスクIDが不正、または重複しています。')
        ids.add(ident)
        try:
            task_name(task['name'])
            valid = (task['priority'] in PRIORITIES and type(task['completed']) is bool
                     and isinstance(task['created_at'], str)
                     and isinstance(task['updated_at'], str)
                     and (isinstance(task['completed_at'], str) if task['completed']
                          else task['completed_at'] is None))
        except (KeyError, TypeError, argparse.ArgumentTypeError):
            valid = False
        if not valid:
            raise TodoError('タスクデータの形式が不正です。')
    return ids


class Store:
    """Serialize access, then recover a journal before reading either data file."""

    def __init__(self, directory):
        # Anchor storage to the real script location, never the caller's cwd.
        root = Path(__file__).resolve().parent
        self.directory = (root / directory).resolve()
        if not self.directory.is_relative_to(root):
            raise TodoError('保存先はtodo.pyのあるフォルダ内を指定してください。')

    def path(self, name):
        path = self.directory / name
        if path.is_symlink():
            raise TodoError(f'データファイルにシンボリックリンクは使えません: {name}')
        return path

    def read(self, name, default):
        path = self.path(name)
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, UnicodeError) as exc:
            raise TodoError(f'{name} を読み込めません。元のファイルを確認してください。') from exc

    def atomic_write(self, name, value):
        destination = self.path(name)
        fd, temporary = tempfile.mkstemp(prefix='.write-', dir=self.directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            self.sync_directory()
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def sync_directory(self):
        # Windows cannot open a directory for fsync via os.open. File contents
        # are still flushed above; pending.json handles interrupted updates.
        if WINDOWS:
            return
        fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def validate(state, archive):
        if (not isinstance(state, dict) or state.get('version') != 1
                or not isinstance(archive, dict) or archive.get('version') != 1):
            raise TodoError('未対応または不正なデータ形式です。')
        active_ids = validate_tasks(state.get('tasks'))
        archived_ids = validate_tasks(archive.get('tasks'))
        if active_ids & archived_ids or any(not t['completed'] for t in archive['tasks']):
            raise TodoError('アーカイブのタスクが不正です。')
        next_id = state.get('next_id')
        if type(next_id) is not int or next_id <= max(active_ids | archived_ids, default=0):
            raise TodoError('次のタスクIDが不正です。')
        undo = state.get('undo')
        if undo is not None:
            if not isinstance(undo, dict):
                raise TodoError('取り消しデータが不正です。')
            a = validate_tasks(undo.get('tasks'))
            b = validate_tasks(undo.get('archive'))
            if a & b or max(a | b, default=0) >= next_id:
                raise TodoError('取り消しデータのIDが不正です。')
            if any(not t['completed'] for t in undo['archive']):
                raise TodoError('取り消しデータのアーカイブが不正です。')

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(self.path('.lock')):
            journal = self.read('pending.json', None)
            if self.path('pending.json').exists():
                if not isinstance(journal, dict) or 'state' not in journal or 'archive' not in journal:
                    raise TodoError('復旧用データの形式が不正です。')
                self.validate(journal['state'], journal['archive'])
                self.finish(journal)
            if self.path('tasks.json').exists() != self.path('archive.json').exists():
                raise TodoError('データファイルが片方だけ存在します。削除や移動がないか確認してください。')
            self.state = self.read('tasks.json', {'version': 1, 'next_id': 1, 'tasks': [], 'undo': None})
            self.archive = self.read('archive.json', {'version': 1, 'tasks': []})
            self.validate(self.state, self.archive)
            yield self

    def finish(self, journal):
        self.atomic_write('tasks.json', journal['state'])
        self.atomic_write('archive.json', journal['archive'])
        self.path('pending.json').unlink()
        self.sync_directory()

    def snapshot(self):
        # JSON round-trip makes the one-level undo independent of live dictionaries.
        self.state['undo'] = json.loads(json.dumps({
            'tasks': self.state['tasks'], 'archive': self.archive['tasks']}))

    def commit(self):
        self.validate(self.state, self.archive)
        journal = {'state': self.state, 'archive': self.archive}
        self.atomic_write('pending.json', journal)
        self.finish(journal)


def find_task(tasks, ident):
    for task in tasks:
        if task['id'] == ident:
            return task
    raise TodoError(f'ID {ident} のタスクが見つかりません。')


def find_tasks(tasks, ids):
    # Validate the entire selection before mutating anything; ignore repeated IDs.
    return [find_task(tasks, ident) for ident in dict.fromkeys(ids)]


def width(value):
    return sum(0 if unicodedata.combining(c) else
               2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in value)


def table(tasks):
    tasks = sorted(tasks, key=lambda t: (t['completed'], PRIORITIES[t['priority']], t['id']))
    rows = [['ID', '状態', '優先度', 'タスク名', '完了日']]
    rows += [[str(t['id']), '[x]' if t['completed'] else '[ ]', t['priority'],
              t['name'], datetime.fromisoformat(t['completed_at']).astimezone().date().isoformat()
              if t['completed_at'] else '-'] for t in tasks]
    sizes = [max(width(row[i]) for row in rows) for i in range(5)]
    color = (sys.stdout.isatty() and not os.environ.get('NO_COLOR')
             and os.environ.get('TERM') != 'dumb')
    for index, row in enumerate(rows):
        line = '  '.join(value + ' ' * (sizes[i] - width(value))
                         for i, value in enumerate(row)).rstrip()
        style = PRIORITY_COLORS.get(row[2]) if index and color else None
        if style:
            line = style + line + '\033[0m'
        print(line)
    print(f"表示: {len(tasks)}件 / 未完了: {sum(not t['completed'] for t in tasks)}件 / "
          f"完了: {sum(t['completed'] for t in tasks)}件")


def confirm(message, yes):
    if yes:
        return True
    try:
        return input(message + ' [y/N] ').strip().lower() in ('y', 'yes')
    except EOFError:
        return False


def parser():
    result = argparse.ArgumentParser(description='日本語対応のファイル管理TODO CLI')
    result.add_argument('--data-dir', default='.todo', help='todo.pyのあるフォルダを基準とする保存先 (既定: .todo)')
    commands = result.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list', help='タスク一覧を表示')
    visibility = listing.add_mutually_exclusive_group()
    visibility.add_argument('--all', action='store_true', help='完了済みを含める')
    visibility.add_argument('--completed', action='store_true', help='完了済みだけを表示')
    listing.add_argument('--priority', choices=PRIORITIES)
    listing.add_argument('--search', help='タスク名の部分一致検索')
    add = commands.add_parser('add', help='タスクを追加')
    add.add_argument('name', type=new_task, help='タスク名（h: / m: / l: で優先度を指定可能）')
    add.add_argument('--priority', choices=PRIORITIES,
                     help='名前の接頭辞より優先（指定がない場合はmiddle）')
    edit = commands.add_parser('edit', help='タスク名・優先度を編集')
    edit.add_argument('id', type=positive_id)
    edit.add_argument('--name', type=task_name)
    edit.add_argument('--priority', choices=PRIORITIES)
    for command, help_text in [('check', '完了にする'), ('uncheck', '未完了に戻す'),
                               ('delete', '削除する'), ('restore', 'アーカイブから完了状態で戻す')]:
        sub = commands.add_parser(command, help=help_text)
        sub.add_argument('ids', type=positive_id, nargs='+', metavar='ID',
                         help='対象ID（空白区切りで複数指定可能）')
        if command == 'delete':
            sub.add_argument('-y', '--yes', action='store_true', help='確認を省略')
    archive = commands.add_parser('archive', help='完了済みをアーカイブ / 一覧表示')
    archive.add_argument('targets', nargs='*', type=archive_target, metavar='ID|list',
                         help='対象IDを指定。省略で完了済み全件、listで一覧表示')
    archive.add_argument('-y', '--yes', action='store_true', help='確認を省略')
    commands.add_parser('undo', help='直前の変更を1回取り消す')
    return result


def run(args):
    if args.command == 'add':
        args.name, inferred_priority = args.name
        args.priority = args.priority or inferred_priority or 'middle'
    if args.command == 'edit' and args.name is None and args.priority is None:
        raise TodoError('--name または --priority を指定してください。')
    if args.command == 'archive' and 'list' in args.targets and args.targets != ['list']:
        raise TodoError('archive list とIDは同時に指定できません。')
    with Store(args.data_dir).locked() as store:
        tasks = store.state['tasks']
        archived = store.archive['tasks']
        command = args.command
        if command == 'list':
            selected = [t for t in tasks if args.all or t['completed'] == args.completed]
            if args.priority:
                selected = [t for t in selected if t['priority'] == args.priority]
            if args.search is not None:
                selected = [t for t in selected if args.search.casefold() in t['name'].casefold()]
            table(selected)
            return
        if command == 'archive' and args.targets == ['list']:
            table(archived)
            return
        if command == 'undo':
            previous = store.state['undo']
            if previous is None:
                raise TodoError('取り消せる操作がありません。')
            store.state['tasks'] = previous['tasks']
            store.archive['tasks'] = previous['archive']
            store.state['undo'] = None
            # Never reuse an ID, including IDs of additions that were undone.
            store.commit()
            print('直前の変更を取り消しました。')
            return
        task = None
        selected = []
        if command == 'edit':
            task = find_task(tasks, args.id)
        if command in ('check', 'uncheck', 'delete', 'restore'):
            selected = find_tasks(archived if command == 'restore' else tasks, args.ids)
        if command == 'archive':
            selected = (find_tasks(tasks, args.targets) if args.targets
                        else [t for t in tasks if t['completed']])
            if any(not t['completed'] for t in selected):
                raise TodoError('未完了のタスクはアーカイブできません。先にcheckしてください。')
        label = 'ID ' + ', '.join(str(t['id']) for t in selected)
        if command == 'delete' and not confirm(f'{label} を削除しますか？', args.yes):
            print('キャンセルしました。')
            return
        if command == 'archive':
            if not selected:
                print('アーカイブするタスクはありません。')
                return
            if not confirm(f'完了済みの{len(selected)}件（{label}）をアーカイブしますか？', args.yes):
                print('キャンセルしました。')
                return
        if command in ('check', 'uncheck'):
            selected = [t for t in selected if t['completed'] != (command == 'check')]
            if not selected:
                print('すでに指定の状態です。')
                return
            label = 'ID ' + ', '.join(str(t['id']) for t in selected)
        if command == 'edit' and all(value is None or task[key] == value for key, value in
                                     [('name', args.name), ('priority', args.priority)]):
            print('変更はありません。')
            return
        store.snapshot()
        now = timestamp()
        if command == 'add':
            ident = store.state['next_id']
            tasks.append({'id': ident, 'name': args.name, 'priority': args.priority,
                          'completed': False, 'created_at': now, 'updated_at': now,
                          'completed_at': None})
            store.state['next_id'] += 1
            message = f'追加しました: ID {ident} / {args.name}'
        elif command == 'edit':
            if args.name is not None:
                task['name'] = args.name
            if args.priority is not None:
                task['priority'] = args.priority
            task['updated_at'] = now
            message = f'ID {args.id} を編集しました。'
        elif command in ('check', 'uncheck'):
            for task in selected:
                task['completed'] = command == 'check'
                task['completed_at'] = now if task['completed'] else None
                task['updated_at'] = now
            message = f"{label} を{'完了' if command == 'check' else '未完了'}にしました。"
        elif command == 'delete':
            ids = {t['id'] for t in selected}
            store.state['tasks'] = [t for t in tasks if t['id'] not in ids]
            message = f'{label} を削除しました。'
        elif command == 'archive':
            ids = {t['id'] for t in selected}
            archived.extend(selected)
            store.state['tasks'] = [t for t in tasks if t['id'] not in ids]
            message = f'{len(selected)}件をアーカイブしました（{label}）。'
        elif command == 'restore':
            ids = {t['id'] for t in selected}
            store.archive['tasks'] = [t for t in archived if t['id'] not in ids]
            tasks.extend(selected)
            for task in selected:
                task['updated_at'] = now
            message = f'{label} をアーカイブから戻しました（完了状態）。'
        store.commit()
        print(message)


def main(argv=None):
    if sys.version_info < (3, 11):
        print('Python 3.11以降が必要です。', file=sys.stderr)
        return 1
    try:
        run(parser().parse_args(argv))
        return 0
    except (TodoError, OSError) as exc:
        print(f'エラー: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\n中断しました。次回起動時に保存処理を復旧します。', file=sys.stderr)
        return 130


if __name__ == '__main__':
    if WINDOWS:
        # Keep Japanese output readable when redirected or captured by another program.
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    sys.exit(main())
