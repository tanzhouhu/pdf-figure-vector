---
name: pdf-figure-vector
description: 把学术 PDF 图表或独立 PNG/JPG/WebP 图片复刻成矢量 SVG，并可为 PDF 输出可编辑 PPTX。当用户要求「把论文这张图转成矢量 / 把图片转 SVG / 图片转矢量 / 复刻 PDF 的图 / 提取 Figure」时使用。要点：PDF 优先逐元素重建，独立位图用 VTracer 自动描摹；两者都提供预览与图像对比验收。
---

# PDF 图表与位图转矢量 SVG / PPTX

把 PDF 里的某张图按原始绘制顺序重建成 SVG（矢量路径 + 内嵌字体子集 + 原位图片），
同时导出元素可编辑的 PPTX。目标是和 PDF 逐像素接近（正常 MAE < 10/255）。
独立 PNG/JPG/WebP/BMP/TIFF 也可以直接描摹成只含矢量路径的 SVG；这种自动描摹
不还原可编辑文字，文字会变成路径。

## 目录

```
scripts/
  probe.py            # 探查页面：尺寸、位图 bbox、矢量范围、文字块；--bbox 判断位图/矢量
  extract_figure.py   # 主脚本：抽取并重建 SVG/PPTX
  trace_image.py      # 独立位图：PNG/JPG/WebP/BMP/TIFF → 矢量 SVG + PPTX
  svg_to_pptx.py      # 把描摹出的 SVG 路径转成 PPTX 原生自由形状
  vd_canvas.py        # Canvas 后端（SVG + PPTX 输出），勿改
  compare.py          # PDF 渲染 vs Chrome 截图，出 MAE 与差异图
  compare_pptx.py     # PowerPoint 导出 PNG vs PDF，出 MAE 与差异图
  verify_pptx.py      # 统计矢量形状/位图，检测是否整张贴图
  open_pptx.py        # 自动弹出 PowerPoint 供作者亲自检查
  open_preview.py     # 起预览服务并自动打开预览窗口
  vscode_preview.ps1  # Windows：UI Automation 把预览开进 VS Code
  cleanup.py          # 删掉 assets/、fonts/、诊断图等中间产物
  preview.html        # 自动刷新预览页（每 0.5 s 重载 SVG）
```

## 环境

用当前环境的 Python（下文命令里的 `python` 指你自己的解释器）。依赖见
`requirements.txt`：`PyMuPDF`、`Pillow`、`numpy`、`python-pptx`、`fontTools`、
`vtracer`。缺什么就 `pip install -r requirements.txt`。

Windows / macOS / Linux 都能跑。系统字体查找会按平台扫各自的字体目录
（Windows `%WINDIR%\Fonts`；macOS `~/Library/Fonts`、`/Library/Fonts`、
`/Library/Fonts/Microsoft`、`/System/Library/Fonts`；Linux `~/.fonts`、
`~/.local/share/fonts`、`/usr/share/fonts`），文件名对不上时读字体的
name 表匹配族名。macOS 通常没有 Cambria Math，坏字体的逐字反查会自动
退到 STIX/Latin Modern Math；都找不到时置信度不够就不改写原文（不会更糟）。

下面命令都假设当前目录是 skill 根目录（`scripts/` 的上一级）。

## 其它 agent / 其它环境（Codex CLI、远程、无 GUI）

预览方式按环境自动选，换 agent 不用改任何代码：

| 运行环境 | 预览行为 |
|---|---|
| Claude Code 扩展 / VS Code 集成终端（Windows） | 开进 VS Code 集成浏览器 |
| Codex CLI、普通终端、其它编辑器、macOS/Linux 有 GUI | 默认浏览器 |
| SSH / headless（无 `DISPLAY`、无 `WAYLAND_DISPLAY`） | 只打印 URL，服务继续跑；配合 `ssh -L 8765:127.0.0.1:8765 <user>@<host>` 在本地看 |
| 想强制指定 | 环境变量 `PDFVEC_PREVIEW=vscode\|browser\|none` |

命令行开关：`--browser` 强制默认浏览器；`--no-open` 只起服务不开窗口。

