<#
    KKAFIO context menu registration.
    Called by register_context_menu.bat - not meant to be run standalone,
    though it works fine if you do (just pass -Exe/-Gui yourself).
    Registry-only, HKCU, no Administrator required.
#>
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [Parameter(Mandatory = $true)][string]$Gui
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

# ---------------------------------------------------------------------------
# Task catalog. ArgsTemplate uses {P} as a stand-in for the path placeholder
# ("%1" for a single-file/folder click, "%V" for a folder-background click).
# ---------------------------------------------------------------------------
$FolderTasks = @(
    [ordered]@{ Id = 'FilterConvertKKS';       Cli = 'filter-convert-kks';       ArgsTemplate = '--input "{P}"' }
    [ordered]@{ Id = 'FilterDuplicateContents'; Cli = 'filter-duplicate-contents'; ArgsTemplate = '--input "{P}"' }
    [ordered]@{ Id = 'DownloadMissingMods';     Cli = 'download-missing-mods';     ArgsTemplate = '--chara-dir "{P}" --scene-dir "{P}" --coord-dir "{P}" --mods-dir "{P}"' }
    [ordered]@{ Id = 'InstallContents';         Cli = 'install-contents';          ArgsTemplate = '--input "{P}"' }
    [ordered]@{ Id = 'UninstallContents';       Cli = 'uninstall-contents';        ArgsTemplate = '--input "{P}"' }
    [ordered]@{ Id = 'GroupChara';              Cli = 'group-chara';               ArgsTemplate = '--input "{P}"' }
    [ordered]@{ Id = 'UngroupChara';            Cli = 'ungroup-chara';             ArgsTemplate = '--input "{P}"' }
    [ordered]@{ Id = 'RenameChara';             Cli = 'rename-chara';              ArgsTemplate = '--input "{P}"' }
)

# Always registered, not part of the numbered picker: GUI shortcut, plus the
# two PNG-file-association commands (different registry root/context).
$RunGuiTask   = [ordered]@{ Id = 'RunGUI' }
$PngTasks = @(
    [ordered]@{ Id = 'ArchiveChara'; Cli = 'archive-cards'; ArgsTemplate = '--context-menu "{P}"' }
    [ordered]@{ Id = 'DeleteChara';  Cli = 'delete-cards';  ArgsTemplate = '--context-menu "{P}"' }
)

