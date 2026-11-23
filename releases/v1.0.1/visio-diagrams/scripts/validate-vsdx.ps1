# Native open, save, reopen, inspection and edit evidence using desktop Visio.
param(
  [Parameter(Mandatory)][string]$Vsdx,
  [string]$Planned,
  [string]$Report,
  [string]$PreviewPath,
  [string]$RoundTripPath,
  [string]$MoveNode,
  [switch]$SkipMove,
  [ValidateRange(36,1200)][double]$ExportDpi,
  [switch]$RecordBaselineFonts,
  [object]$VisioApplication,
  [string]$Swimlanes
)
$ErrorActionPreference = 'Stop'
$Vsdx = (Resolve-Path -LiteralPath $Vsdx).ProviderPath
if (-not $Planned) { $Planned = [IO.Path]::ChangeExtension($Vsdx, '.planned.json') }
$plan = Get-Content -LiteralPath $Planned -Raw -Encoding utf8 | ConvertFrom-Json
$laneSpec = if ($Swimlanes) { Get-Content -LiteralPath $Swimlanes -Raw -Encoding utf8 | ConvertFrom-Json }
foreach ($path in @($Report, $PreviewPath, $RoundTripPath)) {
  if ($path -and (Test-Path -LiteralPath $path)) { throw "Refusing to overwrite: $path" }
}
$temporary = $null
if (-not $RoundTripPath) {
  $temporary = Join-Path ([IO.Path]::GetTempPath()) ('visio-roundtrip-' + [guid]::NewGuid() + '.vsdx')
  $RoundTripPath = $temporary
}
$RoundTripPath = [IO.Path]::GetFullPath($RoundTripPath)
$sourceHash = (Get-FileHash -LiteralPath $Vsdx).Hash
$result = [ordered]@{ status = 'FAIL'; source = $Vsdx; sourceSha256 = $sourceHash; scope = 'Desktop Visio COM' }
$generationPath = [IO.Path]::ChangeExtension($Vsdx, '.validation.json')
if (Test-Path -LiteralPath $generationPath -PathType Leaf) {
  $generation = Get-Content -LiteralPath $generationPath -Raw -Encoding UTF8 | ConvertFrom-Json
  if ($generation.identity.runId -and $generation.hashes.vsdx -eq $sourceHash) {
    $result.runId = $generation.identity.runId
  }
}

$family = if ($plan.fontFamily) { [string]$plan.fontFamily } else { 'Microsoft YaHei' }
$fontAliases = @($family) + @($plan.fontAliases | Where-Object { $_ })
if ($family -in @('Microsoft YaHei', ([string][char]0x5FAE + [char]0x8F6F + [char]0x96C5 + [char]0x9ED1))) {
  $fontAliases += @('Microsoft YaHei', ([string][char]0x5FAE + [char]0x8F6F + [char]0x96C5 + [char]0x9ED1))
}
$result.expectedFont = $family
$result.fontAliases = @($fontAliases | Sort-Object -Unique)
$result.fontVerification = if ($RecordBaselineFonts) { 'BASELINE_OBSERVATION' } else { 'REQUIRED' }
$result.environment = [ordered]@{ windows = [Environment]::OSVersion.Version.ToString()
                                  culture = (Get-Culture).Name; powershell = $PSVersionTable.PSVersion.ToString() }
$app = $VisioApplication
$ownsApp = $null -eq $app
$doc = $null

