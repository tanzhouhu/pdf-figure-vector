"""用本地 PowerPoint 打开生成的 PPTX，让作者亲自检查是否为矢量。

用法：
  python open_pptx.py --pptx out/Figure.pptx

优先找 POWERPNT.EXE（Office 16+），找不到才退回系统默认关联程序（可能是 WPS）。
不等待程序退出。无图形界面（SSH / headless Linux）或没有可用程序时只打印路径，
不抛异常——其它 agent（Codex、远程终端）也能照常跑完。
"""
import argparse
import os
import subprocess
import sys


def find_powerpoint():
    if sys.platform != "win32":
        return None
    paths = [
        r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE",
        r"C:\Program Files (x86)\Microsoft Office\root\Office16\POWERPNT.EXE",
        r"C:\Program Files\Microsoft Office\Office16\POWERPNT.EXE",
        r"C:\Program Files (x86)\Microsoft Office\Office16\POWERPNT.EXE",
        r"C:\Program Files\Microsoft Office\root\Office15\POWERPNT.EXE",
    ]
    for p in paths:
        if os.path.isfile(p):
            return p
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\POWERPNT.EXE")
        p = winreg.QueryValue(key, None)
        if p and os.path.isfile(p):
            return p
    except OSError:
        pass
    return None


def has_gui():
    if sys.platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pptx", required=True)
    a = ap.parse_args()
    path = os.path.abspath(a.pptx)
    if not os.path.isfile(path):
        raise SystemExit("找不到文件: " + path)

    if not has_gui():
        print("没有图形界面，未自动打开。请把文件取回本地检查：")
        print("  " + path)
        print("远程机器可先做端口/文件传输，例如：")
        print("  scp <user>@<host>:" + path + " .")
        return

    app = "默认程序"
    try:
        if sys.platform == "win32":
            exe = find_powerpoint()
            if exe:
                subprocess.Popen([exe, path])
                app = "PowerPoint"
            else:
                os.startfile(path)
                app = "默认程序（可能是 WPS）"
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        print("自动打开失败（没装 PowerPoint/LibreOffice 或没有默认程序）。")
        print("请手动打开：")
        print("  " + path)
        return

    print("已用 %s 打开：%s" % (app, path))
    print("检查方法：")
    print("  1. 点图中任一图形，能单独选中（出现独立边框/控制点）→ 矢量")
    print("  2. 整张图只能作为一个对象框选 → 仍是位图")
    print("  3. 右键图片区域看是否有「编辑图片」→ 那是原 PDF 自带的照片，正常")


if __name__ == "__main__":
    main()
