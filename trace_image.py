"""Trace a standalone raster image into an SVG made of vector paths.

Usage:
  python scripts/trace_image.py --image Figure1.png --out out/Figure1 --preview
  python scripts/trace_image.py --image Figure1.jpg --out out/Figure1 \
      --preset diagram --scale 2
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageOps, ImageStat


HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

PRESETS = {
    # More colors and smaller layer differences keep flat diagrams readable.
    "diagram": {
        "colormode": "color",
        "hierarchical": "stacked",
        "mode": "spline",
        "filter_speckle": 2,
        "color_precision": 8,
        "layer_difference": 8,
        "corner_threshold": 60,
        "length_threshold": 4.0,
        "max_iterations": 10,
        "splice_threshold": 45,
        "path_precision": 5,
    },
    "logo": {
        "colormode": "binary",
        "hierarchical": "stacked",
        "mode": "spline",
        "filter_speckle": 2,
        "color_precision": 6,
        "layer_difference": 16,
        "corner_threshold": 60,
        "length_threshold": 4.0,
        "max_iterations": 10,
        "splice_threshold": 45,
        "path_precision": 5,
    },
    "photo": {
        "colormode": "color",
        "hierarchical": "stacked",
        "mode": "spline",
        "filter_speckle": 4,
        "color_precision": 6,
        "layer_difference": 16,
        "corner_threshold": 60,
        "length_threshold": 4.0,
        "max_iterations": 10,
        "splice_threshold": 45,
        "path_precision": 3,
    },
}


def sanitize_name(value):
    value = re.sub(r"[^0-9A-Za-z_.-]+", "_", value).strip("._")
    return value or "Figure"


def parse_background(value):
    if value.lower() == "transparent":
        return None
    try:
        return ImageColor.getrgb(value)[:3]
    except ValueError as exc:
        raise SystemExit("invalid --background: %s" % value) from exc


def prepare_image(src, dst, scale, background):
    with Image.open(src) as image:
        image.load()
        image = ImageOps.exif_transpose(image)
        original_size = image.size
        if scale != 1.0:
            size = (
                max(1, round(image.width * scale)),
                max(1, round(image.height * scale)),
            )
            image = image.resize(size, Image.Resampling.LANCZOS)

        if background is None:
            image = image.convert("RGBA")
        else:
            rgba = image.convert("RGBA")
            canvas = Image.new("RGBA", rgba.size, background + (255,))
            canvas.alpha_composite(rgba)
            image = canvas.convert("RGB")

        image.save(dst, format="PNG")
        return original_size, image.size


def trace_to_svg(src, dst, params):
    try:
        import vtracer
    except ImportError as exc:
        raise SystemExit(
            "vtracer is not installed. Run: pip install -r requirements.txt"
        ) from exc

    vtracer.convert_image_to_svg_py(
        src,
        dst,
        colormode=params["colormode"],
        hierarchical=params["hierarchical"],
        mode=params["mode"],
        filter_speckle=params["filter_speckle"],
        color_precision=params["color_precision"],
        layer_difference=params["layer_difference"],
        corner_threshold=params["corner_threshold"],
        length_threshold=params["length_threshold"],
        max_iterations=params["max_iterations"],
        splice_threshold=params["splice_threshold"],
        path_precision=params["path_precision"],
    )
    if not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        raise SystemExit("vtracer did not produce an SVG")


def add_svg_metadata(path, title, width, height):
    with open(path, encoding="utf-8") as handle:
        svg = handle.read()
    match = re.search(r"<svg\b[^>]*>", svg)
    if not match:
        raise SystemExit("invalid SVG produced by vtracer")
    opening = match.group(0)
    if "viewBox=" not in opening:
        opening = opening[:-1] + ' viewBox="0 0 %d %d">' % (width, height)
    if 'width="' not in opening:
        opening = opening[:-1] + ' width="%d">' % width
    if 'height="' not in opening:
        opening = opening[:-1] + ' height="%d">' % height
    if "<title" not in svg:
        opening += "<title>%s</title>" % escape(title)
    svg = svg[:match.start()] + opening + svg[match.end():]
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(svg)


def svg_stats(path):
    root = ET.parse(path).getroot()
    counts = {}
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        counts[tag] = counts.get(tag, 0) + 1
    return counts


def find_chrome():
    from compare import find_chrome as compare_find_chrome
    return compare_find_chrome()


def shot_svg(chrome, svg, width, height, path):
    from compare import shot_svg as compare_shot_svg
    return compare_shot_svg(chrome, svg, width, height, path)


def load_source_for_qa(source, max_width):
    source_img = Image.open(source).convert("RGBA")
    white = Image.new("RGBA", source_img.size, "white")
    white.alpha_composite(source_img)
    source_img = white.convert("RGB")

    ratio = min(1.0, max_width / float(max(source_img.size)))
    size = (
        max(1, round(source_img.width * ratio)),
        max(1, round(source_img.height * ratio)),
    )
    source_img = source_img.resize(size, Image.Resampling.LANCZOS)
    return source_img


def quality_check(source, svg, qa_dir, max_width, chrome_path):
    os.makedirs(qa_dir, exist_ok=True)

    source_img = load_source_for_qa(source, max_width)
    size = source_img.size
    source_img.save(os.path.join(qa_dir, "_source.png"))

    chrome = chrome_path or find_chrome()
    rendered = shot_svg(
        chrome,
        svg,
        size[0],
        size[1],
        os.path.join(qa_dir, "_svg.png"),
    )
    diff = ImageChops.difference(source_img, rendered).convert("L")
    mae = ImageStat.Stat(diff).mean[0]
    diff.point(lambda value: min(255, value * 3)).save(
        os.path.join(qa_dir, "_diff.png")
    )

    sheet = Image.new(
        "RGB",
        (size[0] * 2 + 12, size[1] + 24),
        "white",
    )
    sheet.paste(source_img, (0, 24))
    sheet.paste(rendered, (size[0] + 12, 24))
    draw = ImageDraw.Draw(sheet)
    draw.text((6, 6), "Source image", fill="black")
    draw.text((size[0] + 18, 6), "SVG trace", fill="black")
    sheet.save(os.path.join(qa_dir, "_side.png"))
    return mae, qa_dir


def quality_check_pptx(source, pptx, qa_dir, max_width):
    from compare_pptx import export_powerpoint, export_soffice

    os.makedirs(qa_dir, exist_ok=True)
    source_img = load_source_for_qa(source, max_width)
    size = source_img.size
    source_img.save(os.path.join(qa_dir, "_source.png"))

    png = os.path.join(qa_dir, "_ppt.png")
    if os.name == "nt":
        try:
            export_powerpoint(pptx, png, size[0], size[1], 1)
        except Exception:
            export_soffice(pptx, png)
    else:
        export_soffice(pptx, png)

    rendered = Image.open(png).convert("RGB")
    if rendered.size != size:
        rendered = rendered.resize(size, Image.Resampling.LANCZOS)
    rendered.save(png)
    diff = ImageChops.difference(source_img, rendered).convert("L")
    mae = ImageStat.Stat(diff).mean[0]
    diff.point(lambda value: min(255, value * 3)).save(
        os.path.join(qa_dir, "_ppt_diff.png")
    )

    sheet = Image.new("RGB", (size[0] * 2 + 12, size[1] + 24), "white")
    sheet.paste(source_img, (0, 24))
    sheet.paste(rendered, (size[0] + 12, 24))
    draw = ImageDraw.Draw(sheet)
    draw.text((6, 6), "Source image", fill="black")
    draw.text((size[0] + 18, 6), "PPTX render", fill="black")
    sheet.save(os.path.join(qa_dir, "_ppt_side.png"))
    return mae


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="PNG/JPG/WebP/BMP/TIFF input")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--name", help="output SVG name without extension")
    ap.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        default="diagram",
        help="trace preset (default: diagram)",
    )
    ap.add_argument(
        "--scale",
        type=float,
        default=0.0,
        help="pre-upscale factor; 0 auto-upscales small images, 2 for text",
    )
    ap.add_argument(
        "--background",
        default="white",
        help="white, black, #RRGGBB, CSS color, or transparent",
    )
    ap.add_argument("--colormode", choices=("color", "binary"))
    ap.add_argument("--hierarchical", choices=("stacked", "cutout"))
    ap.add_argument("--mode", choices=("spline", "polygon", "none"))
    ap.add_argument("--filter-speckle", type=int)
    ap.add_argument("--color-precision", type=int)
    ap.add_argument("--layer-difference", type=int)
    ap.add_argument("--corner-threshold", type=float)
    ap.add_argument("--length-threshold", type=float)
    ap.add_argument("--max-iterations", type=int)
    ap.add_argument("--splice-threshold", type=float)
    ap.add_argument("--path-precision", type=int)
    ap.add_argument("--preview", action="store_true", help="open preview after tracing")
    ap.add_argument("--no-pptx", action="store_true", help="skip PPTX generation")
    ap.add_argument(
        "--pptx-curve-steps",
        type=int,
        default=8,
        help="curve flattening steps for native PPTX paths",
    )
    ap.add_argument(
        "--pptx-qa",
        action="store_true",
        help="render PPTX and compare it with the source image",
    )
    ap.add_argument(
        "--open-pptx",
        action="store_true",
        help="open the generated PPTX after conversion",
    )
    ap.add_argument(
        "--keep-qa",
        action="store_true",
        help="keep source/SVG/PPTX comparison images in out/_qa",
    )
    ap.add_argument("--no-qa", action="store_true", help="skip image-vs-SVG QA")
    ap.add_argument("--qa-width", type=int, default=2000)
    ap.add_argument("--chrome", help="Chrome/Chromium path for QA")
    args = ap.parse_args()

    source = os.path.abspath(args.image)
    if not os.path.isfile(source):
        raise SystemExit("image not found: %s" % source)
    if os.path.splitext(source)[1].lower() not in IMAGE_EXTS:
        raise SystemExit("unsupported image type: %s" % source)
    if args.scale < 0:
        raise SystemExit("--scale must be zero or positive")
    with Image.open(source) as probe:
        source_size = probe.size
    scale = args.scale or (2.0 if max(source_size) < 1200 else 1.0)

    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    name = sanitize_name(args.name or os.path.splitext(os.path.basename(source))[0])
    svg_path = os.path.join(out, name + ".svg")

    params = dict(PRESETS[args.preset])
    for key in (
        "colormode",
        "hierarchical",
        "mode",
        "filter_speckle",
        "color_precision",
        "layer_difference",
        "corner_threshold",
        "length_threshold",
        "max_iterations",
        "splice_threshold",
        "path_precision",
    ):
        value = getattr(args, key)
        if value is not None:
            params[key] = value

    background = parse_background(args.background)
    pptx_path = None
    with tempfile.TemporaryDirectory(prefix="pdfvec_trace_") as temp:
        prepared = os.path.join(temp, "input.png")
        traced = os.path.join(temp, "trace.svg")
        original_size, prepared_size = prepare_image(
            source, prepared, scale, background
        )
        trace_to_svg(prepared, traced, params)
        add_svg_metadata(
            traced,
            os.path.basename(source),
            prepared_size[0],
            prepared_size[1],
        )
        shutil.copyfile(traced, svg_path)

        stats = svg_stats(svg_path)
        path_count = stats.get("path", 0)
        image_count = stats.get("image", 0)
        print(
            "saved %s (%dx%d -> %dx%d px, scale %.1f, %d paths, %d images)"
            % (
                svg_path,
                original_size[0],
                original_size[1],
                prepared_size[0],
                prepared_size[1],
                scale,
                path_count,
                image_count,
            )
        )
        if path_count == 0:
            raise SystemExit("trace produced no vector paths")
        if image_count:
            print("warning: output still contains embedded raster images")

        qa_dir = (
            os.path.join(out, "_qa")
            if args.keep_qa
            else os.path.join(temp, "qa")
        )
        if not args.no_qa:
            try:
                mae, qa_dir = quality_check(
                    prepared,
                    svg_path,
                    qa_dir,
                    max(256, args.qa_width),
                    args.chrome,
                )
                print("QA MAE %.2f / 255; compare: %s" % (mae, qa_dir))
            except (Exception, SystemExit) as exc:
                print("QA skipped: %s" % exc)

        if not args.no_pptx:
            try:
                from svg_to_pptx import build_pptx

                pptx_path, shape_count = build_pptx(
                    svg_path,
                    out,
                    name,
                    args.pptx_curve_steps,
                )
                print(
                    "saved %s (%d native freeform shapes)"
                    % (pptx_path, shape_count)
                )
            except Exception as exc:
                raise SystemExit("PPTX generation failed: %s" % exc) from exc

            if args.pptx_qa:
                try:
                    pptx_mae = quality_check_pptx(
                        prepared,
                        pptx_path,
                        qa_dir,
                        max(256, args.qa_width),
                    )
                    print(
                        "PPTX QA MAE %.2f / 255; compare: %s"
                        % (pptx_mae, qa_dir)
                    )
                except (Exception, SystemExit) as exc:
                    print("PPTX QA skipped: %s" % exc)

        if args.keep_qa and os.path.isdir(qa_dir):
            print("QA files kept: %s" % qa_dir)
        elif os.path.isdir(qa_dir):
            print("QA files cleaned automatically")

    if args.preview:
        cmd = [
            sys.executable,
            os.path.join(HERE, "open_preview.py"),
            "--out",
            out,
            "--file",
            os.path.basename(svg_path),
        ]
        subprocess.run(cmd, check=False)

    if args.open_pptx and pptx_path:
        cmd = [
            sys.executable,
            os.path.join(HERE, "open_pptx.py"),
            "--pptx",
            pptx_path,
        ]
        subprocess.run(cmd, check=False)


if __name__ == "__main__":
    main()
