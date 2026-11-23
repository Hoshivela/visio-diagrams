# Windows PowerShell 5.1. Dot-sourced by setup.ps1; source is UTF-8 with BOM.
function Get-VisioAgentProfiles {
    [ordered]@{
        Codex     = @{label = 'Codex'; root = '.agents\skills'}
        None      = @{label = '仅安装制图工具'; root = $null}
        ClaudeCode = @{label = 'Claude Code'; root = '.claude\skills'}
        Cursor    = @{label = 'Cursor'; root = '.cursor\skills'}
        GeminiCLI = @{label = 'Gemini CLI'; root = '.gemini\skills'}
        WorkBuddy = @{label = 'WorkBuddy'; root = '.workbuddy\skills'}
        Custom    = @{label = '其他支持 SKILL.md 的 Agent（指定 Skill 父目录）'; root = $null}
        DSH       = @{label = 'DeepSeek Harness (DSH)'; root = '.dsh\skills'}
    }
}

function Select-VisioAgent($Profiles, [string]$Previous, [switch]$NonInteractive) {
    if ($NonInteractive) {
        if ($Previous) { return $Previous }
        return 'None'
    }
    $ids = @($Profiles.Keys)
    Write-Host '请选择你使用的 Agent。安装器会将 Visio Skill 登记到所选位置。'
    for ($i = 0; $i -lt $ids.Count; $i++) {
        Write-Host ('{0}. {1}' -f ($i + 1), $Profiles[$ids[$i]].label)
    }
    $prompt = '请输入编号或名称'
    if ($Previous) { $prompt += "（回车沿用 $($Profiles[$Previous].label)）" }
    while ($true) {
        $answer = Read-Host $prompt
        if ($null -eq $answer) { throw '未收到 Agent 选择。请在交互窗口重新运行 Install.cmd，或显式传入 AgentChoice。' }
        $answer = $answer.Trim()
        if (!$answer -and $Previous) { return $Previous }
        for ($i = 0; $i -lt $ids.Count; $i++) {
            $id = $ids[$i]
            if ($answer -in @([string]($i + 1), $id, $Profiles[$id].label)) { return $id }
        }
        Write-Host '请输入上面列出的编号或名称。'
    }
}

function Get-VisioDesktopCapability {
    $executable = $null
    try {
        $kind = [type]::GetTypeFromProgID('Visio.Application')
        if ($kind) {
            $key = 'HKEY_CLASSES_ROOT\CLSID\{' + $kind.GUID + '}\LocalServer32'
            $command = [Microsoft.Win32.Registry]::GetValue($key, '', '')
            if ($command -match '^\s*"?(.+?\.exe)(?:"|\s|$)') {
                $path = [Environment]::ExpandEnvironmentVariables($Matches[1])
                if ([IO.File]::Exists($path)) { $executable = $path }
            }
        }
    } catch { }
    [pscustomobject]@{ registered = [bool]$executable; executable = $executable; roundTrip = 'NOT_RUN' }
}

