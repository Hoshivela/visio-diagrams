# Style a new copy of a single-page Visio Core workflow using desktop Visio.
# NoteNode selects folded boxes; EndNode selects blank circular end markers.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Vsdx,
    [Parameter(Mandatory)][string]$Planned,
    [Parameter(Mandatory)][string]$Output,
    [string[]]$NoteNode = @(),
    [string[]]$EndNode = @(),
    [switch]$PreserveColors
)
$ErrorActionPreference = 'Stop'
$source = (Get-Item -LiteralPath $Vsdx).FullName
$planFile = (Get-Item -LiteralPath $Planned).FullName
$dest = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
if ([IO.Path]::GetExtension($source) -ine '.vsdx' -or -not [IO.File]::Exists($source)) {
    throw "Expected a source VSDX file: $source"
}
if ([IO.Path]::GetExtension($dest) -ine '.vsdx' -or -not [IO.Directory]::Exists([IO.Path]::GetDirectoryName($dest))) {
    throw "Output must be a .vsdx in an existing directory: $dest"
}
if (Test-Path -LiteralPath $dest) { throw "Refusing to overwrite: $dest" }
$receiptPath = [IO.Path]::ChangeExtension($dest, '.style.json')
if (Test-Path -LiteralPath $receiptPath) { throw "Receipt already exists: $receiptPath" }
$sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
$planHash = (Get-FileHash -LiteralPath $planFile -Algorithm SHA256).Hash
$plan = Get-Content -LiteralPath $planFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ($plan.nodes -isnot [array] -or -not $plan.nodes.Count -or $plan.edges -isnot [array]) {
    throw "Expected nodes and edges arrays in planned scene: $planFile"
}
$nodes = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
foreach ($node in $plan.nodes) {
    if ([string]::IsNullOrWhiteSpace($node.id) -or $nodes.ContainsKey($node.id)) {
        throw "Missing or duplicate planned node ID: '$($node.id)'"
    }
    $nodes.Add($node.id, $node)
}
foreach ($id in $NoteNode) {
    if (-not $nodes.ContainsKey($id) -or $nodes[$id].kind -cne 'box') {
        throw "NoteNode must identify a planned box: '$id'"
    }
}
foreach ($id in $EndNode) {
    $node = $nodes[$id]
    if ($null -eq $node -or $node.kind -cne 'ellipse' -or
        -not [string]::IsNullOrWhiteSpace($node.text) -or -not [string]::IsNullOrWhiteSpace($node.detail)) {
        throw "EndNode must identify a blank planned ellipse: '$id'"
    }
}
foreach ($edge in $plan.edges) {
    if (-not $nodes.ContainsKey([string]$edge.from) -or -not $nodes.ContainsKey([string]$edge.to)) {
        throw "Unknown endpoint on planned edge '$($edge.id)'"
    }
}

function Set-Cell($Shape, $Name, $Formula) { $Shape.CellsU($Name).FormulaU = $Formula }
function Set-Point($Shape, $Section, $Row, $X, $Y) {
    $Shape.CellsSRC($Section,$Row,0).FormulaU = $X
    $Shape.CellsSRC($Section,$Row,1).FormulaU = $Y
}
function Add-Outline($Shape) {
    # visSectionFirstComponent = 10. Start Geometry2 with a component row.
    if ($Shape.SectionExists(11,0)) { throw "Geometry2 already exists on $($Shape.NameU)" }
    [void]$Shape.AddSection(11)
    [void]$Shape.AddRow(11,0,$Shape.RowType(10,0))
    Set-Cell $Shape 'Geometry2.NoFill' '1'
}
function Add-Fold($Shape) {
    # The generated box has one MoveTo followed by four LineTo rows.
    if ($Shape.RowCount(10) -ne 6) { throw "Expected rectangular geometry on $($Shape.NameU)" }
    $move = $Shape.RowType(10,1)
    $line = $Shape.RowType(10,2)
    $cut = 'MIN(0.11 in,Width*0.25,Height*0.25)'
    Add-Outline $Shape
    [void]$Shape.AddRow(10,-1,$line)
    Set-Point $Shape 10 3 'Width' "Height-$cut"
    Set-Point $Shape 10 4 "Width-$cut" 'Height'
    Set-Point $Shape 10 5 '0 in' 'Height'
    Set-Point $Shape 10 6 '0 in' '0 in'
    $row = $Shape.AddRow(11,-1,$move)
    Set-Point $Shape 11 $row "Width-$cut" 'Height'
    $row = $Shape.AddRow(11,-1,$line)
    Set-Point $Shape 11 $row "Width-$cut" "Height-$cut"
    $row = $Shape.AddRow(11,-1,$line)
    Set-Point $Shape 11 $row 'Width' "Height-$cut"
}
function Add-EndRing($Shape) {
    if (-not [string]::IsNullOrWhiteSpace($Shape.Text) -or
        [Math]::Abs($Shape.CellsU('Width').ResultIU - $Shape.CellsU('Height').ResultIU) -gt .001 -or
        $Shape.RowCount(10) -ne 2) { throw "Expected a blank circle on $($Shape.NameU)" }
    Add-Outline $Shape
    [void]$Shape.AddRow(11,-1,$Shape.RowType(10,1))
    foreach ($cell in @{
        X='Width*0.5'; Y='Height*0.5'; A='Width*0.84'; B='Height*0.5'; C='Width*0.5'; D='Height*0.84'
    }.GetEnumerator()) {
        Set-Cell $Shape ('Geometry2.' + $cell.Key + '1') $cell.Value
    }
}