# ---------------------------------------------------------------------------
# Localized labels (task labels reuse the same emoji as MXU's interface.json
# task list, for a consistent look between the GUI and the context menu) and
# localized UI prompt strings.
# ---------------------------------------------------------------------------
$Labels = @{
    en = @{
        FilterConvertKKS        = "🔧 Filter & Convert KKS Cards"
        FilterDuplicateContents = "🧹 Filter Duplicate Contents"
        DownloadMissingMods     = "⬇️ Download Missing Mods"
        InstallContents         = "📦 Install Contents"
        UninstallContents       = "↩️ Uninstall Contents"
        GroupChara              = "🗂️ Group Characters"
        UngroupChara            = "📂 Ungroup Characters"
        RenameChara             = "✏️ Rename Characters"
        RunGUI                  = "▶️ Run GUI"
        ArchiveChara            = "🎁 Archive Card/Scene"
        DeleteChara             = "🗑️ Delete Card/Scene"
    }
    zh_cn = @{
        FilterConvertKKS        = "🔧 过滤并转换 KKS 卡片"
        FilterDuplicateContents = "🧹 过滤重复内容"
        DownloadMissingMods     = "⬇️ 下载缺失模组"
        InstallContents         = "📦 安装内容"
        UninstallContents       = "↩️ 卸载内容"
        GroupChara              = "🗂️ 分组角色"
        UngroupChara            = "📂 取消分组角色"
        RenameChara             = "✏️ 重命名角色"
        RunGUI                  = "▶️ 启动图形界面"
        ArchiveChara            = "🎁 归档卡片/场景"
        DeleteChara             = "🗑️ 删除卡片/场景"
    }
    zh_tw = @{
        FilterConvertKKS        = "🔧 過濾並轉換 KKS 卡片"
        FilterDuplicateContents = "🧹 過濾重複內容"
        DownloadMissingMods     = "⬇️ 下載遺失模組"
        InstallContents         = "📦 安裝內容"
        UninstallContents       = "↩️ 解除安裝內容"
        GroupChara              = "🗂️ 分組角色"
        UngroupChara            = "📂 取消分組角色"
        RenameChara             = "✏️ 重新命名角色"
        RunGUI                  = "▶️ 啟動圖形介面"
        ArchiveChara            = "🎁 歸檔卡片/場景"
        DeleteChara             = "🗑️ 刪除卡片/場景"
    }
    ja = @{
        FilterConvertKKS        = "🔧 KKSカードのフィルタと変換"
        FilterDuplicateContents = "🧹 重複コンテンツをフィルタ"
        DownloadMissingMods     = "⬇️ 不足しているMODをダウンロード"
        InstallContents         = "📦 コンテンツをインストール"
        UninstallContents       = "↩️ コンテンツをアンインストール"
        GroupChara              = "🗂️ キャラをグループ化"
        UngroupChara            = "📂 キャラのグループ化を解除"
        RenameChara             = "✏️ キャラをリネーム"
        RunGUI                  = "▶️ GUIを起動"
        ArchiveChara            = "🎁 カード／シーンをアーカイブ"
        DeleteChara             = "🗑️ カード／シーンを削除"
    }
    ko = @{
        FilterConvertKKS        = "🔧 KKS 카드 필터 및 변환"
        FilterDuplicateContents = "🧹 중복 콘텐츠 필터"
        DownloadMissingMods     = "⬇️ 누락된 모드 다운로드"
        InstallContents         = "📦 콘텐츠 설치"
        UninstallContents       = "↩️ 콘텐츠 제거"
        GroupChara              = "🗂️ 캐릭터 그룹화"
        UngroupChara            = "📂 캐릭터 그룹 해제"
        RenameChara             = "✏️ 캐릭터 이름 변경"
        RunGUI                  = "▶️ GUI 실행"
        ArchiveChara            = "🎁 카드/씬 보관"
        DeleteChara             = "🗑️ 카드/씬 삭제"
    }
    ru = @{
        FilterConvertKKS        = "🔧 Фильтр и конвертация карточек KKS"
        FilterDuplicateContents = "🧹 Фильтр дублирующегося содержимого"
        DownloadMissingMods     = "⬇️ Загрузить недостающие моды"
        InstallContents         = "📦 Установить содержимое"
        UninstallContents       = "↩️ Удалить содержимое"
        GroupChara              = "🗂️ Группировать персонажей"
        UngroupChara            = "📂 Разгруппировать персонажей"
        RenameChara             = "✏️ Переименовать персонажей"
        RunGUI                  = "▶️ Запустить графический интерфейс"
        ArchiveChara            = "🎁 Архивировать карточку/сцену"
        DeleteChara             = "🗑️ Удалить карточку/сцену"
    }
}

