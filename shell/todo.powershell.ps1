# PowerShell の $PROFILE に、このファイルの内容をコピーしてください。
# 対象: Windows / macOS / Linux の PowerShell（7以降を推奨）+ Python 3.11以降。
# 次のパスを、ご自身の環境の todo-cli フォルダの絶対パスに変更してください。
$script:TodoCliDir = '/absolute/path/to/todo-cli'
# Windowsの例: $script:TodoCliDir = 'C:\Users\your-name\Projects\todo-cli'
$script:TodoCliScript = Join-Path $script:TodoCliDir 'todo.py'
if ($env:OS -eq 'Windows_NT') {
    $script:TodoCliPython = Join-Path $script:TodoCliDir '.venv/Scripts/python.exe'
} else {
    $script:TodoCliPython = Join-Path $script:TodoCliDir '.venv/bin/python'
}
# 事前にtodo-cli内で uv venv --managed-python --python 3.14 .venv を実行します。
# データは実行場所にかかわらず todo-cli/.todo に保存されます。

function todo {
    # 高度な関数にせず、--priority などもそのままPythonへ渡します。
    if ($args.Count -eq 0) {
        & $script:TodoCliPython $script:TodoCliScript --help
        return
    }

    & $script:TodoCliPython $script:TodoCliScript @args
}

# todo の省略形
function t {
    todo @args
}
