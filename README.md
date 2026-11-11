# TODO CLI

Windows・macOS・Ubuntuで使える、日本語対応のTODO管理ツールです。Python 3.11以降が必要です。

## 準備

uvをインストール済みの場合、`todo-cli` フォルダで実行します。

```sh
uv python install 3.14
uv venv --managed-python --python 3.14 .venv
```

macOS・Ubuntuでの起動：

```sh
.venv/bin/python todo.py --help
```

Windows（PowerShell）での起動：

```powershell
.\.venv\Scripts\python.exe todo.py --help
```

## `todo` コマンドの設定

次のひな形を設定ファイルへコピーし、先頭のパスを実際の `todo-cli` フォルダの絶対パスに変更してください。

| シェル | ひな形 | コピー先 | 再読み込み |
| --- | --- | --- | --- |
| bash | [todo.bash-zsh.sh](shell/todo.bash-zsh.sh) | `~/.bashrc` | `source ~/.bashrc` |
| zsh | [todo.bash-zsh.sh](shell/todo.bash-zsh.sh) | `~/.zshrc` | `source ~/.zshrc` |
| PowerShell | [todo.powershell.ps1](shell/todo.powershell.ps1) | `$PROFILE` | `. $PROFILE` |

設定後は、どのフォルダからでも以下のコマンドを使えます。

## 基本操作

| 操作 | コマンド例 |
| --- | --- |
| 追加（優先度：高） | `todo add "h:見積書を送付する"` |
| 追加（優先度：中） | `todo add "m:資料を確認する"` |
| 追加（優先度：低） | `todo add "l:読みたい本を整理する"` |
| 未完了の一覧 | `todo list` |
| 完了を含む一覧 | `todo list --all` |
| 完了のみの一覧 | `todo list --completed` |
| 名前で検索 | `todo list --search "見積"` |
| 優先度で絞り込み | `todo list --priority high` |
| 名前を変更 | `todo edit 1 --name "見積書を確認して送付する"` |
| 優先度を変更 | `todo edit 1 --priority low` |
| 完了にする | `todo check 1` |
| 未完了に戻す | `todo uncheck 1` |
| 削除 | `todo delete 1` |
| 完了済み全件をアーカイブ | `todo archive` |
| アーカイブの一覧 | `todo archive list` |
| アーカイブから戻す | `todo restore 1` |
| 直前の変更を取り消す | `todo undo` |
| ヘルプ | `todo --help` |

優先度の省略時は `middle` です。`h:`・`m:`・`l:` は追加時に名前から取り除かれます。`--priority high` などを併用した場合はオプションを優先します。

一覧では、`high` はくすんだ赤、`middle` はくすんだ青、`low` は通常の文字色で表示します。

削除・アーカイブは `y` で確定、Enterでキャンセルします。`-y` を付けると確認を省略できます。アーカイブから戻したタスクは完了状態のままです。

## 一括操作

IDを空白で区切って指定します。

```sh
todo check 1 3 5
todo uncheck 1 3
todo delete 1 3
todo archive 2 4
todo restore 2 4
```

アーカイブできるのは完了済みのタスクです。存在しないIDなどが含まれる場合は全件変更しません。`todo undo` で直前の一括操作全体を取り消せます。

## 保存先

データは常に `todo-cli/.todo` に保存されます。別の保存先を使う場合は、`todo-cli` 内のフォルダを指定してください。

```sh
todo --data-dir ./my-tasks list
```

WindowsとWSLで同じ一覧を使う場合は、Windows側の `todo-cli` を共有します。WSL側のbash・zsh用ひな形の先頭を次のように設定してください。パスは自分の環境に合わせて変更します。

```sh
TODO_CLI_DIR='/mnt/c/Users/your-name/Projects/todo-cli'
TODO_CLI_SCRIPT='C:\Users\your-name\Projects\todo-cli\todo.py'
TODO_CLI_PYTHON="$TODO_CLI_DIR/.venv/Scripts/python.exe"
```

Windows側で作成した仮想環境のPythonを呼び出すため、WSL用の仮想環境は不要です。