$Ui = @{
    en = @{
        Removing      = "Removing any existing KKAFIO context menu entries..."
        ChooseLang    = "Select your language:"
        TasksHeader   = "Available tasks:"
        PromptTasks   = "Enter the task numbers in the order you want them to appear, separated by spaces (e.g. 3 1 4 6). Leave blank to include all tasks in the default order:"
        Invalid       = "Invalid input. Please enter numbers between 1 and {0}, separated by spaces."
        Registering   = "Registering context menu..."
        Done          = "KKAFIO context menu registered successfully."
        RestartNote   = "Note: If it doesn't appear immediately, restart Explorer."
        PressKey      = "Press any key to exit..."
    }
    zh_cn = @{
        Removing      = "正在移除现有的 KKAFIO 右键菜单项..."
        ChooseLang    = "选择语言："
        TasksHeader   = "可用任务："
        PromptTasks   = "请按你希望在菜单中出现的顺序输入任务编号，以空格分隔（例如 3 1 4 6）。留空则按默认顺序包含全部任务："
        Invalid       = "输入无效。请输入 1 到 {0} 之间的数字，并以空格分隔。"
        Registering   = "正在注册右键菜单..."
        Done          = "KKAFIO 右键菜单注册成功。"
        RestartNote   = "提示：如果没有立即显示，请重启资源管理器 (Explorer)。"
        PressKey      = "按任意键退出..."
    }
    zh_tw = @{
        Removing      = "正在移除現有的 KKAFIO 右鍵選單項目..."
        ChooseLang    = "選擇語言："
        TasksHeader   = "可用任務："
        PromptTasks   = "請依你希望在選單中出現的順序輸入任務編號，以空格分隔（例如 3 1 4 6）。留空則依預設順序包含全部任務："
        Invalid       = "輸入無效。請輸入 1 到 {0} 之間的數字，並以空格分隔。"
        Registering   = "正在註冊右鍵選單..."
        Done          = "KKAFIO 右鍵選單註冊成功。"
        RestartNote   = "提示：如果沒有立即顯示，請重新啟動檔案總管 (Explorer)。"
        PressKey      = "按任意鍵結束..."
    }
    ja = @{
        Removing      = "既存の KKAFIO 右クリックメニュー項目を削除しています..."
        ChooseLang    = "言語を選択:"
        TasksHeader   = "利用可能なタスク:"
        PromptTasks   = "メニューに表示したい順番でタスク番号をスペース区切りで入力してください（例: 3 1 4 6）。空欄の場合は既定の順序ですべてのタスクを含めます:"
        Invalid       = "入力が無効です。1〜{0} の数字をスペース区切りで入力してください。"
        Registering   = "右クリックメニューを登録しています..."
        Done          = "KKAFIO 右クリックメニューの登録が完了しました。"
        RestartNote   = "注: すぐに表示されない場合は、エクスプローラーを再起動してください。"
        PressKey      = "続けるには何かキーを押してください..."
    }
    ko = @{
        Removing      = "기존 KKAFIO 오른쪽 버튼 메뉴 항목을 제거하는 중..."
        ChooseLang    = "언어 선택:"
        TasksHeader   = "사용 가능한 작업:"
        PromptTasks   = "메뉴에 표시할 순서대로 작업 번호를 공백으로 구분하여 입력하세요(예: 3 1 4 6). 비워두면 기본 순서로 모든 작업이 포함됩니다:"
        Invalid       = "잘못된 입력입니다. 1~{0} 사이의 숫자를 공백으로 구분하여 입력하세요."
        Registering   = "오른쪽 버튼 메뉴를 등록하는 중..."
        Done          = "KKAFIO 오른쪽 버튼 메뉴가 성공적으로 등록되었습니다."
        RestartNote   = "참고: 바로 표시되지 않으면 탐색기를 다시 시작하세요."
        PressKey      = "종료하려면 아무 키나 누르세요..."
    }
    ru = @{
        Removing      = "Удаление существующих пунктов контекстного меню KKAFIO..."
        ChooseLang    = "Выберите язык:"
        TasksHeader   = "Доступные задачи:"
        PromptTasks   = "Введите номера задач в том порядке, в котором они должны отображаться в меню, через пробел (например, 3 1 4 6). Оставьте пустым, чтобы включить все задачи в порядке по умолчанию:"
        Invalid       = "Неверный ввод. Введите числа от 1 до {0}, разделённые пробелами."
        Registering   = "Регистрация контекстного меню..."
        Done          = "Контекстное меню KKAFIO успешно зарегистрировано."
        RestartNote   = "Примечание: если меню не появилось сразу, перезапустите проводник (Explorer)."
        PressKey      = "Нажмите любую клавишу для выхода..."
    }
}

