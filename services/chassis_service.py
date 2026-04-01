"""
services/chassis_service.py
底盘服务层（Edge Agent）

职责：
    对 ESP32Driver 做语义封装，对外暴露底盘业务接口
    上层（skill_dispatcher / API层）不需要关心 WebSocket 细节，
    只需要调用 move_forward() / stop() 等方法

特性：
    - 统一的底盘操作接口
    - 记录当前底盘状态（最近一次上报）
    - 摇杆模式心跳维持
    - 急停/解除急停
"""

import asyncio
import logging
from typing import Dict, Optional

from drivers.esp32_driver import ESP32Driver

logger = logging.getLogger(__name__)


class ChassisService:
    """
    底盘服务，封装底盘所有控制操作

    使用示例：
        driver  = ESP32Driver("ws://192.168.1.100:81")
        chassis = ChassisService(driver)
        await chassis.start()

        await chassis.move_forward(speed=0.5)
        await chassis.stop()
        await chassis.emergency_stop()

        status = chassis.get_status()
    """

    def __init__(self, driver: ESP32Driver):
        self._driver = driver

        # 本地缓存的最新底盘状态（来自 ESP32 状态上报）
        self._status: Dict = {
            "state":     "unknown",
            "linear":    0.0,
            "angular":   0.0,
            "emergency": False,
        }

        # 摇杆心跳任务
        self._joystick_heartbeat_task: Optional[asyncio.Task] = None
        self._joystick_active = False

    async def start(self):
        """
        启动底盘服务
        连接 ESP32 并注册状态回调
        """
        connected = await self._driver.connect()
        if not connected:
            logger.error("[ChassisService] Failed to connect to ESP32")
            return False

        # 注册 ESP32 状态推送回调
        self._driver.on_status(self._on_esp32_status)
        logger.info("[ChassisService] Started, connected to ESP32")
        return True

    async def stop_service(self):
        """停止底盘服务（关闭时调用）"""
        await self.stop()
        self._stop_joystick_heartbeat()
        await self._driver.disconnect()

    # ==================== 运动控制接口 ====================

    async def move_forward(self, speed: float = 0.5) -> Dict:
        """
        前进
        :param speed: 速度 0.0~1.0
        :return: 执行结果
        """
        self._stop_joystick_heartbeat()
        logger.info(f"[ChassisService] move_forward speed={speed}")
        return await self._driver.send_skill("chassis.move", {
            "direction": "forward",
            "speed": float(speed)
        }, source="system")

    async def move_backward(self, speed: float = 0.5) -> Dict:
        """后退"""
        self._stop_joystick_heartbeat()
        logger.info(f"[ChassisService] move_backward speed={speed}")
        return await self._driver.send_skill("chassis.move", {
            "direction": "backward",
            "speed": float(speed)
        }, source="system")

    async def turn_left(self, speed: float = 0.5) -> Dict:
        """原地左转"""
        self._stop_joystick_heartbeat()
        logger.info(f"[ChassisService] turn_left speed={speed}")
        return await self._driver.send_skill("chassis.move", {
            "direction": "left",
            "speed": float(speed)
        }, source="system")

    async def turn_right(self, speed: float = 0.5) -> Dict:
        """原地右转"""
        self._stop_joystick_heartbeat()
        logger.info(f"[ChassisService] turn_right speed={speed}")
        return await self._driver.send_skill("chassis.move", {
            "direction": "right",
            "speed": float(speed)
        }, source="system")

    async def move_timed(self, direction: str, speed: float = 0.5,
                         duration_ms: int = 2000) -> Dict:
        """
        运动指定时间后自动停止
        :param direction:   "forward" / "backward" / "left" / "right"
        :param speed:       速度 0.0~1.0
        :param duration_ms: 持续时间（毫秒）
        """
        self._stop_joystick_heartbeat()
        logger.info(f"[ChassisService] move_timed dir={direction} speed={speed} duration={duration_ms}ms")
        return await self._driver.send_skill("chassis.move", {
            "direction": direction,
            "speed":     float(speed),
            "duration":  int(duration_ms)
        }, source="system")

    async def stop(self) -> Dict:
        """停止运动（自由停转）"""
        self._stop_joystick_heartbeat()
        logger.info("[ChassisService] stop")
        return await self._driver.send_skill("chassis.stop", {}, source="system")

    async def emergency_stop(self) -> Dict:
        """
        急停（最高优先级，主动刹车）
        急停后底盘不响应任何运动指令，必须调用 clear_emergency_stop() 解除
        """
        self._stop_joystick_heartbeat()
        logger.warning("[ChassisService] EMERGENCY STOP!")
        return await self._driver.send_skill("system.emergency_stop", {}, source="system")

    async def clear_emergency_stop(self) -> Dict:
        """解除急停状态"""
        logger.info("[ChassisService] Clear emergency stop")
        return await self._driver.send_skill("system.clear_emergency_stop", {}, source="system")

    # ==================== 摇杆差速驱动模式 ====================

    async def drive(self, linear: float, angular: float) -> Dict:
        """
        摇杆差速驱动（App 摇杆持续发送）
        :param linear:  线速度 -1.0(后退) ~ +1.0(前进)
        :param angular: 角速度 -1.0(左转) ~ +1.0(右转)

        调用此方法会自动启动心跳任务（保持 ESP32 不触发失联停车）
        停止摇杆时请调用 stop()，会自动停止心跳任务
        """
        if not self._joystick_active:
            self._start_joystick_heartbeat()

        return await self._driver.send_skill("chassis.drive", {
            "linear":  float(linear),
            "angular": float(angular)
        }, source="app")

    def _start_joystick_heartbeat(self):
        """启动摇杆心跳任务，每 500ms 刷新一次失联超时"""
        self._joystick_active = True
        if self._joystick_heartbeat_task is None or self._joystick_heartbeat_task.done():
            self._joystick_heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            logger.debug("[ChassisService] Joystick heartbeat started")

    def _stop_joystick_heartbeat(self):
        """停止摇杆心跳任务"""
        self._joystick_active = False
        if self._joystick_heartbeat_task and not self._joystick_heartbeat_task.done():
            self._joystick_heartbeat_task.cancel()
            self._joystick_heartbeat_task = None
            logger.debug("[ChassisService] Joystick heartbeat stopped")

    async def _heartbeat_loop(self):
        """心跳循环：每 500ms 发送一次心跳"""
        try:
            while self._joystick_active:
                await self._driver.send_heartbeat()
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    # ==================== 状态查询 ====================

    def get_status(self) -> Dict:
        """
        获取最新底盘状态（来自 ESP32 最近一次上报）
        :return: 包含 state / linear / angular / emergency 等字段的字典
        """
        connected = self._driver.is_connected
        return {
            **self._status,
            "connected": connected
        }

    async def fetch_status(self) -> Dict:
        """
        主动拉取底盘状态（通过 WebSocket 请求）
        与 get_status() 的区别：这是实时请求，不依赖推送缓存
        """
        resp = await self._driver.send_skill("system.get_status", {})
        if resp and resp.get("success"):
            return resp.get("data", {})
        return {}

    # ==================== 内部回调 ====================

    def _on_esp32_status(self, data: Dict):
        """接收 ESP32 D-C-C 状态广播，更新本地缓存
        D-C-C 广播格式：{"t":"s","s":"fwd","l":0.5,"a":0.0,"em":false,"up":12345}
        """
        if data.get("t") == "s":
            self._status = {
                "state":     data.get("s", "unknown"),
                "linear":    data.get("l", 0.0),
                "angular":   data.get("a", 0.0),
                "emergency": data.get("em", False),
                "uptime_ms": data.get("up", 0),
            }