function Assert-Near([double]$Actual, [double]$Expected, [string]$Context, [double]$Tolerance = .001) {
  if ([Math]::Abs($Actual - $Expected) -gt $Tolerance) { throw "$Context actual=$Actual expected=$Expected" }
}
function Shape-Value($Shape, [string]$Cell) {
  return [double]$Shape.CellsU($Cell).ResultIU
}
function Page-Vertex($Line, [int]$Row) {
  $x = 0.0; $y = 0.0
  $Line.XYToPage([double]$Line.CellsSRC(10, $Row, 0).ResultIU,
                 [double]$Line.CellsSRC(10, $Row, 1).ResultIU, [ref]$x, [ref]$y)
  return @($x, $y)
}
function Read-Font($Fonts, $Cell) {
  # The live ID belongs to this document and may change at SaveAs or OpenEx.
  $id = [int]$Cell.ResultIU
  $fontName = if ($id -gt 0) { [string]$Fonts.ItemFromID($id).Name }
  return [ordered]@{ id = $id; name = $fontName; formula = [string]$Cell.FormulaU }
}
function Check-Text($Shape, [string]$Expected, [double]$Points, $Fonts, [string[]]$Aliases, [string]$Role,
                    $DetailNode = $null) {
  $lf = [string][char]10
  $cr = [string][char]13
  $actual = ([string]$Shape.Text).Replace($cr + $lf, $lf).Replace($cr, $lf)
  if ($actual -cne $Expected -and $actual -cne ($Expected + $lf)) {
    throw "Text mismatch: $($Shape.NameU) actual=[$actual] expected=[$Expected]"
  }
  $size = (Shape-Value $Shape 'Char.Size') * 72
  Assert-Near $size $Points "$($Shape.NameU) font pt" .01
  $runs = [Collections.Generic.List[Object]]::new()
  if ($Expected) {
    $characters = $Shape.Characters
    $count = [int]$characters.End
    if ($count -le 0) { throw "No text runs on $($Shape.NameU)" }
    $rows = @([int]$characters.CharPropsRow(0))
    $expectedStyles = @{}
    if ($DetailNode) {
      $titleEnd = ([string]$DetailNode.text).Length
      $rows = @(foreach ($isDetail in @($false, $true)) {
        $start = if ($isDetail) { $titleEnd + 1 } else { 0 }
        $end = if ($isDetail) { $Expected.Length } else { $titleEnd }
        $characters.End = $end
        $characters.Begin = $start
        $oneRow = [int]$characters.CharPropsRow(0)
        $sectionRows = @($oneRow)
        if ($oneRow -lt 0) {
          $sectionRows = @(for ($i = $start; $i -lt $end; $i++) {
            $characters.End = $i + 1
            $characters.Begin = $i
            [int]$characters.CharPropsRow(0)
          }) | Sort-Object -Unique
        }
        foreach ($row in $sectionRows) {
          if ($expectedStyles.ContainsKey($row) -and $expectedStyles[$row] -ne $isDetail) {
            throw "Title and detail unexpectedly share a character row on $($Shape.NameU)"
          }
          $expectedStyles[$row] = $isDetail
          $row
        }
      }) | Sort-Object -Unique
    } elseif ($rows[0] -lt 0) {
      $rows = @(for ($i = 0; $i -lt $count; $i++) {
        $characters.End = $i + 1
        $characters.Begin = $i
        [int]$characters.CharPropsRow(0)
      }) | Sort-Object -Unique
    }
    foreach ($row in $rows) {
      if ($row -lt 0) { throw "Unresolved character formatting on $($Shape.NameU)" }
      # Visio Character section 3, Font 0, AsianFont 51, Size 7.
      $latin = Read-Font $Fonts ($Shape.CellsSRC(3, $row, 0))
      $asian = Read-Font $Fonts ($Shape.CellsSRC(3, $row, 51))
      $runSize = [double]$Shape.CellsSRC(3, $row, 7).ResultIU * 72
      $fontScale = [double]$Shape.CellsSRC(3, $row, 5).ResultIU
      if ($DetailNode) { Assert-Near $fontScale 1 "$($Shape.NameU) run $row horizontal font scale" .001 }
      $runPoints = $Points
      $style = [int]$Shape.CellsSRC(3, $row, 2).ResultIU
      if ($DetailNode -and $expectedStyles.ContainsKey($row)) {
        $isDetail = $expectedStyles[$row]
        if ($isDetail) {
          $runPoints = if ($DetailNode.detailFontSize) { [double]$DetailNode.detailFontSize } else { 10.0 }
        }
        $bold = -not $isDetail -and [bool]$DetailNode.bold
        if (($style -band 1) -ne [int]$bold) { throw "Unexpected text weight on $($Shape.NameU), row $row" }
      }
      Assert-Near $runSize $runPoints "$($Shape.NameU) run $row at pt" .01
      $familyMatches = $latin.name -in $Aliases -and $asian.name -in $Aliases
      if (-not $familyMatches -and -not $RecordBaselineFonts) {
        throw "Font mismatch on $($Shape.NameU) row $row. Latin=$($latin.name) ($($latin.id)), Asian=$($asian.name) ($($asian.id)), expected $($Aliases -join ' / ')"
      }
      $runs.Add([ordered]@{ row = $row; latin = $latin; asian = $asian; actualPt = $runSize; fontScale = $fontScale; bold = [bool]($style -band 1); familyMatches = $familyMatches })
    }
  }
  return [ordered]@{ shape = [string]$Shape.NameU; role = $Role; text = $Expected
                     expectedPt = $Points; actualPt = $size; runs = $runs.ToArray() }
}
function Check-Frame($Shape, $Frame, [double]$PageHeight) {
  Assert-Near (Shape-Value $Shape 'PinX') ([double]$Frame.x + [double]$Frame.w / 2) "$($Shape.NameU) PinX"
  Assert-Near (Shape-Value $Shape 'PinY') ($PageHeight - [double]$Frame.y - [double]$Frame.h / 2) "$($Shape.NameU) PinY"
  Assert-Near (Shape-Value $Shape 'Width') ([double]$Frame.w) "$($Shape.NameU) Width"
  Assert-Near (Shape-Value $Shape 'Height') ([double]$Frame.h) "$($Shape.NameU) Height"
}
function Check-TextFrame($Shape, $Frame) {
  if ($Frame) {
    Assert-Near (Shape-Value $Shape 'TxtWidth') ([double]$Frame.w) "$($Shape.NameU) TxtWidth"
    Assert-Near (Shape-Value $Shape 'TxtHeight') ([double]$Frame.h) "$($Shape.NameU) TxtHeight"
  }
}
function Read-GlueIndex($Page) {
  # Snapshot each connection once. A drag, save or reopen needs a fresh index.
  $index = @{}
  foreach ($link in $Page.Connects) {
    $key = '{0}/{1}' -f $link.FromSheet.ID, $link.FromPart
    if ($index.ContainsKey($key)) { throw "Multiple connections on endpoint $key" }
    $index[$key] = @([int]$link.ToSheet.ID, [string]$link.ToCell.Name)
  }
  return $index
}
function Endpoint-Glue($Index, $Line, [string]$Cell, $Node) {
  $part = if ($Cell -eq 'BeginX') { 9 } else { 12 }
  $link = $Index[('{0}/{1}' -f $Line.ID, $part)]
  if ($null -eq $link -or $link[0] -ne $Node.ID) {
    throw "Missing or incorrect $Cell glue: $($Line.NameU) -> $($Node.NameU)"
  }
  $targetCell = $link[1]
  if ($targetCell -notmatch '^Connections\.X[0-9]+$') { throw "Not a connection-point glue: $targetCell" }
  return $targetCell
}
function Swimlane-Frame($Shape) {
  # The horizontal lane master rotates the header shape. Return its page-space
  # bounds in left, bottom, right, top order for both master orientations.
  $x1=$y1=$x2=$y2=0.0
  $Shape.XYToPage(0,0,[ref]$x1,[ref]$y1)
  $Shape.XYToPage((Shape-Value $Shape 'Width'),(Shape-Value $Shape 'Height'),[ref]$x2,[ref]$y2)
  return [Math]::Min($x1,$x2),[Math]::Min($y1,$y2),[Math]::Max($x1,$x2),[Math]::Max($y1,$y2)
}
function Check-Swimlanes($Page, $Plan, $Spec) {
  $orientation = if ($null -eq $Spec.orientation) { 'horizontal' } else { $Spec.orientation }
  if ($orientation -cnotin @('horizontal','vertical')) { throw "Invalid swimlane orientation: $orientation" }
  $vertical = $orientation -ceq 'vertical'
  $expectedDirection = if ($vertical) { 0 } else { 2 }
  $expectedMaster = if ($vertical) { 'Swimlane (vertical)' } else { 'Swimlane' }
  $list = $Page.Shapes.ItemU('native_swimlane_list')
  if ($list.Master.NameU -cne 'Swimlane List') { throw 'Expected a native Swimlane List master' }
  $listDirection = Shape-Value $list 'User.msvSDListDirection'
  if ($listDirection -ne $expectedDirection) { throw "Native swimlane list direction differs for $orientation" }
  $ids = @($list.ContainerProperties.GetListMembers())
  if ($ids.Count -ne $Spec.lanes.Count) { throw 'Incorrect number of native swimlanes in list' }
  $containers = @($Page.GetContainers(0))
  $owners = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
  for ($i=0; $i -lt $Spec.lanes.Count; $i++) {
    $lane = $Spec.lanes[$i]
    $shape = $Page.Shapes.ItemU("lane_$($lane.id)")
    if ($ids[$i] -ne $shape.ID -or $containers -notcontains $shape.ID -or $shape.Master.NameU -cne $expectedMaster) {
      throw "Incorrect native swimlane master, order or container for $($lane.id)"
    }
    $expectedTitle = $lane.title.Trim() -replace '[\r\n]+',''
    $headers = @(foreach ($child in $shape.Shapes) {
      if ((([string]$child.Text).Trim() -replace '[\r\n]+','') -ceq $expectedTitle) { $child }
    })
    if ($headers.Count -ne 1) { throw "Swimlane title differs for $($lane.id)" }
    $heading = $headers[0]
    $pageHeight = [double]$Plan.page.height
    if ($vertical) {
      $expectedLeft=$lane.left; $expectedRight=$lane.right
      $expectedTop=$pageHeight-$Spec.top; $expectedBottom=$pageHeight-$Spec.bottom
      $bodyLeft=$expectedLeft; $bodyTop=$pageHeight-$Spec.headerBottom
      $headerRight=$expectedRight; $headerBottom=$bodyTop
    } else {
      $expectedLeft=$Spec.left; $expectedRight=$Spec.right
      $expectedTop=$pageHeight-$lane.top; $expectedBottom=$pageHeight-$lane.bottom
      $bodyLeft=$Spec.headerRight; $bodyTop=$expectedTop
      $headerRight=$bodyLeft; $headerBottom=$expectedBottom
    }
    $left,$bottom,$right,$top = Swimlane-Frame $shape
    Assert-Near $left $expectedLeft "Lane $($lane.id) left"
    Assert-Near $right $expectedRight "Lane $($lane.id) right"
    Assert-Near $top $expectedTop "Lane $($lane.id) top"
    Assert-Near $bottom $expectedBottom "Lane $($lane.id) bottom"
    $hl,$hb,$hr,$ht = Swimlane-Frame $heading
    Assert-Near $hl $expectedLeft "Lane $($lane.id) heading left"
    Assert-Near $hr $headerRight "Lane $($lane.id) heading right"
    Assert-Near $ht $expectedTop "Lane $($lane.id) heading top"
    Assert-Near $hb $headerBottom "Lane $($lane.id) heading bottom"
    $textAngle = Shape-Value $heading 'TxtAngle'
    $x1=$y1=$x2=$y2=0.0
    $heading.XYToPage(0,0,[ref]$x1,[ref]$y1)
    $heading.XYToPage([Math]::Cos($textAngle),[Math]::Sin($textAngle),[ref]$x2,[ref]$y2)
    $pageTextAngle = [Math]::Atan2(($y2-$y1),($x2-$x1))
    Assert-Near $pageTextAngle 0 "Lane $($lane.id) heading text angle"
    $members = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($memberId in $shape.ContainerProperties.GetMemberShapes(0)) {
      $member = $Page.Shapes.ItemFromID([int]$memberId)
      $name = [string]$member.NameU
      if (-not $name.StartsWith('node_', [StringComparison]::Ordinal)) { continue }
      $id = $name.Substring(5)
      if (-not $owners.Add($id)) { throw "Multiple swimlanes own node $id" }
      $null = $members.Add($id)
      $x1,$y1,$x2,$y2 = Swimlane-Frame $member
      if ($x1 -lt $bodyLeft-.001 -or $x2 -gt $right+.001 -or $y1 -lt $bottom-.001 -or $y2 -gt $bodyTop+.001) {
        throw "Node $id extends outside the body of lane $($lane.id)"
      }
    }
    if (-not $members.SetEquals([string[]]@($lane.members))) { throw "Swimlane membership differs for $($lane.id)" }
    [ordered]@{ id=$lane.id; title=$lane.title; master=$shape.Master.NameU; nativeId=$shape.ID
                orientation=$orientation; listDirection=$listDirection; headingTextAngleRad=$pageTextAngle
                members=@($lane.members); frameIn=@($left,$bottom,$right,$top); headingFrameIn=@($hl,$hb,$hr,$ht) }
  }
  if ($owners.Count -ne $Plan.nodes.Count) { throw 'Missing swimlane ownership for one or more nodes' }
}
function Check-Drawing($Doc, $Plan) {
  $Page = $Doc.Pages.Item(1)
  $fonts = $Doc.Fonts
  $texts = [Collections.Generic.List[Object]]::new()
  $memberships = [Collections.Generic.List[Object]]::new()
  $height = Shape-Value $Page.PageSheet 'PageHeight'
  Assert-Near $height ([double]$Plan.page.height) 'PageHeight'
  Assert-Near (Shape-Value $Page.PageSheet 'PageWidth') ([double]$Plan.page.width) 'PageWidth'
  $expectedShapes = $Plan.nodes.Count + $Plan.edges.Count + @($Plan.edges | Where-Object {$_.label -and $_.text}).Count
  if ($Plan.page.title) { $expectedShapes++ }
  $expectedShapes += $Plan.groups.Count
  if ($laneSpec) { $expectedShapes += $laneSpec.lanes.Count + 1 }
  if ($Page.Shapes.Count -ne $expectedShapes -or $Page.Connects.Count -ne 2 * $Plan.edges.Count) { throw 'Shape or native glue counts differ' }
  foreach ($n in $Plan.nodes) {
    $shape = $Page.Shapes.ItemU("node_$($n.id)")
    $points = if ($n.fontSize) { [double]$n.fontSize } else { 11.0 }
    $role = if ($n.kind -eq 'container') { 'container' } else { 'node' }
    if ($n.detail) {
      $texts.Add((Check-Text $shape ($n.text + [char]10 + $n.detail) $points $fonts $fontAliases $role $n))
    } else {
      $texts.Add((Check-Text $shape $n.text $points $fonts $fontAliases $role))
    }
    Check-Frame $shape $n $height
    Check-TextFrame $shape $n._textFrame
    $master = if ($n.master) { $n.master } else {
      switch ($n.kind) { 'box' {'Process'} 'diamond' {'Decision'} 'terminator' {'Start/End'} }
    }
    if ($master -and $shape.Master.NameU -cne $master) { throw "Master mismatch for $($n.id)" }
    if ($n.kind -eq 'container') {
      $expected = @($Plan.nodes | Where-Object {$_.parent -eq $n.id} | ForEach-Object {[string]$_.id} | Sort-Object)
      $actualMembers = @($shape.ContainerProperties.GetMemberShapes(16) |
        ForEach-Object { [string]$Page.Shapes.ItemFromID([int]$_).NameU } |
        Where-Object { $_ -like 'node_*' } | ForEach-Object {$_.Substring(5)} | Sort-Object)
      if (($actualMembers -join ',') -cne ($expected -join ',')) { throw "Explicit container membership differs for $($n.id)" }
      $memberships.Add(@{id = $n.id; members = $actualMembers})
    }
  }
  foreach ($g in $Plan.groups) {
    $shape = $Page.Shapes.ItemU("group_$($g.id)")
    $texts.Add((Check-Text $shape $g.text $g.fontSize $fonts $fontAliases 'visual-group'))
    Check-Frame $shape $g $height
    Check-TextFrame $shape $g._textFrame
  }
  if ($Plan.page.title) {
    $points = if ($Plan.page.titleFontSize) { [double]$Plan.page.titleFontSize } else { 16.0 }
    $shape = $Page.Shapes.ItemU('diagram_title')
    $texts.Add((Check-Text $shape $Plan.page.title $points $fonts $fontAliases 'title'))
    Check-TextFrame $shape $Plan.page._title
  }
  $vertices = 0; $maxDeviation = 0.0; $index = 0
  $glueIndex = Read-GlueIndex $Page
  foreach ($e in $Plan.edges) {
    $index++
    $line = $Page.Shapes.ItemU("edge_$index")
    if (-not $line.OneD -or $line.Master.NameU -cne 'Dynamic connector') { throw "Not a native Dynamic connector: edge_$index" }
    $points = if ($e.fontSize) { [double]$e.fontSize } else { 10.0 }
    if ($e.label -and $e.text) {
      $shape = $Page.Shapes.ItemU("edge_label_$index")
      $texts.Add((Check-Text $shape $e.text $points $fonts $fontAliases 'edge-label'))
      Check-Frame $shape $e.label $height
      Check-TextFrame $shape $e.label
      $null = Check-Text $line '' $points $fonts $fontAliases 'connector'
    } else {
      $texts.Add((Check-Text $line $e.text $points $fonts $fontAliases 'connector'))
      Check-TextFrame $line $e._inlineLabel
    }
    $null = Endpoint-Glue $glueIndex $line 'BeginX' ($Page.Shapes.ItemU("node_$($e.from)"))
    $null = Endpoint-Glue $glueIndex $line 'EndX' ($Page.Shapes.ItemU("node_$($e.to)"))
    $want = @($e.routePoints)
    if ($want.Count -lt 2) { throw "No resolved path for edge_$index" }
    if ($line.RowCount(10) -ne $want.Count + 1) {
      throw "Geometry rows $($line.RowCount(10)), expected $($want.Count + 1) on edge_$index"
    }
    for ($i = 0; $i -lt $want.Count; $i++) {
      $p = @(Page-Vertex $line ($i + 1))
      $dx = [Math]::Abs($p[0] - [double]$want[$i][0])
      $dy = [Math]::Abs($height - $p[1] - [double]$want[$i][1])
      $maxDeviation = [Math]::Max($maxDeviation, [Math]::Max($dx, $dy))
      if ($dx -gt .001 -or $dy -gt .001) { throw "Route mismatch: edge_$index vertex $i actual=[$p] expected=$($want[$i] -join ',') in top-down coordinates" }
      $vertices++
    }
  }
  $reading = [ordered]@{ pageIn = @((Shape-Value $Page.PageSheet 'PageWidth'), $height)
                     shapes = $Page.Shapes.Count; connects = $Page.Connects.Count; routeVertices = $vertices
                     maxRouteDeviationIn = $maxDeviation; texts = $texts.ToArray(); containers = $memberships.ToArray() }
  if ($laneSpec) {
    $reading.swimlanes = @(Check-Swimlanes $Page $Plan $laneSpec)
    $reading.businessEndpoints = $glueIndex.Count
  }
  return $reading
}

