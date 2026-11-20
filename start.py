"""一键菜单。Windows 双击 start.cmd，macOS/Linux 运行 sh start.sh。

只做三件事：看回放、验证成绩、拿你的 bot 挑战；不需要装别的东西。
"""
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = 8123


def env_report():
    print(f"  Python : {sys.version.split()[0]}")
    try:
        import pymunk
        print(f"  pymunk : {pymunk.version}")
    except Exception:
        print("  pymunk : 未安装 -> pip install -r requirements.txt（第 1、2 期需要）")
    try:
        import pygame
        print(f"  pygame : {pygame.version.ver}（可以出片）")
    except Exception:
        print("  pygame : 未安装（只影响渲染成片，不影响验证成绩）")
    if shutil.which("ffmpeg"):
        print("  ffmpeg : 已安装")
    else:
        print("  ffmpeg : 未安装（渲染成片才需要）")
    node = shutil.which("node")
    if node:
        out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
        print(f"  Node   : {out}")
    else:
        print("  Node   : 未安装（第 3 期需要 Node 22+）")


def serve():
    os.chdir(ROOT)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{PORT}/ep1-obstacle/replays/viewer.html"
    print(f"  回放页面：{url}\n  按 Ctrl+C 结束本地服务")
    webbrowser.open(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()


def verify_ep1():
    subprocess.run([sys.executable, "verify.py"], cwd=os.path.join(ROOT, "ep1-obstacle"))


def verify_ep2():
    subprocess.run([sys.executable, "verify.py"], cwd=os.path.join(ROOT, "ep2-longjump"))


def verify_ep3():
    node = shutil.which("node")
    if not node:
        print("  没装 Node，第 3 期跑不了（需要 Node 22+）")
        return
    subprocess.run([node, "verify.mjs"], cwd=os.path.join(ROOT, "ep3-golf"))


def challenge_ep1():
    path = input("  你的 bot.py 路径：").strip().strip('"')
    if path:
        subprocess.run([sys.executable, "challenge.py", path], cwd=os.path.join(ROOT, "ep1-obstacle"))


MENU = """
===== AI 运动会 =====
  1) 看第 1 期回放（起本地服务 + 打开浏览器）
  2) 验证第 1 期成绩（重跑 22 份 bot）
  3) 验证第 2 期成绩（重放 78 跳）
  4) 验证第 3 期成绩（重算 324 份回放，需要 Node）
  5) 用我的 bot 挑战第 1 期
  6) 检查环境
  q) 退出
"""


def main():
    print(MENU)
    while True:
        try:
            choice = input("选择> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if choice in ("q", "quit", "exit"):
            return
        if choice == "1":
            serve()
            return
        if choice == "2":
            verify_ep1()
        elif choice == "3":
            verify_ep2()
        elif choice == "4":
            verify_ep3()
        elif choice == "5":
            challenge_ep1()
        elif choice == "6":
            env_report()
        else:
            print("  ?")
        print(MENU)


if __name__ == "__main__":
    main()
