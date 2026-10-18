"""检查 PPTX 是否为真矢量：统计形状类别与位图占比。

用法：
  python verify_pptx.py --pptx out/Figure.pptx

判定：
  - 全屏位图（覆盖 >=90% 画面）存在 → 是贴图，不是矢量，退出码 2
  - 否则矢量形状为主，退出码 0；PDF 里本来就有的小位图会被单独列出
"""
import argparse
import os
import sys

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE


def iter_shapes(shapes):
    for sh in shapes:
        if sh.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from iter_shapes(sh.shapes)
        else:
            yield sh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pptx", required=True)
    a = ap.parse_args()

    if not os.path.isfile(a.pptx):
        raise SystemExit("找不到文件: " + a.pptx)

    prs = Presentation(a.pptx)
    slide_w = prs.slide_width
    slide_h = prs.slide_height
    print("幻灯片: %.2f x %.2f in"
          % (slide_w / 914400, slide_h / 914400))

    kinds = {}
    pictures = []
    full_slide = []
    for i, slide in enumerate(prs.slides, 1):
        for sh in iter_shapes(slide.shapes):
            t = str(sh.shape_type)
            kinds[t] = kinds.get(t, 0) + 1
            if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
                area = sh.width * sh.height
                pictures.append(sh)
                if (sh.width >= slide_w * 0.9
                        and sh.height >= slide_h * 0.9):
                    full_slide.append((i, sh))
                elif area >= slide_w * slide_h * 0.5:
                    full_slide.append((i, sh))

    total = sum(kinds.values())
    print("\n形状统计 (%d 个):" % total)
    for t, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print("  %-28s %d" % (t, n))

    vector = total - len(pictures)
    print("\n矢量形状: %d" % vector)
    print("位图图片: %d" % len(pictures))
    for sh in pictures[:10]:
        print("  %.1f%% 画面  (%.2f x %.2f in)"
              % (100.0 * sh.width * sh.height / (slide_w * slide_h),
                 sh.width / 914400, sh.height / 914400))
    if len(pictures) > 10:
        print("  ... 共 %d 张" % len(pictures))

    if full_slide:
        print("\n[FAIL] 发现覆盖半屏以上的位图，这是贴图不是矢量：")
        for i, sh in full_slide:
            print("  第 %d 页 %.0f x %.0f px" % (i, sh.width, sh.height))
        sys.exit(2)

    if total == 0:
        print("\n[FAIL] 幻灯片里没有形状")
        sys.exit(2)

    print("\n[OK] 不是全屏贴图，主体是可编辑矢量形状")
    print("提示：在 PowerPoint 里点一下图形，能选中单个形状/路径即为矢量；"
          "若整张图只能作为一个对象选中，则仍是位图。")


if __name__ == "__main__":
    main()