function Export-Preview($Page, [string]$Path, [double]$Dpi, $App, $Document) {
  if (-not $Dpi) {
    $Page.Export($Path)
    return
  }
  $settings = $App.Settings
  $mode = $units = $sizeMode = $sizeUnits = 0
  $x = $y = $w = $h = 0.0
  $settings.GetRasterExportResolution([ref]$mode, [ref]$x, [ref]$y, [ref]$units)
  $settings.GetRasterExportSize([ref]$sizeMode, [ref]$w, [ref]$h, [ref]$sizeUnits)
  $scope = $null
  $services = $Document.DiagramServicesEnabled
  try {
    $settings.SetRasterExportResolution(3, $Dpi, $Dpi, 0)
    $settings.SetRasterExportSize(2, 0, 0, 0)
    # A temporary backdrop sets the export bounds to the entire page.
    $scope = $App.BeginUndoScope('Export full page')
    $Document.DiagramServicesEnabled = 0
    $width = Shape-Value $Page.PageSheet 'PageWidth'
    $height = Shape-Value $Page.PageSheet 'PageHeight'
    $background = $Page.DrawRectangle(0, 0, $width, $height)
    $background.CellsU('FillForegnd').FormulaU = 'RGB(255,255,255)'
    if ($laneSpec) {
      # A filled shape can obscure swimlanes even after SendToBack.
      $background.CellsU('FillPattern').FormulaU = '0'
      $background.CellsU('LineColor').FormulaU = 'RGB(255,255,255)'
      $background.CellsU('LineWeight').FormulaU = '0 in'
      $background.CellsU('LinePattern').FormulaU = '1'
    } else {
      $background.CellsU('FillPattern').FormulaU = '1'
      $background.CellsU('LinePattern').FormulaU = '0'
    }
    $background.CellsU('ShdwPattern').FormulaU = '0'
    $background.SendToBack()
    $Page.Export($Path)
  } finally {
    try {
      if ($null -ne $scope) { $App.EndUndoScope($scope, $false) }
      $Document.DiagramServicesEnabled = $services
    } finally {
      $settings.SetRasterExportSize($sizeMode, $w, $h, $sizeUnits)
      $settings.SetRasterExportResolution($mode, $x, $y, $units)
    }
  }
}

