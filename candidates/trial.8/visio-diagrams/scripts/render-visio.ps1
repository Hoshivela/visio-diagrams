param(
  [Parameter(Mandatory)][string]$SpecPath,
  [Parameter(Mandatory)][string]$OutputPath,
  [ValidateSet('spec','on','off')][string]$ColorFill='spec',
  [ValidateSet('python','com')][string]$Backend='python',
  [string]$StencilPath,
  [string]$FontFamily
)
$ErrorActionPreference='Stop'
$nativeArgs=@('-X','utf8',(Join-Path $PSScriptRoot 'render_native.py'),$SpecPath,$OutputPath,'--color-fill',$ColorFill,'--backend',$Backend)
if($StencilPath){$nativeArgs+=@('--stencil',$StencilPath)}
if($PSBoundParameters.ContainsKey("FontFamily")){$nativeArgs+=@("--font-family",$FontFamily)}
& python @nativeArgs
if($LASTEXITCODE -ne 0){throw 'Visio generation failed. See the JSON diagnostic.'}