各脚本的跨环境边界：

- `open_preview.py`：只有 VS Code 那条路依赖 Windows PowerShell UIA，其余是标准库。
- `open_pptx.py`：Windows 找 POWERPNT.EXE → 默认关联程序；macOS `open`；
  Linux `xdg-open`；无 GUI 或启动失败时只打印文件路径，不抛异常。
- `compare.py`：需要 Chrome/Chromium/Edge，常见安装路径 + `shutil.which` 都会找。
- `extract_figure.py` / `probe.py` / `vd_canvas.py`：纯 Python + PyMuPDF，跨平台。
- `trace_image.py` / `svg_to_pptx.py`：Pillow + VTracer + python-pptx；
  QA 需要 Chrome/Chromium/Edge，PPTX 渲染验收还需要 PowerPoint 或 LibreOffice。
- `vscode_preview.ps1`：仅 Windows + VS Code 使用，其它环境不会被调用。

连浏览器都打不开的纯服务器：直接用 `scp` 把 SVG/PPTX 拿回本地看；
预览服务（http://127.0.0.1:端口）本身不依赖图形界面。

**双端同步（Claude Code ↔ Codex）**

这份 skill 在 Claude Code 和 Codex 两边都会改；Codex 侧可能已经初始化为 Git 仓库。

- 两份位置：`~/.claude/skills/pdf-figure-vector` 与 `~/.codex/skills/pdf-figure-vector`。
- 动手改 `scripts/` 前先看文件修改时间/内容，确认没有吞掉另一侧刚做的改动。
- 发现被回退时按函数逐块补回（`looks_broken` / `register_fonts` /
  `span_subset` / `decode_span` / `emit_text` 是热点），不要整文件覆盖。
- 收尾必做：`diff -rq <两边目录> --exclude=__pycache__` 必须无输出；
  有差异先逐文件 diff 确认是不是对方的新改动，再决定合并方向，并留备份。
- 每次改动在下方「坑清单」补一条；另一侧同步时按条目核对。

## 独立 PNG/JPG 转换

用户给的是独立图片时，不要伪造 PDF 再走 `extract_figure.py`；直接用
`trace_image.py`：

```powershell
python scripts\trace_image.py --image Figure1.png `
    --out out\Figure1 --preset diagram --preview
```

支持的输入：PNG、JPG/JPEG、WebP、BMP、TIFF。输出是只含矢量路径的 SVG，
并默认再用 `svg_to_pptx.py` 生成一份路径可单独选中的 PPTX；默认 `diagram`
预设适合框架图、流程图和示意图。

预设选择：

- `diagram`：默认；保留彩色图层和细节，适合论文示意图、流程图、UI 图。
- `logo`：二值描摹；适合黑白 logo、线稿、印章、单色图标。
- `photo`：彩色照片近似描摹；路径多、文件大，不适合追求干净可编辑结构。

常用选项：

```powershell
# 小图默认自动放大到 2x；文字较小也可显式放大
--scale 2

# 保留透明背景；默认 white 会把透明区合成到白底
--background transparent

# 跳过 Chrome 图像对比
--no-qa

# 只要 SVG，不生成 PPTX
--no-pptx

# 生成后把 PPTX 渲染出来和原图比 MAE
--pptx-qa

# 保留 _qa/ 验收图；默认只在临时目录计算，完成后删除
--keep-qa

# 生成后自动用 PowerPoint 打开验收
--open-pptx

