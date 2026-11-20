# Windows PowerShell 5.1. Dot-sourced by setup.ps1; source is UTF-8 with BOM.
function Get-VisioCodexHosts {
    $cli = Get-Command -Name codex.exe,codex.cmd,codex.ps1 -CommandType Application,ExternalScript -ErrorAction SilentlyContinue | Select-Object -First 1
    $desktop = $null
    if (Get-Command Get-AppxPackage -ErrorAction SilentlyContinue) {
        try {
            $package = Get-AppxPackage -Name OpenAI.Codex -ErrorAction Stop | Select-Object -First 1
            if ($package) { $desktop = $package.InstallLocation }
        } catch { }
    }
    if (!$desktop -and $cli -and $cli.Source -match '(?i)^(.*[\\/]WindowsApps[\\/]OpenAI\.Codex_[^\\/]+)[\\/]') {
        $desktop = $Matches[1] # CLI bundled in the installed desktop MSIX.
    }
    if (!$desktop) {
        $app = Get-Process -Name Codex -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -match '[\\/]app[\\/]Codex\.exe$' } | Select-Object -First 1
        if ($app) { $desktop = $app.Path }
    }
    [pscustomobject]@{ cli = if ($cli) { $cli.Source } else { $null }; desktop = $desktop }
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

function Register-VisioCodex([string]$InstallRoot, [string]$SkillRoot) {
    $state = Read-VisioRegistration $InstallRoot
    if (!$SkillRoot) {
        $SkillRoot = if ($state) { $state.skillRoot } else { Join-Path ([Environment]::GetFolderPath('UserProfile')) '.agents\skills' }
    }
    $parent = (Get-VisioFullPath $SkillRoot).TrimEnd('\')
    $target = Join-Path $parent 'visio-diagrams'
    Assert-VisioPlainPath $target
    if ($state -and $parent -ne $state.skillRoot) { throw "先卸载已登记的 Skill 再更换位置：$($state.skillRoot)" }
    if ([IO.Directory]::Exists($target) -or [IO.File]::Exists($target)) { Assert-VisioRegistration $target $state }
    $token = [guid]::NewGuid().ToString('N')
    $stage = Join-Path $parent ('.visio-stage-' + $token)
    $backup = Join-Path $parent ('.visio-old-' + $token)
    $record = Join-Path $InstallRoot 'agent-registration.json'
    $pending = Join-Path $InstallRoot ('.agent-' + $token + '.tmp')
    $source = Join-Path $InstallRoot 'current\visio-diagrams'
    $python = Join-Path $InstallRoot 'current\runtime\python.exe'
    $utf8 = [Text.UTF8Encoding]::new($false)
    $marker = '.hoshivela-owner.json'
    [void][IO.Directory]::CreateDirectory($parent)
    [void][IO.Directory]::CreateDirectory($stage)
    $installed = $false
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
        $data = [ordered]@{product = 'Hoshivela.VisioDiagrams'; installRoot = $InstallRoot; skillRoot = $parent; files = $hashes}
        $json = $data | ConvertTo-Json -Depth 4
        [IO.File]::WriteAllText((Join-Path $stage $marker), $json, $utf8)
        [IO.File]::WriteAllText($pending, $json, $utf8)
        if ([IO.Directory]::Exists($target)) {
            Assert-VisioRegistration $target $state
            [IO.Directory]::Move($target, $backup)
        }
        try {
            [IO.Directory]::Move($stage, $target)
            $installed = $true
            if ([IO.File]::Exists($record)) { [IO.File]::Replace($pending, $record, [NullString]::Value) }
            else { [IO.File]::Move($pending, $record) }
        } catch {
            if ($installed) { [IO.Directory]::Move($target, $stage) }
            if ([IO.Directory]::Exists($backup)) { [IO.Directory]::Move($backup, $target) }
            throw
        }
        if ([IO.Directory]::Exists($backup)) { Clear-VisioSkillDirectory $backup $parent }
    } finally {
        if ([IO.File]::Exists($pending)) { [IO.File]::Delete($pending) }
        if ([IO.Directory]::Exists($stage)) { Clear-VisioSkillDirectory $stage $parent }
    }
    $target
}

