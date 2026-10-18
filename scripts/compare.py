"""把重建的 SVG 与 PDF 原图逐像素对比，输出 MAE 与差异图。

用法：
  python compare.py --pdf paper.pdf --page 3 --bbox 54,56,542.5,225.5 \
      --svg out/Figure.svg --out out

输出 out/_ref.png（PDF 渲染）、_mine.png（Chrome 截图）、
_diff.png（差异放大 3 倍）、_side.png（左右对照）。
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

import fitz
from PIL import Image, ImageChops, ImageDraw, ImageStat

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def find_chrome():
    for p in CHROME_CANDIDATES:
        if os.path.isfile(p):
            return p
    p = shutil.which("chrome") or shutil.which("google-chrome") \
        or shutil.which("chromium")
    if p:
        return p
    raise SystemExit("找不到 Chrome，请用 --chrome 指定路径")


def render_pdf(pdf, page_no, bbox, W, H, margin, path):
    X0, Y0, X1, Y1 = bbox
    S = (W - 2 * margin) / (X1 - X0)
    doc = fitz.open(pdf)
    page = doc[page_no]
    pix = page.get_pixmap(matrix=fitz.Matrix(S, S),
                          clip=fitz.Rect(X0, Y0, X1, Y1), alpha=False)
    ref = Image.new("RGB", (W, H), "white")
    ref.paste(Image.frombytes("RGB", (pix.width, pix.height), pix.samples),
              (int(round(margin)), int(round(margin))))
    ref.save(path)
    return ref


def shot_svg(chrome, svg, W, H, path):
    url = "file:///" + os.path.abspath(svg).replace("\\", "/")
    cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
           "--force-device-scale-factor=1", "--window-size=%d,%d" % (W, H),
           "--screenshot=" + os.path.abspath(path), url]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=120)
    for _ in range(20):
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            break
        time.sleep(0.2)
    img = Image.open(path).convert("RGB")
    if img.size != (W, H):
        img = img.resize((W, H))
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--bbox", required=True)
    ap.add_argument("--svg", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--width", type=int, default=1948)
    ap.add_argument("--margin", type=int, default=8)
    ap.add_argument("--chrome", default=None)
    a = ap.parse_args()

    bbox = tuple(float(v) for v in a.bbox.split(","))
    out = a.out or os.path.dirname(os.path.abspath(a.svg))
    W = a.width
    H = int(round((bbox[3] - bbox[1]) * (W - 2 * a.margin) / (bbox[2] - bbox[0])
                  + 2 * a.margin))

    ref = render_pdf(a.pdf, a.page, bbox, W, H, a.margin,
                     os.path.join(out, "_ref.png"))
    chrome = a.chrome or find_chrome()
    mine = shot_svg(chrome, a.svg, W, H, os.path.join(out, "_shot.png"))
    mine.save(os.path.join(out, "_mine.png"))

    diff = ImageChops.difference(ref, mine).convert("L")
    mae = ImageStat.Stat(diff).mean[0]
    diff.point(lambda v: min(255, v * 3)).save(
        os.path.join(out, "_diff.png"))

    sheet = Image.new("RGB", (W, H // 2 + 20), "white")
    sheet.paste(ref.resize((W // 2, H // 2)), (0, 20))
    sheet.paste(mine.resize((W // 2, H // 2)), (W // 2, 20))
    d = ImageDraw.Draw(sheet)
    d.text((6, 4), "PDF reference", fill="black")
    d.text((W // 2 + 6, 4), "SVG rebuild %s" % os.path.basename(a.svg),
           fill="black")
    sheet.save(os.path.join(out, "_side.png"))

    print("MAE %.2f / 255  (%.2f%%)" % (mae, mae / 255 * 100))
    if mae < 10:
        print("很好：几乎逐像素一致")
    elif mae < 25:
        print("可接受：大致一致，放大看细节")
    else:
        print("偏差较大：检查字体、z-order、图片位置")


if __name__ == "__main__":
    main()
