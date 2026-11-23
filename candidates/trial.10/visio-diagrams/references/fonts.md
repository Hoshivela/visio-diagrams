# 字体选择和原生核验

工具默认使用 Windows 安装的 Microsoft YaHei（微软雅黑）。用户指定其他字体时，调用方传入 `--font-family "家族名"` 或 `GenerationConfig(font_family="家族名")`。默认适用于正式 CLI、API 和 Geometry JSON 兼容命令。1.2.0 起，`VISIO_CORE_FONT` 与旧 Geometry JSON 的字体配置从默认选择规则中移除，已有脚本可把本次需要的家族传入显式参数。`VISIO_CORE_STENCIL` 继续用于指定基本流程模具。

工具以 Windows GDI 解析安装家族、确认所需字形并测量对应字号与字重。可用家族及其同义名称来自选中的 Windows 字体。场景的 `fontFamily` 和 `fontAliases` 给出写入名称及可用别名。SVG 的文字和 VSDX 的西文及东亚字体单元引用同一已解析家族。目标机器也应安装该家族。显式 COM 兼容后端在 Visio 中按同一家族设置两种字形脚本的字体，动态连接线根据其原生文字块计算排版。

`FONT_MISSING` 说明所请求家族不可用，`FONT_GLYPH_MISSING` 说明该家族缺少所需字形。两种诊断都列出家族名称。固定框容量不足时会返回文字或空间诊断。每次生成使用新的输出路径，成功回执与图纸一并发布。

`validate-vsdx.ps1` 通过 Visio 逐个读取标题、容器、节点和关系文字的实际格式行，解析西文和东亚字体 ID 对应的家族并比对字号。报告包含打开原件、另存后和重开副本三个阶段的名字。仅运行离线生成时，`visioAcceptance` 为 `NOT_RUN`。核验时使用真实 Visio 及同一发版的场景文件。
