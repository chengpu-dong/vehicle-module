"""
drivers/esp32_driver.py
Edge Agent 硬件接口层 — ESP32 通信驱动

职责：
    封装与 ESP32 WebSocket 服务器的底层通信细节。
    上层（chassis_service 等）调用 send_skill()，本层负责将其
    转换为 D-C-C 紧凑协议后发给 ESP32，并将响应翻译回标准格式。

D-C-C 协议 (Domain-Command-Content)：
    发送：{"d":"chassis","c":"fwd","v":0.5,"rid":"a1b2c3d4"}
    响应：{"rid":"a1b2c3d4","ok":true,"s":"fwd"}
    广播：{"t":"s","s":"fwd","l":0.5,"a":0.0,"em":false,"up":12345}

特性：
    - 自动重连（断线后指数退避重试）
    - 请求 rid 跟踪（匹配响应）
    - 消息回调注册（状态广播推送）
    - 异步发送（不阻塞主线程）
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Callable, Dict, Optional, Any

import websockets
from websockets.exceptions import ConnectionClosed, InvalidURI

logger = logging.getLogger(__name__)


class ESP32Driver:
    """
    ESP32 WebSocket 通信驱动

    使用示例：
        driver = ESP32Driver("ws://192.168.1.100:81")
        await driver.connect()

        # 发送技能指令
        resp = await driver.send_skill("chassis.move", {
            "direction": "forward",
            "speed": 0.5
        })
        print(resp)  # {"success": True, "state": "moving_forward", ...}

        # 注册状态推送回调
        driver.on_status(lambda data: print("Status:", data))
    """

    def __init__(self, ws_url: str, connect_timeout: float = 5.0,
                 response_timeout: float = 3.0, max_retries: int = 5):
        """
        :param ws_url:           ESP32 WebSocket 地址，例如 "ws://192.168.1.100:81"
        :param connect_timeout:  连接超时（秒）
        :param response_timeout: 等待响应超时（秒）
        :param max_retries:      断线重连最大次数（每轮，之后继续尝试）
        """
        self._ws_url         = ws_url
        self._connect_timeout  = connect_timeout
        self._response_timeout = response_timeout
        self._max_retries    = max_retries

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected      = False
        self._reconnecting   = False

        # 等待中的请求：rid -> asyncio.Future
        self._pending: Dict[str, asyncio.Future] = {}

        # 状态推送回调列表
        self._status_callbacks: list[Callable[[Dict], None]] = []

        # 后台接收任务
        self._recv_task: Optional[asyncio.Task] = None

    # ==================== 连接管理 ====================

    async def connect(self) -> bool:
        """
        连接到 ESP32 WebSocket 服务器
        :return: True=连接成功
        """
        try:
            logger.info(f"[ESP32Driver] Connecting to {self._ws_url} ...")
            self._ws = await asyncio.wait_for(
                websockets.connect(self._ws_url),
                timeout=self._connect_timeout
            )
            self._connected = True
            # 启动后台接收循环
            self._recv_task = asyncio.create_task(self._recv_loop())
            logger.info("[ESP32Driver] Connected successfully")
            return True
        except asyncio.TimeoutError:
            logger.error(f"[ESP32Driver] Connection timeout to {self._ws_url}")
            return False
        except (InvalidURI, OSError, ConnectionRefusedError) as e:
            logger.error(f"[ESP32Driver] Connection failed: {e}")
            return False

    async def disconnect(self):
        """主动断开连接"""
        self._connected = False
        if self._recv_task:
            self._recv_task.cancel()
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("[ESP32Driver] Disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    # ==================== 消息发送 ====================

    def _skill_to_dcc(self, skill: str, params: dict, rid: str) -> dict:
        """
        将内部 skill 格式转换为 D-C-C 紧凑协议（Edge ↔ ESP32 通信层）

        :param skill:  技能名，例如 "chassis.move"
        :param params: 技能参数字典
        :param rid:    请求 ID（用于响应匹配）
        :return: D-C-C 格式的消息字典
        """
        msg: dict = {"rid": rid}
        if skill == "chassis.move":
            cmd_map = {
                "forward":  "fwd",
                "backward": "bwd",
                "left":     "lt",
                "right":    "rt",
                "stop":     "stp",
            }
            direction = params.get("direction", "stop")
            c = cmd_map.get(direction, "stp")
            msg.update({"d": "chassis", "c": c, "v": params.get("speed", 0.5)})
            if params.get("duration", 0) > 0:
                msg["t"] = params["duration"]
        elif skill == "chassis.drive":
            msg.update({
                "d": "chassis", "c": "drv",
                "l": params.get("linear", 0.0),
                "a": params.get("angular", 0.0),
            })
        elif skill == "chassis.stop":
            msg.update({"d": "chassis", "c": "stp"})
        elif skill == "system.emergency_stop":
            msg.update({"d": "sys", "c": "estop"})
        elif skill == "system.clear_emergency_stop":
            msg.update({"d": "sys", "c": "clr"})
        elif skill == "system.get_status":
            msg.update({"d": "sys", "c": "stat"})
        elif skill == "system.heartbeat":
            msg.update({"d": "sys", "c": "hb"})
        else:
            # 未知技能：尽量拆分 domain.command
            parts = skill.split(".", 1)
            msg.update({
                "d": parts[0],
                "c": parts[1] if len(parts) > 1 else skill,
            })
        return msg

    async def send_skill(self, skill: str, params: Dict = None,
                         source: str = "system") -> Optional[Dict]:
        """
        发送技能调用指令，并等待 ESP32 响应。
        内部自动将 skill 转换为 D-C-C 协议后发送。

        :param skill:   技能名称，例如 "chassis.move"
        :param params:  技能参数字典
        :param source:  指令来源（仅本地日志记录，不发给 ESP32）
        :return: 标准响应字典 {"success":bool,"message":str,"data":{...}}，连接失败返回 None
        """
        if not self.is_connected:
            logger.warning("[ESP32Driver] Not connected, cannot send skill")
            return None

        rid = str(uuid.uuid4())[:8]
        payload = self._skill_to_dcc(skill, params or {}, rid)

        # 创建 Future 等待响应
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[rid] = future

        try:
            await self._ws.send(json.dumps(payload))
            logger.debug(f"[ESP32Driver] Sent D-C-C skill={skill} rid={rid} payload={payload}")

            # 等待响应（带超时）
            response = await asyncio.wait_for(future, timeout=self._response_timeout)
            return response

        except asyncio.TimeoutError:
            logger.warning(f"[ESP32Driver] Response timeout for rid={rid} skill={skill}")
            self._pending.pop(rid, None)
            return {"success": False, "message": "response_timeout"}

        except ConnectionClosed:
            logger.error("[ESP32Driver] Connection closed while sending")
            self._pending.pop(rid, None)
            self._connected = False
            asyncio.create_task(self._auto_reconnect())
            return {"success": False, "message": "connection_lost"}

    async def send_heartbeat(self) -> bool:
        """
        发送心跳包，保持 ESP32 不触发失联停车
        建议每 500ms 调用一次（在摇杆控制模式下）
        :return: True=成功
        """
        resp = await self.send_skill("system.heartbeat")
        return resp is not None and resp.get("success", False)

    # ==================== 回调注册 ====================

    def on_status(self, callback: Callable[[Dict], None]):
        """
        注册状态推送回调
        ESP32 每秒广播一次状态，触发此回调

        :param callback: 接受一个 dict 参数的函数
        示例：
            driver.on_status(lambda d: print(f"State: {d['state']}"))
        """
        self._status_callbacks.append(callback)

    # ==================== 内部方法 ====================

    async def _recv_loop(self):
        """后台循环：持续接收 ESP32 消息"""
        try:
            async for raw_message in self._ws:
                self._handle_message(raw_message)
        except ConnectionClosed:
            logger.warning("[ESP32Driver] Connection closed, starting reconnect...")
            self._connected = False
            # 触发所有等待中的 Future（失败）
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_result({"success": False, "message": "connection_lost"})
            self._pending.clear()
            asyncio.create_task(self._auto_reconnect())
        except asyncio.CancelledError:
            pass  # 正常取消

    def _handle_message(self, raw: str):
        """解析收到的消息，分发到等待中的 Future 或状态回调"""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"[ESP32Driver] Invalid JSON: {raw}")
            return

        # D-C-C 协议：通过 "rid" 字段判断是否为技能响应
        rid = data.get("rid")
        if rid and rid in self._pending:
            # D-C-C 响应 → 翻译回标准格式供上层使用
            fut = self._pending.pop(rid)
            if not fut.done():
                ok = data.get("ok", False)
                legacy = {
                    "success": ok,
                    "message": data.get("err", "ok") if not ok else "ok",
                    "data":    {"state": data.get("s", "")},
                }
                fut.set_result(legacy)
                logger.debug(f"[ESP32Driver] D-C-C response rid={rid}: ok={ok} s={data.get('s')}")
        elif rid:
            logger.debug(f"[ESP32Driver] Unmatched D-C-C response rid={rid}")

        elif data.get("t") == "s":
            # D-C-C 状态广播推送：{"t":"s","s":"fwd","l":0.5,"a":0.0,"em":false,"up":...}
            for cb in self._status_callbacks:
                try:
                    cb(data)
                except Exception as e:
                    logger.error(f"[ESP32Driver] Status callback error: {e}")
        else:
            logger.debug(f"[ESP32Driver] Unhandled message: {data}")

    async def _auto_reconnect(self):
        """断线自动重连（指数退避）"""
        if self._reconnecting:
            return
        self._reconnecting = True

        delay = 1.0
        attempt = 0
        while not self._connected:
            attempt += 1
            logger.info(f"[ESP32Driver] Reconnecting... attempt {attempt} (delay={delay:.1f}s)")
            await asyncio.sleep(delay)
            success = await self.connect()
            if success:
                break
            delay = min(delay * 2, 30.0)  # 最长 30 秒重试间隔

        self._reconnecting = False
