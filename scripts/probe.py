"""探查 PDF 页面内容，帮定位图形的 pt 包围盒。

用法：
  python probe.py --pdf paper.pdf --page 3
  python probe.py --pdf paper.pdf --page 3 --render out/page3.png --zoom 2
  python probe.py --pdf paper.pdf --page 3 --bbox 54,56,542.5,225.5

列出页面尺寸、位图包围盒、文字块和矢量绘制范围。
--render 把整页渲染成 PNG，配合看图工具估 bbox。
--bbox 额外判断该区域是原生矢量还是位图：位图为主时直接抽取只会把位图
嵌进 SVG/PPTX，必须改走「手工重绘」分支（见 SKILL.md）。
"""
import argparse
import os

import fitz


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True, help="0 起页索引")
    ap.add_argument("--render", default=None, help="整页渲染 PNG 路径")
    ap.add_argument("--zoom", type=float, default=2.0)
    ap.add_argument("--bbox", default=None,
                    help="图区域 x0,y0,x1,y1（pt），判断位图/矢量构成")
    a = ap.parse_args()

    doc = fitz.open(a.pdf)
    page = doc[a.page]
    print("page size (pt): %.1f x %.1f" % (page.rect.width, page.rect.height))

    if a.render:
        os.makedirs(os.path.dirname(os.path.abspath(a.render)) or ".",
                    exist_ok=True)
        pix = page.get_pixmap(matrix=fitz.Matrix(a.zoom, a.zoom), alpha=False)
        pix.save(a.render)
        print("rendered", a.render, "%.0fx%.0f px  (pt = px / %.2f)"
              % (pix.width, pix.height, a.zoom))

    imgs = page.get_image_info()
    print("\nimages (%d):" % len(imgs))
    for i, info in enumerate(imgs[:20]):
        b = info["bbox"]
        print("  [%d] (%.1f, %.1f) - (%.1f, %.1f)  %dx%d px"
              % (i, b[0], b[1], b[2], b[3],
                 info.get("width", 0), info.get("height", 0)))

    drawings = page.get_drawings()
    print("\ndrawings: %d" % len(drawings))
    if drawings:
        xs0 = [d["rect"].x0 for d in drawings]
        ys0 = [d["rect"].y0 for d in drawings]
        xs1 = [d["rect"].x1 for d in drawings]
        ys1 = [d["rect"].y1 for d in drawings]
        print("  union bbox: (%.1f, %.1f) - (%.1f, %.1f)"
              % (min(xs0), min(ys0), max(xs1), max(ys1)))

    if a.bbox:
        vals = [float(x) for x in a.bbox.split(",")]
        if len(vals) != 4:
            raise SystemExit("--bbox 需要 4 个数：x0,y0,x1,y1")
        clip = fitz.Rect(*vals)
        area = clip.get_area()
        covered = 0.0
        n_img = 0
        for info in imgs:
            inter = fitz.Rect(info["bbox"]) & clip
            if not inter.is_empty:
                n_img += 1
                covered += inter.get_area()
        ratio = min(1.0, covered / area) if area else 0.0
        inside_d = [d for d in drawings
                    if fitz.Rect(d["rect"]).intersects(clip)]
        chars = len(page.get_text("text", clip=clip).replace("\n", "").strip())
        print("\nfigure region (%.1f, %.1f) - (%.1f, %.1f):"
              % (clip.x0, clip.y0, clip.x1, clip.y1))
        print("  位图覆盖: %.0f%%（%d 张）" % (ratio * 100, n_img))
        print("  矢量绘制: %d" % len(inside_d))
        print("  文字字符: %d" % chars)
        if ratio >= 0.6 and len(inside_d) < 20 and chars < 200:
            print("  ⚠️ 位图为主：直接抽取只会把位图嵌进 SVG/PPTX，"
                  "成品仍是一张图。")
            print("     必须手工重绘成真矢量：见 SKILL.md「图本身是位图时」。")
        elif ratio < 0.2:
            print("  ✓ 原生矢量为主：按正常流程抽取即可。")
        else:
            print("  混合：位图和矢量/文字都有。先正常抽取，再决定位图部分"
                  "是否手工重绘。")

    blocks = page.get_text("blocks")
    print("\ntext blocks (%d):" % len(blocks))
    for b in blocks:
        t = b[4].strip().replace("\n", " ")
        if not t:
            continue
        print("  (%.1f, %.1f) - (%.1f, %.1f)  %s"
              % (b[0], b[1], b[2], b[3], t[:60]))


if __name__ == "__main__":
    main()
