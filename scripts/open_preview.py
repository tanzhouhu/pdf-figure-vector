"""启动预览服务，并把预览窗口开到合适的地方（VS Code / 默认浏览器 / 只给 URL）。

用法：
  python open_preview.py --out out --file Figure.svg
  python open_preview.py --out out --file Figure.svg --browser   # 强制默认浏览器
  python open_preview.py --out out --file Figure.svg --no-open   # 只起服务

行为：
1. 把 preview.html 复制到 --out。
2. 在 --out 起一个常驻静态服务，只监听 127.0.0.1；同一目录重复调用会复用。
3. 按运行环境自动选预览方式：
   - 检测到 VS Code 宿主（Windows 上通过 VSCODE_PID / TERM_PROGRAM=vscode /
     Claude Code 扩展）→ 尝试开进 VS Code 集成浏览器；
   - 其它 agent / 编辑器（如 Codex CLI、普通终端、非 Windows）→ 直接开默认浏览器；
   - 没有图形界面（SSH、headless Linux）→ 不开窗口，只打印 URL（服务继续跑）。
   可用环境变量 PDFVEC_PREVIEW=vscode|browser|none 强制指定。

服务用独立进程启动，脚本退出后继续运行，方便边生成边看。
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = ".preview.json"
BASE_PORT = 8765


def free_port(start):
    for port in range(start, start + 200):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise SystemExit("找不到空闲端口")


def read_state(out):
    try:
        with open(os.path.join(out, STATE), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def server_ok(port, out):
    """端口活着且服务的正是这个目录。"""
    try:
        url = "http://127.0.0.1:%d/%s" % (port, STATE)
        with urllib.request.urlopen(url, timeout=1.0) as r:
            data = json.loads(r.read().decode("utf-8"))
        return os.path.normcase(os.path.abspath(data.get("out", ""))) == \
            os.path.normcase(os.path.abspath(out))
    except Exception:
        return False


def start_server(out, port=0):
    st = read_state(out)
    if st and st.get("port") and server_ok(st["port"], out):
        return st["port"], False

    port = port or free_port(BASE_PORT)
    state = {"port": port, "out": os.path.abspath(out), "pid": None}
    with open(os.path.join(out, STATE), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS |
                                   subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port),
         "--bind", "127.0.0.1", "--directory", os.path.abspath(out)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, **kwargs)
    state["pid"] = proc.pid
    with open(os.path.join(out, STATE), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    for _ in range(60):
        if server_ok(port, out):
            return port, True
        time.sleep(0.1)
    raise SystemExit("预览服务启动失败（端口 %d）" % port)


def vscode_command():
    """老版本 VS Code 用 Simple Browser，新版本用集成浏览器。"""
    try:
        # Windows 上 code 是 code.cmd，必须过 shell（或 cmd /c）才能跑。
        out = subprocess.run("code --version", shell=True, capture_output=True,
                             text=True, timeout=8).stdout
        nums = [int(x) for x in re.findall(r"\d+", out.splitlines()[0])[:2]]
    except Exception:
        return "Simple Browser: Show"
    # 集成浏览器（Browser: Open Integrated Browser）从 1.141 起提供
    if len(nums) >= 2 and tuple(nums) >= (1, 141):
        return "Open Integrated Browser"
    return "Simple Browser: Show"


def env_mode():
    """PDFVEC_PREVIEW=vscode|browser|none，其它值忽略。"""
    v = os.environ.get("PDFVEC_PREVIEW", "").strip().lower()
    return v if v in ("vscode", "browser", "none") else ""


def in_vscode_host():
    """当前进程是否跑在 VS Code 宿主里（扩展或集成终端）。"""
    if os.environ.get("VSCODE_PID") or os.environ.get("VSCODE_IPC_HOOK"):
        return True
    if os.environ.get("TERM_PROGRAM", "").lower() == "vscode":
        return True
    entry = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    return entry.startswith("claude-vscode")


def has_gui():
    if os.name == "nt" or sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def open_in_browser(url):
    if not has_gui():
        print("没有图形界面，未自动打开。请在本地浏览器访问：")
        print("  " + url)
        print("远程机器可先做端口转发，例如：")
        print("  ssh -L 8765:127.0.0.1:8765 <user>@<host>")
        return False
    try:
        ok = webbrowser.open(url)
    except Exception:
        ok = False
    if ok:
        print("已在默认浏览器打开")
    else:
        print("自动打开失败，请手动在浏览器访问：")
        print("  " + url)
    return ok


def open_in_vscode(url, needle):
    """用 PowerShell 走命令面板。

    返回 "vscode"（确认打开）、"fallback"（脚本已退回默认浏览器）、
    "error"（脚本没跑起来，调用方需要自己开浏览器）。
    """
    if os.name != "nt":
        return "error"
    ps = os.path.join(HERE, "vscode_preview.ps1")
    if not os.path.isfile(ps):
        return "error"
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive",
           "-ExecutionPolicy", "Bypass", "-File", ps, "-Url", url,
           "-Command", vscode_command(), "-Needle", needle]
    # Redirect to a file rather than a pipe: piping this script's stdout makes
    # powershell.exe hang before running a single statement.
    fd, tmp = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=45)
        with open(tmp, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return "error"
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    print(text.strip())
    if "VSCODE_OK" in text:
        return "vscode"
    if "FALLBACK" in text:
        return "fallback"
    return "error"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="图表输出目录")
    ap.add_argument("--file", default="Figure.svg", help="要预览的 SVG 文件名")
    ap.add_argument("--port", type=int, default=0, help="固定端口（默认自动选）")
    ap.add_argument("--browser", action="store_true",
                    help="不用 VS Code，直接开默认浏览器")
    ap.add_argument("--no-open", action="store_true", help="只起服务，不开窗口")
    a = ap.parse_args()

    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    src = os.path.join(HERE, "preview.html")
    if os.path.isfile(src):
        shutil.copyfile(src, os.path.join(out, "preview.html"))

    port, started = start_server(out, a.port)
    url = "http://127.0.0.1:%d/preview.html?file=%s" % (
        port, urllib.parse.quote(a.file))
    print("preview", url, "(new)" if started else "(reused)")

    if a.no_open:
        return

    mode = env_mode()
    if not mode and a.browser:
        mode = "browser"

    if mode == "none":
        print("未自动打开窗口，请在浏览器访问：")
        print("  " + url)
        return

    if mode == "browser":
        open_in_browser(url)
        return

    if mode == "vscode" or (os.name == "nt" and in_vscode_host()):
        status = open_in_vscode(url, "127.0.0.1:%d" % port)
        if status == "vscode":
            return
        if status == "fallback":
            print("VS Code 预览不可用，已退回默认浏览器")
            return
        print("VS Code 预览调用失败，改用默认浏览器")
    open_in_browser(url)


if __name__ == "__main__":
    main()
