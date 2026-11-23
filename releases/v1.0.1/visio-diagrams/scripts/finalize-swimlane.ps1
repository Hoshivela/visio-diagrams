# Register native swimlanes and exercise the core acceptance checks in the same private Visio session.
param(
  [Parameter(Mandatory)][string]$Source,
  [Parameter(Mandatory)][string]$Drawing,
  [Parameter(Mandatory)][string]$Planned,
  [Parameter(Mandatory)][string]$Swimlanes,
  [Parameter(Mandatory)][string]$Preview,
  [Parameter(Mandatory)][string]$Report,
  [Parameter(Mandatory)][string]$Validator
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
# A Windows PowerShell process started by PowerShell 7 can inherit only the newer module paths.
$env:PSModulePath = (Join-Path $PSHOME 'Modules') + [IO.Path]::PathSeparator + $env:PSModulePath
$spec = Get-Content -LiteralPath $Swimlanes -Encoding UTF8 -Raw | ConvertFrom-Json
$plan = Get-Content -LiteralPath $Planned -Encoding UTF8 -Raw | ConvertFrom-Json
foreach ($path in @($Drawing, $Preview, $Report)) {
  if (Test-Path -LiteralPath $path) { throw "Output already exists: $path" }
}

function Register-Lanes($Page, $Spec) {
  $list = $Page.Shapes.ItemU('native_swimlane_list').ContainerProperties
  for ($i = 0; $i -lt $Spec.lanes.Count; $i++) {
    $row = $Spec.lanes[$i]
    $shape = $Page.Shapes.ItemU('lane_' + $row.id)
    if (@($list.GetListMembers()) -notcontains [int]$shape.ID) {
      $list.InsertListMember($shape, $i + 1)
    }
    $container = $shape.ContainerProperties
    $members = @($container.GetMemberShapes(0))
    foreach ($id in $row.members) {
      $node = $Page.Shapes.ItemU('node_' + $id)
      if ($members -notcontains [int]$node.ID) { $container.AddMember($node, 2) }
    }
  }
}

function Test-PreviewBands($Bitmap, $Spec, $Plan) {
  $sx = $Bitmap.Width / $Spec.page.width
  $sy = $Bitmap.Height / $Spec.page.height
  if ([Math]::Abs($sx / $sy - 1) -gt .012) { throw 'Preview does not match the page aspect ratio.' }
  # Exclude business frames when locating exposed background. Text, arrowheads and
  # sub-pixel boundaries may cover isolated samples, so inspect several on each scan.
  $frames = @($Plan.nodes) + @($Plan.edges | ForEach-Object { if ($_.label) { $_.label } })
  $result = [Collections.Generic.List[object]]::new()
  $vertical = $Spec.orientation -ceq 'vertical'
  foreach ($lane in $Spec.lanes) {
    if ($vertical) {
      $laneStart=$lane.left; $laneEnd=$lane.right
      $tableStart=$Spec.top; $headerEnd=$Spec.headerBottom; $tableEnd=$Spec.bottom
    } else {
      $laneStart=$lane.top; $laneEnd=$lane.bottom
      $tableStart=$Spec.left; $headerEnd=$Spec.headerRight; $tableEnd=$Spec.right
    }
    $bands = @(@{name='header'; start=$tableStart; end=$headerEnd; color=$lane.headerFill},
               @{name='body'; start=$headerEnd; end=$tableEnd; color=$lane.fill})
    foreach ($band in $bands) {
      $want = [Drawing.ColorTranslator]::FromHtml($band.color)
      $tested = $matched = 0
      for ($i = 0; $i -lt 24; $i++) {
        $along = $band.start + ($i + .5) * ($band.end - $band.start) / 24
        $exposed = $false
        foreach ($fraction in @(.045,.955,.15,.85,.35,.65)) {
          $across = $laneStart + ($laneEnd - $laneStart) * $fraction
          if ($vertical) { $x=$across; $y=$along } else { $x=$along; $y=$across }
          $covered = $false
          if ($band.name -eq 'body') {
            foreach ($f in $frames) {
              if ($x -ge $f.x - .01 -and $x -le $f.x + $f.w + .01 -and
                  $y -ge $f.y - .01 -and $y -le $f.y + $f.h + .01) { $covered = $true; break }
            }
          }
          if ($covered) { continue }
          $exposed = $true
          $pixel = $Bitmap.GetPixel([Math]::Min($Bitmap.Width-1, [Math]::Max(0,[int]($x*$sx))),
                                    [Math]::Min($Bitmap.Height-1, [Math]::Max(0,[int]($y*$sy))))
          if ($pixel.A -ge 248 -and [Math]::Abs($pixel.R-$want.R) -lt 8 -and
              [Math]::Abs($pixel.G-$want.G) -lt 8 -and [Math]::Abs($pixel.B-$want.B) -lt 8) {
            $matched++
            break
          }
        }
        if ($exposed) { $tested++ }
      }
      if ($tested -eq 0 -or $matched -lt .9 * $tested) {
        throw "Native preview did not show the $($band.name) of lane $($lane.id) ($matched/$tested scans)."
      }
      $counts = @{lane=$lane.id; part=$band.name}
      if ($vertical) { $counts.sampledRows=$tested; $counts.matchedRows=$matched }
      else { $counts.sampledColumns=$tested; $counts.matchedColumns=$matched }
      $result.Add($counts)
    }
  }
  return @{status='PASS'; width=$Bitmap.Width; height=$Bitmap.Height; bands=$result.ToArray()}
}

$timer = [Diagnostics.Stopwatch]::StartNew()
$phase = [ordered]@{}
$app = $null
$doc = $null
try {
  $app = New-Object -ComObject Visio.InvisibleApp
  $app.AlertResponse = 7
  $phase.startVisio = $timer.Elapsed.TotalSeconds
  $timer.Restart()
  $doc = $app.Documents.OpenEx((Resolve-Path -LiteralPath $Source).ProviderPath, 2)
  Register-Lanes $doc.Pages.Item(1) $spec
  $doc.SaveAs([IO.Path]::GetFullPath($Drawing)) | Out-Null
  $doc.Close()
  $doc = $null
  $phase.registerAndSave = $timer.Elapsed.TotalSeconds
  $timer.Restart()
  $native = (& $Validator -Vsdx $Drawing -Planned $Planned -Swimlanes $Swimlanes -PreviewPath $Preview -ExportDpi 150 -VisioApplication $app) | ConvertFrom-Json
  if ($native.status -ne 'PASS') { throw "Desktop verification failed: $($native.error)" }
  $phase.verifyReopenExportAndMove = $timer.Elapsed.TotalSeconds
} finally {
  if ($doc) { $doc.Saved = $true; $doc.Close() }
  if ($app) { $app.Quit() }
}
$timer.Restart()
Add-Type -AssemblyName System.Drawing
$bitmap = [Drawing.Bitmap]::new([IO.Path]::GetFullPath($Preview))
try { $pixels = Test-PreviewBands $bitmap $spec $plan } finally { $bitmap.Dispose() }
$phase.checkPreviewPixels = $timer.Elapsed.TotalSeconds
$native | Add-Member -NotePropertyName previewVisibility -NotePropertyValue $pixels
$native | Add-Member -NotePropertyName phaseSeconds -NotePropertyValue $phase
[IO.File]::WriteAllText([IO.Path]::GetFullPath($Report), ($native | ConvertTo-Json -Depth 15), [Text.UTF8Encoding]::new($false))