function Get-VisioDrawingCapabilities([string]$InstallRoot) {
    $python = Join-Path $InstallRoot 'current\runtime\python.exe'
    # ASCII so native stdin works in both Windows PowerShell and PowerShell 7.
    $program = @'
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET
from zipfile import ZipFile
import json
import sys
import visio_core
from visio_core.resources import DEFAULT_FONT, resolve_stencil
from visio_core.text_layout import WindowsFont
from visio_core.vsdx_writer import load_masters
from swimlane_shapes import _stencil

root = Path(sys.argv[1]).resolve()
if Path(visio_core.__file__).resolve().parent.parent != root / "current" / "visio-diagrams" / "scripts":
    raise RuntimeError("Core imported outside installed scripts")

def font():
    with WindowsFont(DEFAULT_FONT) as face:
        missing = face.check_glyphs("\u4e2dA", 11, False)
        if missing:
            raise ValueError("Missing glyph: " + missing)
        return face.family

def basic_stencil():
    path = resolve_stencil()
    missing = {"Process", "Decision", "Start/End", "Dynamic connector"} - load_masters(path).keys()
    if missing:
        raise ValueError("Missing masters: " + ", ".join(sorted(missing)))
    return str(path)

def lane_stencil():
    path = _stencil(None)
    with ZipFile(path) as archive:
        masters = ET.fromstring(archive.read("visio/masters/masters.xml"))
        missing = {"Swimlane", "Swimlane (vertical)", "Swimlane List"} - {m.get("NameU") for m in masters}
        if missing:
            raise ValueError("Missing masters: " + ", ".join(sorted(missing)))
    return str(path)

def generate():
    with TemporaryDirectory(prefix="visio-install-check-") as directory:
        target = Path(directory) / "check.vsdx"
        description = {"schema": "visio.diagram/1.0", "layout": {"mode": "intent"},
                       "nodes": [{"id": "a", "kind": "box", "text": "\u68c0\u67e5"},
                                 {"id": "b", "kind": "terminator", "text": "\u5b8c\u6210"}],
                       "edges": [{"from": "a", "to": "b"}]}
        result = visio_core.generate(description, target)
        if result["status"] != "GENERATED":
            raise ValueError(json.dumps(result["diagnostics"], ensure_ascii=True))
        receipt = json.loads(target.with_suffix(".validation.json").read_bytes())
        if receipt["status"] != result["status"] or not target.is_file():
            raise RuntimeError("Missing offline receipt or drawing")
        return result["status"]

def probe(check):
    try:
        return {"ok": True, "detail": check()}
    except Exception as error:
        return {"ok": False, "detail": str(error)}

checks = {"runtime": sys.executable, "pythonVersion": sys.version.split()[0],
          "font": probe(font), "basicStencil": probe(basic_stencil), "swimlaneStencil": probe(lane_stencil)}
checks["generation"] = probe(generate)
print(json.dumps(checks, ensure_ascii=True))
'@
    $lines = $program | & $python -B - $InstallRoot
    if ($LASTEXITCODE -ne 0) { throw "私有 Python 或制图程序执行失败（退出码 $LASTEXITCODE）。" }
    ($lines -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop
}

function Get-VisioSkillFiles {
    'SKILL.md', 'agents\openai.yaml', 'README.md', 'references\input-format.md', 'references\fonts.md'
}

function Get-VisioFullPath([string]$Path) {
    [IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path))
}

