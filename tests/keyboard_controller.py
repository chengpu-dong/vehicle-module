#!/usr/bin/env python3
"""
tests/keyboard_controller.py
键盘遥控测试工具 —— 用键盘实时控制底盘

用法：
    python tests/keyboard_controller.py             # 直连 ESP32（读 configs/device.yaml）
    python tests/keyboard_controller.py --mock      # Mock 模式（不需要真实硬件）
    python tests/keyboard_controller.py --esp32 ws://192.168.43.36:81

按键说明：
    W / ↑            前进
    S / ↓            后退
    A / ←            左转
    D / →            右转
    W+A / ↑+←       左前（差速）
    W+D / ↑+→       右前（差速）
    S+A / ↓+←       左后（差速）
    S+D / ↓+→       右后（差速）
    空格 / Q          停止
    E                急停（需按 R 解除）
    R                解除急停
    Ctrl+C           退出

注意：macOS 首次运行可能需要在「系统设置→隐私与安全→辅助功能」中给终端授权。
"""

import asyncio
import json
import os
import sys
import argparse
import threading
import termios
import tty
from pathlib import Path
from typing import Optional, Set

import yaml

# ==================== 终端原始模式控制 ====================

HIDE_CURSOR = "\033[?25l"   # 隐藏光标
SHOW_CURSOR = "\033[?25h"   # 显示光标

def _disable_echo() -> list:
    """禁用终端回显，返回原始设置用于恢复"""
    try:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        new[3] &= ~(termios.ECHO | termios.ICANON)   # 关闭回显和行缓冲
        termios.tcsetattr(fd, termios.TCSANOW, new)
        return old
    except Exception:
        return None

def _restore_echo(old_settings):
    """恢复终端回显"""
    if old_settings is None:
        return
    try:
        fd = sys.stdin.fileno()
        termios.tcsetattr(fd, termios.TCSANOW, old_settings)
    except Exception:
        pass

# ==================== ANSI 颜色 ====================
RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RED    = "\033[91m"
BLUE   = "\033[94m"
GRAY   = "\033[90m"
MAGENTA= "\033[95m"

try:
    from pynput import keyboard as kb
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False


# ==================== 按键 → 内部名称映射 ====================

def key_to_name(key) -> Optional[str]:
    """将 pynput Key 或 KeyCode 转为内部标识符"""
    try:
        # 字母键
        c = key.char.lower() if key.char else None
        if c in ('w', 's', 'a', 'd', 'q', 'e', 'r'):
            return c
        if c == ' ':
            return 'space'
    except AttributeError:
        pass

    # 特殊键
    special = {
        kb.Key.up:    'up',
        kb.Key.down:  'down',
        kb.Key.left:  'left',
        kb.Key.right: 'right',
        kb.Key.space: 'space',
    }
    return special.get(key)


# ==================== 按键组合 → 指令 ====================

DIRECTION_DISPLAY = {
    'forward':       '⬆️  前进',
    'backward':      '⬇️  后退',
    'left':          '⬅️  左转',
    'right':         '➡️  右转',
    'forward_left':  '↖  左前',
    'forward_right': '↗  右前',
    'backward_left': '↙  左后',
    'backward_right':'↘  右后',
    'stop':          '⏹  停止',
    'estop':         '🛑  急停！',
    'clear_estop':   '✅  解除急停',
}

