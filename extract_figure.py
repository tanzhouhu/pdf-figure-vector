"""把 PDF 页面指定区域重建为矢量 SVG / PPTX。

用法示例：
  python extract_figure.py --pdf paper.pdf --page 3 \
      --bbox 54,56,542.5,225.5 --out C:\\out\\fig2 --name Figure2

bbox 是 PDF 的 point 坐标，顺序 x0,y0,x1,y1。--page 是 0 起的页索引。
加 --live 会边生成边写 SVG（配合 preview.html 逐元素观看）。
"""
import argparse
import io
import math
import os
import re
import subprocess
import sys
import time
import unicodedata

import fitz
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, ImageStat

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from vd_canvas import Canvas  # noqa: E402


# ---------------- 几何 / 颜色 ----------------

def hexcol(rgb):
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, int(round(v * 255)))) for v in rgb)


_SUSPECT_CODES = (set(range(0x21, 0x30)) | set(range(0x3A, 0x41))
                  | set(range(0x5B, 0x61)) | set(range(0x7B, 0x7F))
                  | {0x00B7, 0x00B0, 0x00A0, 0x00AD, 0x2019})


def looks_broken(text, eff_text=None):
    """span 里出现可疑标点码位，可能是 ToUnicode 把字形映射错了。

    '#'、'!' 常是下标 0/1，'·'(U+00B7) 之类也会混进来。短 span 有一个
    可疑字符就试；整串下标（如 'overall' 被映射成 "$%&'())"）字符多但
    一半以上都可疑，也试。解出来和原文一样就不动。

    还有一种坏法：整个 span 的每个字形都被映射成同一个字符
    （'PoE' → 'PPP'，'concat' → 'cccccc'），码位本身是正常字母，
    只能靠「非空白字符全相同」识别。
    """
    if not text:
        return False
    n_sus = sum(ord(c) in _SUSPECT_CODES for c in text)
    if len(text) <= 6 and n_sus > 0:
        return True
    if len(text) <= 24 and n_sus * 2 >= len(text):
        return True
    # 这段 PDF 常给每个字形额外塞一个 gid=-1 的重复占位，判断「整串同
    # 字符」时要看真正有字形的那些（eff_text），否则每个 'q' 都会被误判。
    src = eff_text if eff_text is not None else text
    letters = [c for c in src if not c.isspace()]
    if (len(letters) >= 2 and len(set(letters)) == 1
            and letters[0] not in ".·⋅⋯…-_"):
        return True
    return False


