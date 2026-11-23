---
name: visio-diagrams
description: 根据用户的自然语言或业务数据绘制可编辑的 Microsoft Visio 图（.vsdx），包括流程图、架构图、关系图以及横向和纵向泳道图。也用于核验这些图纸的布局、连线与原生可编辑性。
metadata:
  version: "1.5.0rc6-agent-trial.10"
---

# Visio 制图

根据本次需求完成绘图并交付 VSDX。自行把原文中的对象、关系、分支条件和终止点整理为 UTF-8 JSON，保存在本次工作目录。用英文标识符关联节点，用原文语言填写显示文字。将用户要求的配色、字体、展示尺寸写入输入或生成参数。只有缺失信息会改变图意时才询问用户。

## 选择图型

- 普通流程图、架构图和关系图优先用 `layout.mode = "intent"`。用判断节点和有向连线表达分支、终止与回路；架构图中区分真实归属和视觉分组。只绘制当前输入支持的关系。
- 复画和泳道图用 `positioned`。泳道将职责分配到横向行或纵向列，遵循用户指定的方向。未指定时按阅读方向、步骤数和跨道交接选择。横向道标题在左，纵向道标题在顶部。另写泳道定义 JSON，将每个业务节点分配到一道。
- 节点、边、母版、坐标和泳道字段见[输入格式](references/input-format.md)。原生验证、可选参数及旧 Geometry 输入见[使用说明](README.md)。

## 从本次输入生成图纸

在 PowerShell 中初始化运行路径。直接使用 ZIP 的开发者先进入解压后的 `visio-diagrams` 目录，使用 Python 3.11 或更高版本。安装器登记的 Skill 已绑定安装路径。

```powershell
# runtime-binding
$tool = (Get-Location).Path
$python = 'python'
# end-runtime-binding
$scripts = Join-Path $tool 'scripts'
```

将 `$spec` 和 `$output` 分别设为本次 JSON 和新 VSDX 的绝对路径。先确定用户的输出目录，再切换到脚本目录。用选定的运行时直接调用正式入口。

```powershell
Push-Location -LiteralPath $scripts
try {
    & $python -B -m visio_core $spec $output
} finally {
    Pop-Location
}
```

泳道图将 `$lanes` 设为泳道定义的绝对路径，改用 `& $python -B (Join-Path $scripts 'render-swimlane.py') $spec $lanes $output`。此入口需要桌面 Visio 和跨职能泳道模具。

## 读取结果并交付

普通图读取输出旁的 `.validation.json`，失败时读取命令输出的 JSON；泳道图检查 `.swimlane.json` 和对应产物。退出码 0 也可能伴随布局退化。

- `GENERATED` 表示已写出普通图。核对预览中的文字、形状、业务箭头和分支，包括所有终止点。查看回执中的字号、路由及布局诊断。
- `DEGRADED` 表示可以查看初稿，须检查具体诊断、重叠、折线和目标尺寸。保留原稿，针对受影响对象调整并用新文件名复画。交付时说明仍存在的质量问题。
- `FAILED` 时依据诊断修正字段、资源或输出路径，成功后再交付图纸。机器缺少字体或模具时说明缺项和恢复方法。

需要桌面核验时，在本次运行的 PowerShell 进程中调用安装目录的验证器。把 `$report`、`$png`、`$copy` 设为用户工作目录的新路径，`$nodeId` 设为带连线的代表节点 ID。若运行脚本的临时 Windows PowerShell 5.1 进程继承了其他版本的模块搜索路径，先将该进程自己的内置 Modules 目录加入搜索路径。

```powershell
if ($PSVersionTable.PSEdition -eq 'Desktop') {
    $env:PSModulePath = (Join-Path $PSHOME 'Modules') + [IO.Path]::PathSeparator + $env:PSModulePath
}
& (Join-Path $scripts 'validate-vsdx.ps1') -Vsdx $output -Report $report -PreviewPath $png -RoundTripPath $copy -MoveNode $nodeId
```

返回可打开的 VSDX 路径，按需附预览和回执。分别说明生成与离线核查结果，以及桌面 Visio 实际打开、移动带连线节点、另存和重开的执行情况。首发业务验证范围为单页顺序流程与单判断分流终止流程。
