# Visio 制图工具 · 1.5.0rc6-agent-trial.8

输入为当前任务的 UTF-8 JSON，输出为可编辑的单页 VSDX。制图内核版本为 `1.5.0rc6`。节点、方向、文字、泳道归属和样式由当前输入描述。字段与限制见[输入格式](references/input-format.md)。

## Windows 用户安装与首次使用

1. 在 Windows 10 或 11 的 x64 电脑上，把整个 ZIP 解压到一个普通用户目录。基本流程图需可用的微软雅黑及基本流程图模具 BASFLO_M.VSSX。需要桌面验证时安装带许可的 Microsoft Visio；泳道还需 XFUNC_M.VSSX。
2. 双击解压目录最外层的 **Install.cmd**。安装器在当前用户目录部署随包附带的私有 Python 和内核。需要从自然语言制图时，在安装窗口选择已检测到的 **Codex**。读完自检结果，先处理列出的缺项。
3. 完全退出并重新打开 Codex。在存放图纸的工作目录开启新会话，直接描述需要绘制的流程、交付位置和必要的视觉要求。Skill 会整理输入并生成可编辑的 VSDX。用户无需准备 JSON 或命令。
4. 默认安装重复运行同一个 Install.cmd 即可复查或更新。从安装包双击 **Uninstall.cmd** 可按当前用户移除本项目管理的文件；生成的图纸请保存在自己的工作目录。

从 trial.7 或更早的手工解压版迁移时，请保留旧目录并按上述步骤安装到新的默认位置。已有的未托管同名目录会被保留。

默认安装目录为 `%LOCALAPPDATA%\Hoshivela\VisioDiagrams`。该目录的 `capabilities.json` 记录自检结果。完整能力可用、已安装但部分能力受限、安装失败三种结果会在安装窗口分别说明。注册时如已有同名 Skill，请按提示保全已有文件后再处理冲突。

### 首发验证范围

已用正式业务需求验证顺序操作和单判断分流终止的单页流程图。系统架构、关系图、横向和纵向泳道保留现有入口，正式业务覆盖仍待补充。风格、文字和归属取自当前输入。对需要验收的图纸，应在目标展示尺寸查看实际布局，并按需运行桌面验证。

### 怎样读生成结果

| 回执 | 处理 |
| --- | --- |
| GENERATED | 核对业务关系、显示字号、箭头和质量提示，按用途决定是否增加原生验证。 |
| DEGRADED | 阅读 diagnostics、layout、deliveryLayout 和 routing，找出受影响的图元或线路。保留首稿和回执，修改输入后换新文件名复画并复验。 |
| FAILED | 检查回执中的缺失资源、输入字段或输出路径，处理原因后重试。 |

普通生成同时写出同名的 .svg 预览、.planned.json 布局和 .validation.json 回执。CLI 的退化状态也可能返回退出码 0。核验原生编辑时请用桌面 Visio，另存、重开，并检查带连接节点移动后的位移和胶合。

### 开发者直接调用

保留使用已有 Python 3.11+ 的方式。进入解压包 visio-diagrams 下的 scripts 目录，可运行以下命令。已安装的 Skill 通过安装器登记的私有运行时调用内核。

```powershell
python -m visio_core --version
python -m visio_core "C:\工作目录\输入.json" "C:\工作目录\输出.vsdx"
```

使用 `--font-family` 和 `--stencil` 指定字体、基本流程模具；`--color-fill off` 可关闭彩色填充。使用 `--target-width-mm`、`--target-height-mm` 和 `--min-font-pt` 设置目标尺寸及字号下限，通过 `deliveryLayout` 检查实效字号。输出路径须使用新文件名。[字体说明](references/fonts.md)

### Python API

从包内 `scripts` 目录启动 Python，或将该目录加入调用程序的模块搜索路径。入口为 `from visio_core import generate, GenerationConfig`。调用 `generate(description, output, config=GenerationConfig(...))` 返回与命令行一致的结果字典。将 `description` 设为当前任务的 JSON 文件路径或已解析的字典，`output` 设为新的 VSDX 路径。可选配置字段包括 `font_family`、`color_fill`、`stencil`、`backend`、`target_width_mm`、`target_height_mm` 和 `min_font_pt`。

### 横向与纵向泳道

在同一 `scripts` 目录运行下列命令。第一个文件定义业务节点的位置和连接，第二个文件定义泳道方向、边界、标题和成员。两份输入的页面尺寸须一致。

在泳道定义顶层设置 `"orientation": "vertical"` 可将职责从左到右分列，标题放在每列顶部。横向行式泳道使用 `"orientation": "horizontal"`，旧输入省略该字段时继续按横向处理。切换方向时，按新内容区重新定位节点和固定路线。两种方向使用同一条命令。

```powershell
python .\render-swimlane.py "C:\工作目录\定位流程.json" "C:\工作目录\泳道定义.json" "C:\工作目录\泳道图.vsdx"
```

可附加 `--font-family`、展示尺寸和字号参数，以及 `--stencil`、`--swimlane-stencil`。命令使用桌面 Visio 另存重开、核对成员与端点，并输出 VSDX、原生 PNG 及 `.swimlane.json` 回执。

### 原生核验与修订

对普通生成的图，在 `scripts` 目录运行下列命令。验证器读取与 VSDX 同名的 `.planned.json`，在桌面 Visio 中打开、另存、重开；可移动有连线的代表节点，核对端点是否跟随。

```powershell
.\validate-vsdx.ps1 -Vsdx "C:\工作目录\输出.vsdx" -Report "C:\工作目录\原生验证.json" -PreviewPath "C:\工作目录\预览.png" -RoundTripPath "C:\工作目录\另存副本.vsdx"
```

用 `-Planned` 指向不同位置的布局文件，用 `-MoveNode` 指定要抽查的节点 ID。对修订稿，可用 `python -m visio_core.review compare --help` 查看输入差异检查参数，或用 `python -m visio_core.review summarize --help` 查看生成、原生核验和审阅回执的汇总参数。

只有当前任务明确要求额外的样式变换时才单独调用 `style-workflow.ps1`。它从指定的 VSDX 和最终布局创建新副本，`-NoteNode` 和 `-EndNode` 选取对应节点，`-PreserveColors` 可保留原配色。`python .\review-style.py --help` 提供副本与来源、文字、箭头及端点的核对入口。
