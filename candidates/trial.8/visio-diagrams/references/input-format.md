# 输入格式

## 单页图

使用 UTF-8 JSON 对象，顶层字段为 `schema`（值为 `visio.diagram/1.0`）、`nodes`、`edges`，另可设置 `page` 和 `layout`。几何单位为英寸，页面左上角为原点，向右为 x 正向，向下为 y 正向。字体大小以磅计。

| 对象 | 字段与含义 |
| --- | --- |
| 页面 `page` | `width`、`height` 为固定页面尺寸，省略时由布局推导。`title`、`titleFontSize` 设置标题，`colorFill` 为是否启用节点彩色填充的布尔值。 |
| 节点 `nodes[]` | `id` 为唯一且无空白的字符串，`text` 为显示文字。`kind` 可取 `box`、`diamond`、`terminator`、`ellipse`、`text`、`container`。基本流程模具的矩形、判断、起止框分别使用 `Process`、`Decision`、`Start/End` 母版。 |
| 节点样式 | `fill` 接受 `#RRGGBB`，`fontSize` 为字号，`bold`、`dashed` 为布尔值。意图模式中的矩形可用 `detail`、`detailFontSize` 在同一形状内显示标题和正文。 |
| 尺寸与位置 | `size` 的字段为 `width`、`height`，`position` 的字段为 `x`、`y`。意图模式将显式位置和尺寸作为硬约束；可调整的建议放入节点的 `draft.position` 或 `draft.size`。 |
| 容器 | 成员以 `parent` 引用真实容器 ID。每个容器须有直接成员，`padding` 和 `headerHeight` 控制内边距与标题区。 |
| 连接 `edges[]` | `from`、`to` 引用起止节点 ID，箭头指向 `to`。可设唯一的 `id`、标签 `text`、`fontSize` 和 `dashed`。端口 `fromPort`、`toPort` 可取 `left`、`right`、`top`、`bottom`。 |

## 布局和连线

用 `layout.mode` 选择布局。自动排线的输入上限为 40 个节点、80 条关系。

- `intent` 按意图排版。`layout.direction` 可取 `down` 或 `right`。`layout.groups` 接受含 `id`、`title`、`members` 的对象数组，可另设标题 `fontSize`；视觉组为单层，成员互不重叠。`layout.parallel` 接受列表的数组，每个列表至少含两个叶节点 ID，列表顺序给出并列成员的次序。`layout.order` 用节点 ID 列表的数组指定主方向上的局部顺序。`edges[].role` 可取 `main`、`auxiliary`、`feedback`，每种角色均保留连接方向。
- `auto` 适合无环有向图，按拓扑层次计算节点位置。可用 `layer` 固定节点层号；前驱层号需小于后继层号。
- `positioned` 为每个非容器节点给出 `position`，按需给出 `size`。要锁定一条正交折线，同时指定两端侧边与 `route`。它是按行进方向排列的二维坐标点数组，首尾位于相应侧边中点。固定关系标签框用 `label` 的 `x`、`y`、`w`、`h`。图元移动后应复查独立标签的位置。

## 泳道方向与边界

将定位流程 JSON 与泳道定义 JSON 分别传给 `render-swimlane.py`。流程文件包含 `page.width`、`page.height` 及位于各道内的节点。泳道定义的顶层 `orientation` 接受 `horizontal` 和 `vertical`，省略时使用 `horizontal`。两个文件均使用页面左上角原点和英寸。

| 方向 | 泳道表的公共边界 | 每道的边界和顺序 | 节点所在区域 |
| --- | --- | --- | --- |
| 横向 `horizontal` | `left < headerRight < right` | 每行 `top < bottom`，从上往下，相邻行共边 | 左侧标题栏以右、本行上下界以内 |
| 纵向 `vertical` | `top < headerBottom < bottom` | 每列 `left < right`，从左往右，相邻列共边 | 顶部标题栏以下、本列左右界以内 |

仅填写所选方向的边界，全部边界须在页面内。不同行可以有不同高度，不同列可以有不同宽度。标题文字在编排后的页面上水平阅读。

| 泳道文件字段 | 含义 |
| --- | --- |
| `page.width`、`page.height` | 与已生成流程页面相同的正数尺寸。 |
| `lanes` | 按所选方向排序的非空对象数组。 |
| `lanes[].id`、`title` | 标识符和非空标题，标识符忽略大小写后仍须唯一。 |
| `lanes[].members` | 业务节点 ID 数组。每个节点恰好隶属一条泳道，整个节点框须落在该道的内容区内。成员须可加入原生容器。 |
| `lanes[].fill`、`headerFill` | 可选，用 `#RRGGBB` 分别设置道体和标题栏的填充色。 |

## 既有 Geometry 输入

既有 Geometry JSON 可交给同一核心命令。其 `page.width`、`page.height` 指定页幅，节点几何直接写作 `x`、`y`、`w`、`h`，仍以页面左上角为原点。连接通过 `from`、`to` 引用节点。需要 Visio 控制旧输入的排线时可显式指定 `--backend com`。使用已有 VSDX 作为输入进行原位编辑属于另一个工作流；本生成器从结构化描述创建新的原生文件。