def bbox_of(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def contains(outer, inner, tol=0.05):
    return (outer[0] - tol <= inner[0] and outer[1] - tol <= inner[1]
            and outer[2] + tol >= inner[2] and outer[3] + tol >= inner[3])


def densify(pts, step=0.4):
    out = []
    for i, (x, y) in enumerate(pts):
        out.append((x, y))
        if i + 1 < len(pts):
            x2, y2 = pts[i + 1]
            n = int(math.hypot(x2 - x, y2 - y) / step)
            for k in range(1, n):
                t = k / n
                out.append((x + (x2 - x) * t, y + (y2 - y) * t))
    return out


def circle_of(pts):
    pts = densify(pts)
    bx = bbox_of(pts)
    cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
    rs = [math.hypot(x - cx, y - cy) for x, y in pts]
    if not rs:
        return None
    mean = sum(rs) / len(rs)
    if mean < 1e-6 or (max(rs) - min(rs)) > 0.12 * mean:
        return None
    return cx, cy, mean


def corner_radius(pts):
    bx = bbox_of(pts)
    top = [x for x, y in pts if y < bx[1] + 0.35]
    if len(top) >= 2:
        return max(0.0, min(top) - bx[0])
    return 0.0


def rdp(pts, eps=0.008):
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    norm = math.hypot(dx, dy)
    best, idx = -1.0, -1
    for i in range(1, len(pts) - 1):
        px, py = pts[i]
        if norm < 1e-9:
            d = math.hypot(px - a[0], py - a[1])
        else:
            d = abs(dy * px - dx * py + b[0] * a[1] - b[1] * a[0]) / norm
        if d > best:
            best, idx = d, i
    if best > eps:
        left = rdp(pts[:idx + 1], eps)
        right = rdp(pts[idx:], eps)
        return left[:-1] + right
    return [a, b]


def subpaths(dr, curve_steps=32):
    """把 PyMuPDF 的 items 拆成连续子路径，三次曲线采样成折线。"""
    subs, cur, end = [], None, None
    eps = 0.02
    for it in dr["items"]:
        k = it[0]
        if k == "l":
            _, a, b = it
            if cur is None or (end is not None and
                               (abs(a.x - end.x) > eps or abs(a.y - end.y) > eps)):
                if cur:
                    subs.append(cur)
                cur = [(a.x, a.y)]
            cur.append((b.x, b.y))
            end = b
        elif k == "c":
            _, a, b, c, d = it
            if cur is None or (end is not None and
                               (abs(a.x - end.x) > eps or abs(a.y - end.y) > eps)):
                if cur:
                    subs.append(cur)
                cur = [(a.x, a.y)]
            p0 = a
            for i in range(1, curve_steps + 1):
                t = i / float(curve_steps)
                mt = 1 - t
                cur.append((
                    mt ** 3 * p0.x + 3 * mt * mt * t * b.x
                    + 3 * mt * t * t * c.x + t ** 3 * d.x,
                    mt ** 3 * p0.y + 3 * mt * mt * t * b.y
                    + 3 * mt * t * t * c.y + t ** 3 * d.y))
            end = d
        elif k == "re":
            r = it[1]
            if cur:
                subs.append(cur)
            cur = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
            end = fitz.Point(r.x0, r.y0)
        elif k == "qu":
            q = it[1]
            if cur:
                subs.append(cur)
            cur = [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y),
                   (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
            end = q.ll
    if cur:
        subs.append(cur)
    return subs


# ---------------- 字体 ----------------

def pptx_family(name):
    n = name.lower()
    if "optima" in n:
        return "Candara"
    if "helvetica" in n or "arial" in n:
        return "Arial"
    if "times" in n or "nimbusrom" in n:
        return "Times New Roman"
    if "palatino" in n or "bookantiqua" in n:
        return "Palatino Linotype"
    if "calibri" in n:
        return "Calibri"
    if "cambriamath" in n:
        return "Cambria Math"
    if "cambria" in n:
        return "Cambria"
    return "Arial"


def patch_subset_font(path, bold):
    """补 Windows cmap 子表与 OS/2，否则 Chrome 的 OTS 会拒绝加载。"""
    from fontTools.ttLib import TTFont, newTable
    from fontTools.ttLib.tables.O_S_2f_2 import Panose
    from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

    f = TTFont(path)
    # 换掉 post 表后 fontTools 就再也拿不到字形名（format 3.0 不存名字），
    # 保存时会抛 "illegal use of getGlyphOrder()" 并把文件写坏。先记下来。
    try:
        order = f.getGlyphOrder()
    except Exception:
        order = None
    # 没有 cmap 的子集（PowerPoint 导出的 CambriaMath 常见）补不出
    # Windows 子表，但仍然要补 post/OS/2：字形轮廓还能按 gid 用。
    if "cmap" in f:
        cmap = f["cmap"]
        if not any(t.platformID == 3 and t.platEncID == 1 for t in cmap.tables):
            sub = CmapSubtable.newSubtable(4)
            sub.platformID, sub.platEncID, sub.language = 3, 1, 0
            sub.cmap = dict(cmap.tables[0].cmap)
            cmap.tables.append(sub)
    # 子集常常没有 post 表，或 format 2 的 stringData 长度对不上；
    # Chrome 的 OTS 会直接拒绝整个字体（post: missing required table）。
    post = newTable("post")
    post.formatType = 3.0
    post.italicAngle = 0.0
    post.underlinePosition = -100
    post.underlineThickness = 50
    post.isFixedPitch = 0
    post.minMemType42 = 0
    post.maxMemType42 = 0
    post.minMemType1 = 0
    post.maxMemType1 = 0
    f["post"] = post
    if order:
        f.setGlyphOrder(order)
    if "OS/2" not in f:
        head, hhea = f["head"], f["hhea"]
        em = head.unitsPerEm
        o = newTable("OS/2")
        o.version = 4
        o.xAvgCharWidth = int(em * 0.5)
        o.usWeightClass = 700 if bold else 400
        o.usWidthClass = 5
        o.fsType = 0
        o.ySubscriptXSize = int(em * 0.65)
        o.ySubscriptYSize = int(em * 0.6)
        o.ySubscriptXOffset = 0
        o.ySubscriptYOffset = int(em * 0.075)
        o.ySuperscriptXSize = int(em * 0.65)
        o.ySuperscriptYSize = int(em * 0.6)
        o.ySuperscriptXOffset = 0
        o.ySuperscriptYOffset = int(em * 0.35)
        o.yStrikeoutSize = int(em * 0.05)
        o.yStrikeoutPosition = int(em * 0.25)
        o.sFamilyClass = 0
        o.panose = Panose()
        o.ulUnicodeRange1 = o.ulUnicodeRange2 = 0
        o.ulUnicodeRange3 = o.ulUnicodeRange4 = 0
        o.achVendID = "NONE"
        o.fsSelection = 0x20 if bold else 0x40
        o.usFirstCharIndex = 0
        o.usLastCharIndex = 0xFFFF
        o.sTypoAscender = hhea.ascent
        o.sTypoDescender = hhea.descent
        o.sTypoLineGap = 0
        o.usWinAscent = hhea.ascent
        o.usWinDescent = -hhea.descent
        o.ulCodePageRange1 = 1
        o.ulCodePageRange2 = 0
        o.sxHeight = 0
        o.sCapHeight = 0
        o.usDefaultChar = 0
        o.usBreakChar = 32
        o.usMaxContext = 0
        o.usLowerOpticalPointSize = 0
        o.usUpperOpticalPointSize = 0xFFFF
        f["OS/2"] = o
    f.save(path)


_SYS_FONT_HINTS = {
    "cambriamath": ("cambria.ttc", 1),
    "cambria": ("cambria.ttc", 0),
    "palatinolinotype": ("pala.ttf", 0),
    "timesnewroman": ("times.ttf", 0),
    "arial": ("arial.ttf", 0),
    "calibri": ("calibri.ttf", 0),
    "candara": ("candara.ttf", 0),
}


def system_font_path(family):
    """按字体族名找系统字体文件，返回 (path, ttc_index) 或 None。"""
    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    key = re.sub(r"[^a-z0-9]", "", (family or "").lower())
    if not key or not os.path.isdir(fonts_dir):
        return None
    hint = _SYS_FONT_HINTS.get(key)
    if hint:
        p = os.path.join(fonts_dir, hint[0])
        if os.path.exists(p):
            return p, hint[1]
    for ext in (".ttf", ".otf", ".ttc"):
        p = os.path.join(fonts_dir, key + ext)
        if os.path.exists(p):
            return p, 0
    return None


def glyph_mask(text, fnt, px=128):
    """渲染一路字形并归一化成 64x64 掩膜（只比形状，不比位置）。"""
    im = Image.new("L", (px * 4, px * 3), 255)
    ImageDraw.Draw(im).text((px, px * 2), text, font=fnt, fill=0, anchor="ls")
    a = np.array(im)
    ys, xs = np.where(a < 128)
    if not len(xs):
        return None
    box = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = box.shape
    m = max(h, w)
    sq = np.full((m, m), 255, dtype=np.uint8)
    sq[(m - h) // 2:(m - h) // 2 + h, (m - w) // 2:(m - w) // 2 + w] = box
    small = Image.fromarray(sq).resize((64, 64), Image.LANCZOS)
    return np.array(small) < 128


def mask_iou(a, b):
    if a is None or b is None:
        return 0.0
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum()) / float(u) if u else 0.0


class FigureBuilder:
    _cands = None

    def __init__(self, pdf, page_no, bbox, out, name="Figure",
                 width=1948, margin=8, curve_steps=32, simplify_eps=0.008):
        self.doc = fitz.open(pdf)
        self.page = self.doc[page_no]
        self.X0, self.Y0, self.X1, self.Y1 = bbox
        self.out = out
        self.name = name
        self.W = width
        self.margin = margin
        self.curve_steps = max(4, int(curve_steps))
        self.simplify_eps = max(0.0001, float(simplify_eps))
        self.S = (width - 2 * margin) / (self.X1 - self.X0)
        self.H = int(round((self.Y1 - self.Y0) * self.S + 2 * margin))
        self.OX = margin
        self.OY = margin
        self.assets = os.path.join(out, "assets")
        self.fonts = os.path.join(out, "fonts")
        os.makedirs(self.assets, exist_ok=True)
        os.makedirs(self.fonts, exist_ok=True)
        self.font_map = {}
        self.subsets = {}
        self.span_font = {}
        self.span_score = {}
        self.glyph_dec = {}
        self.gid_fonts = {}
        self.gid_dec = {}
        self.cand_masks = {}
        self.sys_fonts = {}
        self.clip = fitz.Rect(self.X0 - 0.5, self.Y0 - 0.5,
                              self.X1 + 0.5, self.Y1 + 0.5)

    def T(self, x):
        return self.OX + (x - self.X0) * self.S

    def U(self, y):
        return self.OY + (y - self.Y0) * self.S

    def inside(self, r):
        cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
        return (self.clip.x0 <= cx <= self.clip.x1
                and self.clip.y0 <= cy <= self.clip.y1)

    def parse_dash(self, dashes):
        if not dashes:
            return None
        m = re.findall(r"[\d.]+", dashes)
        if not m:
            return None
        vals = [float(v) for v in m[:len(m) - 1] if float(v) > 0]
        if not vals:
            return None
        return " ".join(f"{v * self.S:.2f}" for v in vals)

    def font_needs(self):
        """图内每种字体实际要渲染的字符，用来验证子集是否覆盖。"""
        need = {}
        for sp in self.page.get_texttrace():
            if sp.get("type") != 0 or not sp.get("chars"):
                continue
            if not self.inside(sp["bbox"]):
                continue
            base = (sp.get("font") or "").split("+")[-1]
            chars = need.setdefault(base, set())
            for ch in sp["chars"]:
                if ch[0]:
                    chars.add(chr(ch[0]))
        return need

    def register_fonts(self, cv):
        """注册全部字体子集。

        PDF 里同名子集常有好几个（按字符切分），CJK/数学字体尤其如此：
        只认最大的那个会让别的 span 拿错字形甚至整段回退。这里全部登记，
        每个 span 再按「码位→字形号」或栅格比对挑真正对得上的那个。
        """
        raw = {}
        for xref, ext, ftype, basefont, fname, enc in self.page.get_fonts():
            if ext not in ("ttf", "otf"):
                continue
            base = basefont.split("+")[-1]
            info = self.doc.extract_font(xref)
            if not info or not info[3]:
                continue
            raw.setdefault(base, []).append((xref, info[3]))
        need = self.font_needs()
        for base, entries in raw.items():
            fam_base = "PF" + re.sub(r"[^A-Za-z0-9]", "", base)
            subs = []
            for xref, buf in sorted(entries):
                fam = "%s_%d" % (fam_base, xref)
                path = os.path.join(self.fonts, fam + ".ttf")
                with open(path, "wb") as f:
                    f.write(buf)
                try:
                    patch_subset_font(path, "bold" in base.lower())
                    from fontTools.ttLib import TTFont
                    tt = TTFont(path)
                    cmap = tt.getBestCmap() or {}
                    order = tt.getGlyphOrder()
                except Exception as e:  # 损坏的子集就跳过
                    print("font patch failed:", base, xref, e)
                    # 有些子集（PowerPoint 导出的 CambriaMath 常见）没有
                    # cmap，补表必失败，但字形轮廓仍然可用：照样登记，
                    # 让按 gid 反查真字符的路径能用；不进 cv.font_faces，
                    # Chrome 那边仍回退系统字体。
                    try:
                        from fontTools.ttLib import TTFont
                        tt = TTFont(path)
                        order = tt.getGlyphOrder()
                    except Exception:
                        continue
                    subs.append(dict(fam=fam, path=path, cmap={}, gid={},
                                     size=len(buf), base=base, xref=xref))
                    continue
                gid = {u: order.index(n) for u, n in cmap.items()}
                subs.append(dict(fam=fam, path=path, cmap=cmap, gid=gid,
                                 size=len(buf), base=base, xref=xref))
                # 没有 cmap 的子集内嵌给 Chrome 也会被 OTS 拒绝，别放进去
                if cmap:
                    cv.font_faces.append((fam, path))
            if not subs:
                continue
            self.subsets[base.lower()] = subs
            # 默认字体：覆盖整图所需字符的最好子集；都不覆盖则走系统字体
            best = None
            for sub in sorted(subs, key=lambda d: -d["size"]):
                if all(ord(c) in sub["cmap"] for c in need.get(base, ())):
                    best = sub
                    break
            if best:
                self.font_map[base.lower()] = best["fam"]
            else:
                miss = len([c for c in need.get(base, ())
                            if not any(ord(c) in s["cmap"] for s in subs)])
                print("font %s 无子集覆盖全部字符（缺 %d 个），缺字回退系统字体 %s"
                      % (base, miss, pptx_family(base)))

    def svg_family(self, name):
        base = name.split("+")[-1].lower()
        return self.font_map.get(base, pptx_family(name))

    @staticmethod
    def span_key(sp):
        # 同一 (码位, gid) 在不同子集可以是不同字形，缓存必须按 span 区分
        base = (sp.get("font") or "").split("+")[-1].lower()
        chars = [c for c in sp["chars"] if c[0]]
        return (base, tuple((c[0], c[1]) for c in chars),
                round(sp["bbox"][0], 2), round(sp["bbox"][1], 2))

    def span_subset(self, sp):
        """给一个文字 span 挑字体子集；挑不出来返回 None（走系统字体）。

        优先用「码位→字形号」完全对上的子集；对不上再拿子集渲染结果和
        页面栅格比 IoU。结果按（字体, 码位+字形号序列）缓存。
        """
        base = (sp.get("font") or "").split("+")[-1].lower()
        chars = [c for c in sp["chars"] if c[0]]
        key = self.span_key(sp)
        if key in self.span_font:
            return self.span_font[key]
        subs = self.subsets.get(base) or []
        pick = None
        if subs:
            exact = [s for s in subs
                     if all(c[0] in s["cmap"] for c in chars)]
            by_gid = [s for s in exact
                      if all(s["gid"][c[0]] == c[1] for c in chars)]
            cands = by_gid or exact
            if cands:
                # 子集按码位切分，不同子集的同名 gid 会撞车（'#' 在一个子集
                # 里是 0、另一个里是省略号），必须用页面栅格确认；单一候选
                # 也验，验不过再退到全部子集比较。
                pick, score = self.match_subset(sp, cands)
                if score < 0.28 and cands is not exact and exact:
                    alt, alt_score = self.match_subset(sp, exact)
                    if alt_score > score:
                        pick, score = alt, alt_score
                if score < 0.28:
                    pick = by_gid[0] if by_gid else None
            else:
                score = 0.0
        else:
            score = 0.0
        self.span_font[key] = pick
        self.span_score[key] = score
        return pick

    def match_subset(self, sp, cands, Z=12):
        """用页面栅格判断哪个子集渲染得对（同名字体可能编码各不相同）。

        阈值按裁切区域的中位亮度自适应，浅色底上的深色字也能分出来。
        返回 (子集, 分数)，分数低说明谁都对不上。
        """
        clip = fitz.Rect(sp["bbox"])
        clip = fitz.Rect(clip.x0 - 0.15, clip.y0 - 0.15,
                         clip.x1 + 0.15, clip.y1 + 0.15)
        pm = self.page.get_pixmap(matrix=fitz.Matrix(Z, Z),
                                  clip=clip, alpha=False)
        lum = np.array(Image.frombytes(
            "RGB", (pm.width, pm.height), pm.samples).convert("L"))
        bg = float(np.median(lum))
        ref = lum < (bg - 35)
        text = "".join(chr(c[0]) for c in sp["chars"] if c[0])
        ox, oy = sp["chars"][0][2]
        px = max(6, int(round(sp["size"] * Z)))
        best = (-1.0, cands[0])
        for sub in cands:
            im = Image.new("L", (ref.shape[1], ref.shape[0]), 255)
            try:
                fnt = ImageFont.truetype(sub["path"], px)
            except Exception:
                continue
            ImageDraw.Draw(im).text(
                ((ox - clip.x0) * Z, (oy - clip.y0) * Z), text,
                font=fnt, fill=0, anchor="ls")
            a = np.array(im) < 160
            u = np.logical_or(ref, a).sum()
            score = float(np.logical_and(ref, a).sum()) / float(u) if u else 0
            if score > best[0]:
                best = (score, sub)
        return best[1], best[0]

    def decode_span(self, sp, sub):
        """逐字反查，返回 (文本, 最低置信度)。

        子集由 span_subset 的页面栅格匹配选定；这里不再跨子集乱跳——
        同一码位在别的子集里可能是完全不同的字形，跳过去只会解出别的字。
        """
        chars = [c for c in sp["chars"] if c[0]]
        got = [self.decode_char(sub, c[0]) for c in chars]
        return "".join(g[0] for g in got), min(g[1] for g in got)

    def decode_char(self, sub, code):
        """反查 broken ToUnicode：拿子集字形和系统字体逐字比轮廓。

        PDF 有时把下标编码成 '!'/'"' 之类，视觉上是 𝑨/𝟏。SVG 用子集字体
        能画对，但 PPTX 需要真实字符，这里按字形掩膜反查。
        返回 (字符, 置信度)；置信度不足时返回原字符。
        """
        ch = chr(code)
        key = (sub["fam"], code)
        if key in self.glyph_dec:
            return self.glyph_dec[key]
        res = (ch, 0.0)
        try:
            sub_fnt = ImageFont.truetype(sub["path"], 128)
        except Exception:
            self.glyph_dec[key] = res
            return res
        tm = glyph_mask(ch, sub_fnt)
        sysf = self.system_font(sub)
        if tm is not None and sysf is not None:
            best = (0.0, ch)
            for cand in self.decode_candidates():
                m = self.cand_mask(cand, sysf)
                s = mask_iou(tm, m)
                if s > best[0]:
                    best = (s, cand)
            if best[0] >= 0.70:
                # 数学字母/粗体数字统一折回 ASCII，PPTX 里字重才一致
                dec = unicodedata.normalize("NFKC", best[1])
                res = (dec, best[0])
                if dec != ch:
                    print("glyph repair: %r -> %r (IoU %.2f)"
                          % (ch, dec, best[0]))
        self.glyph_dec[key] = res
        return res

    @staticmethod
    def _codes_repeat(sp):
        """span 的码位是否互相重复（字形靠 gid 才能区分）。"""
        chars = [c for c in sp["chars"] if c[0] and c[1] >= 0]
        codes = [c[0] for c in chars if not chr(c[0]).isspace()]
        return len(codes) >= 2 and len(set(codes)) < len(codes)

    def gid_font(self, sub):
        """把子集字体重打包成「U+E000+gid → 字形」的临时字体。

        有的子集（常见于 PowerPoint 导出的 CambriaMath）完全没有 cmap，
        既不能内嵌给 Chrome，也不能用码位渲染；但按 gid 取字形仍可用。
        """
        key = sub.get("xref") or abs(hash(sub.get("path", ""))) % 10**8
        if key in self.gid_fonts:
            return self.gid_fonts[key]
        fnt = None
        try:
            from fontTools.ttLib import TTFont
            from fontTools.ttLib.tables._c_m_a_p import (
                CmapSubtable, table__c_m_a_p)
            t = TTFont(sub["path"])
            names = t.getGlyphOrder()
            cm = CmapSubtable.newSubtable(4)
            cm.platformID, cm.platEncID, cm.language = 3, 1, 0
            cm.cmap = {0xE000 + i: n for i, n in enumerate(names)}
            cmap = table__c_m_a_p()
            cmap.tableVersion = 0
            cmap.tables = [cm]
            t["cmap"] = cmap
            path = os.path.join(self.assets, "gid_%s.ttf" % key)
            t.save(path)
            fnt = ImageFont.truetype(path, 128)
        except Exception as e:
            print("gid font failed: %s %s" % (sub.get("base"), e))
        self.gid_fonts[key] = fnt
        return fnt

    def decode_gid(self, sub, gid):
        """按字形号反查真字符（返回原始数学字符, 置信度）。"""
        key = (sub.get("fam"), gid)
        if key in self.gid_dec:
            return self.gid_dec[key]
        res = ("", 0.0)
        fnt = self.gid_font(sub)
        sysf = self.system_font(sub)
        if fnt is not None and sysf is not None and gid < 0x1100:
            tm = glyph_mask(chr(0xE000 + gid), fnt)
            if tm is not None:
                best = (0.0, "")
                for cand in self.decode_candidates():
                    s = mask_iou(tm, self.cand_mask(cand, sysf))
                    if s > best[0]:
                        best = (s, cand)
                if best[0] >= 0.70:
                    res = (best[1], best[0])
        self.gid_dec[key] = res
        return res

    def decode_span_gid(self, sp, subs):
        """逐字形按 gid 反查，返回 (文本, 最低置信度)。

        码位不可信时（整串同字符、或者子集根本没有 cmap）只能走这条路。
        同一 gid 在不同子集里是不同字形，所以逐字取所有子集里最像的那个。
        """
        out, confs = [], []
        for c in sp["chars"]:
            if not c[0] or c[1] < 0:
                continue
            ch = chr(c[0])
            if ch.isspace():
                out.append(ch)
                confs.append(1.0)
                continue
            best = ("", 0.0)
            for sub in subs:
                got, score = self.decode_gid(sub, c[1])
                if score > best[1]:
                    best = (got, score)
            out.append(best[0] or ch)
            confs.append(best[1])
        return "".join(out), (min(confs) if confs else 0.0)

    def system_font(self, sub):
        fam = pptx_family(sub["base"])  # 子集名 → 系统近似族
        if fam in self.sys_fonts:
            return self.sys_fonts[fam]
        info = system_font_path(fam)
        fnt = None
        if info:
            try:
                fnt = ImageFont.truetype(info[0], 128, index=info[1])
            except Exception:
                fnt = None
        self.sys_fonts[fam] = fnt
        return fnt

    @staticmethod
    def decode_candidates():
        if FigureBuilder._cands is None:
            chars = [chr(u) for u in range(0x21, 0x7F)]
            chars += [chr(u) for u in range(0x1D400, 0x1D800)]
            # 撇号/双撇号常被编码成 '!'、'"'，候选里必须有真身
            chars += [chr(u) for u in (0x2032, 0x2033, 0x2034, 0x00B4,
                                       0x2032, 0x2212, 0x00B7, 0x00D7,
                                       0x2026, 0x22EF, 0x22EE)]
            FigureBuilder._cands = sorted(set(chars))
        return FigureBuilder._cands

    def cand_mask(self, ch, fnt):
        key = (id(fnt), ch)
        if key not in self.cand_masks:
            self.cand_masks[key] = glyph_mask(ch, fnt)
        return self.cand_masks[key]

    def collect_images(self):
        images, inline = [], []
        Z = 12

        def crop_render(i, b):
            # 带蒙版 / 内联图直接裁高分辨率渲染，保真且省内存
            pm = self.page.get_pixmap(matrix=fitz.Matrix(Z, Z),
                                      clip=fitz.Rect(b), alpha=False)
            im = Image.frombytes("RGB", (pm.width, pm.height), pm.samples)
            path = os.path.join(self.assets, "inline%02d.png" % i)
            im.save(path)
            return path

        def matches_page(im, b):
            # 原始位图可能被 PDF 的裁剪/覆盖遮住一部分（例如一张长条里画了
            # 三个 logo，页面只露中间一个）。和页面渲染对不上就走裁切渲染。
            pm = self.page.get_pixmap(matrix=fitz.Matrix(3, 3),
                                      clip=fitz.Rect(b), alpha=False)
            ref = Image.frombytes("RGB", (pm.width, pm.height),
                                  pm.samples).resize(im.size)
            diff = ImageChops.difference(im.convert("RGB"), ref)
            return sum(ImageStat.Stat(diff).mean) / 3.0

        for i, info in enumerate(self.page.get_image_info(xrefs=True)):
            b = info["bbox"]
            if not self.inside(b):
                continue
            small = (info.get("width", 0) <= 32
                     or info.get("height", 0) <= 32)
            if info["xref"] > 0 and not info.get("has-mask") and not small:
                ex = self.doc.extract_image(info["xref"])
                ext = ex["ext"]
                tr = info.get("transform") or (1, 0, 0, 1, 0, 0)
                det = tr[0] * tr[3] - tr[1] * tr[2]
                transposed = (abs(tr[0]) < 1e-6 and abs(tr[3]) < 1e-6
                              and det < 0)
                if transposed:
                    im = ImageOps.mirror(
                        Image.open(io.BytesIO(ex["image"])).convert("RGB")
                        .rotate(-90, expand=True))
                    if matches_page(im, b) >= 8:
                        inline.append((b, crop_render(i, b)))
                        continue
                    path = os.path.join(self.assets, "img%02d.png" % i)
                    im.save(path, "PNG")
                else:
                    im = Image.open(io.BytesIO(ex["image"])).convert("RGB")
                    if matches_page(im, b) >= 8:
                        inline.append((b, crop_render(i, b)))
                        continue
                    path = os.path.join(self.assets, "img%02d.%s" % (i, ext))
                    with open(path, "wb") as f:
                        f.write(ex["image"])
                images.append((b, path))
            else:
                inline.append((b, crop_render(i, b)))
        return images, inline

    def build(self, live=None, live_every=1, live_delay=0.5):
        raw = [dr for dr in self.page.get_drawings()
               if self.inside((dr["rect"].x0, dr["rect"].y0,
                               dr["rect"].x1, dr["rect"].y1))]
        # "fs" 同时有填充和描边，必须两边都画；只按 "f"/非 "f" 分会丢填充
        draws = [d for d in raw if d.get("type") in ("f", "fs", "s")]
        n_fs = sum(1 for d in draws if d.get("type") == "fs")

        texts = [sp for sp in self.page.get_texttrace()
                 if sp.get("type") == 0 and sp.get("chars")
                 and self.inside(sp["bbox"])]
        texts.sort(key=lambda s: s["seqno"])
        draws.sort(key=lambda d: d["seqno"])

        # 图片/渐变没有 seqno：用绘制日志里紧随其后的路径推算插入位置
        log = self.page.get_bboxlog()
        extras = []
        for i, (typ, r) in enumerate(log):
            if typ not in ("fill-image", "fill-shade"):
                continue
            if typ == "fill-image" and not self.inside(r):
                continue
            nxt = None
            for j in range(i + 1, min(i + 6, len(log))):
                if log[j][0].endswith("path"):
                    nxt = log[j][1]
                    break
            seq = None
            if nxt is not None:
                for d in raw:
                    b2 = d["rect"]
                    if (abs(b2.x0 - nxt[0]) < 0.6 and abs(b2.y0 - nxt[1]) < 0.6
                            and abs(b2.x1 - nxt[2]) < 0.6
                            and abs(b2.y1 - nxt[3]) < 0.6):
                        seq = d["seqno"] - 0.5
                        break
            if seq is None:
                seq = max(d["seqno"] for d in raw) + 0.5
            extras.append((seq, typ, i, r))

        cv = Canvas(width=self.W, height=self.H)
        self.register_fonts(cv)
        n_poly = n_ring = n_text = 0

        def emit_fill(dr):
            nonlocal n_poly, n_ring
            fill = hexcol(tuple(dr["fill"]))
            fo = dr.get("fill_opacity", 1.0)
            if fo is not None and fo >= 0.999:
                fo = None
            subs = subpaths(dr, self.curve_steps)
            if not subs:
                return
            if len(subs) == 2:
                ba, bb = bbox_of(subs[0]), bbox_of(subs[1])
                outer, inner = (ba, bb) if area(ba) >= area(bb) else (bb, ba)
                if contains(outer, inner) and area(inner) < area(outer) * 0.995:
                    oc = circle_of(subs[0])
                    ic = circle_of(subs[1])
                    if oc and ic:
                        rmid = (oc[2] + ic[2]) / 2
                        cv.circle(self.T(oc[0]), self.U(oc[1]), rmid * self.S,
                                  fill=None, stroke=fill,
                                  sw=abs(oc[2] - ic[2]) * self.S,
                                  stroke_opacity=fo)
                    else:
                        mx = (outer[0] + inner[0]) / 2
                        my = (outer[1] + inner[1]) / 2
                        mx1 = (outer[2] + inner[2]) / 2
                        my1 = (outer[3] + inner[3]) / 2
                        ow, oh = outer[2] - outer[0], outer[3] - outer[1]
                        iw, ih = inner[2] - inner[0], inner[3] - inner[1]
                        sw = (((ow - iw) + (oh - ih)) / 4) * self.S
                        cv.rect(self.T(mx), self.U(my),
                                (mx1 - mx) * self.S, (my1 - my) * self.S,
                                fill=None, stroke=fill, sw=max(0.35, sw),
                                rx=corner_radius(subs[0]) * self.S,
                                stroke_opacity=fo)
                    n_ring += 1
                    return
            boxes = [bbox_of(s) for s in subs]
            for i, sp in enumerate(subs):
                b = boxes[i]
                # 只丢又小又短的噪点；虚线短划（~0.5×1.5pt）也是细长条
                if (len(subs) > 1 and area(b) < 0.6
                        and max(b[2] - b[0], b[3] - b[1]) < 0.8):
                    continue
                is_hole = False
                for j, ob in enumerate(boxes):
                    if i != j and area(ob) > area(b) * 1.02 and contains(ob, b):
                        is_hole = True
                        break
                if is_hole:
                    continue
                pts = rdp(sp, eps=self.simplify_eps)
                if len(pts) < 3:
                    continue
                cv.poly([(self.T(x), self.U(y)) for x, y in pts],
                        fill=fill, stroke=None, closed=True, fill_opacity=fo)
                n_poly += 1

        def emit_stroke(dr):
            color = hexcol(tuple(dr["color"]))
            sw = max(0.4, (dr.get("width") or 0.5) * self.S)
            so = dr.get("stroke_opacity", 1.0)
            if so is not None and so >= 0.999:
                so = None
            dash = self.parse_dash(dr.get("dashes"))
            # 're'/'qu' 矩形在 PDF 里隐式闭合，closePath 不会置位
            closed = bool(dr.get("closePath")) or any(
                it[0] in ("re", "qu") for it in dr["items"])
            for sp in subpaths(dr, self.curve_steps):
                pts = rdp(sp, eps=self.simplify_eps)
                if len(pts) < 2:
                    continue
                cv.poly([(self.T(x), self.U(y)) for x, y in pts],
                        fill=None, stroke=color, sw=sw, closed=closed,
                        dash=dash, stroke_opacity=so)

        def covered_by_image(bb):
            for b, _ in inline:
                if (b[0] - 0.4 <= bb[0] and b[1] - 0.4 <= bb[1]
                        and b[2] + 0.4 >= bb[2] and b[3] + 0.4 >= bb[3]):
                    return True
            return False

        def emit_text(sp):
            nonlocal n_text
            text = "".join(chr(c[0]) for c in sp["chars"] if c[0])
            if not text.strip():
                return
            size = sp["size"] * self.S
            sub = self.span_subset(sp)
            fam = sub["fam"] if sub else self.svg_family(sp["font"])
            # 有些 PDF 的 ToUnicode 是坏的（下标被映射成 '!'/'"'）：SVG 用
            # 子集字体能画对，PPTX 得按字形反查出真字符，否则复制出来是乱码。
            pptx_text = None
            sys_text = None
            eff_text = "".join(chr(c[0]) for c in sp["chars"]
                               if c[0] and c[1] >= 0)
            if looks_broken(text, eff_text):
                base = (sp.get("font") or "").split("+")[-1].lower()
                subs = list(self.subsets.get(base) or [])
                if sub and sub not in subs:
                    subs.insert(0, sub)
                dec, conf = ("", 0.0)
                # 整串同字符 / 子集没有 cmap：码位完全不可信，只能按 gid 查
                if self._codes_repeat(sp) or sub is None:
                    dec, conf = self.decode_span_gid(sp, subs)
                elif sub:
                    dec, conf = self.decode_span(sp, sub)
                if conf >= 0.70:
                    if sub is None:
                        # 子集连 cmap 都没有，SVG 也没法按码位渲染——用反查
                        # 出的数学字符配系统字体（Cambria Math 自带这些字形）。
                        text = dec
                        fam = pptx_family(sp["font"])
                        sys_text = True
                        pptx_text = unicodedata.normalize("NFKC", dec)
                    elif len(dec) == len(text):
                        # 括号/逗号/等号这些公式里的常用真字符，只在子集与
                        # 页面对得很准时才改写；否则宁可信原文。
                        shaky = (self.span_score.get(
                            self.span_key(sp), 0.0) < 0.45)
                        common = set("()[]{},.=+-/:;<>?")
                        fixed = "".join(
                            (d if not (o in common and shaky) else o)
                            for o, d in zip(text, dec))
                        if fixed != text:
                            pptx_text = fixed
                    else:
                        # 原始码位带 gid=-1 的重复占位，长度对不上
                        pptx_text = unicodedata.normalize("NFKC", dec)
                elif sub and covered_by_image(sp["bbox"]):
                    # 这个小下标整块被页面裁切图盖住，字形已由图片提供；
                    # 再叠一个解不准的文本框只会露出来。
                    return
            bold = ("bold" in sp["font"].lower()
                    or bool(sp["flags"] & 16))
            if sys_text:
                # 按 gid 反查出的 span 的 chars 里有 gid=-1 的重复占位，
                # 真正的字形每个只出现一次。
                draw_chars = [c for c in sp["chars"]
                              if c[0] and c[1] >= 0]
                if len(draw_chars) != len(text):
                    draw_chars = [c for c in sp["chars"] if c[0]]
                bold = False
            else:
                draw_chars = [c for c in sp["chars"] if c[0]]
            ox, oy = draw_chars[0][2]
            gx = [self.T(c[2][0]) for c in draw_chars]
            gy = [self.U(c[2][1]) for c in draw_chars]
            # 竖排 / 倾斜文字：dir 不是 (1,0)，每个字形绕自身原点旋转
            dx, dy = sp.get("dir") or (1.0, 0.0)
            rot = 0.0
            if abs(dy) > 1e-3 or abs(dx - 1.0) > 1e-3:
                rot = math.degrees(math.atan2(dy, dx))
            cv.text(
                self.T(ox), self.U(oy) - 0.32 * size, text, size=size,
                bold=bold,
                italic=(False if sys_text else bool(sp["flags"] & 2)),
                color=hexcol(tuple(sp["color"])), anchor="left",
                family=fam, pptx_family=pptx_family(sp["font"]),
                svg_weight=("normal" if fam.startswith("PF") else None),
                glyph_x=gx, svg_baseline=self.U(oy), glyph_y=gy, rotate=rot,
                pptx_text=pptx_text)
            n_text += 1

        def emit_image(b, path):
            cv.image(self.T(b[0]), self.U(b[1]),
                     (b[2] - b[0]) * self.S, (b[3] - b[1]) * self.S, path)

        images, inline = self.collect_images()

        elems = ([(d["seqno"], 0, d) for d in draws]
                 + [(sp["seqno"], 1, sp) for sp in texts]
                 + [(e[0], 2, e) for e in extras])
        elems.sort(key=lambda t: (t[0], t[1]))

        used_inline = set()
        for n, (seq, kind, obj) in enumerate(elems, 1):
            if kind == 0:
                typ = obj.get("type")
                if typ in ("f", "fs") and obj.get("fill") is not None:
                    emit_fill(obj)
                if typ in ("s", "fs") and obj.get("color") is not None:
                    emit_stroke(obj)
            elif kind == 1:
                emit_text(obj)
            else:
                seq2, typ, idx, r = obj
                if typ == "fill-image":
                    matched = False
                    for b, path in images:
                        if (abs(b[0] - r[0]) < 0.6 and abs(b[1] - r[1]) < 0.6
                                and abs(b[2] - r[2]) < 0.6
                                and abs(b[3] - r[3]) < 0.6):
                            emit_image(b, path)
                            matched = True
                            break
                    if not matched:
                        # 带蒙版的图被改成了渲染裁切，从 inline 里按 bbox 找
                        for j, (b, path) in enumerate(inline):
                            if j in used_inline:
                                continue
                            if (abs(b[0] - r[0]) < 0.6
                                    and abs(b[1] - r[1]) < 0.6
                                    and abs(b[2] - r[2]) < 0.6
                                    and abs(b[3] - r[3]) < 0.6):
                                emit_image(b, path)
                                used_inline.add(j)
                                break
                else:
                    for j, (b, path) in enumerate(inline):
                        if j not in used_inline:
                            emit_image(b, path)
                            used_inline.add(j)
                            break
            if live and n % live_every == 0:
                cv.to_svg(live)
                time.sleep(live_delay)

        print("ops", len(cv.ops), "draws", len(draws), "fs", n_fs,
              "polys", n_poly, "rings", n_ring,
              "text", n_text, "images", len(images), "inline", len(inline),
              "canvas", self.W, self.H)
        return cv

    def save(self, cv, write_pptx=True):
        svg = os.path.join(self.out, self.name + ".svg")
        cv.to_svg(svg)
        print("saved", svg)
        if write_pptx:
            pptx = os.path.join(self.out, self.name + ".pptx")
            cv.to_pptx(pptx)
            print("saved", pptx)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True, help="0 起页索引")
    ap.add_argument("--bbox", required=True, help="x0,y0,x1,y1（pt）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--name", default="Figure", help="输出文件名（不含扩展名）")
    ap.add_argument("--width", type=int, default=1948)
    ap.add_argument("--margin", type=int, default=8)
    ap.add_argument("--curve-steps", type=int, default=32,
                    help="三次贝塞尔每段采样数；越大越顺滑（默认 32）")
    ap.add_argument("--simplify-eps", type=float, default=0.008,
                    help="折线 RDP 简化误差，单位 pt；越小越保真（默认 0.008）")
    ap.add_argument("--live", action="store_true", help="边生成边写 SVG")
    ap.add_argument("--no-preview", action="store_true",
                    help="--live 时不起预览服务、不自动打开 VS Code 预览")
    ap.add_argument("--live-every", type=int, default=1,
                    help="每 N 个元素写一次（默认 1）")
    ap.add_argument("--live-delay", type=float, default=0.5)
    ap.add_argument("--no-pptx", action="store_true")
    ap.add_argument("--no-open", action="store_true",
                    help="写完后不自动打开 PowerPoint（默认会自动弹出）")
    ap.add_argument("--no-clean", action="store_true",
                    help="保留 assets/、fonts/ 等中间产物（默认自动清理）")
    a = ap.parse_args()
    bbox = tuple(float(v) for v in a.bbox.split(","))
    if len(bbox) != 4:
        raise SystemExit("--bbox 需要 4 个数：x0,y0,x1,y1")
    os.makedirs(a.out, exist_ok=True)
    if a.live and not a.no_preview:
        subprocess.run([sys.executable, os.path.join(HERE, "open_preview.py"),
                        "--out", a.out, "--file", a.name + ".svg"])
    b = FigureBuilder(a.pdf, a.page, bbox, a.out, a.name, a.width, a.margin,
                      curve_steps=a.curve_steps,
                      simplify_eps=a.simplify_eps)
    svg = os.path.join(a.out, a.name + ".svg")
    cv = b.build(live=svg if a.live else None,
                 live_every=a.live_every, live_delay=a.live_delay)
    b.save(cv, write_pptx=not a.no_pptx)

    if not a.no_pptx:
        pptx = os.path.join(a.out, a.name + ".pptx")
        print()
        subprocess.run([sys.executable, os.path.join(HERE, "verify_pptx.py"),
                        "--pptx", pptx])
        if not a.no_open:
            subprocess.run([sys.executable, os.path.join(HERE, "open_pptx.py"),
                            "--pptx", pptx])

    if not a.no_clean:
        subprocess.run([sys.executable, os.path.join(HERE, "cleanup.py"),
                        "--out", a.out])


if __name__ == "__main__":
    main()