function Remove-VisioIntegration([string]$InstallRoot, [string]$SkillRoot) {
    try {
        $InstallRoot = Get-VisioFullPath $InstallRoot
        $state = Read-VisioRegistration $InstallRoot
        if ($state) {
            if ($SkillRoot -and (Get-VisioFullPath $SkillRoot).TrimEnd('\') -ne $state.skillRoot) { throw '所选 Skill 位置与登记记录不符。' }
            $target = Join-Path $state.skillRoot 'visio-diagrams'
            if (Test-Path -LiteralPath $target) {
                Assert-VisioRegistration $target $state
                Clear-VisioSkillDirectory $target $state.skillRoot
            }
            [IO.File]::Delete((Join-Path $InstallRoot 'agent-registration.json'))
        }
        [pscustomobject]@{status = 'removed'; message = '本安装登记的 Codex Skill 已移除或不存在。'}
    } catch {
        [pscustomobject]@{status = 'preserved'; message = "Skill 登记保留。$($_.Exception.Message)"}
    }
}

function Invoke-VisioIntegration {
    param(
        [Parameter(Mandatory)][string]$InstallRoot,
        [ValidateSet('Codex', 'None', 'Auto')][string]$AgentChoice = 'Auto',
        [string]$SkillRoot,
        [switch]$NonInteractive
    )
    $InstallRoot = Get-VisioFullPath $InstallRoot
    $hostsFound = Get-VisioCodexHosts
    $hasCodex = $hostsFound.cli -or $hostsFound.desktop
    $agent = [ordered]@{selection = $AgentChoice; status = 'not-registered'; path = $null; detected = $hostsFound}
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
    if ($AgentChoice -eq 'Auto' -and $hasCodex) {
        $AgentChoice = 'Codex'
        if (!$NonInteractive) {
            Write-Host '检测到 Codex。1 登记 Visio 制图 Skill（默认），2 仅安装制图工具。'
            do { $answer = Read-Host '请选择 1 或 2' } until ($answer -in @('', '1', '2'))
            if ($answer -eq '2') { $AgentChoice = 'None' }
        }
    }
    $agent.selection = $AgentChoice
    $status = if ($notes.Count) { 'partial' } else { 'ready' }
    if ($AgentChoice -eq 'Codex') {
        try {
            $agent.path = Register-VisioCodex $InstallRoot $SkillRoot
            $agent.status = 'registered'
            $notes.Add("Codex Skill 已登记在 $($agent.path)。请重新打开 Codex，用自然语言描述需要绘制的 Visio 图。")
        } catch {
            $status = 'partial'
            $agent.status = 'registration-failed'
            $notes.Add("未完成 Codex Skill 登记。$($_.Exception.Message)")
        }
    } elseif ($AgentChoice -eq 'None') {
        $status = 'partial'
        $removal = Remove-VisioIntegration $InstallRoot $SkillRoot
        if ($removal.status -eq 'preserved') { $status = 'partial'; $agent.status = 'preserved'; $notes.Add($removal.message) }
        else { $agent.status = 'skipped'; $notes.Add('已选择仅安装制图工具。需要自然语言制图时重新运行安装器并选择 Codex。') }
    }
    if (!$hasCodex -and $AgentChoice -ne 'None') {
        $status = 'partial'
        $notes.Add('未检测到本机 Codex。自然语言制图入口尚未验证，请安装或启动 Codex 后重新运行安装器。')
    }
    if ($drawing.generation.ok) { $notes.Insert(0, '已用私有运行时生成普通 VSDX 并回读离线校验报告。') }
    if ($probes.desktopVisio.registered) { $notes.Add('已检测到桌面 Visio 登记；安装自检未执行打开、编辑和另存。') }
    [pscustomobject]@{status = $status; agent = $agent; probes = $probes; message = ($notes -join [Environment]::NewLine)}
}