function Resolve-VisioDshSkillRoot([string]$DshDirectory, [string]$UserDirectory) {
    # Match dsh-home-paths: expand only the current user's tilde, then resolve from cwd.
    if ($DshDirectory -eq '~') {
        $DshDirectory = $UserDirectory
    } elseif ($DshDirectory -match '^~[\\/]') {
        $DshDirectory = [IO.Path]::Combine($UserDirectory, $DshDirectory.Substring(2).TrimStart('\','/'))
    }
    [IO.Path]::GetFullPath([IO.Path]::Combine((Get-Location).ProviderPath, $DshDirectory, 'skills'))
}

function Get-VisioFileHash([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
    finally { $stream.Dispose(); $sha.Dispose() }
}

function Assert-VisioPlainPath([string]$Path) {
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        if ([IO.File]::Exists($current) -or [IO.Directory]::Exists($current)) {
            if ([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) {
                throw "路径包含链接，已保留原文件：$current"
            }
        }
        $current = [IO.Path]::GetDirectoryName($current)
    }
}

function Read-VisioRegistration([string]$InstallRoot) {
    $file = Join-Path $InstallRoot 'agent-registration.json'
    Assert-VisioPlainPath $file
    if (![IO.File]::Exists($file)) { return }
    $state = [IO.File]::ReadAllText($file) | ConvertFrom-Json -ErrorAction Stop
    if ($state.product -ne 'Hoshivela.VisioDiagrams' -or $state.installRoot -ne $InstallRoot -or
        !$state.skillRoot -or (Get-VisioFullPath $state.skillRoot) -ne $state.skillRoot) {
        throw "Skill 登记记录无效，已保留原文件：$file"
    }
    # trial.8 recorded only Codex. Keep the original on-disk bytes for the ownership check.
    if (!$state.PSObject.Properties['agent']) { $state | Add-Member -NotePropertyName agent -NotePropertyValue 'Codex' }
    $profiles = Get-VisioAgentProfiles
    if (!$state.agent -or !$profiles.Contains($state.agent) -or $state.agent -eq 'None') {
        throw "Skill 登记的 Agent 无法识别，已保留原文件：$file"
    }
    $state
}

function Assert-VisioRegistration([string]$Target, $State) {
    Assert-VisioPlainPath $Target
    if (!$State -or $Target -ne (Join-Path $State.skillRoot 'visio-diagrams')) {
        throw "同名 Skill 已存在，请先将原目录备份到 Skill 搜索路径之外，再运行安装器：$Target"
    }
    $marker = '.hoshivela-owner.json'
    $allow = @(Get-VisioSkillFiles) + @($marker, 'agents', 'references')
    foreach ($folder in @('', 'agents', 'references')) {
        $path = if ($folder) { Join-Path $Target $folder } else { $Target }
        foreach ($entry in [IO.Directory]::EnumerateFileSystemEntries($path)) {
            $relative = $entry.Substring($Target.Length + 1)
            if ($allow -notcontains $relative -or ([IO.File]::GetAttributes($entry) -band [IO.FileAttributes]::ReparsePoint)) {
                throw "Skill 存在额外文件或链接，已保留：$entry"
            }
        }
    }
    $record = Join-Path $State.installRoot 'agent-registration.json'
    if ((Get-VisioFileHash $record) -ne (Get-VisioFileHash (Join-Path $Target $marker))) {
        throw "Skill 所属记录不匹配，已保留：$Target"
    }
    foreach ($name in (Get-VisioSkillFiles)) {
        $hash = Get-VisioFileHash (Join-Path $Target $name)
        if ($State.files.$name -ne $hash) { throw "Skill 文件已修改，已保留：$Target\$name" }
    }
}

function Clear-VisioSkillDirectory([string]$Directory, [string]$Parent) {
    $full = [IO.Path]::GetFullPath($Directory)
    if ([IO.Path]::GetDirectoryName($full) -ne $Parent -or
        [IO.Path]::GetFileName($full) -notmatch '^(visio-diagrams|\.visio-(stage|old)-[a-f0-9]{32})$') {
        throw "无法确认 Skill 路径：$full"
    }
    # Delete only the fixed list. A newly added/unknown file prevents deleting its directory.
    foreach ($name in (@(Get-VisioSkillFiles) + '.hoshivela-owner.json')) {
        $file = Join-Path $full $name
        Assert-VisioPlainPath $file
        if ([IO.File]::Exists($file)) { [IO.File]::Delete($file) }
    }
    foreach ($name in @('agents', 'references', '')) {
        $folder = if ($name) { Join-Path $full $name } else { $full }
        if ([IO.Directory]::Exists($folder)) { [IO.Directory]::Delete($folder) }
    }
}

function Register-VisioSkill([string]$InstallRoot, [string]$Agent, [string]$SkillRoot, $State, [bool]$FollowDshHome = $false) {
    if ($SkillRoot -notmatch '^(?:[A-Za-z]:[\\/]|[\\/]{2}[^\\/]+[\\/][^\\/]+)') {
        throw '请提供 Skill 父目录的完整路径，例如 D:\我的工具\skills。'
    }
    $parent = (Get-VisioFullPath $SkillRoot).TrimEnd('\')
    if ($parent.Length -le [IO.Path]::GetPathRoot($SkillRoot).TrimEnd('\').Length) {
        throw '请选择用于存放 Skill 的文件夹。'
    }
    $target = Join-Path $parent 'visio-diagrams'
    if ($target -eq $InstallRoot -or $target.StartsWith($InstallRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $InstallRoot.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Skill 目录与制图工具安装目录重叠，请另选 Skill 父目录。'
    }
    Assert-VisioPlainPath $target
    if ([IO.Directory]::Exists($target) -or [IO.File]::Exists($target)) { Assert-VisioRegistration $target $State }
    $token = [guid]::NewGuid().ToString('N')
    $stage = Join-Path $parent ('.visio-stage-' + $token)
    $oldTarget = $null
    $backup = $null
    if ($State) {
        $oldTarget = Join-Path $State.skillRoot 'visio-diagrams'
        Assert-VisioPlainPath $oldTarget
        if ($oldTarget.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase) -or
            $target.StartsWith($oldTarget + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw '所选 Skill 目录与先前登记的目录嵌套，请另选位置。'
        }
        if ([IO.Directory]::Exists($oldTarget) -or [IO.File]::Exists($oldTarget)) {
            Assert-VisioRegistration $oldTarget $State
            $backup = Join-Path $State.skillRoot ('.visio-old-' + $token)
        }
    }
    $record = Join-Path $InstallRoot 'agent-registration.json'
    $pending = Join-Path $InstallRoot ('.agent-' + $token + '.tmp')
    $source = Join-Path $InstallRoot 'current\visio-diagrams'
    $python = Join-Path $InstallRoot 'current\runtime\python.exe'
    $utf8 = [Text.UTF8Encoding]::new($false)
    $marker = '.hoshivela-owner.json'
    [void][IO.Directory]::CreateDirectory($parent)
    [void][IO.Directory]::CreateDirectory($stage)
    $installed = $false
    $cleanupWarning = $null
    try {
        $hashes = [ordered]@{}
        foreach ($name in (Get-VisioSkillFiles)) {
            $dest = Join-Path $stage $name
            [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($dest))
            $src = Join-Path $source $name
            if ($name -eq 'SKILL.md') {
                $content = [IO.File]::ReadAllText($src)
                $start = $content.IndexOf('# runtime-binding')
                $end = $content.IndexOf('# end-runtime-binding')
                if ($start -lt 0 -or $end -lt $start) { throw 'Skill 运行路径标记缺失，请重新解压安装包。' }
                $toolLiteral = [Management.Automation.Language.CodeGeneration]::EscapeSingleQuotedStringContent($source)
                $pythonLiteral = [Management.Automation.Language.CodeGeneration]::EscapeSingleQuotedStringContent($python)
                $binding = "# runtime-binding`r`n`$tool = '$toolLiteral'`r`n`$python = '$pythonLiteral'`r`n"
                [IO.File]::WriteAllText($dest, $content.Substring(0, $start) + $binding + $content.Substring($end), $utf8)
            } else {
                [IO.File]::Copy($src, $dest)
            }
            $hashes[$name] = Get-VisioFileHash $dest
        }
        $data = [ordered]@{product = 'Hoshivela.VisioDiagrams'; installRoot = $InstallRoot; skillRoot = $parent; agent = $Agent; files = $hashes}
        if ($FollowDshHome) { $data.followDshHome = $true }
        $json = $data | ConvertTo-Json -Depth 4
        [IO.File]::WriteAllText((Join-Path $stage $marker), $json, $utf8)
        [IO.File]::WriteAllText($pending, $json, $utf8)
        if ($backup) {
            Assert-VisioRegistration $oldTarget $State
            [IO.Directory]::Move($oldTarget, $backup)
        }
        try {
            [IO.Directory]::Move($stage, $target)
            $installed = $true
            if ([IO.File]::Exists($record)) { [IO.File]::Replace($pending, $record, [NullString]::Value) }
            else { [IO.File]::Move($pending, $record) }
        } catch {
            if ($installed) { [IO.Directory]::Move($target, $stage) }
            if ($backup) { [IO.Directory]::Move($backup, $oldTarget) }
            throw
        }
        if ($backup) {
            try { Clear-VisioSkillDirectory $backup $State.skillRoot }
            catch { $cleanupWarning = "旧 Skill 已退出登记，其备份仍需清理：$backup。$($_.Exception.Message)" }
        }
    } finally {
        if ([IO.File]::Exists($pending)) { [IO.File]::Delete($pending) }
        if ([IO.Directory]::Exists($stage)) { Clear-VisioSkillDirectory $stage $parent }
    }
    [pscustomobject]@{path = $target; warning = $cleanupWarning}
}

function Remove-VisioIntegration([string]$InstallRoot, [string]$SkillRoot) {
    try {
        $InstallRoot = Get-VisioFullPath $InstallRoot
        $state = Read-VisioRegistration $InstallRoot
        if ($state) {
            if ($SkillRoot -and (Get-VisioFullPath $SkillRoot).TrimEnd('\') -ne $state.skillRoot) { throw '所选 Skill 位置与登记记录不符。' }
            $target = Join-Path $state.skillRoot 'visio-diagrams'
            $backup = $null
            Assert-VisioPlainPath $target
            if ([IO.Directory]::Exists($target) -or [IO.File]::Exists($target)) {
                Assert-VisioRegistration $target $state
                $backup = Join-Path $state.skillRoot ('.visio-old-' + [guid]::NewGuid().ToString('N'))
                [IO.Directory]::Move($target, $backup)
            }
            try { [IO.File]::Delete((Join-Path $InstallRoot 'agent-registration.json')) }
            catch {
                if ($backup) { [IO.Directory]::Move($backup, $target) }
                throw
            }
            if ($backup) {
                try { Clear-VisioSkillDirectory $backup $state.skillRoot }
                catch {
                    return [pscustomobject]@{status = 'removed'; message = "Skill 已退出登记，备份仍需清理：$backup。$($_.Exception.Message)"}
                }
            }
        }
        [pscustomobject]@{status = 'removed'; message = '本安装登记的 Visio Skill 已移除或不存在。'}
    } catch {
        [pscustomobject]@{status = 'preserved'; message = "Skill 登记保留。$($_.Exception.Message)"}
    }
}

function Invoke-VisioIntegration {
    param(
        [Parameter(Mandatory)][string]$InstallRoot,
        [ValidateSet('Auto','Codex','None','ClaudeCode','Cursor','GeminiCLI','WorkBuddy','Custom','DSH')][string]$AgentChoice = 'Auto',
        [string]$SkillRoot,
        [switch]$NonInteractive
    )
    $InstallRoot = Get-VisioFullPath $InstallRoot
    $profiles = Get-VisioAgentProfiles
    $agent = [ordered]@{selection = $AgentChoice; status = 'not-registered'; path = $null; discovery = 'NOT_VERIFIED'
        activeSelection = $null; activePath = $null}
    $probes = [ordered]@{desktopVisio = Get-VisioDesktopCapability; drawing = $null}
    $notes = [Collections.Generic.List[string]]::new()
    try {
        $drawing = Get-VisioDrawingCapabilities $InstallRoot
        $probes.drawing = $drawing
        if (!$drawing.font.ok) {
            $notes.Add('普通图需要微软雅黑。请通过 Windows 字体或可选语言功能补齐该字体，再运行安装器。')
        }
        if (!$drawing.basicStencil.ok) {
            $notes.Add('基本流程图模具不可用。请通过已有许可的 Visio 安装补齐 BASFLO_M.VSSX，或让 VISIO_CORE_STENCIL 指向可用的完整模具文件。')
        }
        if (!$drawing.generation.ok) {
            if ($drawing.font.ok -and $drawing.basicStencil.ok) {
                throw "普通图生成自检失败。请检查临时目录可写性并重新安装。详情：$($drawing.generation.detail)"
            }
            $notes.Add('普通图暂不能生成；补齐上述条件后重新运行 Install.cmd。')
        }
    } catch {
        return [pscustomobject]@{status = 'failed'; agent = $agent; probes = $probes
            message = "核心自检失败，安装未完成。请重新解压完整的 Windows x64 安装包。$($_.Exception.Message)"}
    }
    if (!$probes.desktopVisio.registered) {
        $notes.Add('未检测到可用的 Visio.Application 桌面程序登记。桌面复核及泳道生成需要已获许可的桌面 Visio，请安装或修复后重试。')
    }
    if (!$drawing.swimlaneStencil.ok) {
        $notes.Add('跨职能泳道模具不可用。需要泳道时请从已许可的 Visio 安装补齐 XFUNC_M.VSSX。')
    }
    $status = if ($notes.Count) { 'partial' } else { 'ready' }
    try {
        $previous = Read-VisioRegistration $InstallRoot
        if ($previous) {
            $agent.activeSelection = $previous.agent
            $agent.activePath = Join-Path $previous.skillRoot 'visio-diagrams'
        }
        if ($AgentChoice -eq 'Auto') {
            $AgentChoice = Select-VisioAgent $profiles $agent.activeSelection -NonInteractive:$NonInteractive
        }
        $agent.selection = $AgentChoice
        if ($AgentChoice -eq 'None') {
            $status = 'partial'
            $removal = Remove-VisioIntegration $InstallRoot $SkillRoot
            if ($removal.status -eq 'preserved') {
                $agent.status = 'preserved'
                $notes.Add($removal.message)
            } else {
                $agent.status = 'skipped'
                $agent.activeSelection = $null
                $agent.activePath = $null
                if ($previous) { $notes.Add($removal.message) }
                $notes.Add('仅安装制图工具。需要接入 Agent 时，重新运行 Install.cmd 并选择使用的 Agent。')
            }
        } else {
            $profile = $profiles[$AgentChoice]
            if (!$SkillRoot -and $AgentChoice -eq 'Custom' -and !$NonInteractive) {
                $prompt = '输入该 Agent 用于发现 SKILL.md 的父目录完整路径，安装器将在其中创建 visio-diagrams'
                if ($previous -and $previous.agent -eq 'Custom') { $prompt += "（回车沿用 $($previous.skillRoot)）" }
                $SkillRoot = Read-Host $prompt
            }
            if (!$SkillRoot -and $previous -and $previous.agent -eq $AgentChoice -and !$previous.followDshHome) {
                $SkillRoot = $previous.skillRoot
            }
            $followDshHome = $AgentChoice -eq 'DSH' -and !$SkillRoot
            if (!$SkillRoot -and $profile.root) {
                $userDirectory = $env:USERPROFILE
                if (!$userDirectory) { $userDirectory = [Environment]::GetFolderPath('UserProfile') }
                $SkillRoot = Join-Path $userDirectory $profile.root
                if ($followDshHome -and ![string]::IsNullOrWhiteSpace($env:DSH_HOME)) {
                    $SkillRoot = Resolve-VisioDshSkillRoot $env:DSH_HOME $userDirectory
                }
            }
            if (!$SkillRoot) { throw '自定义接入需要提供该 Agent 的 Skill 父目录。重新运行 Install.cmd 选择目录，或通过 -SkillRoot 指定完整路径。' }
            $registration = Register-VisioSkill $InstallRoot $AgentChoice $SkillRoot $previous $followDshHome
            $agent.path = $registration.path
            if ($registration.warning) { $status = 'partial'; $notes.Add($registration.warning) }
            if ($AgentChoice -eq 'Custom' -and $agent.path.StartsWith($PSScriptRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
                $notes.Add('所选 Skill 位于解压包内，删除解压包会一并移除 Skill。可以重新运行安装器，将 Skill 登记到 Agent 的长期使用目录。')
            }
            $agent.status = 'registered'
            $agent.activeSelection = $AgentChoice
            $agent.activePath = $agent.path
            $notes.Add("已将 Visio Skill 登记到 $($agent.path)。请重新打开所选 Agent，在新会话中用自然语言制图，验证 Skill 是否可用。")
        }
    } catch {
        $status = 'partial'
        $agent.status = 'registration-failed'
        $notes.Add("未完成所选 Agent 的接入。$($_.Exception.Message)")
        if ($agent.activePath) { $notes.Add("原登记位置：$($agent.activePath)。") }
    }
    if ($drawing.generation.ok) { $notes.Insert(0, '已用私有运行时生成普通 VSDX 并回读离线校验报告。') }
    if ($probes.desktopVisio.registered) { $notes.Add('已检测到桌面 Visio 登记；安装自检未执行打开、编辑和另存。') }
    [pscustomobject]@{status = $status; agent = $agent; probes = $probes; message = ($notes -join [Environment]::NewLine)}
}
