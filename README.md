# pdf-figure-vector

把学术 PDF 里的 Figure 原样复刻成**自包含矢量 SVG**和**可编辑 PPTX**，并逐像素与 PDF 比对。

这个仓库同时是一个 [Claude Code](https://claude.com/claude-code)/Codex Skill。作为
Codex Skill 放到 `~/.codex/skills/pdf-figure-vector/`，或作为 Claude Code Skill 放到
`~/.claude/skills/pdf-figure-vector/` 后，直接说「把这篇 PDF 的图 2 转成矢量图」即可。

## 特点

- **逐元素重建**：按 PDF 原始绘制顺序输出路径、文字、位图，z-order 正确
- **实时预览**：`--live` 边画边写 SVG，自动起本地服务并在 VS Code 集成浏览器（或默认浏览器）中打开
- **字体内嵌**：从 PDF 抽取字体子集，补齐 cmap / OS/2 后在 Chrome 与 SVG 中正常显示
- **位图处理**：带蒙版（SMask）、色键图标、转置图自动走高倍渲染裁切
- **位图自动识别**：`probe.py --bbox` 判断图是原生矢量还是位图；位图会提示改走手工重绘，不会把一张图片冒充成矢量
- **逐像素验收**：Chrome 截图与 PDF 渲染对比，输出 MAE 与左右对照图
- **PPTX 可编辑**：文字/形状/图片分别生成，自动校验是否为整张位图贴图
- **自动清理**：生成结束后删掉 `assets/`、`fonts/`、诊断 PNG、调参脚本等中间产物，只留成品
- **跨 agent**：Claude Code、Codex CLI、普通终端、SSH/headless 都能跑；预览按环境自动降级
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

## 已知限制

- 子集字体在 Windows 上不可直接用于 PPTX，PPTX 使用近似字体；SVG 仍使用内嵌子集
- 极少数特殊混合模式（如部分 PDF 透明组）无法完全等价还原
- 自动打开 VS Code 预览依赖 Windows UI Automation；其他平台 / 其他 agent（如 Codex CLI）
  退回默认浏览器，headless 环境只打印 URL
- 自动弹出 PowerPoint 仅 Windows（或 macOS/Linux 有默认程序）时生效，否则打印路径

