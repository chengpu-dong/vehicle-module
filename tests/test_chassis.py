"""
tests/test_chassis.py
底盘模块单元测试

运行方式：
  python -m pytest tests/ -v
  python tests/test_chassis.py       # 直接运行（不依赖 pytest）

测试内容：
  1. CommandParser：语音文本 -> 技能调用 映射
  2. SkillDispatcher：技能路由与急停逻辑
  3. 集成测试：parser -> dispatcher -> mock_chassis 完整链路
"""

import asyncio
import sys
import os

# 确保从项目根目录导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.command_parser import CommandParser
from core.skill_dispatcher import SkillDispatcher


# ==================== Mock ChassisService ====================

class MockChassisService:
    """最简单的 Mock 底盘服务，记录调用历史"""

    def __init__(self):
        self.call_log = []

    async def move_forward(self, speed=0.5):
        self.call_log.append(("move_forward", speed))
        return {"success": True, "message": "ok", "data": {"state": "moving_forward"}}

    async def move_backward(self, speed=0.5):
        self.call_log.append(("move_backward", speed))
        return {"success": True, "message": "ok", "data": {"state": "moving_backward"}}

    async def turn_left(self, speed=0.5):
        self.call_log.append(("turn_left", speed))
        return {"success": True, "message": "ok", "data": {"state": "turning_left"}}

    async def turn_right(self, speed=0.5):
        self.call_log.append(("turn_right", speed))
        return {"success": True, "message": "ok", "data": {"state": "turning_right"}}

    async def stop(self):
        self.call_log.append(("stop",))
        return {"success": True, "message": "ok", "data": {"state": "stopped"}}

    async def emergency_stop(self):
        self.call_log.append(("emergency_stop",))
        return {"success": True, "message": "ok", "data": {"state": "emergency_stop"}}

    async def clear_emergency_stop(self):
        self.call_log.append(("clear_emergency_stop",))
        return {"success": True, "message": "ok"}

    async def move_timed(self, direction, speed=0.5, duration_ms=2000):
        self.call_log.append(("move_timed", direction, speed, duration_ms))
        return {"success": True, "message": "ok", "data": {"state": "moving"}}

    async def drive(self, linear, angular):
        self.call_log.append(("drive", linear, angular))
        return {"success": True, "message": "ok", "data": {"state": "driving"}}

    async def fetch_status(self):
        return {"state": "stopped", "emergency": False}

    def get_status(self):
        return {"state": "stopped", "emergency": False, "connected": True}


# ==================== CommandParser 测试 ====================

def test_parser_forward():
    """测试前进关键词识别"""
    parser = CommandParser()
    result = parser.parse("前进")
    assert result is not None, "前进 应被识别"
    assert result["skill"] == "chassis.move"
    assert result["params"]["direction"] == "forward"
    print("  ✅ test_parser_forward passed")


def test_parser_backward():
    parser = CommandParser()
    result = parser.parse("后退")
    assert result is not None
    assert result["params"]["direction"] == "backward"
    print("  ✅ test_parser_backward passed")


def test_parser_stop():
    parser = CommandParser()
    result = parser.parse("停止")
    assert result is not None
    assert result["skill"] == "chassis.stop"
    print("  ✅ test_parser_stop passed")


def test_parser_turn_left():
    parser = CommandParser()
    result = parser.parse("左转")
    assert result is not None
    assert result["params"]["direction"] == "left"
    print("  ✅ test_parser_turn_left passed")


def test_parser_emergency_stop():
    parser = CommandParser()
    result = parser.parse("急停")
    assert result is not None
    assert result["skill"] == "system.emergency_stop"
    print("  ✅ test_parser_emergency_stop passed")


def test_parser_timed_forward():
    """测试带时长指令：前进两秒"""
    parser = CommandParser()
    result = parser.parse("前进两秒")
    assert result is not None, "前进两秒 应被识别"
    assert result["skill"] == "chassis.move"
    assert result["params"]["direction"] == "forward"
    assert result["params"]["duration"] == 2000, f"期望 2000ms，实际 {result['params']['duration']}"
    print("  ✅ test_parser_timed_forward passed")


def test_parser_timed_forward_digits():
    """测试带时长指令（数字版）：前进3秒"""
    parser = CommandParser()
    result = parser.parse("前进3秒")
    assert result is not None
    assert result["params"]["duration"] == 3000
    print("  ✅ test_parser_timed_forward_digits passed")


def test_parser_unknown():
    """未知命令应返回 None"""
    parser = CommandParser()
    result = parser.parse("帮我点杯咖啡")
    assert result is None, "未知命令应返回 None"
    print("  ✅ test_parser_unknown passed")


def test_parser_empty():
    """空字符串应返回 None"""
    parser = CommandParser()
    result = parser.parse("")
    assert result is None
    print("  ✅ test_parser_empty passed")


def test_parser_wave():
    """挥手命令映射到机械臂"""
    parser = CommandParser()
    result = parser.parse("挥手")
    assert result is not None
    assert result["skill"] == "arm.execute_preset"
    assert result["params"]["action"] == "wave"
    print("  ✅ test_parser_wave passed")


def test_parser_source_tag():
    """验证解析结果包含 source 字段"""
    parser = CommandParser()
    result = parser.parse("前进", source="asr", confidence=0.9)
    assert result["source"] == "asr"
    assert result["confidence"] == 0.9
    print("  ✅ test_parser_source_tag passed")