# ---------------------------------------------------------------------------
# Step 1: unregister any existing KKAFIO context menu entries first, so
# re-running this script (e.g. with a different language or task selection)
# never leaves stale entries behind.
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host $Ui.en.Removing
$RootsToRemove = @(
    'HKCU:\Software\Classes\Directory\shell\KKAFIO'
    'HKCU:\Software\Classes\Directory\Background\shell\KKAFIO'
    'HKCU:\Software\Classes\SystemFileAssociations\.png\shell\KKAFIO'
)
foreach ($root in $RootsToRemove) {
    if (Test-Path $root) { Remove-Item -Path $root -Recurse -Force }
}

# ---------------------------------------------------------------------------
# Step 2: ask for a language.
# ---------------------------------------------------------------------------
$LangMenu = [ordered]@{
    '1' = @{ Code = 'en';    Name = 'English' }
    '2' = @{ Code = 'zh_cn'; Name = '简体中文' }
    '3' = @{ Code = 'zh_tw'; Name = '繁體中文' }
    '4' = @{ Code = 'ja';    Name = '日本語' }
    '5' = @{ Code = 'ko';    Name = '한국어' }
    '6' = @{ Code = 'ru';    Name = 'Русский' }
}

Write-Host ""
Write-Host "Select your language / 选择语言 / 選擇語言 / 言語を選択 / 언어 선택 / Выберите язык:"
foreach ($key in $LangMenu.Keys) {
    Write-Host ("  {0}. {1}" -f $key, $LangMenu[$key].Name)
}

$LangCode = 'en'
while ($true) {
    $choice = (Read-Host "> ").Trim()
    if ($LangMenu.Contains($choice)) {
        $LangCode = $LangMenu[$choice].Code
        break
    }
    Write-Host "Invalid choice, try again / 输入无效，请重试 / 輸入無效，請重試 / 無効な入力です、再入力してください / 잘못된 입력입니다. 다시 시도하세요 / Неверный выбор, попробуйте снова."
}

$L = $Labels[$LangCode]
$T = $Ui[$LangCode]

# ---------------------------------------------------------------------------
# Step 3: ask which tasks to include, and in what order, as a list of numbers.
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host $T.TasksHeader
for ($i = 0; $i -lt $FolderTasks.Count; $i++) {
    $n = $i + 1
    Write-Host ("  {0}. {1}" -f $n, $L[$FolderTasks[$i].Id])
}

Write-Host ""
Write-Host $T.PromptTasks
$SelectedTasks = $null
while ($null -eq $SelectedTasks) {
    $raw = (Read-Host "> ").Trim()
    if ([string]::IsNullOrWhiteSpace($raw)) {
        $SelectedTasks = 1..$FolderTasks.Count
        break
    }
    $parts = $raw -split '[,\s]+' | Where-Object { $_ -ne '' }
    $numbers = @()
    $ok = $true
    foreach ($p in $parts) {
        $n = 0
        if (-not [int]::TryParse($p, [ref]$n) -or $n -lt 1 -or $n -gt $FolderTasks.Count) {
            $ok = $false
            break
        }
        $numbers += $n
    }
    if ($ok -and $numbers.Count -gt 0) {
        $SelectedTasks = $numbers
    } else {
        Write-Host ($T.Invalid -f $FolderTasks.Count)
    }
}

Write-Host ""
Write-Host $T.Registering

# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------
function New-MenuRoot {
    param([string]$RootPath, [string]$ShellSubPath)
    New-Item -Path $RootPath -Force | Out-Null
    New-ItemProperty -Path $RootPath -Name 'MUIVerb' -Value 'KKAFIO' -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $RootPath -Name 'Icon' -Value ('"' + $Exe + '"') -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $RootPath -Name 'ExtendedSubCommandsKey' -Value $ShellSubPath -PropertyType String -Force | Out-Null
    New-Item -Path (Join-Path $RootPath 'shell') -Force | Out-Null
}