# 指定输出文件名（不含 .svg）
--name Figure2
```

转换后会默认执行图像 vs SVG 对比并打印 MAE，但对比图片放在临时目录，
完成后自动删除。加 `--keep-qa` 才保留 `out\Figure1\_qa\`。自动描摹是
像素轮廓追踪，不是 OCR：

- 文字会变成路径，不能在 SVG 里直接改字。
- 很小的文字、JPEG 压缩噪点和抗锯齿边缘可能变形；优先用高分辨率 PNG，
  或提供 `--scale 2`。
- 如果要求文字仍可编辑、结构仍可单独操作，改走下文「图本身是位图时：
  手工重绘成真矢量」。
- 照片、渐变、复杂纹理自动描摹只能近似；不要承诺还原原始摄影细节。

## PDF 图表工作流程（严格照做）

用户明确要求「看着画出来」。**先开预览窗口，再逐元素生成**，不要憋到最后才给成品。

### 1. 定位页面和 bbox

先问清是第几张图；用文字或图注确认页索引（**0 起**）。

```powershell
python scripts\probe.py --pdf paper.pdf --page 3 --render out\page3.png --zoom 2
```

Read `page3.png` 看图，再算 bbox：`pt = 像素 / zoom`。
图形范围参考 probe 输出的 drawings union bbox；**排除页眉和下方图注**。
拿不准就把 bbox 稍微收紧，宁少含图注。

算出 bbox 后**再跑一次带 `--bbox` 的 probe**，先判断这张图是原生矢量还是位图：

```powershell
python scripts\probe.py --pdf paper.pdf --page 3 --bbox 54,56,542.5,225.5
```

- `✓ 原生矢量为主` → 走第 2 步正常抽取。
- `⚠️ 位图为主` → **不要直接抽取**：那只会把位图原样嵌进 SVG/PPTX，
  成品仍是一张图，`verify_pptx.py` 也会报 `[FAIL]`。改走下文
  「图本身是位图时：手工重绘成真矢量」。
- `混合` → 先正常抽取，再决定位图那部分是否重绘。

### 2. 准备输出目录（预览会自动打开）

```powershell
$out = "C:\out\<Name>_figure"
New-Item -ItemType Directory -Force $out
```

不需要手动起服务或复制 `preview.html`：`--live` 时会自动调用
`open_preview.py`，它负责

- 建目录、复制 `preview.html`
- 起一个只监听 `127.0.0.1` 的常驻静态服务（自动挑空闲端口，复用已有服务）
- 按运行环境自动选打开方式：**VS Code 宿主**（Claude Code 扩展 / 集成终端）
  开进 VS Code 集成浏览器；其它 agent（Codex CLI、普通终端、非 Windows）
  直接开默认浏览器；没有图形界面就只打印 URL 并给出 `ssh -L` 端口转发提示

所以第 3 步直接跑 `--live` 即可，窗口会自己弹出来。想强制某种方式见下文
「其它 agent / 其它环境」。

### 3. 边生成边写盘

```powershell
python scripts\extract_figure.py `
    --pdf paper.pdf --page 3 --bbox 54,56,542.5,225.5 `
    --out $out --name Figure2 --live `
    --curve-steps 32 --simplify-eps 0.008
```

`--live` 默认每画 1 个元素写一次 SVG、停 0.5 s。元素多时（>500）可
`--live-every 3`。跑的时候告诉用户「正在画」，让它自己在窗口里看。
完全不想开预览（也不起服务）时加 `--no-preview`。

`--curve-steps` 控制三次贝塞尔采样密度，默认 32；`--simplify-eps` 控制
RDP 折线简化误差，默认 0.008 pt。放大后线条不够顺滑时优先增大前者、
减小后者，代价是 SVG/PPTX 体积和形状点数增加。

### 4. 逐像素核对

```powershell
python scripts\compare.py `
    --pdf paper.pdf --page 3 --bbox 54,56,542.5,225.5 `
    --svg $out\Figure2.svg --out $out
```

看 `_side.png`（左 PDF / 右 SVG）和 `_diff.png`。MAE < 10 就基本一致；
偏大就按下面「坑清单」逐项排查，改 `extract_figure.py` 或看是否需要用
`Canvas` 原语补画。

SVG 对齐后，必须再检查 PowerPoint 实际渲染结果：

```powershell
python scripts\compare_pptx.py `
    --pdf paper.pdf --page 3 --bbox 54,56,542.5,225.5 `
    --pptx $out\Figure2.pptx --out $out\_qa
```

Windows 会自动调用 PowerPoint COM 导出 PNG；其它平台可先用 LibreOffice
导出 PNG，再通过 `--png` 传给脚本。输出 `_ppt_side.png` 和
`_ppt_diff.png`。PPTX-vs-PDF MAE < 10 才算视觉验收通过。

### 5. 定稿出 PPTX（自动弹出给作者验收）

