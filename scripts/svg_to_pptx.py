"""Convert a VTracer-style fill-path SVG into native PowerPoint freeform shapes.

Usage:
  python scripts/svg_to_pptx.py --svg Figure.svg --out out --name Figure
"""

import argparse
import math
import os
import re
import sys
import xml.etree.ElementTree as ET


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from vd_canvas import Canvas  # noqa: E402


NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"
TOKEN_RE = re.compile(r"[A-Za-z]|" + NUMBER)
TRANSLATE_RE = re.compile(
    r"translate\(\s*(%s)(?:\s*[,\s]\s*(%s))?\s*\)" % (NUMBER, NUMBER)
)


def parse_number(value, default=0.0):
    if value is None:
        return default
    match = re.search(NUMBER, value)
    return float(match.group(0)) if match else default


def parse_translate(transform):
    if not transform:
        return 0.0, 0.0
    tx = ty = 0.0
    cleaned = transform
    for match in TRANSLATE_RE.finditer(transform):
        tx += float(match.group(1))
        ty += float(match.group(2)) if match.group(2) else 0.0
        cleaned = cleaned.replace(match.group(0), "")
    if cleaned.replace(",", " ").strip():
        raise ValueError("unsupported SVG transform: %s" % transform)
    return tx, ty


def cubic_points(p0, p1, p2, p3, steps):
    points = []
    for index in range(1, steps + 1):
        t = index / float(steps)
        mt = 1.0 - t
        x = (
            mt ** 3 * p0[0]
            + 3 * mt ** 2 * t * p1[0]
            + 3 * mt * t ** 2 * p2[0]
            + t ** 3 * p3[0]
        )
        y = (
            mt ** 3 * p0[1]
            + 3 * mt ** 2 * t * p1[1]
            + 3 * mt * t ** 2 * p2[1]
            + t ** 3 * p3[1]
        )
        points.append((x, y))
    return points


def quadratic_points(p0, p1, p2, steps):
    points = []
    for index in range(1, steps + 1):
        t = index / float(steps)
        mt = 1.0 - t
        x = mt ** 2 * p0[0] + 2 * mt * t * p1[0] + t ** 2 * p2[0]
        y = mt ** 2 * p0[1] + 2 * mt * t * p1[1] + t ** 2 * p2[1]
        points.append((x, y))
    return points


def parse_path(data, curve_steps):
    tokens = TOKEN_RE.findall(data or "")
    contours = []
    current = []
    pos = (0.0, 0.0)
    start = (0.0, 0.0)
    command = None
    index = 0

    def finish():
        nonlocal current
        if len(current) >= 2:
            contours.append(current)
        current = []

    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token
            index += 1
            if command in ("Z", "z"):
                finish()
                pos = start
                command = None
            continue
        if command is None:
            raise ValueError("path coordinates without a command")
        cmd = command

        if cmd in ("M", "m"):
            finish()
            x = float(tokens[index])
            y = float(tokens[index + 1])
            index += 2
            if cmd == "m":
                x += pos[0]
                y += pos[1]
            pos = (x, y)
            start = pos
            current = [pos]
            command = "L" if cmd == "M" else "l"
        elif cmd in ("L", "l"):
            x = float(tokens[index])
            y = float(tokens[index + 1])
            index += 2
            if cmd == "l":
                x += pos[0]
                y += pos[1]
            pos = (x, y)
            current.append(pos)
        elif cmd in ("H", "h"):
            x = float(tokens[index])
            index += 1
            if cmd == "h":
                x += pos[0]
            pos = (x, pos[1])
            current.append(pos)
        elif cmd in ("V", "v"):
            y = float(tokens[index])
            index += 1
            if cmd == "v":
                y += pos[1]
            pos = (pos[0], y)
            current.append(pos)
        elif cmd in ("C", "c"):
            values = [float(value) for value in tokens[index:index + 6]]
            index += 6
            if len(values) != 6:
                raise ValueError("incomplete cubic path command")
            if cmd == "c":
                values = [
                    values[i] + (pos[0] if i % 2 == 0 else pos[1])
                    for i in range(6)
                ]
            p1 = (values[0], values[1])
            p2 = (values[2], values[3])
            p3 = (values[4], values[5])
            current.extend(cubic_points(pos, p1, p2, p3, curve_steps))
            pos = p3
        elif cmd in ("Q", "q"):
            values = [float(value) for value in tokens[index:index + 4]]
            index += 4
            if len(values) != 4:
                raise ValueError("incomplete quadratic path command")
            if cmd == "q":
                values = [
                    values[i] + (pos[0] if i % 2 == 0 else pos[1])
                    for i in range(4)
                ]
            p1 = (values[0], values[1])
            p2 = (values[2], values[3])
            current.extend(quadratic_points(pos, p1, p2, curve_steps))
            pos = p2
        else:
            raise ValueError("unsupported SVG path command: %s" % command)

    finish()
    return contours