# ==================== SkillDispatcher 测试 ====================

async def test_dispatcher_forward():
    """调度器路由前进指令"""
    mock_chassis = MockChassisService()
    dispatcher = SkillDispatcher()
    dispatcher.register_chassis_service(mock_chassis)

    result = await dispatcher.dispatch({
        "skill":  "chassis.move",
        "params": {"direction": "forward", "speed": 0.5},
        "source": "test"
    })
    assert result["success"] is True
    assert len(mock_chassis.call_log) == 1
    assert mock_chassis.call_log[0][0] == "move_forward"
    print("  ✅ test_dispatcher_forward passed")


async def test_dispatcher_emergency_stop():
    """急停后不允许继续运动"""
    mock_chassis = MockChassisService()
    dispatcher = SkillDispatcher()
    dispatcher.register_chassis_service(mock_chassis)

    # 触发急停
    stop_result = await dispatcher.dispatch({
        "skill":  "system.emergency_stop",
        "params": {},
        "source": "test"
    })
    assert stop_result["success"] is True
    assert dispatcher.is_emergency_stopped is True

    # 急停后尝试前进，应被拦截
    move_result = await dispatcher.dispatch({
        "skill":  "chassis.move",
        "params": {"direction": "forward", "speed": 0.5},
        "source": "test"
    })
    assert move_result["success"] is False
    assert move_result["error_code"] == "emergency_stop_active"
    print("  ✅ test_dispatcher_emergency_stop passed")


async def test_dispatcher_clear_emergency():
    """解除急停后可以继续运动"""
    mock_chassis = MockChassisService()
    dispatcher = SkillDispatcher()
    dispatcher.register_chassis_service(mock_chassis)

    await dispatcher.dispatch({"skill": "system.emergency_stop", "params": {}})
    assert dispatcher.is_emergency_stopped is True

    await dispatcher.dispatch({"skill": "system.clear_emergency_stop", "params": {}})
    assert dispatcher.is_emergency_stopped is False

    move_result = await dispatcher.dispatch({
        "skill":  "chassis.move",
        "params": {"direction": "forward", "speed": 0.5},
        "source": "test"
    })
    assert move_result["success"] is True
    print("  ✅ test_dispatcher_clear_emergency passed")


async def test_dispatcher_unknown_skill():
    """未注册技能返回错误"""
    dispatcher = SkillDispatcher()
    result = await dispatcher.dispatch({
        "skill":  "nonexistent.skill",
        "params": {},
        "source": "test"
    })
    assert result["success"] is False
    assert result["error_code"] == "unknown_skill"
    print("  ✅ test_dispatcher_unknown_skill passed")


# ==================== 集成测试 ====================

async def test_integration_asr_to_chassis():
    """完整链路：语音文本 -> 解析 -> 调度 -> Mock底盘"""
    parser   = CommandParser()
    mock_chassis = MockChassisService()
    dispatcher = SkillDispatcher()
    dispatcher.register_chassis_service(mock_chassis)

    # 模拟用户说"前进"
    skill_call = parser.parse("前进", source="asr", confidence=0.92)
    assert skill_call is not None

    result = await dispatcher.dispatch(skill_call)
    assert result["success"] is True
    assert mock_chassis.call_log[0][0] == "move_forward"
    print("  ✅ test_integration_asr_to_chassis passed")


async def test_integration_timed_move():
    """完整链路：带时长语音 -> 调度 -> Mock底盘"""
    parser = CommandParser()
    mock_chassis = MockChassisService()
    dispatcher = SkillDispatcher()
    dispatcher.register_chassis_service(mock_chassis)

    skill_call = parser.parse("前进两秒", source="asr")
    assert skill_call is not None
    assert skill_call["params"]["duration"] == 2000

    result = await dispatcher.dispatch(skill_call)
    assert result["success"] is True
    assert mock_chassis.call_log[0][0] == "move_timed"
    assert mock_chassis.call_log[0][3] == 2000  # duration_ms
    print("  ✅ test_integration_timed_move passed")


# ==================== 测试运行器 ====================

def run_sync_tests():
    """运行所有同步测试"""
    print("\n【CommandParser 测试】")
    test_parser_forward()
    test_parser_backward()
    test_parser_stop()
    test_parser_turn_left()
    test_parser_emergency_stop()
    test_parser_timed_forward()
    test_parser_timed_forward_digits()
    test_parser_unknown()
    test_parser_empty()
    test_parser_wave()
    test_parser_source_tag()


async def run_async_tests():
    """运行所有异步测试"""
    print("\n【SkillDispatcher 测试】")
    await test_dispatcher_forward()
    await test_dispatcher_emergency_stop()
    await test_dispatcher_clear_emergency()
    await test_dispatcher_unknown_skill()

    print("\n【集成测试】")
    await test_integration_asr_to_chassis()
    await test_integration_timed_move()


if __name__ == "__main__":
    print("=" * 50)
    print("  底盘控制模块 - 单元测试")
    print("=" * 50)

    try:
        run_sync_tests()
        asyncio.run(run_async_tests())
        print("\n" + "=" * 50)
        print("  ✅ 所有测试通过！")
        print("=" * 50)
    except AssertionError as e:
        print(f"\n  ❌ 测试失败: {e}")
        sys.exit(1)