不带 `--live` 跑一次（`--no-pptx` 可先跳过 PPTX）。

**写 PPTX 前必须确认该文件没在 PowerPoint 里打开**（文件锁会让写入失败）。
如果之前导出过预览图，先关掉 Presentation 再重跑。

写完后 `extract_figure.py` **默认自动做两件事**：

1. `verify_pptx.py` 统计形状：打印矢量形状数、位图数，若发现覆盖半屏以上
   的位图直接报 `[FAIL]`（说明整张是贴图）。
2. `open_pptx.py` 用本地 PowerPoint（找不到才退回默认关联程序，可能是 WPS）
   弹出成品，交给你亲自点选检查。

不需要自动弹出时加 `--no-open`。单独手动验收：

```powershell
python scripts\verify_pptx.py --pptx $out\Figure2.pptx
python scripts\open_pptx.py --pptx $out\Figure2.pptx
```

检查方法：点任一图形能单独选中 → 矢量；整张图只能作为一个对象选中 → 还是位图。
图里少数 `PICTURE` 是 PDF 原本就有的照片/火焰图标，属正常。

```powershell
# 可选：PPT 导出 PNG 做二次核对（COM 可能连到 WPS，认 version 16）
$app = New-Object -ComObject PowerPoint.Application
$p = $app.Presentations.Open("$out\Figure2.pptx",0,0,-1)
$p.Slides.Item(1).Export("$out\_ppt.png","PNG",1948,686)
$p.Close(); $app.Quit()
```

### 6. 收尾（自动清理）

`extract_figure.py` 写完会**自动**调 `cleanup.py`：

- 删 `assets/`、`fonts/`（内容已 base64 内嵌进 SVG，是冗余中间产物）
- 删诊断文件：`_*.png/_*.py/_*.json/_*.log/_*.txt`、`patch_*.py`、
  `fit_*.py`、**目录内所有 `*.png`**、`_qa/`、`preview.html`、
  `.preview.json`、`__pycache__/`
- 默认只保留 `*.svg` 和 `*.pptx`；加 `--keep-qa` 保留验收图，
  加 `--keep-preview` 保留预览页
- 不动 `~$*.pptx`（PowerPoint 锁文件，关掉 PPT 自动消失）

需要留素材重跑时加 `--no-clean`。单独手动清理，想先看会删什么：

```powershell
python scripts\cleanup.py --out $out --dry-run
python scripts\cleanup.py --out $out
```

**弹出来的 PowerPoint 不要替用户关**——留给作者自己检查完再关。

## 转换完成后检查（必须）

1. `compare.py`：SVG-vs-PDF MAE < 10。
2. `compare_pptx.py`：PPTX-vs-PDF MAE < 10；字体替换常使 MAE 比 SVG 略高
   0.2–1.0，属正常。
3. `verify_pptx.py`：矢量形状数 > 0；位图总面积远小于半屏；没有整页贴图。
4. 打开 PPTX 抽查：任取几个线条、箭头、公式文字，能单独选中和编辑。
5. 检查 SVG/PPTX 没有带入页眉、页脚或 Figure caption。
6. 检查文件名、页面 bbox、模型图和图注对应关系，避免导错 Figure。
7. 独立图片：确认 SVG 有矢量路径、没有 `<image>`，并确认 PPTX 使用
   `FREEFORM`、位图数为 0。默认 QA 图会删除；需要留档时加 `--keep-qa`
   再检查 `_qa\_side.png`、`_diff.png`、`_ppt_side.png` 与 `_ppt_diff.png`。
   文字已转成路径时，不承诺还能直接编辑文字。

曲线不够顺滑时不要接受“像素 MAE 低但放大有折角”。先把
`--curve-steps` 提到 32–64，并把 `--simplify-eps` 降到 0.004–0.008，
重新生成后再做 PPTX 渲染对比。
清理只删本目录里已知的中间产物，不碰其它文件。

## 图本身是位图时：手工重绘成真矢量

