# KiCad DXF Import Plugin

将 DXF 文件（AutoCAD R12-R2018+）导入 KiCad PCB 编辑器。
支持 LINE、CIRCLE、ARC、LWPOLYLINE、POLYLINE、TEXT、MTEXT、
ELLIPSE、SPLINE、DIMENSION、LEADER 等实体。

基于 **KiCad IPC API**（`kicad-python`），通过 NNG socket
连接运行中的 KiCad 实例，无需加载到 KiCad 内部解释器。

---

## 功能特性

- **纯 Python DXF 解析器** — 无需第三方 CAD 库
- **丰富的实体支持**（12 种 DXF 实体类型）
- **原生 KiCad 标注** — DXF DIMENSION/LEADER 转换为 KiCad PCB_DIM 对象
- **中文/CJK 支持** — 自动检测 DXF 编码（GBK/BIG5/SHIFT-JIS/UTF-8）
- **弧形折线插值** — 含凸度（bulge）的 LWPOLYLINE 精确转换为圆弧段
- **B 样条逼近** — 基于 de Boor 算法
- **智能坐标转换** — DXF Y 向上 → KiCad Y 向下，自动翻转
- **wx 设置对话框** — 图层、比例、线宽、标注选项可视化配置

### 支持的 DXF 实体

| 实体类型 | KiCad IPC 对象 | 说明 |
|----------|----------------|------|
| `LINE` | `BoardSegment` | 直线段 |
| `CIRCLE` | `BoardCircle` | 圆 |
| `ARC` | `BoardArc` | 圆弧（起点/中点/终点） |
| `LWPOLYLINE` | 多个 `BoardSegment` | 轻量多段线，含凸度弧插值 |
| `POLYLINE` | 多个 `BoardSegment` | 2D 多段线 |
| `TEXT` | `BoardText` | 单行文字 |
| `MTEXT` | `BoardText` | 多行文字（自动行距） |
| `ELLIPSE` | `BoardArc` / `BoardSegment` | 椭圆 / 椭圆弧 |
| `SPLINE` | 多个 `BoardSegment` | B 样条（de Boor 逼近） |
| `DIMENSION` | `AlignedDimension` / `OrthogonalDimension` / `RadialDimension` | 对齐/正交/径向标注 |
| `LEADER` | `LeaderDimension` | 引线标注 |

---

## 环境要求

- **KiCad 10.0+**
- **Python**: `kicad-python >= 0.7.0`

KiCad 启动 IPC 插件时自动创建虚拟环境并安装依赖。
也可手动安装到 KiCad 自带 Python：

```bash
"D:\Program Files\KiCad\10.0\bin\python.exe" -m pip install kicad-python
```

---

## 安装

复制到 KiCad 第三方插件目录：

```
%APPDATA%/kicad/10.0/3rdparty/plugins/com_github_haydenhu_dxf_import/
```

目录结构：
```
com_github_haydenhu_dxf_import/
├── dxf_import.py       # IPC 插件主入口
├── dxf_reader.py       # DXF 解析器（纯 Python）
├── plugin.json         # KiCad IPC 插件描述符
├── requirements.txt    # kicad-python>=0.7.0
├── icon.png            # 插件图标
└── README.md           # 本文档
```

安装后重启 KiCad，插件出现在 **Tools → External Plugins → Import DXF**。

---

## 使用方法

### 从 KiCad GUI 启动

1. KiCad 中打开一个 PCB 文件（Pcbnew）
2. 菜单 **Tools → External Plugins → Import DXF**
3. 弹出文件对话框 → 选择 DXF 文件
4. 弹出设置对话框 → 配置参数，点击 **Import**

### 设置对话框参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| 1 DXF unit = | 1 mm | 单位预设（mm/inch/mil/cm/µm） |
| Scale factor | 1.0 | 自定义缩放系数（DXF 单位 → mm） |
| Target layer | Edge.Cuts | 图形元素放置的电路层 |
| Text/tables layer | Eco1.User | 文字元素放置的层 |
| Line width | 0.1 mm | 图形线段线宽 |
| Use KiCad native dimensions | 勾选 | DIMENSION/LEADER 转为原生标注对象 |

### 命令行（独立运行）

```bash
# 直传文件路径（跳过文件对话框）
python dxf_import.py 电路板边框.dxf

# 完整参数（跳过所有对话框）
python dxf_import.py 电路板边框.dxf \
  --layer Edge.Cuts \
  --text-layer Eco1.User \
  --dim-layer Cmts.User \
  --scale 1.0 \
  --line-width 0.1 \
  --no-dialog
```

### 命令行参数参考

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `dxf_file` | — | DXF 文件路径（省略则弹出文件对话框） |
| `--layer` | `Edge.Cuts` | 图形目标层 |
| `--text-layer` | `Eco1.User` | 文字目标层 |
| `--dim-layer` | `Cmts.User` | 标注目标层 |
| `--scale` | `1.0` | DXF 单位 → mm 缩放系数 |
| `--line-width` | `0.1` | 线宽（mm） |
| `--no-native-dims` | — | 关闭原生标注转换 |
| `--no-dialog` | — | 跳过所有 wx 对话框 |
| `--socket` | 环境变量 | IPC API socket 路径 |
| `--timeout` | `5000` | IPC API 超时（ms） |

---

## 技术架构

### IPC API vs 传统 ActionPlugin

| 方面 | v1.x (SWIG) | v2.0 (IPC) |
|------|-------------|------------|
| 运行方式 | KiCad 内部加载 Python 模块 | 独立进程，NNG socket 通信 |
| Python 环境 | KiCad 内置 Python | 任意 Python 3.9+ |
| 插件类型 | `pcbnew.ActionPlugin` | `plugin.json` IPC 描述符 |
| 入口文件 | `__init__.py` | `dxf_import.py` |
| 安装位置 | `scripting/plugins/` | `3rdparty/plugins/` |
| 通信机制 | Python SWIG 绑定 | Protocol Buffers + NNG |
| 外部依赖 | 无 | `kicad-python`（pip） |
| 坐标转换 | 导入后 Mirror 所有对象 | 构建时直接取反 Y |

v1.x 代码已存档在 `swig` 分支，Release 标签 `v1.0`。

---

## 开发

### 本地测试

```bash
# 确保 KiCad 正在运行且打开了 PCB
python dxf_import.py test.dxf --no-dialog
```

### 模块结构

```
dxf_import.py    → DxfImport 类 + main() CLI 入口（约 900 行）
dxf_reader.py    → DXF 解析器：DxfReader + 10 种实体类（约 1100 行）
plugin.json      → KiCad IPC 插件描述
```

### 核心用法

```python
from dxf_import import DxfImport
from dxf_reader import DxfReader
import kipy

kicad = kipy.KiCad()
board = kicad.get_board()
reader = DxfReader()
entities = reader.read("drawing.dxf")

importer = DxfImport(board=board, line_width_nm=100000)
count = importer.import_entities(entities)
print(f"Imported {count} entities")
```

---

## 许可

MIT
