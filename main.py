"""
main.py
Edge Agent 边缘执行层 — 主程序入口

架构说明：
  Brain（本地 LLM）与 Edge Agent 同进程运行，Brain 直接调用
  SkillDispatcher.dispatch() ，无需 WebSocket 中间层。

  Brain (本地LLM)
    └→ dispatcher.dispatch({"skill":"chassis.move","params":{...}})
         └→ ChassisService → ESP32Driver → ESP32 (D-C-C over WebSocket)

启动顺序：
  1. 加载配置
  2. 初始化日志
  3. 创建 ESP32Driver（底层通信，D-C-C 协议）
  4. 创建 ChassisService（底盘服务）
  5. 创建 SkillDispatcher（技能路由）
  6. 进入主循环（保活）

用法：
  python main.py
  python main.py --config configs/device.yaml
  python main.py --esp32 ws://192.168.1.100:81

调试/测试模式（不需要真实 ESP32）：
  python main.py --mock
"""

import asyncio
import argparse
import logging
import logging.handlers
import signal
import sys
from pathlib import Path
from typing import Optional

import yaml

from drivers.esp32_driver import ESP32Driver
from services.chassis_service import ChassisService
from core.skill_dispatcher import SkillDispatcher


# ==================== 日志初始化 ====================

def setup_logging(level: str = "INFO", log_file: Optional[str] = None,
                  max_bytes: int = 5_242_880, backup_count: int = 3):
    """配置日志：控制台 + 可选文件轮转"""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    # 文件输出（如果配置了）
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count,
            encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
        logging.info(f"Logging to file: {log_file}")


# ==================== 配置加载 ====================

def load_config(config_path: str) -> dict:
    """加载 device.yaml 配置文件"""
    path = Path(config_path)
    if not path.exists():
        logging.warning(f"Config file not found: {config_path}, using defaults")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ==================== Mock ESP32 Driver ====================

class MockESP32Driver:
    """
    Mock 驱动：不需要真实硬件，用于本地开发调试
    收到技能调用后打印日志并返回成功响应
    """
    def __init__(self):
        self._status_callbacks = []
        self._connected = True

    async def connect(self) -> bool:
        logging.info("[MockESP32] Mock connected (no real hardware)")
        return True

    async def disconnect(self):
        logging.info("[MockESP32] Mock disconnected")

    @property
    def is_connected(self) -> bool:
        return True

    async def send_skill(self, skill: str, params: dict = None,
                         source: str = "system") -> dict:
        logging.info(f"[MockESP32] 🔧 Skill: {skill} params={params}")
        return {
            "success": True,
            "skill":   skill,
            "message": "ok (mock)",
            "data":    {"state": "mock_ok"}
        }

    async def send_heartbeat(self) -> bool:
        return True

    def on_status(self, callback):
        self._status_callbacks.append(callback)


# ==================== 主应用 ====================

class EdgeAgent:
    """机器人 Edge Agent 边缘执行层主应用"""

    def __init__(self, config: dict, mock: bool = False):
        self._config  = config
        self._mock    = mock
        self._running = False

        # 从配置读取 ESP32 地址
        esp32_cfg = config.get("esp32", {})
        esp32_host = esp32_cfg.get("host", "192.168.1.100")
        esp32_port = esp32_cfg.get("ws_port", 81)
        self._esp32_url = esp32_cfg.get("ws_url",
                                         f"ws://{esp32_host}:{esp32_port}")

        # 各模块实例（Brain 通过 dispatcher 直接调用）
        self._driver:     Optional[ESP32Driver]     = None
        self._chassis:    Optional[ChassisService]  = None
        self._dispatcher: Optional[SkillDispatcher] = None

    async def start(self):
        """启动所有模块"""
        logger = logging.getLogger("EdgeAgent")
        logger.info("=" * 50)
        logger.info("  具身智能机器人 MVP — Edge Agent 边缘执行层")
        logger.info("=" * 50)

        # 1. 创建驱动层
        if self._mock:
            logger.info("[Init] Using MOCK ESP32 driver (no hardware needed)")
            self._driver = MockESP32Driver()
        else:
            logger.info(f"[Init] Connecting to ESP32 at {self._esp32_url}")
            esp32_cfg = self._config.get("esp32", {})
            self._driver = ESP32Driver(
                ws_url=self._esp32_url,
                connect_timeout=esp32_cfg.get("connect_timeout", 5.0),
                response_timeout=esp32_cfg.get("response_timeout", 3.0)
            )

        # 2. 创建服务层
        self._chassis = ChassisService(self._driver)
        ok = await self._chassis.start()
        if not ok and not self._mock:
            logger.error("[Init] Failed to connect to ESP32. "
                         "Tip: Use --mock flag for development without hardware.")
            return False

        # 3. 创建技能调度器并注册服务
        self._dispatcher = SkillDispatcher()
        self._dispatcher.register_chassis_service(self._chassis)

        self._running = True
        logger.info(f"[Init] ✅ All modules ready!")
        logger.info(f"[Init] ESP32 WebSocket: {self._esp32_url}")
        logger.info(f"[Init] Brain can call: agent.dispatcher.dispatch(skill_call)")
        logger.info(f"[Init] Press Ctrl+C to stop")
        return True

    async def run(self):
        """主循环：保活（Brain 在独立协程中运行，不阻塞此处）"""
        logger = logging.getLogger("EdgeAgent")
        while self._running:
            try:
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Main] Loop error: {e}")
                await asyncio.sleep(1.0)

    @property
    def dispatcher(self) -> Optional[SkillDispatcher]:
        """Brain 通过此属性直接调用技能调度器"""
        return self._dispatcher

    @property
    def chassis(self) -> Optional[ChassisService]:
        """直接访问底盘服务（状态查询等）"""
        return self._chassis

    async def stop(self):
        """优雅关闭"""
        logger = logging.getLogger("EdgeAgent")
        logger.info("[Shutdown] Stopping Edge Agent...")
        self._running = False

        if self._chassis:
            await self._chassis.stop_service()

        logger.info("[Shutdown] Done. Goodbye! 🤖")


# ==================== 命令行入口 ====================

async def main():
    parser = argparse.ArgumentParser(description="Robot MVP — Edge Agent 边缘执行层")
    parser.add_argument("--config", default="configs/device.yaml",
                        help="配置文件路径 (default: configs/device.yaml)")
    parser.add_argument("--esp32",  default=None,
                        help="ESP32 WebSocket 地址，覆盖配置文件 (e.g. ws://192.168.1.100:81)")
    parser.add_argument("--mock",   action="store_true",
                        help="使用 Mock 驱动（不需要真实 ESP32 硬件，适合本地开发）")
    parser.add_argument("--log",    default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="日志级别 (default: INFO)")
    args = parser.parse_args()

    # 加载配置
    config = load_config(args.config)

    # 命令行参数覆盖配置文件
    if args.esp32:
        config.setdefault("esp32", {})["ws_url"] = args.esp32

    # 初始化日志
    log_cfg = config.get("logging", {})
    setup_logging(
        level=args.log,
        log_file=log_cfg.get("file"),
        max_bytes=log_cfg.get("max_bytes", 5_242_880),
        backup_count=log_cfg.get("backup_count", 3)
    )

    # 创建并启动应用
    app = EdgeAgent(config, mock=args.mock)

    # 注册信号处理（Ctrl+C 优雅停止）
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(app.stop()))

    ok = await app.start()
    if ok:
        await app.run()


if __name__ == "__main__":
    asyncio.run(main())