def compute_command(pressed: Set[str]):
    """
    根据当前按键集合计算底盘指令。
    返回 (label, skill_dict) 或 (None, None)
    """
    has_w = 'w' in pressed or 'up' in pressed
    has_s = 's' in pressed or 'down' in pressed
    has_a = 'a' in pressed or 'left' in pressed
    has_d = 'd' in pressed or 'right' in pressed

    # 对向键相消
    if has_w and has_s: has_w = has_s = False
    if has_a and has_d: has_a = has_d = False

    if 'e' in pressed:
        return 'estop', {"skill": "system.emergency_stop", "params": {}}
    if 'r' in pressed:
        return 'clear_estop', {"skill": "system.clear_emergency_stop", "params": {}}
    if 'space' in pressed or 'q' in pressed:
        return 'stop', {"skill": "chassis.stop", "params": {}}

    # 斜向（差速 drive）
    if has_w and has_a:
        return 'forward_left',  {"skill": "chassis.drive", "params": {"linear": 0.6, "angular":  0.4}}
    if has_w and has_d:
        return 'forward_right', {"skill": "chassis.drive", "params": {"linear": 0.6, "angular": -0.4}}
    if has_s and has_a:
        return 'backward_left', {"skill": "chassis.drive", "params": {"linear": -0.6, "angular": -0.4}}
    if has_s and has_d:
        return 'backward_right',{"skill": "chassis.drive", "params": {"linear": -0.6, "angular":  0.4}}

    # 单向
    if has_w: return 'forward',  {"skill": "chassis.move", "params": {"direction": "forward",  "speed": 0.5}}
    if has_s: return 'backward', {"skill": "chassis.move", "params": {"direction": "backward", "speed": 0.5}}
    if has_a: return 'left',     {"skill": "chassis.move", "params": {"direction": "left",     "speed": 0.5}}
    if has_d: return 'right',    {"skill": "chassis.move", "params": {"direction": "right",    "speed": 0.5}}

    return None, None


# ==================== 终端界面 ====================

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def print_header(esp32_url: str, connected: bool, mock: bool):
    conn_str = (f"{GREEN}✅ 已连接 {esp32_url}{RESET}" if connected
                else f"{YELLOW}⏳ 连接中...{RESET}") if not mock else f"{CYAN}🔧 Mock 模式{RESET}"
    print(f"{BOLD}{CYAN}{'═'*55}{RESET}")
    print(f"{BOLD}  🤖 底盘键盘遥控测试工具{RESET}")
    print(f"  {conn_str}")
    print(f"{BOLD}{CYAN}{'═'*55}{RESET}")

def print_keymap():
    print(f"""
  {BOLD}按键映射：{RESET}
  ┌─────────────────────────────────────────────┐
  │  {CYAN}W{RESET} / {CYAN}⬆️{RESET}   前进      {CYAN}空格{RESET} / {CYAN}Q{RESET}  停止        │
  │  {CYAN}S{RESET} / {CYAN}⬇️{RESET}   后退      {RED}E{RESET}        急停        │
  │  {CYAN}A{RESET} / {CYAN}⬅️{RESET}   左转      {GREEN}R{RESET}        解除急停    │
  │  {CYAN}D{RESET} / {CYAN}➡️{RESET}   右转      {GRAY}Ctrl+C{RESET}   退出        │
  │                                             │
  │  {CYAN}W+A{RESET}  左前    {CYAN}W+D{RESET}  右前              │
  │  {CYAN}S+A{RESET}  左后    {CYAN}S+D{RESET}  右后              │
  └─────────────────────────────────────────────┘""")

def print_status(pressed: Set[str], label: Optional[str],
                 last_response: Optional[str], last_direction: Optional[str]):
    # 当前按键
    key_strs = []
    for k in sorted(pressed):
        display = {'up': '↑', 'down': '↓', 'left': '←', 'right': '→',
                   'space': 'SPACE'}.get(k, k.upper())
        key_strs.append(f"{CYAN}{BOLD}{display}{RESET}")
    keys_display = ' + '.join(key_strs) if key_strs else f"{GRAY}（无）{RESET}"

    direction_display = DIRECTION_DISPLAY.get(label, '') if label else f"{GRAY}无动作{RESET}"
    color = RED if label == 'estop' else (YELLOW if label == 'stop' else GREEN)

    print(f"\n  {BOLD}当前按键：{RESET} {keys_display}")
    print(f"  {BOLD}执行指令：{RESET} {color}{direction_display}{RESET}")

    if last_direction:
        print(f"\n  {BOLD}{GRAY}上一条：  {last_direction}{RESET}")
    if last_response:
        try:
            resp = json.loads(last_response)
            ok = resp.get("success", False)
            icon = "✅" if ok else "❌"
            msg = resp.get("message", resp.get("data", ""))
            print(f"  {BOLD}{GRAY}ESP32回应：{icon} {msg}{RESET}")
        except Exception:
            print(f"  {BOLD}{GRAY}ESP32回应：{last_response[:60]}{RESET}")

    print(f"\n  {GRAY}按 Ctrl+C 退出{RESET}")