判断依据：`probe.py --bbox` 报「⚠️ 位图为主」（位图覆盖 ≥60%，矢量绘制和
文字几乎没有）。期刊里的框架图/流程图常常整张是一张 JPEG/PNG，
`page.get_drawings()` 返回 0，`extract_figure.py` 抽不出任何可编辑元素。
独立 PNG/JPG 经 `trace_image.py` 后若文字变形、箭头不能单独选中，也改走本节。

流程（FedRAHi Figure 1 就是这么做的，最终 MAE ≈ 13/255，PPTX 634 个可编辑形状）：

1. **先告诉用户**这张图在 PDF 里是位图、重绘耗时较长；用户明确要矢量的就直接开工。
2. 建输出目录、起预览（SVG 首次写盘后预览页就开始自动刷新）：

   ```powershell
   python scripts\open_preview.py --out $out --file Figure1.svg
   ```

3. 在输出目录写 `redraw.py`（它是成品源码，cleanup 不会删），用 `vd_canvas.Canvas`
   逐元素画。骨架：

   ```python
   import os, sys, time
   sys.path.insert(0, r"<skill 路径>/scripts")
   from vd_canvas import Canvas

   OUT, NAME = r"C:\out\X_figure", "Figure1"
   LIVE = "--live" in sys.argv
   W, M = 1948, 8
   X0, Y0, X1, Y1 = 53.8, 83.69, 294.05, 306.67   # probe 的 bbox（pt）
   S = (W - 2 * M) / (X1 - X0)                    # px / pt，与 compare.py 留边一致
   H = round((Y1 - Y0) * S) + 2 * M

   def X(p): return M + (p - X0) * S
   def Y(p): return M + (p - Y0) * S
   def L(p): return p * S

   c = Canvas(W, H)

   def save(label=""):
       c.to_svg(os.path.join(OUT, NAME + ".svg"))
       print("drew", label, flush=True)
       if LIVE:
           time.sleep(0.3)
   ```

   - 坐标全部按 PDF point 写；几何量从高倍渲染图量：
     `page.get_pixmap(matrix=fitz.Matrix(8, 8), clip=fitz.Rect(x0,y0,x1,y1))`，
     `pt = px / 8 + 起点`。
   - 按语义分段画（标题、左域、中域、右域……），每段 `save()` 一次，作者能看着
     图一层层长出来。
   - `Canvas` 的 `sw` 和 `text(size=)` 都是**画布像素**：线宽/字号直接用渲染图
     上量到的像素，或按 pt 量完乘 `S`。
   - `Canvas.text` 不支持富文本：上下标拆成两次 `text`（上/下标字号约 ×0.65，
     偏移按主体字号比例算）。
4. 迭代：`python redraw.py --live` 看过程；每轮改完跑 `compare.py`，看 MAE 和
   `_side.png` / `_diff.png` 定位偏差。MAE 从几十降到 10 出头后，剩下的多半是
   字体抗锯齿和虚线相位，继续磨收益很小。
5. 出 PPTX：脚本末尾 `c.to_pptx(os.path.join(OUT, NAME + ".pptx"))`，再跑
   `verify_pptx.py` + `open_pptx.py` 验收（同第 5 步）。

重绘专属坑：

- 虚线只支持 `stroke-dasharray`，**没有 `stroke-dashoffset`**：虚线相位和原图
  对不齐是常见残差，别反复调。
- 线帽/线角是圆头（SVG `stroke-linecap/linejoin="round"`，PPTX 同理），
  双线箭头、细描边要按这个风格设计。
- 先把颜色收敛成调色板（主色 + 浅色各几档），从渲染图取色，别逐个元素现配。
- 照片/人像这类无法矢量的图案，用简单形状近似即可；确需保留就放小位图，
  但面积要远小于半屏，否则 `verify_pptx.py` 会判成整张贴图。

## 坑清单（都踩过，别重踩）

**z-order 重建**
- drawings/text 有 `seqno`；图片和渐变**没有**。用 `page.get_bboxlog()`
  找紧随其后的 `*path` 矩形，令 `seq = 该 drawing.seqno - 0.5`，
  再与其它元素合并按 seqno 排序，否则图片会盖到错误的层。