try {
  if ($ownsApp) { $app = New-Object -ComObject Visio.InvisibleApp }
  $app.AlertResponse = 7
  $result.visioVersion = [string]$app.Version
  $result.visioBuild = (Get-Item -LiteralPath (Join-Path $app.Path 'VISIO.EXE')).VersionInfo.ProductVersion
  $result.stage = 'open'
  $doc = $app.Documents.OpenEx($Vsdx, 2)
  $result.opened = Check-Drawing $doc $plan
  $result.stage = 'save'
  $doc.SaveAs($RoundTripPath) | Out-Null
  $result.stage = 'saved'
  $result.afterSave = Check-Drawing $doc $plan
  $doc.Close()
  $doc = $null
  if (-not (Test-Path -LiteralPath $RoundTripPath -PathType Leaf)) { throw 'Missing saved VSDX' }
  $result.savedSha256 = (Get-FileHash -LiteralPath $RoundTripPath).Hash
  if (-not $temporary) { $result.saved = $RoundTripPath }
  $result.stage = 'reopen'
  $doc = $app.Documents.OpenEx($RoundTripPath, 0)
  $pg = $doc.Pages.Item(1)
  $result.reopened = Check-Drawing $doc $plan
  if ($PreviewPath) {
    $result.stage = 'export'
    $PreviewPath = [IO.Path]::GetFullPath($PreviewPath)
    Export-Preview $pg $PreviewPath $ExportDpi $app $doc
    if ($ExportDpi) {
      $result.previewDpi = $ExportDpi
      $result.previewBounds = 'FULL_PAGE'
      if ($pg.Shapes.Count -ne $result.reopened.shapes) { throw 'Export background was not rolled back' }
    }
    if (-not (Test-Path -LiteralPath $PreviewPath -PathType Leaf)) { throw 'Missing Visio preview' }
    $result.preview = $PreviewPath
    $result.previewSha256 = (Get-FileHash -LiteralPath $PreviewPath).Hash
  }
  if (($MoveNode -or $plan.edges.Count) -and -not $SkipMove) {
    $result.stage = 'move'
    if (-not $MoveNode) { $MoveNode = [string]$plan.edges[0].from }
    $node = $pg.Shapes.ItemU("node_$MoveNode")
    $affectedNodes = @($MoveNode) + @($plan.nodes | Where-Object { $_.parent -ceq $MoveNode } | ForEach-Object { $_.id })
    $beforeX = Shape-Value $node 'PinX'
    $beforeY = Shape-Value $node 'PinY'
    $before = [Collections.Generic.List[Object]]::new()
    $glueIndex = Read-GlueIndex $pg
    $index = 0
    foreach ($edge in $plan.edges) {
      $index++
      foreach ($end in @('from', 'to')) {
        if ($affectedNodes -cnotcontains $edge.$end) { continue }
        $side = if ($end -eq 'from') { 'Begin' } else { 'End' }
        $line = $pg.Shapes.ItemU("edge_$index")
        $xName = $side + 'X'; $yName = $side + 'Y'
        $target = $pg.Shapes.ItemU("node_$($edge.$end)")
        $glue = Endpoint-Glue $glueIndex $line $xName $target
        $before.Add(@{ line = $line; target = $target; xName = $xName; yName = $yName; glue = $glue
                       x = (Shape-Value $line $xName); y = (Shape-Value $line $yName) })
      }
    }
    $doc.DiagramServicesEnabled = 2
    $selection = $pg.CreateSelection(0)
    $selection.Select($node, 2)
    $selection.Move(.18, .12)
    $actualX = (Shape-Value $node 'PinX') - $beforeX
    $actualY = (Shape-Value $node 'PinY') - $beforeY
    $result.move = [ordered]@{ node = $MoveNode; before = @($beforeX, $beforeY)
                              actualNodeDeltaIn = @($actualX, $actualY); endpoints = @() }
    Assert-Near $actualX .18 'Node move X'
    Assert-Near $actualY .12 'Node move Y'
    $result.stage = 'glue-after-move'
    $glueIndex = Read-GlueIndex $pg
    foreach ($original in $before) {
      $line = $original.line
      $glue = Endpoint-Glue $glueIndex $line $original.xName $original.target
      if ($glue -cne $original.glue) { throw "Moved endpoint changed connection point: $($line.NameU)" }
      $x = Shape-Value $line $original.xName
      $y = Shape-Value $line $original.yName
      Assert-Near ($x - $original.x) $actualX "$($line.NameU) moved endpoint X"
      Assert-Near ($y - $original.y) $actualY "$($line.NameU) moved endpoint Y"
      # Visio may insert bends after edit. Check its current visible end vertex.
      $row = if ($original.xName -eq 'BeginX') { 1 } else { $line.RowCount(10) - 1 }
      $vertex = @(Page-Vertex $line $row)
      Assert-Near $vertex[0] $x "$($line.NameU) moved geometry X"
      Assert-Near $vertex[1] $y "$($line.NameU) moved geometry Y"
      $result.move.endpoints += @{edge = $line.NameU; endpoint = $original.xName; glue = $glue
                                  deltaIn = @(($x - $original.x), ($y - $original.y)); geometryPoint = $vertex
                                  currentVertex = $row}
    }
    $result.move.checkedEndpoints = $result.move.endpoints.Count
    $result.move.endpointStatus = if ($before.Count) { 'PASS' } else { 'NOT_APPLICABLE' }
  }
  if ((Get-FileHash -LiteralPath $Vsdx).Hash -cne $sourceHash) { throw 'Source was modified by the acceptance run' }
  $result.stage = 'finished'
  if (-not $RecordBaselineFonts) { $result.fontVerification = 'PASS' }
  $result.status = 'PASS'
} catch {
  $result.error = $_.Exception.Message
  $result.errorLocation = $_.InvocationInfo.PositionMessage
} finally {
  if ($doc) { $doc.Saved = $true; $doc.Close() }
  if ($app -and $ownsApp) { $app.Quit() }
  if ($temporary -and (Test-Path -LiteralPath $temporary)) { Remove-Item -LiteralPath $temporary }
}
$json = $result | ConvertTo-Json -Depth 10
if ($Report) { [IO.File]::WriteAllText([IO.Path]::GetFullPath($Report), $json, [Text.UTF8Encoding]::new($false)) }
Write-Output $json
if ($result.status -ne 'PASS') {
  if ($VisioApplication) { throw $result.error }
  exit 1
}
