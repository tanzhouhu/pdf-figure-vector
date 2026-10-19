"""清理转换过程的中间产物，默认只留最终 SVG / PPTX。

用法：
  python cleanup.py --out out
  python cleanup.py --out out --dry-run        # 只列出会删什么
  python cleanup.py --out out --keep-assets    # 保留从 PDF 抽出的原图素材
  python cleanup.py --out out --keep-qa        # 保留 _qa/ 验收图
  python cleanup.py --out out --keep-preview   # 保留 preview.html 与预览状态

删除：
  assets/、fonts/（内容已 base64 内嵌进 SVG）、__pycache__/
  _*.png _*.py _*.json _*.log _*.txt（下划线诊断文件）
  patch_*.py、fit_*.py（手工调参/拟合脚本）
  *.png（目录内所有位图都是探查/对比/放大诊断图）
  _qa/、preview.html、.preview.json
保留：
  *.svg、*.pptx；显式 --keep-qa / --keep-preview 时再保留对应文件
PowerPoint 的 ~$ 锁文件不动（关闭 PPT 后系统会自动删）。
"""
import argparse
import glob
import json
import os
import shutil
import signal


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


def stop_preview(out):
    state_path = os.path.join(out, ".preview.json")
    try:
        with open(state_path, encoding="utf-8") as handle:
            state = json.load(handle)
        if (os.path.normcase(os.path.abspath(state.get("out", "")))
                != os.path.normcase(os.path.abspath(out))):
            return
        pid = int(state.get("pid") or 0)
        if pid > 0:
            os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass


def collect(out, keep_assets, keep_qa, keep_preview):
    """返回 (要删的目录列表, 要删的文件列表)。"""
    dirs = []
    if os.path.isdir(os.path.join(out, "__pycache__")):
        dirs.append(os.path.join(out, "__pycache__"))
    if not keep_qa and os.path.isdir(os.path.join(out, "_qa")):
        dirs.append(os.path.join(out, "_qa"))
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
    if not keep_preview:
        for name in ("preview.html", ".preview.json"):
            path = os.path.join(out, name)
            if os.path.isfile(path):
                files.add(path)

    files = sorted(p for p in files
                   if os.path.isfile(p)
                   and not os.path.basename(p).startswith("~$"))
    return dirs, files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep-assets", action="store_true")
    ap.add_argument("--keep-qa", action="store_true")
    ap.add_argument("--keep-preview", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将删除的文件，不实际删除")
    a = ap.parse_args()

    out = os.path.abspath(a.out)
    if not os.path.isdir(out):
        raise SystemExit("目录不存在: " + out)

    if not a.keep_preview and not a.dry_run:
        stop_preview(out)

    dirs, files = collect(out, a.keep_assets, a.keep_qa, a.keep_preview)
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
