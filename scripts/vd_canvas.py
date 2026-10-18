"""矢量画布：同一组绘制指令，同时输出 SVG 和 PPTX（原生形状）。

坐标系统一使用原图像素（1948 x 1280）。PPTX 后端按幻灯片宽度等比映射，
因此 SVG 与 PPTX 的布局完全一致。
"""
import base64
import math
import os
from pptx import Presentation
from pptx.util import Emu, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

W, H = 1948, 1280
SLIDE_W_IN = 13.333333
EMU_PER_PX = SLIDE_W_IN * 914400 / W
PT_PER_PX = SLIDE_W_IN * 72 / W
FONT = "Times New Roman"

INK = "#1a1a1a"


def _hex(c):
    return c.lstrip("#")


def _rgb(c):
    return RGBColor.from_string(_hex(c))


def _set_alpha(solid_fill, alpha):
    if solid_fill is None or alpha is None or alpha >= 1:
        return
    from pptx.oxml.ns import qn
    for clr in solid_fill.iter(qn("a:srgbClr")):
        a = clr.find(qn("a:alpha"))
        if a is None:
            a = clr.makeelement(qn("a:alpha"),
                                {"val": str(int(round(alpha * 100000)))})
            clr.append(a)
        else:
            a.set("val", str(int(round(alpha * 100000))))
        break


def _apply_fill_alpha(sh, alpha):
    if alpha is None or alpha >= 1:
        return
    from pptx.oxml.ns import qn
    try:
        spPr = sh._element.spPr
    except Exception:
        return
    _set_alpha(spPr.find(qn("a:solidFill")), alpha)


def _apply_line_alpha(sh, alpha):
    if alpha is None or alpha >= 1:
        return
    from pptx.oxml.ns import qn
    try:
        spPr = sh._element.spPr
    except Exception:
        return
    ln = spPr.find(qn("a:ln"))
    if ln is not None:
        _set_alpha(ln.find(qn("a:solidFill")), alpha)


def est_text_width(s, size):
    w = 0.0
    for ch in s:
        o = ord(ch)
        if o > 0x2E80:
            w += size * 1.0
        elif ch in "ilI.,;:!|'` ":
            w += size * 0.30
        elif ch in "mwMW":
            w += size * 0.86
        elif ch in "()-":
            w += size * 0.40
        else:
            w += size * 0.56
    return w