# ==================== D-C-C 协议转换（本文件独立，不依赖 drivers）====================

def _skill_to_dcc(skill_dict: dict, rid: str) -> dict:
    """
    将内部 skill_dict 格式转换为 D-C-C 紧凑协议，直接发给 ESP32。
    skill_dict 格式：{"skill": "chassis.move", "params": {"direction":"forward","speed":0.5}}
    D-C-C 格式：    {"d":"chassis","c":"fwd","v":0.5,"rid":"kb-0001"}
    """
    skill  = skill_dict.get("skill", "")
    params = skill_dict.get("params", {})
    msg: dict = {"rid": rid}

    if skill == "chassis.move":
        cmd_map = {"forward": "fwd", "backward": "bwd",
                   "left": "lt", "right": "rt", "stop": "stp"}
        c = cmd_map.get(params.get("direction", "stop"), "stp")
        msg.update({"d": "chassis", "c": c, "v": params.get("speed", 0.5)})
        if params.get("duration", 0) > 0:
            msg["t"] = params["duration"]
    elif skill == "chassis.drive":
        msg.update({"d": "chassis", "c": "drv",
                    "l": params.get("linear", 0.0),
                    "a": params.get("angular", 0.0)})
    elif skill == "chassis.stop":
        msg.update({"d": "chassis", "c": "stp"})
    elif skill == "system.emergency_stop":
        msg.update({"d": "sys", "c": "estop"})
    elif skill == "system.clear_emergency_stop":
        msg.update({"d": "sys", "c": "clr"})
    elif skill == "system.heartbeat":
        msg.update({"d": "sys", "c": "hb"})
    else:
        parts = skill.split(".", 1)
        msg.update({"d": parts[0], "c": parts[1] if len(parts) > 1 else skill})
    return msg


# ==================== 主逻辑 ====================