**矩形描边缺一条边**
- PDF 的 `re`/`qu` 是隐式闭合，`dr["closePath"]` 是 `False`。
  必须额外判断 `any(it[0] in ("re","qu") for it in dr["items"])` 强制闭合。

**字体子集被 Chrome 拒绝**
- PyMuPDF `doc.extract_font()` 抽出的子集只有 `(1,0)` Mac cmap 且可能缺 OS/2，
  Chrome OTS 直接报 `cmap: no supported subtables were found` 并静默回退。
- `patch_subset_font()` 用 fontTools 补 `(3,1)` format 4 cmap 子表 + 最小
  OS/2（version 4）。`extract_figure.py` 已内置；字体文件落在 `fonts/`。
- 子集还经常缺 `post` 表或 format 2 的 stringData 长度对不上，OTS 报
  `post: missing required table`。patch 时一律换成 format 3.0 的 post。
- 判断是否真的用上了：嵌字体名和一个不存在的字体名各截一张，图应不同。

**同名子集要逐 span 选**
- 一个 basefont 在 PDF 里可能有多个子集（`page.get_fonts()` 多条同名字体，
  各有 xref），只取最大的那个会让别的 span 拿错字形。
- `register_fonts()` 全部登记成 `PF<名字>_<xref>`；`span_subset()` 先按
  「码位 → glyph id」精确对（span 的 `chars` 里第三项就是 gid），对不上再用
  子集渲染结果与页面裁切比 IoU。单一候选也要验：同 gid 撞车很常见
  （`#` 在 1105 是省略号、1106 是 2、1107 是 4），阈值 0.28，验不过再拿
  全部子集比一遍，仍不过才走系统字体。
- 栅格比对要用 span 自身 bbox、按裁切区域中位亮度自适应阈值，
  否则彩色底（浅蓝/浅黄）会把整块背景算成墨。

**ToUnicode 坏掉的下标**
- 有些 PDF 把下标里的 `A1`/`B2` 编码成 ASCII 标点（texttrace 里是 `'!"'`），
  视觉上却是数学字母。SVG 用子集字体能画对（`glyph_x` 逐字定位）；
  PPTX 需要真字符，否则复制出来是 `!"`。
- `looks_broken()` 识别可疑 span，`decode_char()` 把子集字形和系统
  Cambria Math 逐字做 64×64 掩膜 IoU 反查（≥0.70 才采信），NFKC 折回
  ASCII 后只写进 `pptx_text`，SVG 仍用原始码位。
- 整串下标也会整段被映射成标点：`overall` 在 texttrace 里是
  `"$%&'())"`（7 字符，7 个都可疑）。判据不能只看 `len ≤ 6`，还要允许
  `len ≤ 24 且一半以上字符可疑`，否则 PPTX 里会留一串乱码。
- 解码只用 `span_subset()` 按页面栅格选定的那个子集，**不要**跨子集逐个
  试最高的 IoU——同一码位在别的子集里是另一个字形（曾把 `%` 解成 `X`）。
- 撇号/双撇号的真身（U+2032 ′、U+2033 ″）不在 ASCII 区间，候选表里
  必须显式加上，否则上标 `′` 解不出来。
- 公式里的真标点要保护：原字符属于 `()[]{},.=+-/:;<>?` 且该 span 的
  页面匹配分 < 0.45 时保留原字符。曾把真括号 `(` 解成 `l`（子集里没有
  真括号字形，最高分 0.416 也过了 0.70 的单字阈值）。
- 被页内裁切图（inline image）整块盖住的低置信 span 直接跳过输出：
  字形已由图片提供，再叠一个解不准的文本框只会露出来。

**整串字形被映射成同一个字符 + 无 cmap 子集**
- 有的 PDF（PowerPoint 导出的 CambriaMath 常见）把整段每个字形都映射成
  同一个字符：`PoE`→`PPP`、`Sigmoid`→`SSSSSSS`、`concatenate`→`ccccccccc`。
  码位全是正常字母，`_SUSPECT_CODES` 判不出来，`looks_broken()` 必须再加
  一条「非空白字符全相同」；这段 PDF 还给每个字形塞了一个 `gid=-1` 的
  零宽占位，判断前先按 `gid>=0` 过滤，否则正常的数学斜体 `q` 会被误判。