class Canvas:
    """收集与后端无关的绘制指令。"""

    def __init__(self, width=W, height=H):
        self.ops = []
        self.w = width
        self.h = height
        self.emu_per_px = SLIDE_W_IN * 914400 / width
        self.pt_per_px = SLIDE_W_IN * 72 / width
        self.font_faces = []  # [(family, ttf_path)] 仅 SVG 内嵌用
        self._b64_cache = {}  # 边生成边写盘时避免重复 base64

    def _b64_data(self, path):
        if path not in self._b64_cache:
            with open(path, "rb") as f:
                self._b64_cache[path] = base64.b64encode(f.read()).decode("ascii")
        return self._b64_cache[path]

    # ---------- 基本图元 ----------
    def rect(self, x, y, w, h, fill=None, stroke=None, sw=1.4, rx=0.0, dash=None,
             fill_opacity=None, stroke_opacity=None):
        self.ops.append(dict(k="rect", x=x, y=y, w=w, h=h, fill=fill,
                             stroke=stroke, sw=sw, rx=rx, dash=dash,
                             fill_opacity=fill_opacity,
                             stroke_opacity=stroke_opacity))

    def circle(self, cx, cy, r, fill=None, stroke=None, sw=1.4, dash=None,
               fill_opacity=None, stroke_opacity=None):
        self.ops.append(dict(k="circle", cx=cx, cy=cy, r=r, fill=fill,
                             stroke=stroke, sw=sw, dash=dash,
                             fill_opacity=fill_opacity,
                             stroke_opacity=stroke_opacity))

    def poly(self, pts, fill=None, stroke=None, sw=1.4, closed=False,
             dash=None, arrow_end=False, arrow_start=False, arrow_size=11,
             fill_opacity=None, stroke_opacity=None):
        self.ops.append(dict(k="poly", pts=list(pts), fill=fill, stroke=stroke,
                             sw=sw, closed=closed, dash=dash,
                             arrow_end=arrow_end, arrow_start=arrow_start,
                             arrow_size=arrow_size,
                             fill_opacity=fill_opacity,
                             stroke_opacity=stroke_opacity))

    def image(self, x, y, w, h, path):
        self.ops.append(dict(k="image", x=x, y=y, w=w, h=h, path=path))

    def line(self, x1, y1, x2, y2, stroke=INK, sw=1.4, dash=None,
             arrow_end=False, arrow_start=False, arrow_size=11):
        self.poly([(x1, y1), (x2, y2)], stroke=stroke, sw=sw, dash=dash,
                  arrow_end=arrow_end, arrow_start=arrow_start,
                  arrow_size=arrow_size)

    def arrow(self, x1, y1, x2, y2, stroke=INK, sw=1.4, dash=None,
              arrow_size=11):
        self.line(x1, y1, x2, y2, stroke=stroke, sw=sw, dash=dash,
                  arrow_end=True, arrow_size=arrow_size)

    def text(self, x, y, s, size=22, bold=False, italic=False,
             color=INK, anchor="middle", family=FONT, spacing=None,
             pptx_family=None, svg_weight=None, glyph_x=None,
             svg_baseline=None, glyph_y=None, rotate=0.0,
             pptx_text=None):
        self.ops.append(dict(k="text", x=x, y=y, s=s, size=size, bold=bold,
                             italic=italic, color=color, anchor=anchor,
                             family=family, spacing=spacing,
                             pptx_family=pptx_family,
                             svg_weight=svg_weight, glyph_x=glyph_x,
                             svg_baseline=svg_baseline, glyph_y=glyph_y,
                             rotate=rotate, pptx_text=pptx_text))

    # ---------- 组合图元 ----------
    def hexagon(self, cx, cy, rx, ry, fill, stroke, sw=1.5):
        pts = [(cx - rx, cy), (cx - rx * 0.5, cy - ry), (cx + rx * 0.5, cy - ry),
               (cx + rx, cy), (cx + rx * 0.5, cy + ry), (cx - rx * 0.5, cy + ry)]
        self.poly(pts, fill=fill, stroke=stroke, sw=sw, closed=True)

    def node(self, kind, cx, cy, r=12, fill=None, stroke=None, sw=1.5):
        if kind == "hex":
            self.hexagon(cx, cy, r * 1.18, r, fill, stroke, sw)
        else:
            self.circle(cx, cy, r, fill=fill, stroke=stroke, sw=sw)

    def envelope(self, cx, cy, w=26, h=20, fill="#b9b9bd", stroke="#8f8f95"):
        self.rect(cx - w / 2, cy - h / 2, w, h, fill=fill, stroke=stroke, sw=1.1)
        self.poly([(cx - w / 2, cy - h / 2 + 1), (cx, cy + 2),
                   (cx + w / 2, cy - h / 2 + 1)], stroke="#ffffff", sw=1.2)

    def gating(self, cx, cy, w=26, h=24, fill="#ffffff", stroke="#8a8a90"):
        self.rect(cx - w / 2, cy - h / 2, w, h, fill=fill, stroke=stroke,
                  sw=1.3, rx=3)
        pts = []
        for i in range(21):
            t = -1 + 2 * i / 20
            pts.append((cx + t * w * 0.28, cy - math.tanh(t * 2.2) * h * 0.28))
        self.poly(pts, stroke="#6f6f75", sw=1.2)

    def bell_curve(self, cx, base_y, half_w, height, stroke="#4a90b8",
                   keep_fill="#cfe7f5", remove_fill="#f6c4c9", thresh=0.40):
        pts = []
        for i in range(81):
            t = -1 + 2 * i / 80
            y = base_y - height * math.exp(-(t * 1.9) ** 2)
            pts.append((cx + t * half_w, y))
        # 粉色（阈值左侧，移除）
        cut = thresh
        pnk = [(px, py) for px, py in pts if px <= cx + cut * half_w]
        pnk = [(cx - half_w, base_y)] + pnk + [(cx + cut * half_w, base_y)]
        self.poly(pnk, fill=remove_fill, stroke=None, closed=True)
        # 蓝色（保留）
        blu = [(px, py) for px, py in pts if px >= cx + cut * half_w]
        blu = [(cx + cut * half_w, base_y)] + blu + [(cx + half_w, base_y)]
        self.poly(blu, fill=keep_fill, stroke=None, closed=True)
        self.poly(pts, stroke=stroke, sw=1.6)
        self.line(cx - half_w, base_y, cx + half_w, base_y, stroke="#6b6b70", sw=1.2)

    # ---------- 输出 ----------
    def to_svg(self, path):
        def dash_attr(d):
            if not d:
                return ""
            return f' stroke-dasharray="{d}"'

        def opac_attr(fo, so):
            out = ""
            if fo is not None and fo < 1:
                out += f' fill-opacity="{fo:.3f}"'
            if so is not None and so < 1:
                out += f' stroke-opacity="{so:.3f}"'
            return out

        buf = [f'<svg xmlns="http://www.w3.org/2000/svg" '
               f'xmlns:xlink="http://www.w3.org/1999/xlink" '
               f'width="{self.w}" height="{self.h}" '
               f'viewBox="0 0 {self.w} {self.h}" font-family="{FONT}">',
               f'<rect width="{self.w}" height="{self.h}" fill="white"/>']
        if self.font_faces:
            css = []
            for fam, fpath in self.font_faces:
                css.append(
                    "@font-face{font-family:'%s';src:url(data:font/ttf;"
                    "base64,%s) format('truetype');}"
                    % (fam, self._b64_data(fpath)))
            buf.insert(1, "<defs><style>" + "".join(css) + "</style></defs>")
        for op in self.ops:
            k = op["k"]
            if k == "rect":
                rx = op["rx"]
                buf.append(
                    f'<rect x="{op["x"]:.2f}" y="{op["y"]:.2f}" width="{op["w"]:.2f}" '
                    f'height="{op["h"]:.2f}" rx="{rx:.2f}"'
                    f' fill="{op["fill"] or "none"}" stroke="{op["stroke"] or "none"}"'
                    f' stroke-width="{op["sw"]}"{dash_attr(op["dash"])}'
                    f'{opac_attr(op["fill_opacity"], op["stroke_opacity"])}/>')
            elif k == "circle":
                buf.append(
                    f'<circle cx="{op["cx"]:.2f}" cy="{op["cy"]:.2f}" r="{op["r"]:.2f}"'
                    f' fill="{op["fill"] or "none"}" stroke="{op["stroke"] or "none"}"'
                    f' stroke-width="{op["sw"]}"{dash_attr(op["dash"])}'
                    f'{opac_attr(op["fill_opacity"], op["stroke_opacity"])}/>')
            elif k == "poly":
                pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in op["pts"])
                tag = "polygon" if op["closed"] else "polyline"
                buf.append(
                    f'<{tag} points="{pts}" fill="{op["fill"] or "none"}"'
                    f' stroke="{op["stroke"] or "none"}" stroke-width="{op["sw"]}"'
                    f' stroke-linejoin="round"'
                    f' stroke-linecap="{"butt" if op["dash"] else "round"}"'
                    f'{dash_attr(op["dash"])}'
                    f'{opac_attr(op["fill_opacity"], op["stroke_opacity"])}/>')
                self._svg_arrow(buf, op["pts"], op, at_end=True)
                self._svg_arrow(buf, op["pts"], op, at_end=False)
            elif k == "image":
                ext = os.path.splitext(op["path"])[1].lower().lstrip(".")
                mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
                data = self._b64_data(op["path"])
                buf.append(
                    f'<image x="{op["x"]:.2f}" y="{op["y"]:.2f}" '
                    f'width="{op["w"]:.2f}" height="{op["h"]:.2f}" '
                    f'preserveAspectRatio="none" '
                    f'xlink:href="data:{mime};base64,{data}"/>')
            elif k == "text":
                s = (op["s"].replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;"))
                weight = op.get("svg_weight") or (
                    "bold" if op["bold"] else "normal")
                style = "italic" if op["italic"] else "normal"
                if op["anchor"] == "middle":
                    anchor, extra = "middle", ""
                elif op["anchor"] in ("start", "left"):
                    anchor, extra = "start", ""
                else:
                    anchor, extra = "end", ""
                glyph_x = op.get("glyph_x")
                glyph_y = op.get("glyph_y")
                rot = op.get("rotate") or 0.0
                baseline = op.get("svg_baseline")
                if baseline is not None:
                    ty = baseline
                    vpos = ' dominant-baseline="auto"'
                else:
                    ty = op["y"]
                    vpos = ' dominant-baseline="central"'
                if glyph_x and len(glyph_x) == len(op["s"]):
                    # 逐字符按 PDF 原点定位，字距与原文完全一致
                    gy_ok = glyph_y and len(glyph_y) == len(op["s"])
                    for i, (ch, gx) in enumerate(zip(op["s"], glyph_x)):
                        if ch == " ":
                            continue
                        gy = glyph_y[i] if gy_ok else ty
                        tr = (f' transform="rotate({rot:.2f} {gx:.2f} {gy:.2f})"'
                              if abs(rot) > 1e-6 else "")
                        esc = (ch.replace("&", "&amp;").replace("<", "&lt;")
                               .replace(">", "&gt;"))
                        buf.append(
                            f'<text x="{gx:.2f}" y="{gy:.2f}"'
                            f' font-size="{op["size"]}" font-weight="{weight}"'
                            f' font-style="{style}" fill="{op["color"]}"'
                            f' text-anchor="start"{vpos}{tr}'
                            f' font-family="{op["family"]}">{esc}</text>')
                else:
                    tr = (f' transform="rotate({rot:.2f} {op["x"]:.2f} {ty:.2f})"'
                          if abs(rot) > 1e-6 else "")
                    buf.append(
                        f'<text x="{op["x"]:.2f}" y="{ty:.2f}" font-size="{op["size"]}"'
                        f' font-weight="{weight}" font-style="{style}" fill="{op["color"]}"'
                        f' text-anchor="{anchor}"{vpos}{tr}'
                        f' font-family="{op["family"]}">{s}</text>')
        buf.append("</svg>")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(buf))

    @staticmethod
    def _svg_arrow(buf, pts, op, at_end):
        flag = op["arrow_end"] if at_end else op["arrow_start"]
        if not flag or not op["stroke"]:
            return
        size = op["arrow_size"]
        if at_end:
            (x1, y1), (x2, y2) = pts[-2], pts[-1]
        else:
            (x1, y1), (x2, y2) = pts[1], pts[0]
        ang = math.atan2(y2 - y1, x2 - x1)
        a = size * 0.42
        p1 = (x2 - size * math.cos(ang) + a * math.sin(ang),
              y2 - size * math.sin(ang) - a * math.cos(ang))
        p2 = (x2 - size * math.cos(ang) - a * math.sin(ang),
              y2 - size * math.sin(ang) + a * math.cos(ang))
        s = " ".join(f"{x:.2f},{y:.2f}" for x, y in [p1, (x2, y2), p2])
        buf.append(f'<polygon points="{s}" fill="{op["stroke"]}" stroke="none"/>')

    def to_pptx(self, path):
        prs = Presentation()
        # python-pptx 模板自带 lastModifiedBy/描述，清掉免得带出无关元数据
        cp = prs.core_properties
        cp.author = ""
        cp.last_modified_by = ""
        cp.comments = ""
        cp.title = ""
        cp.subject = ""
        prs.slide_width = Emu(int(self.w * self.emu_per_px))
        prs.slide_height = Emu(int(self.h * self.emu_per_px))
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        shapes = slide.shapes

        def E(v):
            return Emu(int(round(v * self.emu_per_px)))

        def style(sh, fill, stroke, sw, dash, fill_opacity=None,
                  stroke_opacity=None):
            if fill:
                sh.fill.solid()
                sh.fill.fore_color.rgb = _rgb(fill)
            else:
                sh.fill.background()
            if stroke:
                sh.line.color.rgb = _rgb(stroke)
                sh.line.width = Pt(max(0.0, sw * self.pt_per_px))
                if dash:
                    from pptx.enum.dml import MSO_LINE_DASH_STYLE
                    sh.line.dash_style = MSO_LINE_DASH_STYLE.DASH
            else:
                sh.line.fill.background()
            try:
                sh.shadow.inherit = False
            except Exception:
                pass
            _apply_fill_alpha(sh, fill_opacity)
            _apply_line_alpha(sh, stroke_opacity)

        def add_poly(pts, close):
            b = shapes.build_freeform(pts[0][0], pts[0][1],
                                     scale=self.emu_per_px)
            b.add_line_segments(pts[1:], close=close)
            return b.convert_to_shape()

        for op in self.ops:
            k = op["k"]
            if k == "rect":
                rx = op["rx"]
                if rx > 0:
                    sh = shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                          E(op["x"]), E(op["y"]),
                                          E(op["w"]), E(op["h"]))
                    try:
                        sh.adjustments[0] = min(0.5, rx / max(1, min(op["w"], op["h"])))
                    except Exception:
                        pass
                else:
                    sh = shapes.add_shape(MSO_SHAPE.RECTANGLE,
                                          E(op["x"]), E(op["y"]),
                                          E(op["w"]), E(op["h"]))
                style(sh, op["fill"], op["stroke"], op["sw"], op["dash"],
                      op["fill_opacity"], op["stroke_opacity"])
                sh.text_frame.text = ""
            elif k == "circle":
                sh = shapes.add_shape(MSO_SHAPE.OVAL, E(op["cx"] - op["r"]),
                                      E(op["cy"] - op["r"]), E(2 * op["r"]),
                                      E(2 * op["r"]))
                style(sh, op["fill"], op["stroke"], op["sw"], op["dash"],
                      op["fill_opacity"], op["stroke_opacity"])
            elif k == "poly":
                sh = add_poly(op["pts"], op["closed"])
                style(sh, op["fill"] if op["closed"] else None,
                      op["stroke"], op["sw"], op["dash"],
                      op["fill_opacity"], op["stroke_opacity"])
                # freeform 的原点在首个局部坐标，这里改为按实际范围重设位置
                # （python-pptx 已按局部坐标放置，无需额外处理）
                if op["arrow_end"] or op["arrow_start"]:
                    self._pptx_arrows(shapes, E, op)
            elif k == "image":
                shapes.add_picture(op["path"], E(op["x"]), E(op["y"]),
                                   E(op["w"]), E(op["h"]))
            elif k == "text":
                self._pptx_text(shapes, E, op)
        prs.save(path)

    def _pptx_arrows(self, shapes, E, op):
        pts = op["pts"]
        color = op["stroke"]
        if not color:
            return
        size = op["arrow_size"]

        def tri(x1, y1, x2, y2):
            ang = math.atan2(y2 - y1, x2 - x1)
            a = size * 0.42
            p1 = (x2 - size * math.cos(ang) + a * math.sin(ang),
                  y2 - size * math.sin(ang) - a * math.cos(ang))
            p2 = (x2 - size * math.cos(ang) - a * math.sin(ang),
                  y2 - size * math.sin(ang) + a * math.cos(ang))
            b = shapes.build_freeform(p1[0], p1[1], scale=EMU_PER_PX)
            b.add_line_segments([(x2, y2), p2], close=True)
            sh = b.convert_to_shape()
            sh.fill.solid()
            sh.fill.fore_color.rgb = _rgb(color)
            sh.line.fill.background()
            try:
                sh.shadow.inherit = False
            except Exception:
                pass

        if op["arrow_end"] and len(pts) >= 2:
            tri(pts[-2][0], pts[-2][1], pts[-1][0], pts[-1][1])
        if op["arrow_start"] and len(pts) >= 2:
            tri(pts[1][0], pts[1][1], pts[0][0], pts[0][1])

    def _pptx_text(self, shapes, E, op):
        s = op.get("pptx_text") or op["s"]
        size_px = op["size"]
        size_pt = max(1.0, size_px * self.pt_per_px)
        w = est_text_width(s, size_px) * 1.25 + 12
        h = size_px * 1.5
        if op["anchor"] == "middle":
            left = op["x"] - w / 2
        elif op["anchor"] == "left":
            left = op["x"]
        elif op["anchor"] == "start":
            left = op["x"] - w * 0.06
        else:
            left = op["x"] - w * 0.94
        top = op["y"] - h / 2
        rot = op.get("rotate") or 0.0
        if abs(rot) > 1e-6:
            # PowerPoint 绕文本框中心旋转；把中心挪回去让文字起点落在原点
            a = math.radians(rot)
            start_dx = left - (left + w / 2)
            cx = op["x"] - start_dx * math.cos(a)
            cy = op["y"] - start_dx * math.sin(a)
            left, top = cx - w / 2, cy - h / 2
        tb = shapes.add_textbox(E(left), E(top), E(w), E(h))
        if abs(rot) > 1e-6:
            tb.rotation = rot
        tf = tb.text_frame
        tf.word_wrap = False
        tf.margin_left = 0
        tf.margin_right = 0
        tf.margin_top = 0
        tf.margin_bottom = 0
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = tf.paragraphs[0]
        p.alignment = (PP_ALIGN.LEFT if op["anchor"] == "left"
                       else PP_ALIGN.CENTER)
        r = p.add_run()
        r.text = s
        r.font.size = Pt(size_pt)
        r.font.name = op.get("pptx_family") or op["family"]
        r.font.bold = op["bold"]
        r.font.italic = op["italic"]
        r.font.color.rgb = _rgb(op["color"])
