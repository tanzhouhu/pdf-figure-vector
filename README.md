# pdf-figure-vector
2026.10.19晚20点更新：输出只保留 SVG/PPTX，删除垃圾文件。

把学术 PDF 里的 Figure 原样复刻成**自包含矢量 SVG**和**可编辑 PPTX**；
也可以把独立 PNG/JPG/WebP 图片自动描摹成矢量 SVG 和原生自由形状 PPTX，下载好后可以直接发指令agent帮你操作。

这个仓库同时是一个 [Claude Code](https://claude.com/claude-code)/Codex Skill。作为
Codex Skill 放到 `~/.codex/skills/pdf-figure-vector/`，或作为 Claude Code Skill 放到
`~/.claude/skills/pdf-figure-vector/` 后，直接说「把这篇 PDF 的图 2 转成矢量图」即可，最后希望大家觉得好用的话顺便点点star啦。另外大家可以在评论区提一些意见后续我将持续维护更新。

## 特点

- **逐元素重建**：按 PDF 原始绘制顺序输出路径、文字、位图，z-order 正确
- **独立图片转矢量**：PNG/JPG/WebP/BMP/TIFF 用 VTracer 输出 SVG 和可编辑 PPTX
- **实时预览**：`--live` 边画边写 SVG，自动起本地服务并在 VS Code 集成浏览器（或默认浏览器）中打开
- **字体内嵌**：从 PDF 抽取字体子集，补齐 cmap / OS/2 后在 Chrome 与 SVG 中正常显示
- **位图处理**：带蒙版（SMask）、色键图标、转置图自动走高倍渲染裁切
- **位图自动识别**：`probe.py --bbox` 判断图是原生矢量还是位图；位图会提示改走手工重绘，不会把一张图片冒充成矢量
- **逐像素验收**：Chrome 截图与 PDF 渲染对比，输出 MAE 与左右对照图
- **PPTX 可编辑**：文字/形状/图片分别生成，自动校验是否为整张位图贴图
- **自动清理**：生成结束后删掉 `assets/`、`fonts/`、诊断 PNG、调参脚本等中间产物，只留成品
- **跨 agent**：Claude Code、Codex CLI、普通终端、SSH/headless 都能跑；预览按环境自动降级

## 环境要求

- Python 3.9+
- 依赖见 `requirements.txt`
- 独立图片描摹依赖 `vtracer`
- 对比功能需要 Google Chrome（Windows / macOS / Linux）
- VS Code 自动预览（可选）：
  - Windows + PowerShell
  - VS Code 1.141+（`Open Integrated Browser`），或带 `Simple Browser` 的旧版
  - 无法自动操作 VS Code 时会退回默认浏览器，不影响使用
- 其它 agent / 环境：Codex CLI、普通终端、macOS/Linux 有 GUI → 默认浏览器；
  SSH / headless → 只打印 URL（可 `ssh -L 转发`）。也可用 `PDFVEC_PREVIEW=vscode|browser|none`
  强制指定，见下文「预览方式」

## 安装

```bash
pip install -r requirements.txt
```

作为 Skill 使用：

```bash
# Codex
git clone <this-repo> ~/.codex/skills/pdf-figure-vector

# Claude Code
git clone <this-repo> ~/.claude/skills/pdf-figure-vector
```

## 独立 PNG/JPG 转换

```bash
python scripts/trace_image.py --image Figure1.png \
    --out out/Figure1 --preset diagram --preview
```

预设：

- `diagram`：框架图、流程图、论文示意图，保留彩色细节
- `logo`：黑白 logo、线稿、单色图标
- `photo`：照片近似描摹，路径较多

小字图片可使用 `--scale 2`；透明 PNG 使用 `--background transparent`。
默认会生成 `out/Figure1/_qa/_side.png` 和 `_diff.png` 做视觉对比。
输出文字会转成路径，不能在 SVG 中直接修改文字。

默认同时生成 `Figure1.pptx`。它由 PowerPoint 原生自由形状组成，不嵌入
整张位图。加 `--pptx-qa` 可渲染 PPTX 并生成 `_ppt_side.png` 与 `_ppt_diff.png`。

## 快速开始

所有命令假设当前目录是 skill 根目录（`scripts/` 的上一级）。

1. 探查页面，确认图在哪一页：

```bash
python scripts/probe.py --pdf paper.pdf --page 3 --render out/page3.png --zoom 2
```

算出 bbox 后判断这张图是原生矢量还是位图：

```bash
python scripts/probe.py --pdf paper.pdf --page 3 --bbox 54,56,542.5,225.5
```

提示「位图为主」时不要直接抽取（那只是把图片嵌进去），按 SKILL.md
「图本身是位图时：手工重绘成真矢量」处理。

2. 抽取并生成（`--live` 会边画边预览）：

```bash
python scripts/extract_figure.py --pdf paper.pdf --page 3 \
    --bbox 54,56,542.5,225.5 --out out/fig2 --name Figure2 --live
```

3. 与 PDF 逐像素比对：

```bash
python scripts/compare.py --pdf paper.pdf --page 3 \
    --bbox 54,56,542.5,225.5 --svg out/fig2/Figure2.svg --out out/fig2
```

4. 检查 PPTX 是否真的是矢量（正常会在生成后自动执行）：

```bash
python scripts/verify_pptx.py --pptx out/fig2/Figure2.pptx
```

## 参数说明

`extract_figure.py`：

| 参数 | 说明 |
| --- | --- |
| `--pdf` | PDF 路径 |
| `--page` | 页索引，**0 起** |
| `--bbox` | 图像区域 `x0,y0,x1,y1`（PDF point） |
| `--out` | 输出目录 |
| `--name` | 输出文件名（不含扩展名） |
| `--width` | SVG 画布宽度，默认 1948 |
| `--live` | 边生成边写 SVG 并自动打开预览 |
| `--no-preview` | 配合 `--live`，只画不开预览窗口 |
| `--live-every` | 每 N 个元素写一次，默认 1 |
| `--no-pptx` | 只出 SVG |
| `--no-open` | 生成后不自动弹出 PowerPoint |
| `--no-clean` | 保留中间产物 |

`trace_image.py`：

| 参数 | 说明 |
| --- | --- |
| `--image` | PNG/JPG/JPEG/WebP/BMP/TIFF 输入 |
| `--out` | 输出目录 |
| `--name` | 输出 SVG 文件名（不含扩展名） |
| `--preset` | `diagram`、`logo` 或 `photo`，默认 `diagram` |
| `--scale` | 描摹前放大倍数；默认 `0`，小图自动 2x |
| `--background` | `white`、颜色值或 `transparent` |
| `--preview` | 转换后打开预览标签页 |
| `--no-pptx` | 只生成 SVG，不生成 PPTX |
| `--pptx-qa` | 渲染 PPTX 并与源图对比 |
| `--open-pptx` | 转换后弹出 PowerPoint |
| `--pptx-curve-steps` | PPTX 曲线采样密度，默认 8 |
| `--no-qa` | 跳过原图与 SVG 的对比图 |
| `--filter-speckle` | 过滤小碎块，值越大越干净 |
| `--color-precision` | 颜色精度，常用 `6`–`8` |
| `--layer-difference` | 图层色差阈值，值越小保留细节越多 |
| `--path-precision` | 路径坐标精度，默认预设已调好 |

`open_preview.py`（一般由 `--live` 自动调用）：

```bash
python scripts/open_preview.py --out out/fig2 --file Figure2.svg
python scripts/open_preview.py --out out/fig2 --file Figure2.svg --browser
python scripts/open_preview.py --out out/fig2 --file Figure2.svg --no-open
PDFVEC_PREVIEW=none python scripts/open_preview.py --out out/fig2 --file Figure2.svg
```

## 预览方式

`open_preview.py` 按环境自动选择，不需要改代码：

| 环境 | 行为 |
| --- | --- |
| Claude Code 扩展 / VS Code 集成终端（Windows） | 开进 VS Code 集成浏览器 |
| Codex CLI、普通终端、其它编辑器、有 GUI 的 macOS/Linux | 默认浏览器 |
| SSH / headless（无 `DISPLAY`、无 `WAYLAND_DISPLAY`） | 打印 URL，服务继续跑；用 `ssh -L 8765:127.0.0.1:8765 <user>@<host>` 在本地查看 |
| `PDFVEC_PREVIEW=vscode` / `browser` / `none` | 强制指定 / 只打印 URL |

`--browser` 强制默认浏览器，`--no-open` 只起服务。纯服务器也可以直接把
`preview.html` 和 SVG 用 `scp` 拿回本地看。

## 输出

```
out/fig2/
  Figure2.svg      # 自包含：字体、图片已 base64 内嵌
  Figure2.pptx     # 文字/形状可编辑
  preview.html     # 自动刷新预览页
  redraw.py        # 仅位图重绘时存在：逐元素绘图的成品源码
  .preview.json    # 预览服务的端口记录（临时文件，cleanup 会删）
```

独立图片转换输出：

```
out/Figure1/
  Figure1.svg       # 纯矢量路径
  Figure1.pptx      # 原生自由形状，可拆分编辑
  _qa/
    _source.png     # 原图
    _svg.png        # SVG 实际渲染
    _diff.png       # 差异图
    _side.png       # 左右对比
    _ppt.png        # PPTX 实际渲染（--pptx-qa）
    _ppt_diff.png   # PPTX 差异图
    _ppt_side.png   # 原图与 PPTX 对比
  preview.html      # 使用 --preview 时生成
```

## 已知限制

- 子集字体在 Windows 上不可直接用于 PPTX，PPTX 使用近似字体；SVG 仍使用内嵌子集
- 极少数特殊混合模式（如部分 PDF 透明组）无法完全等价还原
- PNG/JPG 自动描摹会把文字变成路径；小字和 JPEG 噪点可能造成轮廓变形
- 照片描摹只能近似还原，不会恢复原始摄影细节或语义分层
- 自动描摹 PPTX 可能包含数千个自由形状，文件较大且 PowerPoint 渲染较慢
- 极复杂的复合路径或孔洞可能在 PPTX 自由形状中近似，需查看 `--pptx-qa`
- 自动打开 VS Code 预览依赖 Windows UI Automation；其他平台 / 其他 agent（如 Codex CLI）
  退回默认浏览器，headless 环境只打印 URL
- 自动弹出 PowerPoint 仅 Windows（或 macOS/Linux 有默认程序）时生效，否则打印路径

## License

未指定。用于公开分发前请自行添加 LICENSE。