- 同名字体可能有多个子集，其中一个（如 CambriaMath 252）**根本没有 cmap**：
  `patch_subset_font()` 补表失败，`span_subset()` 也挑不到。这种子集不能丢——
  字形轮廓还在，重打包成「U+E000+gid → 字形」的临时字体，按 texttrace 的
  gid 渲染 64×64 掩膜，和系统 Cambria Math 候选字形比 IoU（≥0.70）反查真
  字符；SVG 改用反查结果 + 系统字体渲染，PPTX 写 NFKC 折回后的字符。
- gid 反查是唯一允许跨子集比较的场合：同一 gid 在不同子集是不同字形，逐字
  取所有子集里 IoU 最高的那个；cmap 那条路仍只用 `span_subset()` 选定的子集。
- `patch_subset_font()` 替换 `post` 表（format 3.0）后 fontTools 就拿不到
  字形名了，`save()` 抛 `illegal use of getGlyphOrder()` 并把字体文件写坏。
  必须在替换前 `getGlyphOrder()` 存一份、保存前 `setGlyphOrder()` 塞回去。
- 没有 cmap 的子集即使补了 post/OS/2 也不要放进 `cv.font_faces`，
  Chrome 的 OTS 照样会拒绝；但它仍要登记进 `self.subsets` 供 gid 反查用。
- 系统字体查找必须跨平台：以前只认 `%WINDIR%\Fonts`，在 macOS/Linux 上
  返回 None，整条反查链静默失效。现在按平台扫字体目录（含 macOS 的
  `/Library/Fonts/Microsoft`），文件名对不上就读 name 表匹配族名；首次
  未命中会扫一遍目录（几百个字体，约 1–4 秒），索引缓存在模块级
  `_FONT_INDEX`。mac 没有 Cambria Math 时会退到 STIX/Latin Modern Math。

**同名子集缓存不能跨 span 复用**
- 缓存 key 必须包含 span 的字体、码位+gid 序列**以及 bbox 起点**。
  只按 (base, 码位, gid) 缓存时，先匹配的 `#` span 结果会被别的 span
  复用，`l₀` 显示成 `l·`。

**虚线**
- 虚线短划（约 0.5×1.5pt）也是又小又细的填充矩形，噪点过滤不能只看
  尺寸，否则竖直虚线箭头整条消失。阈值取 `area < 0.6 且最长边 < 0.8`。
- 水平虚线不要用 `stroke-linecap="round"`：圆头两端各外扩约 1.2px，会把
  2.3px 的间隙完全填满，虚线变实线。有 `stroke-dasharray` 时用 `butt`。

**字距/基线**
- 整段 `<text>` 字距会比 PDF 宽约 6%。用 `page.get_texttrace()` 的逐字符
  `origin` 输出每个字的 `<text x>`，并直接用 origin.y 当基线
  （`dominant-baseline="auto"`）。不要再用 `central` 加经验偏移。
- 子集字体本身已是粗体，`font-weight` 强制 `normal`，否则浏览器合成加粗。

**位图**
- `page.get_image_info(xrefs=True)`：`xref > 0` 直接 `doc.extract_image()`；
  `xref == 0` 是页面内联图（常见于渐变/透明度），用 12 倍 `get_pixmap` 后
  按 bbox 裁切，否则会糊。
- 检查 `info["transform"]`：当 `(tr0,tr3)≈0 且 det<0` 时是转置图，
  需要 `ImageOps.mirror(Image.rotate(-90, expand=True))`。

**环形（donut）**
- 一个填充含两个子路径且内路径被包含 → 用 `circle(stroke)` 或
  `rect(stroke)` 画圆环/框线，线宽取内外差的一半，避免用多边形近似。
- 子路径顺序不保证外环在前；线宽必须取 `abs(r_outer - r_inner)`，
  否则 PPTX 会报 `ValueError: value must be in range ...`（负线宽）。

