#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Install','Uninstall')][string]$Action = 'Install',
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Hoshivela\VisioDiagrams'),
    [ValidateSet('Auto','Codex','None','ClaudeCode','Cursor','GeminiCLI','WorkBuddy','Custom','DSH')][string]$AgentChoice = 'Auto',
    [string]$SkillRoot,
    [switch]$NonInteractive
)
$ErrorActionPreference = 'Stop'
$Product = 'Hoshivela.VisioDiagrams'
$Utf8 = New-Object Text.UTF8Encoding($false)
$Log = [IO.Path]::Combine([IO.Path]::GetTempPath(), ('VisioDiagrams-{0}-{1}.log' -f $Action, [guid]::NewGuid().ToString('N')))

function Write-Message([string]$Message) {
    Write-Host $Message
    [IO.File]::AppendAllText($Log, ('{0:s}  {1}' -f [DateTime]::Now, $Message) + [Environment]::NewLine, $Utf8)
}

function FileHash([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant() }
    finally { $stream.Dispose(); $sha.Dispose() }
}

# Reject junctions in both existing parents and the leaf before moving/removing files.
function Assert-PlainPath([string]$Path) {
    $item = $Path
    while ($item) {
        try {
            $attrs = [IO.File]::GetAttributes($item)
            if ($attrs -band [IO.FileAttributes]::ReparsePoint) { throw "路径包含链接，请改用普通目录：$item" }
        } catch [IO.FileNotFoundException] {} catch [IO.DirectoryNotFoundException] {}
        $item = [IO.Path]::GetDirectoryName($item)
    }
}

