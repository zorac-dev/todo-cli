# ~/.bashrc または ~/.zshrc に、このファイルの内容をコピーしてください。
# 次のパスを、ご自身の環境の todo-cli フォルダの絶対パスに変更してください。
TODO_CLI_DIR='/absolute/path/to/todo-cli'
TODO_CLI_SCRIPT="$TODO_CLI_DIR/todo.py"
TODO_CLI_PYTHON="$TODO_CLI_DIR/.venv/bin/python"
# 事前にtodo-cli内で uv venv --managed-python --python 3.14 .venv を実行します。
# データは実行場所にかかわらず todo-cli/.todo に保存されます。

todo() {
    if [ "$#" -eq 0 ]; then
        "$TODO_CLI_PYTHON" "$TODO_CLI_SCRIPT" --help
        return $?
    fi

    "$TODO_CLI_PYTHON" "$TODO_CLI_SCRIPT" "$@"
}