def svg_size(root):
    view_box = root.get("viewBox")
    if view_box:
        values = [float(value) for value in re.findall(NUMBER, view_box)]
        if len(values) == 4:
            return values[2], values[3]
    return parse_number(root.get("width"), 1200), parse_number(root.get("height"), 800)


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def is_dark_fill(value):
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", value or "")
    if not match:
        return False
    raw = match.group(1)
    r, g, b = (int(raw[i:i + 2], 16) for i in (0, 2, 4))
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) < 150


def build_pptx(svg_path, out_dir, name, curve_steps=8):
    root = ET.parse(svg_path).getroot()
    width, height = svg_size(root)
    canvas = Canvas(round(width), round(height))
    shape_count = 0

    for node in root.iter():
        if local_name(node.tag) != "path":
            continue
        data = node.get("d")
        if not data:
            continue
        fill = node.get("fill", "#000000")
        stroke = node.get("stroke")
        fill_opacity = parse_number(node.get("fill-opacity"), 1.0)
        stroke_opacity = parse_number(node.get("stroke-opacity"), 1.0)
        opacity = parse_number(node.get("opacity"), 1.0)
        fill_opacity *= opacity
        stroke_opacity *= opacity
        stroke_width = parse_number(node.get("stroke-width"), 1.0)
        tx, ty = parse_translate(node.get("transform"))

        contours = parse_path(data, max(2, int(curve_steps)))
        dark_multi_contour = (
            len(contours) > 1
            and fill != "none"
            and is_dark_fill(fill)
        )
        for contour in contours:
            points = [(x + tx, y + ty) for x, y in contour]
            if len(points) < 2:
                continue
            if dark_multi_contour:
                # VTracer sometimes packs several thin line regions into one
                # compound path. Filling each subpath separately creates large
                # triangular bridges in PowerPoint, so preserve these as lines.
                canvas.poly(
                    points,
                    fill=None,
                    stroke=fill,
                    sw=2.0,
                    closed=False,
                    stroke_opacity=stroke_opacity,
                )
                shape_count += 1
                continue
            closed = True
            canvas.poly(
                points,
                fill=None if fill == "none" else fill,
                stroke=None if stroke == "none" else stroke,
                sw=stroke_width,
                closed=closed,
                fill_opacity=fill_opacity if fill != "none" else None,
                stroke_opacity=stroke_opacity if stroke else None,
            )
            shape_count += 1

    if not shape_count:
        raise ValueError("no supported filled paths found in SVG")

    os.makedirs(out_dir, exist_ok=True)
    pptx_path = os.path.join(out_dir, name + ".pptx")
    canvas.to_pptx(pptx_path)
    return pptx_path, shape_count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--svg", required=True)
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--name", help="PPTX name without extension")
    ap.add_argument("--curve-steps", type=int, default=8)
    args = ap.parse_args()

    svg = os.path.abspath(args.svg)
    if not os.path.isfile(svg):
        raise SystemExit("SVG not found: %s" % svg)
    name = args.name or os.path.splitext(os.path.basename(svg))[0]
    try:
        path, count = build_pptx(
            svg,
            os.path.abspath(args.out),
            name,
            args.curve_steps,
        )
    except Exception as exc:
        raise SystemExit("SVG to PPTX failed: %s" % exc) from exc
    print("saved %s (%d native freeform shapes)" % (path, count))


if __name__ == "__main__":
    main()