class KeyboardController:
    def __init__(self, esp32_url: str, mock: bool = False):
        self._url      = esp32_url
        self._mock     = mock
        self._pressed  : Set[str] = set()
        self._ws       = None
        self._connected = False
        self._running   = True
        self._cmd_queue : asyncio.Queue = None  # 初始化时赋值
        self._last_label    : Optional[str] = None
        self._last_response : Optional[str] = None
        self._req_id   = 0
        self._lock     = threading.Lock()
        self._last_direction_display : Optional[str] = None
        # 等待中的请求：rid -> asyncio.Future（避免与 _connect_loop 的 recv 冲突）
        self._pending: dict = {}

    # ---------- pynput 回调（运行在子线程）----------

    def _on_press(self, key):
        name = key_to_name(key)
        if name is None:
            return
        changed = False
        with self._lock:
            if name not in self._pressed:
                self._pressed.add(name)
                changed = True
        if changed and self._cmd_queue:
            asyncio.run_coroutine_threadsafe(
                self._cmd_queue.put(('key_change', None)),
                self._loop
            )

    def _on_release(self, key):
        name = key_to_name(key)
        if name is None:
            return
        changed = False
        with self._lock:
            if name in self._pressed:
                self._pressed.discard(name)
                changed = True
        if changed and self._cmd_queue:
            asyncio.run_coroutine_threadsafe(
                self._cmd_queue.put(('key_change', None)),
                self._loop
            )

    # ---------- 发送指令 ----------

    async def _send(self, skill_dict: dict) -> Optional[str]:
        """
        发送技能指令到 ESP32（D-C-C 协议）。
        不直接 recv()，而是注册 Future，由 _connect_loop 统一分发响应，
        避免多个协程同时调用 recv() 产生冲突。
        """
        if self._mock:
            return json.dumps({"success": True, "message": "mock ok",
                               "data": {"state": skill_dict.get("skill", "")}})
        if not self._ws:
            return None
        self._req_id += 1
        rid = f"kb-{self._req_id:04d}"
        dcc = _skill_to_dcc(skill_dict, rid)

        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[rid] = future

        try:
            await self._ws.send(json.dumps(dcc))
            # 等待 _connect_loop 分发响应（最多 2 秒）
            legacy = await asyncio.wait_for(future, timeout=2.0)
            return json.dumps(legacy, ensure_ascii=False)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            return json.dumps({"success": False, "message": "response_timeout"})
        except Exception as e:
            self._pending.pop(rid, None)
            return json.dumps({"success": False, "message": str(e)})

    # ---------- 连接 ESP32 ----------

    async def _connect_loop(self):
        import websockets
        while self._running:
            try:
                async with websockets.connect(self._url, open_timeout=5) as ws:
                    self._ws = ws
                    self._connected = True
                    self._redraw()
                    # 唯一的接收循环：统一处理所有 ESP32 消息
                    while self._running:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                            self._dispatch_message(raw)
                        except asyncio.TimeoutError:
                            pass
                        except Exception:
                            break
            except Exception:
                pass
            finally:
                self._ws = None
                self._connected = False
                # 清空所有等待中的 Future（连接断开）
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_result({"success": False, "message": "connection_lost"})
                self._pending.clear()
            if self._running:
                await asyncio.sleep(2.0)

    def _dispatch_message(self, raw: str):
        """解析 ESP32 消息：响应帧分发到 Future，广播帧忽略"""
        try:
            data = json.loads(raw)
        except Exception:
            return
        rid = data.get("rid")
        if rid and rid in self._pending:
            # D-C-C 响应帧 → 翻译为 legacy 格式后 resolve Future
            fut = self._pending.pop(rid)
            if not fut.done():
                ok = data.get("ok", False)
                legacy = {
                    "success": ok,
                    "message": data.get("err", "ok") if not ok else "ok",
                    "data":    {"state": data.get("s", "")},
                }
                fut.set_result(legacy)
        # t=="s" 是状态广播帧，忽略（不影响控制逻辑）

    # ---------- 界面刷新 ----------

    def _redraw(self):
        with self._lock:
            pressed_copy = set(self._pressed)
        label, _ = compute_command(pressed_copy)
        clear_screen()
        print_header(self._url, self._connected, self._mock)
        print_keymap()
        print_status(pressed_copy, label,
                     self._last_response, self._last_direction_display)

    # ---------- 主事件循环 ----------

    async def _main_loop(self):
        self._cmd_queue = asyncio.Queue()
        last_label = None
        COOLDOWN = 0.08  # 指令最小发送间隔（秒），防止刷屏

        while self._running:
            try:
                event, _ = await asyncio.wait_for(
                    self._cmd_queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                self._redraw()
                continue

            with self._lock:
                pressed_copy = set(self._pressed)

            label, cmd = compute_command(pressed_copy)

            # 仅在方向变化时发送指令
            if label == last_label:
                self._redraw()
                continue

            last_label = label

            if cmd:
                direction_display = DIRECTION_DISPLAY.get(label, label) if label else ''
                print(f"\n  {YELLOW}→ 发送指令：{direction_display}{RESET}")
                resp = await self._send(cmd)
                self._last_response = resp
                self._last_direction_display = direction_display
            else:
                # 没有按键 → 自动停止
                stop_cmd = {"skill": "chassis.stop", "params": {}}
                resp = await self._send(stop_cmd)
                self._last_response = resp
                self._last_direction_display = "（松开按键，自动停止）"

            self._redraw()
            await asyncio.sleep(COOLDOWN)

    # ---------- 启动入口 ----------

    async def run(self):
        self._loop = asyncio.get_running_loop()
        listener = None

        # 启动 ESP32 连接（后台任务）
        if not self._mock:
            asyncio.create_task(self._connect_loop())
        else:
            self._connected = True

        # 启动 pynput 监听（子线程）
        if not PYNPUT_AVAILABLE:
            print(f"{RED}❌ 未找到 pynput 库，请先安装：pip install pynput{RESET}")
            return

        # 检测权限并启动监听
        print(f"\n{YELLOW}⏳ 正在启动键盘监听...{RESET}", flush=True)
        detected = threading.Event()

        def _test_press(key):
            detected.set()

        # 用一个空 listener 探测权限
        test_listener = kb.Listener(on_press=_test_press)
        test_listener.daemon = True
        test_listener.start()

        # 等待最多 5 秒看是否能捕获到按键
        print(f"  {GRAY}请随意按任意键检测权限...（5秒内）{RESET}", flush=True)
        if not detected.wait(timeout=5.0):
            test_listener.stop()
            print(f"\n{RED}⚠️  键盘权限未授权！{RESET}")
            print(f"{YELLOW}请按以下步骤授权：{RESET}")
            print(f"  1. 打开「系统设置 → 隐私与安全 → 输入监控」")
            print(f"  2. 在列表中找到 {BOLD}终端（Terminal）{RESET} 或 {BOLD}iTerm2{RESET}")
            print(f"  3. 打开开关 → 重启终端 → 重新运行脚本")
            print(f"\n{GRAY}（如果列表里没有终端，运行后会自动弹出添加请求）{RESET}")
            return
        test_listener.stop()
        detected.clear()

        # 正式启动监听
        listener = kb.Listener(
            on_press=self._on_press,
            on_release=self._on_release
        )
        listener.daemon = True
        listener.start()

        # 禁用终端回显 + 隐藏光标
        old_term = _disable_echo()
        sys.stdout.write(HIDE_CURSOR)
        sys.stdout.flush()

        self._redraw()

        try:
            await self._main_loop()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self._running = False
            # 恢复终端
            sys.stdout.write(SHOW_CURSOR)
            sys.stdout.flush()
            _restore_echo(old_term)
            if listener:
                listener.stop()
            # 发送停止指令
           
async def main():
    parser = argparse.ArgumentParser(
        description="键盘遥控测试工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python tests/keyboard_controller.py              # 直连 ESP32（读配置文件）
  python tests/keyboard_controller.py --mock       # 不需要硬件，Mock 测试
  python tests/keyboard_controller.py --esp32 ws://192.168.43.36:81
        """
    )
    parser.add_argument("--esp32",  default=None,
                        help="ESP32 WebSocket 地址（覆盖配置文件）")
    parser.add_argument("--config", default="configs/device.yaml",
                        help="配置文件路径（default: configs/device.yaml）")
    parser.add_argument("--mock",   action="store_true",
                        help="Mock 模式，不需要真实 ESP32")
    args = parser.parse_args()

    # 读取配置
    esp32_url = args.esp32
    if not esp32_url and not args.mock:
        config_path = Path(args.config)
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            esp32_cfg = cfg.get("esp32", {})
            host = esp32_cfg.get("host", "192.168.43.36")
            port = esp32_cfg.get("ws_port", 81)
            esp32_url = esp32_cfg.get("ws_url", f"ws://{host}:{port}")
        else:
            esp32_url = "ws://192.168.43.36:81"

    if args.mock:
        esp32_url = "ws://mock"

    ctrl = KeyboardController(esp32_url=esp32_url, mock=args.mock)
    await ctrl.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


