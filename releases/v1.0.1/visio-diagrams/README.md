# Visio 制图工具 · v1.0.1

输入为当前任务的 UTF-8 JSON，输出为可编辑的单页 VSDX。制图内核版本为 `1.5.0rc6`。节点、方向、文字、泳道归属和样式由当前输入描述。字段与限制见[输入格式](references/input-format.md)。

## Windows 用户安装与首次使用

1. 在 Windows 10 或 11 的 x64 电脑上，把整个 ZIP 解压到一个普通用户目录。基本流程图需可用的微软雅黑及基本流程图模具 BASFLO_M.VSSX。需要桌面验证时安装带许可的 Microsoft Visio；泳道还需 XFUNC_M.VSSX。
2. 双击解压目录最外层的 **Install.cmd**，在菜单中选择自己使用的本地 Agent。菜单提供 Codex、WorkBuddy、Claude Code、Cursor、Gemini CLI、DeepSeek Harness（DSH）和豆包工作（DoubaoWork）。安装器会部署随包附带的私有 Python 和内核，并把同一份制图 Skill 写入所选 Agent 的用户目录。若只需要命令行或 Python API，可选“仅安装制图工具”。
3. 安装结束后，查看自检结果和 Skill 登记位置。完全退出并重新打开所选 Agent，在存放图纸的目录新建本地会话，用自然语言描述要画的业务、必要的视觉要求和交付位置。核对生成的 VSDX 和验证回执，即可确认这台电脑上从需求到出图的流程已跑通。
4. 更换 Agent 或复查安装时，再次双击 **Install.cmd**。选择另一 Agent 会将本工具管理的 Skill 转到新位置，选择“仅安装制图工具”会移除先前的登记。要卸载，请双击 **Uninstall.cmd**。将自己的图纸保存在工作目录。

使用 DSH 时，输入 `8` 或 `DSH`。默认登记在 `%USERPROFILE%\.dsh\skills\visio-diagrams`。若已设置 `DSH_HOME`，安装器使用其中的 `skills` 目录。相对路径按启动目录解析，绝对路径在不同工作目录中均指向同一位置。以后更改 DSH 主目录，重新运行安装器并沿用 DSH，即可迁移原登记。若 DSH 配置为只加载自定义技能目录，可用 `Install.cmd -AgentChoice DSH -SkillRoot "你的技能父目录完整路径"` 登记到该目录。[DSH 文件系统技能提供器](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/skill/skill-filesystem/README.md)

使用其他 Agent 时，请先确认它能从本地目录加载 `SKILL.md`，并能调用 Windows PowerShell。选择“其他支持 SKILL.md 的 Agent”，按提示输入它的 Skill 父目录。安装器会在该目录内创建 `visio-diagrams` 子目录。若客户端没有显示该技能，先核对安装窗口给出的登记位置和客户端的技能启用状态，再开启新会话。

从 trial.7 或更早的手工解压版迁移时，请保留旧目录并按上述步骤安装到新的默认位置。已有的未托管同名目录会被保留。

默认安装目录为 `%LOCALAPPDATA%\Hoshivela\VisioDiagrams`。`capabilities.json` 记录本机私有运行时、字体、模具、普通图生成和桌面 Visio 登记的检查结果，以及所选 Agent 的 Skill 写入状态。安装窗口会显示安装完成、部分能力尚未就绪或安装失败。显示“Skill 已登记”表示文件已写入指定目录。客户端能否发现 Skill 并调用内核生成图纸，需按第 3 步在所选 Agent 的新会话中确认。若登记时遇到同名 Skill 冲突，请按提示保全已有文件。

### 豆包工作（Windows 桌面版）

1. 先用准备制图的账号打开豆包桌面版，切换到工作模式，启动一次本地任务，让客户端生成该配置的工作目录。
2. 双击 **Install.cmd**，输入 `9` 或 `DoubaoWork`。安装器查找 `%LOCALAPPDATA%\DoubaoWork\User Data` 和 `%APPDATA%\DoubaoWork` 下已有的配置。只有一个可用配置时自动选用。有多个时，按安装窗口列出的路径选当前任务对应的配置。没有可用配置时，回到豆包工作启动本地任务，再运行安装器。登记位置为 `<配置目录>\.doubaowork\agent_mode\workspace\.user_skills\visio-diagrams`。[火山引擎的豆包工作技能目录说明](https://docs.volcengine.com/docs/agent-toolkit/force-skill-installation-guide?lang=zh)
3. 重新打开豆包工作，在同一配置中新建允许使用本机文件和 Windows PowerShell 的工作任务。用自然语言提出制图需求，指定一个可写的输出目录，核对该目录里的 VSDX 和验证回执。此版本提供本地技能登记，豆包客户端自动发现 Skill、调用内核生成 VSDX 的完整流程尚待实机验证。
4. 更换豆包配置时，在目标配置中先启动一次本地任务，再运行 **Install.cmd** 选择豆包工作和对应路径。安装器迁移本工具管理的登记，每次登记到一个配置。换用其他 Agent 时按安装菜单选择其名称。

无人值守安装若首次遇到多个配置，使用 `Install.cmd -NonInteractive -AgentChoice DoubaoWork -SkillRoot "目标配置的 .user_skills 完整路径"` 指定其中一个。重复安装时可沿用仍有效的登记。

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

## 许可

Visio 制图工具采用 MIT 许可证，全文见安装包根目录和安装后工具目录中的 `LICENSE`。随包附带的 Python 运行时及其组件沿用上游许可证，详见 `runtime/python-embed.zip` 内的 `LICENSE.txt`。
