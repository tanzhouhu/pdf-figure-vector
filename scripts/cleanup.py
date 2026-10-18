"""清理转换过程的中间产物，只留最终 SVG / PPTX / 预览页 / 重绘源码。

用法：
  python cleanup.py --out out
  python cleanup.py --out out --dry-run        # 只列出会删什么
  python cleanup.py --out out --keep-assets    # 保留从 PDF 抽出的原图素材

删除：
  assets/、fonts/（内容已 base64 内嵌进 SVG）、__pycache__/
  _*.png _*.py _*.json _*.log _*.txt（下划线诊断文件）
  patch_*.py、fit_*.py（手工调参/拟合脚本）
  *.png（目录内所有位图都是探查/对比/放大诊断图）
  .preview.json（本地预览服务的端口记录）
保留：
  *.svg、*.pptx、preview.html、以及其它非上述模式的脚本（如 redraw.py）
PowerPoint 的 ~$ 锁文件不动（关闭 PPT 后系统会自动删）。
"""
import argparse
import glob
import os
import shutil


def size_of(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def collect(out, keep_assets):
    """返回 (要删的目录列表, 要删的文件列表)。"""
    dirs = []
    if os.path.isdir(os.path.join(out, "__pycache__")):
        dirs.append(os.path.join(out, "__pycache__"))
    if not keep_assets:
        for name in ("assets", "fonts"):
            p = os.path.join(out, name)
            if os.path.isdir(p):
                dirs.append(p)

    files = set()
    patterns = ("_*.png", "_*.py", "_*.json", "_*.log", "_*.txt",
                "patch_*.py", "fit_*.py")
    for pat in patterns:
        files.update(glob.glob(os.path.join(out, pat)))
    # 目录里的位图全部是探查/对比/放大诊断图，成品 SVG 已自包含
    files.update(glob.glob(os.path.join(out, "*.png")))
    state = os.path.join(out, ".preview.json")
    if os.path.isfile(state):
        files.add(state)

    files = sorted(p for p in files
                   if os.path.isfile(p)
                   and not os.path.basename(p).startswith("~$"))
    return dirs, files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep-assets", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将删除的文件，不实际删除")
    a = ap.parse_args()

    out = os.path.abspath(a.out)
    if not os.path.isdir(out):
        raise SystemExit("目录不存在: " + out)

    dirs, files = collect(out, a.keep_assets)
    total = 0.0
    for p in dirs:
        s = size_of(p)
        total += s
        if a.dry_run:
            print("would remove %-12s %8.1f KB" %
                  (os.path.basename(p) + "/", s / 1024))
        else:
            shutil.rmtree(p)
            print("removed %-12s %8.1f KB" %
                  (os.path.basename(p) + "/", s / 1024))
    for p in files:
        s = size_of(p)
        total += s
        if a.dry_run:
            print("would remove %-28s %8.1f KB" %
                  (os.path.basename(p), s / 1024))
        else:
            os.remove(p)
            print("removed %-28s %8.1f KB" %
                  (os.path.basename(p), s / 1024))

    keep = [os.path.basename(p) for p in
            glob.glob(os.path.join(out, "*.svg")) +
            glob.glob(os.path.join(out, "*.pptx"))]
    print("\n%s %.1f KB；保留: %s" % (
        "将释放" if a.dry_run else "已释放", total / 1024,
        ", ".join(keep) if keep else "（没有 SVG/PPTX）"))
    if not keep:
        raise SystemExit("警告：目录里没有成品文件")


if __name__ == "__main__":
    main()
