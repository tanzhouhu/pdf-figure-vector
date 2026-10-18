"""Compare a PPTX slide rendered by PowerPoint with the source PDF region.

Usage:
  python compare_pptx.py --pdf paper.pdf --page 3 --bbox 54,56,542,225 \
      --pptx out/Figure.pptx --out out

Outputs:
  _ref.png, _ppt.png, _ppt_diff.png, _ppt_side.png
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageChops, ImageDraw, ImageStat

from compare import render_pdf


def export_powerpoint(pptx, png, width, height, slide_no=1):
    # PowerShell COM is more reliable here than win32com's dynamic dispatch:
    # Presentations.Open() can return an object without a discoverable Slides attr.
    env = os.environ.copy()
    env.update({
        "PDFVEC_PPTX": os.path.abspath(pptx),
        "PDFVEC_PNG": os.path.abspath(png),
        "PDFVEC_WIDTH": str(width),
        "PDFVEC_HEIGHT": str(height),
        "PDFVEC_SLIDE": str(slide_no),
    })
    script = r"""
$ErrorActionPreference = 'Stop'
$pptx = $env:PDFVEC_PPTX
$png = $env:PDFVEC_PNG
$w = [int]$env:PDFVEC_WIDTH
$h = [int]$env:PDFVEC_HEIGHT
$slide = [int]$env:PDFVEC_SLIDE
$app = New-Object -ComObject PowerPoint.Application
$pres = $null
$opened = $false
for ($i = 1; $i -le $app.Presentations.Count; $i++) {
    $p = $app.Presentations.Item($i)
    if ($p.FullName -eq $pptx) { $pres = $p; break }
}
if ($null -eq $pres) {
    $pres = $app.Presentations.Open($pptx, $true, $false, $false)
    $opened = $true
}
$pres.Slides.Item($slide).Export($png, 'PNG', $w, $h)
if ($opened) { $pres.Close() }
"""
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True, env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, timeout=120)


def find_soffice():
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def export_soffice(pptx, png):
    exe = find_soffice()
    if not exe:
        raise SystemExit(
            "Cannot export PPTX on this platform. Install LibreOffice or pass --png.")
    tmp = tempfile.mkdtemp(prefix="pdfvec_ppt_")
    try:
        subprocess.run(
            [exe, "--headless", "--convert-to", "png", "--outdir", tmp,
             os.path.abspath(pptx)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=180)
        candidates = sorted(glob.glob(os.path.join(tmp, "*.png")))
        if not candidates:
            raise SystemExit("LibreOffice did not produce a PNG.")
        shutil.copyfile(candidates[0], png)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--bbox", required=True)
    ap.add_argument("--pptx", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--png", default=None,
                    help="Use an existing PPTX-rendered PNG instead of exporting.")
    ap.add_argument("--slide", type=int, default=1)
    ap.add_argument("--width", type=int, default=1948)
    ap.add_argument("--margin", type=int, default=8)
    a = ap.parse_args()

    bbox = tuple(float(v) for v in a.bbox.split(","))
    if len(bbox) != 4:
        raise SystemExit("--bbox needs 4 values: x0,y0,x1,y1")
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    width = a.width
    height = int(round(
        (bbox[3] - bbox[1]) * (width - 2 * a.margin) / (bbox[2] - bbox[0])
        + 2 * a.margin))

    ref = render_pdf(
        a.pdf, a.page, bbox, width, height, a.margin,
        os.path.join(out, "_ref.png"))

    png = os.path.abspath(a.png) if a.png else os.path.join(out, "_ppt.png")
    if not a.png:
        if os.name == "nt":
            try:
                export_powerpoint(a.pptx, png, width, height, a.slide)
            except Exception as exc:
                print("PowerPoint export failed; trying LibreOffice: %s" % exc,
                      file=sys.stderr)
                export_soffice(a.pptx, png)
        else:
            export_soffice(a.pptx, png)

    mine = Image.open(png).convert("RGB")
    if mine.size != (width, height):
        mine = mine.resize((width, height), Image.Resampling.LANCZOS)
    mine.save(os.path.join(out, "_ppt.png"))

    diff = ImageChops.difference(ref, mine).convert("L")
    mae = ImageStat.Stat(diff).mean[0]
    diff.point(lambda v: min(255, v * 3)).save(
        os.path.join(out, "_ppt_diff.png"))

    sheet = Image.new("RGB", (width, height // 2 + 20), "white")
    sheet.paste(ref.resize((width // 2, height // 2)), (0, 20))
    sheet.paste(mine.resize((width // 2, height // 2)), (width // 2, 20))
    draw = ImageDraw.Draw(sheet)
    draw.text((6, 4), "PDF reference", fill="black")
    draw.text((width // 2 + 6, 4), "PPTX render", fill="black")
    sheet.save(os.path.join(out, "_ppt_side.png"))

    print("PPTX vs PDF MAE %.2f / 255  (%.2f%%)" % (mae, mae / 255 * 100))
    if mae < 10:
        print("Excellent: PPTX render is nearly pixel-identical to PDF.")
    elif mae < 25:
        print("Acceptable: inspect fonts and curve details at high zoom.")
    else:
        print("Large difference: inspect font substitution and shapes.")


if __name__ == "__main__":
    main()
