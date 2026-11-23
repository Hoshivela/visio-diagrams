# v1.0.1 · 豆包工作接入与 MIT 许可证

Windows 安装器新增第 9 项 **豆包工作（Doubao Work）**。先在豆包工作启动一次本地任务，再运行安装器。只有一个可用配置时自动登记；有多个配置时可选择当前任务对应的目录。升级可继续使用已登记的 Agent，也可切换到豆包工作。

项目采用 MIT 许可证。仓库根目录、无示例安装包及已安装的制图工具均附有许可证全文。随包 Python 及其组件保留原有许可。制图内核版本为 `1.5.0rc6`。豆包客户端在真实会话中从自然语言需求生成首张 VSDX 的流程仍待验证。

Windows 安装附件为 `visio-diagrams-v1.0.1.zip`，SHA-256 为 `6147d0064bb14a3d1209ab3e9fd963e163783cadaa8bad6315204e6703b393ee`。

# v1.0.0 · Windows x64 正式版

正式发布可编辑的单页业务流程图，验收范围覆盖顺序操作和单判断分流终止。四个正式业务用例覆盖创建、导入、导出和删除。架构图、关系图、横向与纵向泳道保留现有入口，正式业务覆盖仍待扩充。

本版支持按当前用户安装、切换 Agent 与卸载。Codex、Claude Code、Cursor、Gemini CLI、WorkBuddy 和 DeepSeek Harness 均有安装预设。已验证六种登记路径。用户在纯净 Windows 11、Visio 和 DSH 环境中完成实际制图，同输入复画的 VSDX 内部 14 个文件一致。另用桌面 Visio 移动节点、保存并重开，三条连线仍连在原端口。

正式附件沿用技术构建 `1.5.0rc6-agent-trial.10` 的完整字节，制图内核为 `1.5.0rc6`。正式安装包名为 `visio-diagrams-v1.0.0.zip`，SHA-256 为 `15752051d9c7f133ea2189713da3703451515c74f26d0fede5be7f32a2c8b666`。[发行与下载](https://github.com/Hoshivela/visio-diagrams/releases/tag/v1.0.0)

# 1.5.0rc6-agent-trial.10 · Windows x64 试用版

安装时可手动选择六款 Agent，或指定其他兼容 Agent 的技能目录。DeepSeek Harness（DSH）默认登记到 `%USERPROFILE%\.dsh\skills`，也支持 `DSH_HOME`。重装可以沿用登记或切换 Agent。安装器会保留同名冲突文件和用户修改过的技能。

本机通过 12 项 DSH 安装与升级检查及 24 项通用回归。DSH 官方技能加载器已发现并读取制图 Skill，通过其中的私有运行时生成的 VSDX 已在桌面 Visio 验证移动节点、保存和重开。干净 Windows 和 DSH 聊天会话的自然语言首图仍待实测。

# v0.2.0-dev.2 — 开发预览

补充图型契约、自由/矩阵/重复结构判断、模板—实例分层规划、关键需求澄清和最小范围局部让步；区分业务事实与布局偏好。本次规则修订完成16组作者场景走查；它本身未修改渲染/布局算法。本版同时包含此前0.2开发阶段新增的原生容器与有界排线，71项回归通过；时序、泳道、矩阵共享外框和联合布局仍不是已实现能力。

# 当前开发版 v0.2.0-dev — 2026-10-12（尚未发布）

新增显式原生嵌套容器、递归成员边界、标题避障、矩形共享端口、平行候选通道与最多四种线序重排。71 项本机测试通过；真实移动跟随与路线保存见 references/containers-routing.md。复杂压力图仍有交叉，不保证全图最优或交互自动重排。

# v0.1.0-preview.1 — 2026-10-10

首个公开预览版：以 Python 直写 VSDX 为默认生成后端，旧 COM 生成显式选择。

- 保留原生基本流程母版及连接线，写入规划路线和真正胶合的端点。
- 处理实例几何、文本框、连接点缓存与分数端口，修复字体/字号写入和标签偏移。
- 提供离线生成报告、独立 Visio 验收与 PNG 导出；拒绝覆盖输出。
- 分发 skill 说明、脚本、55 项回归测试与通用 JSON 样例。
- 另附 Kubernetes 架构与 Deployment 创建 Pod 的复杂图示例，包含 8 个异常判断。

验收范围：本机 55 项回归测试无跳过；六类基础样例和两张复杂图通过 Visio
路线/字号/文字与代表节点移动验收，预览已查看。复杂流程的 8 个判断包含清单、
API、Pod 准入、调度、卷/网络、镜像、容器运行与就绪问题。

边界：仍需本地 VSSX；PNG 与真实验收仍需 Visio。未实现原生容器成员关系、
泳道、任意复杂母版与最终插图尺寸可读性检查。复杂架构含人工布局与路线，
本版不声明全图自动优化、所有编辑行为或跨版本兼容。Archify 为实验性入口。

## English

First public preview with Python-based native VSDX generation, glued connectors, explicit
route preservation, structural receipts, optional Visio acceptance and PNG export.
The local regression suite passed 55 tests without skips. Native containers, swimlanes,
general automatic layout, final-placement readability and cross-version compatibility
remain outside the accepted scope. A local stencil resource is still required.