**`fs` 类型的填充**
- PyMuPDF 的 drawing `type` 有 `"f"`（填充）、`"s"`（描边）、`"fs"`（两者都有）。
  只按 `type == "f"` 分填充会把所有 `fs` 图形的颜色丢掉（白框、灰节点、
  彩色单元格全变白/透明）。必须对 `fs` 同时调用 `emit_fill` 和 `emit_stroke`。

**带蒙版 / 超小位图**
- `get_image_info()` 的 `has-mask` 为真时，`extract_image()` 只给底图，
  丢了 SMask（例如 2x2 底图 + 全页大小的蒙版），直接嵌入会变成纯色块。
- 16x16 之类的图标常带色键掩码（`/Mask`），`has-mask` 仍是 False，
  抽出原始位图会是一团黑块。
- 这两类都改成用 `page.get_pixmap(matrix=Matrix(12,12), clip=bbox)` 裁切渲染：
  视觉与 PDF 完全一致。判据：`has-mask` 或宽/高 ≤ 32px 或 `xref == 0`。

**曲线**
- 默认三次贝塞尔每条采样 32 段，再以 RDP eps=0.008 pt 化简。
  高倍放大仍有折角时用 `--curve-steps 64 --simplify-eps 0.004`。

**独立位图描摹**
- `trace_image.py` 使用 VTracer，输出是轮廓路径，不会自动恢复文字语义。
- `diagram` 预设对小图默认放大 2x，并尽量保留细节；文字仍可能变形。
- JPEG 的压缩噪点会产生大量碎路径；优先使用 PNG，或降低细节后再描摹。
- `logo` 是二值预设，会丢弃颜色；彩色流程图不要用它。
- `photo` 只能做近似描摹，不能期待干净的分层和可编辑对象。
- 需要可编辑箭头、文字、节点时，自动描摹不是终点，继续手工重绘。

**PPTX**
- 子集字体在 Windows 上不可用，PPTX 用近似字体（Optima→Candara、
  Helvetica→Arial、Times→Times New Roman）。SVG 用内嵌子集，PPTX 用近似。
- PowerPoint/WPS 打开文件时写 PPTX 会失败，先关文件。

**Chrome 截图**
- `--headless=new --hide-scrollbars --force-device-scale-factor=1
  --window-size=W,H`，否则会多出滚动条或缩放。
- 字体问题用 `--enable-logging=stderr --v=1` 才看得到 OTS 报错。

## 常用命令速查

```powershell
# 探查页面
python scripts\probe.py --pdf p.pdf --page 3 --render out\p3.png

# 判断图是原生矢量还是位图（算出 bbox 后跑）
python scripts\probe.py --pdf p.pdf --page 3 --bbox 54,56,542.5,225.5

# 独立 PNG/JPG → 矢量 SVG（自动预览）
python scripts\trace_image.py --image Figure1.png --out out\Figure1 `
    --preset diagram --preview

# 小字图片放大描摹；透明背景保留 alpha
python scripts\trace_image.py --image scan.jpg --out out\scan `
    --preset diagram --scale 2 --background transparent

# 实时生成（看过程，自动打开 VS Code 预览）
python scripts\extract_figure.py --pdf p.pdf --page 3 `
    --bbox 54,56,542.5,225.5 --out out --name Fig2 --live

# 只起预览、不自动开窗口
python scripts\open_preview.py --out out --file Fig2.svg --no-open

# 对比
python scripts\compare.py --pdf p.pdf --page 3 `
    --bbox 54,56,542.5,225.5 --svg out\Fig2.svg --out out

# PPTX 渲染结果对比（必须）
python scripts\compare_pptx.py --pdf p.pdf --page 3 `
    --bbox 54,56,542.5,225.5 --pptx out\Fig2.pptx --out out\_qa

# 验收：检查矢量 + 弹出 PowerPoint
python scripts\verify_pptx.py --pptx out\Fig2.pptx
python scripts\open_pptx.py --pptx out\Fig2.pptx

# 清理中间产物，默认只留 SVG/PPTX
python scripts\cleanup.py --out out

# 显式保留验收图或预览页
python scripts\cleanup.py --out out --keep-qa --keep-preview
```

`--bbox` 顺序 `x0,y0,x1,y1`（pt），`--page` 0 起，`--width` 默认 1948。