function New-MenuItem {
    param([string]$ShellPath, [string]$KeyName, [string]$Label, [string]$CommandLine)
    $itemPath = Join-Path $ShellPath $KeyName
    New-Item -Path $itemPath -Force | Out-Null
    Set-Item -Path $itemPath -Value $Label
    $cmdPath = Join-Path $itemPath 'command'
    New-Item -Path $cmdPath -Force | Out-Null
    Set-Item -Path $cmdPath -Value $CommandLine
}

function Build-Command {
    param([string]$TargetExe, [string]$Cli, [string]$ArgsTemplate, [string]$Placeholder)
    $argsStr = $ArgsTemplate -replace '\{P\}', $Placeholder
    return 'cmd.exe /k ""' + $TargetExe + '" ' + $Cli + ' ' + $argsStr + '"'
}

# ---------------------------------------------------------------------------
# Step 4: register folder context menu (Directory\shell and
# Directory\Background\shell), in the chosen order.
# ---------------------------------------------------------------------------
$FolderRoot = 'HKCU:\Software\Classes\Directory\shell\KKAFIO'
$BgRoot     = 'HKCU:\Software\Classes\Directory\Background\shell\KKAFIO'
New-MenuRoot -RootPath $FolderRoot -ShellSubPath 'Directory\shell\KKAFIO'
New-MenuRoot -RootPath $BgRoot -ShellSubPath 'Directory\Background\shell\KKAFIO'

$index = 1
foreach ($n in $SelectedTasks) {
    $task = $FolderTasks[$n - 1]
    $keyName = "{0:D2}{1}" -f $index, $task.Id
    $label = $L[$task.Id]

    New-MenuItem -ShellPath (Join-Path $FolderRoot 'shell') -KeyName $keyName -Label $label `
        -CommandLine (Build-Command -TargetExe $Exe -Cli $task.Cli -ArgsTemplate $task.ArgsTemplate -Placeholder '%1')
    New-MenuItem -ShellPath (Join-Path $BgRoot 'shell') -KeyName $keyName -Label $label `
        -CommandLine (Build-Command -TargetExe $Exe -Cli $task.Cli -ArgsTemplate $task.ArgsTemplate -Placeholder '%V')

    $index++
}

# "Run GUI" always goes last.
$guiKeyName = "{0:D2}{1}" -f $index, $RunGuiTask.Id
$guiLabel = $L[$RunGuiTask.Id]
$guiCommand = '"' + $Gui + '"'
New-MenuItem -ShellPath (Join-Path $FolderRoot 'shell') -KeyName $guiKeyName -Label $guiLabel -CommandLine $guiCommand
New-MenuItem -ShellPath (Join-Path $BgRoot 'shell') -KeyName $guiKeyName -Label $guiLabel -CommandLine $guiCommand

# ---------------------------------------------------------------------------
# Step 5: register the fixed PNG file-association entries
# (Archive Card/Scene, Delete Card/Scene) - always present, not affected by
# the folder task selection above since they live under a different root.
# ---------------------------------------------------------------------------
$PngRoot = 'HKCU:\Software\Classes\SystemFileAssociations\.png\shell\KKAFIO'
New-MenuRoot -RootPath $PngRoot -ShellSubPath 'SystemFileAssociations\.png\shell\KKAFIO'

$pngIndex = 1
foreach ($task in $PngTasks) {
    $keyName = "{0:D2}{1}" -f $pngIndex, $task.Id
    $label = $L[$task.Id]
    New-MenuItem -ShellPath (Join-Path $PngRoot 'shell') -KeyName $keyName -Label $label `
        -CommandLine (Build-Command -TargetExe $Exe -Cli $task.Cli -ArgsTemplate $task.ArgsTemplate -Placeholder '%1')
    $pngIndex++
}

Write-Host ""
Write-Host $T.Done
Write-Host $T.RestartNote
Write-Host ""
Write-Host $T.PressKey
[void][System.Console]::ReadKey($true)
