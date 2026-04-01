"""
core/skill_dispatcher.py
技能调度器（Edge Agent 路由中心）

职责：
    接收统一 skill_call 格式，分发到对应服务模块执行。
    所有指令来源（Brain / Gateway）统一经此路由到 ChassisService。

    命名空间：
      chassis.*   → ChassisService
      system.*    → 系统操作（急停等）

skill_call 格式（输入）：
    {
        "skill":  "chassis.move",
        "params": {"direction": "forward", "speed": 0.5},
        "source": "brain" | "gateway" | "system"   （可选，仅日志用）
    }

响应格式（输出）：
    {
        "success": True,
        "skill":   "chassis.move",
        "message": "ok",
        "data":    {...}
    }
"""

import asyncio
import logging
import time
import uuid
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)


class SkillDispatcher:
    """
    技能调度器

    使用示例：
        dispatcher = SkillDispatcher()
        dispatcher.register_chassis_service(chassis_svc)

        result = await dispatcher.dispatch({
            "skill":  "chassis.move",
            "params": {"direction": "forward", "speed": 0.5},
            "source": "asr"
        })
    """

    def __init__(self):
        # 底盘服务（通过 register_chassis_service 注入）
        self._chassis_service = None

        # 技能路由表：skill_name -> handler 方法
        self._routes: Dict[str, Any] = {}

        # 急停标志（最高优先级，任何来源的急停都能触发）
        self._emergency_stopped = False

    # ==================== 服务注册 ====================

    def register_chassis_service(self, service):
        """注册底盘服务"""
        self._chassis_service = service
        self._register_chassis_skills()
        logger.info("[Dispatcher] ChassisService registered")

    # ==================== 技能分发主入口 ====================

    async def dispatch(self, skill_call: Dict) -> Dict:
        """
        分发技能调用

        :param skill_call: 技能调用字典，包含 skill / params / source / request_id
        :return: 执行结果字典
        """
        skill      = skill_call.get("skill", "")
        params     = skill_call.get("params", {})
        source     = skill_call.get("source", "system")
        request_id = skill_call.get("request_id") or str(uuid.uuid4())[:8]

        logger.info(f"[Dispatcher] dispatch skill={skill} source={source} req={request_id}")

        # ========== 急停特殊处理（最高优先级，任何来源都可触发）==========
        if skill == "system.emergency_stop":
            return await self._handle_emergency_stop(request_id)

        # 急停状态下只允许解除急停
        if self._emergency_stopped and skill != "system.clear_emergency_stop":
            logger.warning(f"[Dispatcher] BLOCKED by emergency stop: skill={skill}")
            return self._make_error_response(request_id, skill,
                                             "emergency_stop_active",
                                             "System is in emergency stop state")

        # ========== 解除急停 ==========
        if skill == "system.clear_emergency_stop":
            return await self._handle_clear_emergency_stop(request_id)

        # ========== 路由到对应服务 ==========
        handler = self._routes.get(skill)
        if handler is None:
            logger.warning(f"[Dispatcher] Unknown skill: {skill}")
            return self._make_error_response(request_id, skill,
                                             "unknown_skill",
                                             f"Skill '{skill}' is not registered")

        try:
            result = await handler(params)
            # 统一包装返回格式
            if isinstance(result, dict) and "success" in result:
                result["request_id"] = request_id
                result["skill"]      = skill
                return result
            else:
                return self._make_success_response(request_id, skill, data=result)

        except Exception as e:
            logger.error(f"[Dispatcher] Skill execution error: skill={skill} error={e}")
            return self._make_error_response(request_id, skill,
                                             "execution_error", str(e))

    # ==================== 底盘技能路由 ====================

    def _register_chassis_skills(self):
        """注册底盘相关技能路由"""
        svc = self._chassis_service

        async def chassis_move(params: Dict) -> Dict:
            direction   = params.get("direction", "stop")
            speed       = float(params.get("speed", 0.5))
            duration_ms = int(params.get("duration", 0))

            if direction == "forward":
                if duration_ms > 0:
                    return await svc.move_timed("forward", speed, duration_ms)
                return await svc.move_forward(speed)
            elif direction == "backward":
                if duration_ms > 0:
                    return await svc.move_timed("backward", speed, duration_ms)
                return await svc.move_backward(speed)
            elif direction == "left":
                if duration_ms > 0:
                    return await svc.move_timed("left", speed, duration_ms)
                return await svc.turn_left(speed)
            elif direction == "right":
                if duration_ms > 0:
                    return await svc.move_timed("right", speed, duration_ms)
                return await svc.turn_right(speed)
            elif direction == "stop":
                return await svc.stop()
            else:
                return {"success": False, "message": f"Unknown direction: {direction}"}

        async def chassis_drive(params: Dict) -> Dict:
            linear  = float(params.get("linear", 0.0))
            angular = float(params.get("angular", 0.0))
            return await svc.drive(linear, angular)

        async def chassis_stop(params: Dict) -> Dict:
            return await svc.stop()

        async def system_get_status(params: Dict) -> Dict:
            status = await svc.fetch_status()
            return {"success": True, "message": "ok", "data": status}

        # 注册路由
        self._routes["chassis.move"]      = chassis_move
        self._routes["chassis.drive"]     = chassis_drive
        self._routes["chassis.stop"]      = chassis_stop
        self._routes["system.get_status"] = system_get_status

    # ==================== 系统技能 ====================

    async def _handle_emergency_stop(self, request_id: str) -> Dict:
        """处理急停指令"""
        self._emergency_stopped = True
        logger.warning("[Dispatcher] ⚠️  EMERGENCY STOP triggered!")

        results = []
        if self._chassis_service:
            r = await self._chassis_service.emergency_stop()
            results.append(r)

        return {
            "request_id": request_id,
            "success":    True,
            "skill":      "system.emergency_stop",
            "message":    "ok",
            "data":       {"modules_stopped": len(results)}
        }

    async def _handle_clear_emergency_stop(self, request_id: str) -> Dict:
        """处理解除急停"""
        self._emergency_stopped = False
        logger.info("[Dispatcher] Emergency stop cleared")

        if self._chassis_service:
            await self._chassis_service.clear_emergency_stop()

        return {
            "request_id": request_id,
            "success":    True,
            "skill":      "system.clear_emergency_stop",
            "message":    "ok",
            "data":       {}
        }

    # ==================== 工具方法 ====================

    @staticmethod
    def _make_success_response(request_id: str, skill: str,
                                message: str = "ok", data: Any = None) -> Dict:
        return {
            "request_id": request_id,
            "success":    True,
            "skill":      skill,
            "message":    message,
            "data":       data or {}
        }

    @staticmethod
    def _make_error_response(request_id: str, skill: str,
                              error_code: str, message: str) -> Dict:
        return {
            "request_id": request_id,
            "success":    False,
            "skill":      skill,
            "message":    message,
            "error_code": error_code,
            "data":       {}
        }

    @property
    def is_emergency_stopped(self) -> bool:
        return self._emergency_stopped