function Child([string]$Base, [string]$Relative) {
    if (-not $Relative -or $Relative -match '[\\:*?"<>|\x00-\x1f]') { throw "文件路径无效：$Relative" }
    foreach ($part in $Relative.Split('/')) {
        if (-not $part -or $part -match '[. ]$|^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)') {
            throw "文件路径无效：$Relative"
        }
    }
    $path = [IO.Path]::GetFullPath([IO.Path]::Combine($Base, $Relative.Replace('/','\')))
    if (-not $path.StartsWith($Base.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "目录越界：$Relative" }
    Assert-PlainPath $path
    return $path
}

function Read-JsonFile([string]$Path) {
    return ([IO.File]::ReadAllText($Path, $Utf8) | ConvertFrom-Json)
}

function Write-JsonFile([string]$Path, $Value) {
    $PrivateFiles[$Path] = $true
    [IO.File]::WriteAllText($Path, (ConvertTo-Json -InputObject $Value -Depth 20) + [Environment]::NewLine, $Utf8)
}

function Read-OwnedFiles($Record) {
    if ($Record.product -ne $Product -or $Record.format -ne 1 -or $Record.installRoot -ne $InstallRoot -or -not $Record.files) {
        throw "安装记录无法识别，请核对安装目录并保留原文件：$InstallRoot"
    }
    $files = @{}
    foreach ($prop in $Record.files.PSObject.Properties) {
        if ($prop.Name -notmatch '^current/(runtime|visio-diagrams)/' -or $prop.Name -match '\.(vsdx?|vsdm|vsd|vsd[xm]~)$' -or $prop.Value -notmatch '^[a-f0-9]{64}$') {
            throw "安装记录文件项无效：$($prop.Name)"
        }
        $null = Child $InstallRoot $prop.Name
        $files.Add($prop.Name, $prop.Value)
    }
    return $files
}

function Verify-Package {
    $manifest = Read-JsonFile (Child $PSScriptRoot 'INSTALL-MANIFEST.json')
    $sourceFiles = @{}
    foreach ($prop in $manifest.files.PSObject.Properties) {
        if ($prop.Value -notmatch '^[a-f0-9]{64}$') { throw "安装清单哈希无效：$($prop.Name)" }
        $sourceFiles.Add($prop.Name, $prop.Value)
        if ($Action -eq 'Install' -or $prop.Name -in @('setup.ps1','integration.ps1')) {
            $path = Child $PSScriptRoot $prop.Name
            if ((FileHash $path) -ne $prop.Value) { throw "安装包校验失败，请重新完整解压 ZIP：$($prop.Name)" }
        }
    }
    foreach ($name in @('integration.ps1','setup.ps1','runtime/python-embed.zip','visio-diagrams/TRIAL-MANIFEST.json')) {
        if (-not $sourceFiles.ContainsKey($name)) { throw "安装包清单缺少 $name，请重新完整解压 ZIP。" }
    }
    if (-not $manifest.version -or $manifest.runtimeSha256 -ne $sourceFiles['runtime/python-embed.zip']) { throw '安装包版本或 Python 摘要无效。' }
    return $manifest
}

function Stage-Payload($Manifest) {
    $result = @{}
    foreach ($prop in $Manifest.files.PSObject.Properties) {
        if (-not $prop.Name.StartsWith('visio-diagrams/')) { continue }
        $rel = 'current/' + $prop.Name
        $dest = Child $Stage $rel
        $null = [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($dest))
        $PrivateFiles[$dest] = $true
        [IO.File]::Copy((Child $PSScriptRoot $prop.Name), $dest)
        $result.Add($rel, $prop.Value)
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead((Child $PSScriptRoot 'runtime/python-embed.zip'))
    try {
        foreach ($entry in $zip.Entries) {
            if ($entry.FullName.EndsWith('/')) { continue }
            $rel = 'current/runtime/' + $entry.FullName
            $dest = Child $Stage $rel
            $null = [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($dest))
            $PrivateFiles[$dest] = $true
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $dest)
            $result.Add($rel, (FileHash $dest))
        }
    } finally { $zip.Dispose() }
    $pth = @($result.Keys | Where-Object { $_ -match '^current/runtime/python[0-9]+\._pth$' })
    if ($pth.Count -ne 1 -or -not $result.ContainsKey('current/runtime/python.exe')) { throw '随包 Python 运行时结构不完整。' }
    $path = Child $Stage $pth[0]
    [IO.File]::AppendAllText($path, "`r`n../visio-diagrams/scripts`r`n", [Text.Encoding]::ASCII)
    $result[$pth[0]] = FileHash $path
    foreach ($rel in $result.Keys) {
        if ($rel -match '\.vsd[xm]?$') { throw "安装载荷含有图纸文件：$rel" }
    }
    $trial = Read-JsonFile (Child $Stage 'current/visio-diagrams/TRIAL-MANIFEST.json')
    if ($trial.version -ne $Manifest.version) { throw '安装包与制图工具版本不一致。' }
    $python = Child $Stage 'current/runtime/python.exe'
    $version = (& $python -B -m visio_core --version | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $trial.coreVersion -or -not $version.Contains($trial.coreVersion)) {
        throw "私有 Python 或制图内核无法启动。请确认使用 64 位 Windows x64，并重新解压安装包。$version"
    }
    Write-Message "私有运行时与内核检查通过，$version"
    return $result
}

function Ensure-Parent([string]$Path) {
    $parent = [IO.Path]::GetDirectoryName($Path)
    if (-not [IO.Directory]::Exists($parent)) {
        Ensure-Parent $parent
        $null = [IO.Directory]::CreateDirectory($parent)
        $NewDirectories.Add($parent)
    }
}

# Each original is held on the same volume until the core and its ownership record commit.
function Change-File([string]$Rel, [bool]$HasNew) {
    $dest = Child $InstallRoot $Rel
    $entry = [pscustomobject]@{rel=$Rel; backup=$null; wrote=$false}
    $Changes.Add($entry)
    if ([IO.File]::Exists($dest)) {
        $saved = Child $Backup $Rel
        if ([IO.File]::Exists($saved)) { $saved = Child $Backup ('report-' + $Changes.Count) }
        $null = [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($saved))
        [IO.File]::Move($dest, $saved)
        $PrivateFiles[$saved] = $true
        $entry.backup = $saved
    }
    if ($HasNew) {
        Ensure-Parent $dest
        [IO.File]::Move((Child $Stage $Rel), $dest)
        $entry.wrote = $true
    }
}

function Undo-Changes([int]$Start = 0) {
    for ($i = $Changes.Count - 1; $i -ge $Start; $i--) {
        $entry = $Changes[$i]
        $dest = Child $InstallRoot $entry.rel
        if ($entry.wrote) { [IO.File]::Delete($dest) }
        if ($entry.backup) { [IO.File]::Move($entry.backup, $dest) }
    }
    if ($Start -eq 0) {
        for ($i = $NewDirectories.Count - 1; $i -ge 0; $i--) { Remove-Empty $NewDirectories[$i] }
    }
}

function Remove-Empty([string]$Path) {
    if ([IO.Directory]::Exists($Path) -and -not [IO.Directory]::GetFileSystemEntries($Path).Length) {
        [IO.Directory]::Delete($Path)
    }
}

function Trim-Parents([string[]]$Files) {
    $dirs = @{}
    foreach ($rel in $Files) {
        $parent = [IO.Path]::GetDirectoryName((Child $InstallRoot $rel))
        while ($parent -and $parent -ne $InstallRoot) {
            $dirs[$parent] = $true
            $parent = [IO.Path]::GetDirectoryName($parent)
        }
    }
    foreach ($dir in ($dirs.Keys | Sort-Object Length -Descending)) { Remove-Empty $dir }
}

function Remove-Transaction {
    if (-not $Transaction) { return }
    if ([IO.Path]::GetDirectoryName($Transaction) -ne $InstallRoot -or [IO.Path]::GetFileName($Transaction) -notmatch '^\.v-[0-9a-f]{12}$') {
        throw "临时目录路径无效：$Transaction"
    }
    # Delete only the staging files and backups this process created, even when
    # someone added another file to the transient directory while setup ran.
    $dirs = @{}
    foreach ($path in $PrivateFiles.Keys) {
        if (-not $path.StartsWith($Transaction + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "临时文件越界：$path" }
        Assert-PlainPath $path
        if ([IO.File]::Exists($path)) { [IO.File]::Delete($path) }
        $parent = [IO.Path]::GetDirectoryName($path)
        while ($parent -ne $Transaction) {
            $dirs[$parent] = $true
            $parent = [IO.Path]::GetDirectoryName($parent)
        }
    }
    $dirs[$Stage] = $true
    $dirs[$Backup] = $true
    foreach ($dir in ($dirs.Keys | Sort-Object Length -Descending)) { Remove-Empty $dir }
    Remove-Empty $Transaction
}

function Check-Report {
    if ([IO.File]::Exists($ReportPath)) {
        if (-not $Record -or (FileHash $ReportPath) -ne $Record.reportSha256) {
            throw "自检记录已有外部更改，请另存后移走文件并重试：$ReportPath"
        }
    }
}

function Publish-Report($Result, $Files) {
    Write-JsonFile (Child $Stage 'capabilities.json') $Result
    $ledger = [ordered]@{product=$Product; format=1; version=$Manifest.version; installRoot=$InstallRoot; files=$Files
        reportSha256=(FileHash (Child $Stage 'capabilities.json'))}
    Write-JsonFile (Child $Stage 'installation.json') $ledger
    Change-File 'capabilities.json' $true
    Change-File 'installation.json' $true
}

function Install-Product {
    Check-Report
    Write-Message "正在准备版本 $($Manifest.version)……"
    $files = Stage-Payload $Manifest
    $replace = @()
    $obsolete = @()
    foreach ($rel in $files.Keys) {
        $dest = Child $InstallRoot $rel
        if ([IO.Directory]::Exists($dest)) { throw "同名目录阻止安装：$dest" }
        if ([IO.File]::Exists($dest)) {
            $found = FileHash $dest
            if (-not $Owned.ContainsKey($rel) -or $found -ne $Owned[$rel]) {
                throw "同名文件已存在或曾被修改。请备份并移走后重试：$dest"
            }
            if ($found -eq $files[$rel]) { continue }
        }
        $replace += $rel
    }
    foreach ($rel in $Owned.Keys) {
        if ($files.ContainsKey($rel)) { continue }
        $dest = Child $InstallRoot $rel
        if ([IO.File]::Exists($dest)) {
            if ((FileHash $dest) -eq $Owned[$rel]) { $obsolete += $rel }
            else { Write-Message "保留已修改的旧版文件：$dest" }
        }
    }
    foreach ($rel in $replace) { Change-File $rel $true }
    foreach ($rel in $obsolete) { Change-File $rel $false }

    # Establish an uninstallable core before publishing the external Agent entry.
    # A failed blocking probe still rolls the entire pending installation back.
    Publish-Report ([ordered]@{status='pending'; message='自检待完成，请重新运行 Install.cmd。'; log=$Log}) $files
    $options = @{InstallRoot=$InstallRoot; AgentChoice=$AgentChoice; NonInteractive=$NonInteractive}
    if ($SkillRoot) { $options.SkillRoot = $SkillRoot }
    $result = Invoke-VisioIntegration @options
    if ($result.status -notin @('ready','partial')) {
        throw "制图自检未通过：$($result.message)"
    }
    $script:Committed = $true
    $reportStart = $Changes.Count
    try {
        $result | Add-Member -NotePropertyName installerVersion -NotePropertyValue $Manifest.version -Force
        $result | Add-Member -NotePropertyName log -NotePropertyValue $Log -Force
        Publish-Report $result $files
    } catch {
        Write-Message "已保留内核和接入结果。最终自检报告写入失败，请关闭占用文件的程序并重试 Install.cmd。"
        try { Undo-Changes $reportStart } catch { $script:KeepBackup = $true; Write-Message "保留恢复文件：$Transaction" }
        throw
    }
    Trim-Parents $obsolete
    Write-Message $result.message
    if ($result.probes.drawing.generation.ok -eq $false) {
        Write-Message "已部署，普通制图当前不可用。$($result.probes.drawing.generation.detail)"
    }
    Write-Message "自检报告：$ReportPath"
    if ($result.status -eq 'ready') { Write-Message '安装完成。'; return 0 }
    Write-Message '工具已安装，部分能力尚未就绪。请按自检提示处理后重新运行 Install.cmd。'
    return 2
}

function Uninstall-Product {
    $remove = @()
    foreach ($rel in $Owned.Keys) {
        $dest = Child $InstallRoot $rel
        if ([IO.File]::Exists($dest)) {
            if ((FileHash $dest) -eq $Owned[$rel]) { $remove += $rel }
            else { Write-Message "保留已修改的文件：$dest" }
        }
    }
    if ([IO.File]::Exists($ReportPath)) {
        if ((FileHash $ReportPath) -eq $Record.reportSha256) { $remove += 'capabilities.json' }
        else { Write-Message "保留已修改的自检记录：$ReportPath" }
    }
    foreach ($rel in $remove) { Change-File $rel $false }
    Change-File 'installation.json' $false
    $options = @{InstallRoot=$InstallRoot}
    if ($SkillRoot) { $options.SkillRoot = $SkillRoot }
    $result = Remove-VisioIntegration @options
    if ($result.status -ne 'removed') {
        throw "卸载尚未完成。$($result.message) 请把已修改的 Skill 文件夹备份并移到 Skill 搜索路径外，然后重新运行 Uninstall.cmd。"
    }
    Write-Message $result.message
    $script:Committed = $true
    Trim-Parents @($Owned.Keys)
    Write-Message '已卸载。自行添加或修改的文件将保留在原处。'
    return 0
}

$Transaction = $null
$Committed = $false
$KeepBackup = $false
$CreatedRoot = $false
$HasLock = $false
$Mutex = $null
$Changes = New-Object 'Collections.Generic.List[object]'
$NewDirectories = New-Object 'Collections.Generic.List[string]'
$PrivateFiles = @{}
$exitCode = 1
try {
    $InstallRoot = [IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($InstallRoot)).TrimEnd('\')
    if ($InstallRoot.Length -le [IO.Path]::GetPathRoot($InstallRoot).TrimEnd('\').Length -or
        $InstallRoot -eq $PSScriptRoot -or $InstallRoot.StartsWith($PSScriptRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $PSScriptRoot.StartsWith($InstallRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw '请为安装目录选择与解压包分开的专用子目录。'
    }
    Assert-PlainPath $InstallRoot
    Write-Message "Visio 制图工具（当前用户）。目录：$InstallRoot"
    $lockHash = [Security.Cryptography.SHA256]::Create()
    try { $key = [BitConverter]::ToString($lockHash.ComputeHash($Utf8.GetBytes($InstallRoot.ToUpperInvariant()))).Replace('-','') }
    finally { $lockHash.Dispose() }
    $Mutex = New-Object Threading.Mutex($false, ('Local\VisioDiagrams-' + $key))
    try { $HasLock = $Mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $HasLock = $true }
    if (-not $HasLock) { throw '另一个安装或卸载程序正在运行，请等待它结束再重试。' }

    $LedgerPath = Child $InstallRoot 'installation.json'
    $ReportPath = Child $InstallRoot 'capabilities.json'
    $Record = $null
    $Owned = @{}
    if ([IO.File]::Exists($LedgerPath)) {
        $Record = Read-JsonFile $LedgerPath
        $Owned = Read-OwnedFiles $Record
    } elseif ($Action -eq 'Install' -and [IO.Directory]::Exists($InstallRoot) -and [IO.Directory]::GetFileSystemEntries($InstallRoot).Length) {
        throw "目录已有文件且缺少安装记录。请保留原目录，并为安装选择新的空目录：$InstallRoot"
    }
    if ($Action -eq 'Uninstall' -and -not $Record) {
        Write-Message '此位置没有本工具的安装记录，现有文件已保留。'
        $exitCode = 0
    } else {
        if ($Action -eq 'Install') {
            $architecture = $env:PROCESSOR_ARCHITECTURE
            if ($env:PROCESSOR_ARCHITEW6432) { $architecture = $env:PROCESSOR_ARCHITEW6432 }
            if (-not [Environment]::Is64BitOperatingSystem -or $architecture -ne 'AMD64' -or [Environment]::OSVersion.Version.Major -lt 10) {
                throw '此安装包需要 64 位 Windows 10 或 Windows 11 的 x64 电脑。'
            }
        }
        $Manifest = Verify-Package
        . (Child $PSScriptRoot 'integration.ps1')
        if (-not [IO.Directory]::Exists($InstallRoot)) {
            $null = [IO.Directory]::CreateDirectory($InstallRoot)
            $CreatedRoot = $true
        }
        $Transaction = Child $InstallRoot ('.v-' + [guid]::NewGuid().ToString('N').Substring(0,12))
        if ([IO.Directory]::Exists($Transaction)) { $Transaction = $null; throw '临时目录重名，请重新运行安装程序。' }
        $Stage = Child $Transaction 's'
        $Backup = Child $Transaction 'b'
        $null = [IO.Directory]::CreateDirectory($Stage)
        $null = [IO.Directory]::CreateDirectory($Backup)
        if ($Action -eq 'Install') { $exitCode = Install-Product }
        else { $exitCode = Uninstall-Product }
    }
} catch {
    Write-Message "操作失败：$($_.Exception.Message)"
    if (-not $Committed -and $Changes.Count) {
        try { Undo-Changes; Write-Message '已还原本次更改的程序文件。' }
        catch {
            $KeepBackup = $true
            Write-Message "还原未完成。请保留此目录和日志供恢复：$Transaction。$($_.Exception.Message)"
        }
    }
    $exitCode = 1
} finally {
    try {
        if ($Transaction -and -not $KeepBackup) { Remove-Transaction }
        if ($HasLock -and ($CreatedRoot -or ($Committed -and $Action -eq 'Uninstall'))) { Remove-Empty $InstallRoot }
    } catch { Write-Message "清理未完成，请保留日志：$($_.Exception.Message)"; $exitCode = 1 }
    if ($HasLock) { $Mutex.ReleaseMutex() }
    if ($Mutex) { $Mutex.Dispose() }
    Write-Host "日志：$Log"
}
exit $exitCode