# Open only a private working copy. A same-directory rename publishes the
# complete result without overwriting any output created by a concurrent run.
$unique = [Guid]::NewGuid().ToString('N')
$local = [IO.Path]::Combine([IO.Path]::GetTempPath(), "visio-style-$unique.vsdx")
$pending = [IO.Path]::Combine([IO.Path]::GetDirectoryName($dest), ".visio-style-$unique.tmp")
$receiptTemp = "$pending.json"
$app = $null
$doc = $null
try {
    [IO.File]::Copy($source,$local)
    [IO.File]::SetAttributes($local,[IO.FileAttributes]::Normal)
    $app = New-Object -ComObject Visio.InvisibleApp
    $app.AlertResponse = 7
    $doc = $app.Documents.OpenEx($local,0)
    if ($doc.Pages.Count -ne 1) { throw 'Expected a single-page Visio Core workflow' }
    $page = $doc.Pages.Item(1)
    $nativeNodes = @($page.Shapes | Where-Object { $_.NameU -clike 'node_*' })
    $nativeEdges = @($page.Shapes | Where-Object { $_.NameU -cmatch '^edge_[0-9]+$' })
    if ($nativeNodes.Count -ne $plan.nodes.Count -or $nativeEdges.Count -ne $plan.edges.Count) {
        throw 'Planned node or connector count differs from the VSDX'
    }
    foreach ($node in $plan.nodes) {
        $s = $page.Shapes.ItemU("node_$($node.id)")
        if ($s.NameU -cne "node_$($node.id)") { throw "Node identity mismatch: $($node.id)" }
        if ($node.kind -cin @('text','container')) { continue }
        if (-not $PreserveColors) {
            Set-Cell $s 'FillForegnd' 'RGB(255,255,255)'
            Set-Cell $s 'LineColor' 'RGB(28,28,28)'
        }
        Set-Cell $s 'LineWeight' '0.65 pt'
        if ($node.id -cin $NoteNode) { Add-Fold $s }
        elseif ($node.id -cin $EndNode) { Add-EndRing $s }
        elseif ($node.kind -ceq 'box') { Set-Cell $s 'Rounding' '0.085 in' }
    }
    $index = 0
    foreach ($edge in $plan.edges) {
        $index++
        $s = $page.Shapes.ItemU("edge_$index")
        # Bind the numbered connector to its planned endpoints before styling.
        foreach ($side in @('from','to')) {
            $part = if ($side -eq 'from') { 9 } else { 12 }
            $links = @($s.Connects | Where-Object { $_.FromPart -eq $part })
            if ($links.Count -ne 1 -or $links[0].ToSheet.NameU -cne "node_$($edge.$side)") {
                throw "Planned $side endpoint differs on edge_$index ($($edge.id))"
            }
        }
        if (-not $PreserveColors) { Set-Cell $s 'LineColor' 'RGB(28,28,28)' }
        if ($edge.from -cin $NoteNode -or $edge.to -cin $NoteNode) {
            Set-Cell $s 'LineWeight' '0.5 pt'
        } else { Set-Cell $s 'LineWeight' '0.7 pt' }
    }
    [void]$doc.Save()
    $doc.Close()
    $doc = $null
    [IO.File]::Copy($local,$pending)
    if ((Get-FileHash -LiteralPath $source).Hash -ne $sourceHash -or
        (Get-FileHash -LiteralPath $planFile).Hash -ne $planHash) {
        throw 'Source drawing or planned scene changed during styling'
    }
    $styledHash = (Get-FileHash -LiteralPath $pending).Hash
    $receipt = [ordered]@{
        schema = 'visio.style/1'; operationId = $unique; status = 'STYLED'
        source = $source; sourceSha256 = $sourceHash; planned = $planFile; plannedSha256 = $planHash
        output = [IO.Path]::GetFileName($dest); outputSha256 = $styledHash
        preserveColors = [bool]$PreserveColors; noteNodes = $NoteNode; endNodes = $EndNode
        visioVersion = [string]$app.Version
    }
    [IO.File]::WriteAllText($receiptTemp, ($receipt | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
    [IO.File]::Move($pending,$dest)
    try { [IO.File]::Move($receiptTemp,$receiptPath) }
    catch {
        if ((Get-FileHash -LiteralPath $dest).Hash -eq $styledHash) { [IO.File]::Delete($dest) }
        throw
    }
    Write-Output $dest
} finally {
    try {
        if ($doc) { $doc.Saved = $true; $doc.Close() }
    } finally {
        try { if ($app) { $app.Quit() } }
        finally {
            [IO.File]::Delete($local)
            [IO.File]::Delete($pending)
            [IO.File]::Delete($receiptTemp)
        }
    }
}
