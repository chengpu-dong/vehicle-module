# vehicle-module · 具身智能机器人 MVP - 底盘控制模块

基于 **ESP32 + Python 上位机** 的四轮差速底盘控制系统，是机器人 MVP 的第一阶段实现。

---

## 目录

- [一、整体架构图](#一整体架构图)
- [二、分层设计原则](#二分层设计原则)
- [三、每个文件详解](#三每个文件详解)
- [四、一次完整的指令执行流程](#四一次完整的指令执行流程)
- [五、数据流总结](#五数据流总结)
- [六、快速开始](#六快速开始)
- [七、通信协议参考](#七通信协议参考)
- [八、安全机制](#八安全机制)
- [九、开发进度](#九开发进度)

---

## 一、整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                       手机 App / 浏览器                          │
│              发送 JSON 技能指令 / 语音 ASR 结果                    │
└──────────────────────┬──────────────────────────────────────────┘
                       │ WebSocket  ws://上位机IP:8765
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                    上位机（树莓派 / 电脑）                         │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  app_api/websocket_server.py   ← App 接入点             │   │
│  │  接收 App 的指令/ASR结果，统一交给下层处理                 │   │
│  └──────────────┬────────────────────────────┬─────────────┘   │
│                 │ skill_call JSON             │ asr_text        │
│                 ▼                            ▼                  │
│  ┌──────────────────────┐    ┌──────────────────────────────┐  │
│  │ core/                │    │ core/                        │  │
│  │ skill_dispatcher.py  │◀───│ command_parser.py            │  │
│  │ 技能路由 + 急停控制   │    │ 语音文本 → 技能调用           │  │
│  └──────────┬───────────┘    └──────────────────────────────┘  │
│             │ chassis.move / chassis.stop / ...                 │
│             ▼                                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  services/chassis_service.py                             │  │
│  │  底盘语义接口（moveForward/stop/drive/emergencyStop）      │  │
│  │  + 摇杆心跳维持任务                                       │  │
│  └──────────────────────┬───────────────────────────────────┘  │
│                         │ send_skill(JSON)                      │
│  ┌──────────────────────▼───────────────────────────────────┐  │
│  │  drivers/esp32_driver.py                                 │  │
│  │  WebSocket 客户端，request_id 响应匹配，自动重连           │  │
│  └──────────────────────┬───────────────────────────────────┘  │
└─────────────────────────┼───────────────────────────────────────┘
                          │ WebSocket  ws://ESP32_IP:81
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                      ESP32 固件（下位机）                         │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  firmware/chassis/chassis.ino                            │  │
│  │  WiFi连接 + WebSocket Server + JSON指令解析 + 状态上报    │  │
│  └──────────────────────┬───────────────────────────────────┘  │
│                         │ 语义命令                              │
│  ┌──────────────────────▼───────────────────────────────────┐  │
│  │  chassis_controller.h/cpp                                │  │
│  │  四轮差速计算 / 急停 / 心跳超时停车 / 速度限制             │  │
│  └──────┬──────────┬──────────┬──────────┬──────────────────┘  │
│         │          │          │          │                      │
│  ┌──────▼──┐ ┌─────▼──┐ ┌────▼───┐ ┌───▼────┐                 │
│  │Motor FL │ │Motor FR│ │Motor RL│ │Motor RR│                 │
│  │motor_   │ │motor_  │ │motor_  │ │motor_  │                 │
│  │driver   │ │driver  │ │driver  │ │driver  │                 │
│  └──────┬──┘ └────┬───┘ └────┬───┘ └────┬───┘                 │
└─────────┼─────────┼──────────┼──────────┼─────────────────────┘
          │ LEDC PWM│           │          │
          ▼         ▼           ▼          ▼
     L298N 电机驱动模块 × 2  →  四轮直流减速电机
```

---

## 二、分层设计原则

整个系统严格遵守**单向依赖**：上层调用下层，下层不知道上层存在。

```
App层
 ↓ 只发 JSON，不关心硬件
接口层（app_api）
 ↓ 只做协议转换，不做业务判断
解析层（command_parser）
 ↓ 只做文本→技能映射，不执行任何动作
调度层（skill_dispatcher）
 ↓ 只做路由和权限控制，不操作硬件
服务层（chassis_service）
 ↓ 只表达"底盘语义"，不知道 WebSocket 细节
驱动层（esp32_driver）
 ↓ 只做网络通信，不知道底盘语义
ESP32固件（chassis.ino）
 ↓ 只处理 JSON 协议，不知道业务逻辑
底盘控制器（chassis_controller）
 ↓ 只懂"前进/停止"，不知道协议
电机驱动（motor_driver）
   只操作 GPIO 和 PWM，完全不知道上面任何事
```

**好处**：任何一层可以独立替换，互不影响：

| 变更场景 | 只需修改 |
|----------|----------|
| 换成串口通信 | `drivers/esp32_driver.py` |
| 换成 LLM 解析指令 | `core/command_parser.py` |
| 换成 ROS2 接口 | `app_api/` |
| 电机驱动换 TB6612 | `firmware/chassis/motor_driver.cpp` 引脚逻辑 |
| 新增机械臂模块 | 新增 `services/arm_service.py` + 注册到 Dispatcher |

---

## 三、每个文件详解

### 📁 firmware/chassis/（ESP32 C++ 固件）

#### `config.h` — 所有硬件参数的唯一配置点

```
作用：集中定义所有需要修改的参数，换硬件只改这一个文件

关键参数：
  WIFI_SSID / WIFI_PASSWORD     ← WiFi 连接信息
  HEARTBEAT_TIMEOUT_MS = 1500   ← 1.5秒无心跳自动停车
  MAX_SPEED_PERCENT    = 80     ← 全局最大速度限制 80%
  MOTOR_FL_IN1/IN2/PWM          ← 左前电机引脚
  PWM_FREQ / PWM_RESOLUTION     ← PWM 频率和分辨率
```

#### `motor_driver.h/.cpp` — 最底层：控制单个电机

```
作用：封装 ESP32 的 LEDC（PWM）API，对外只暴露 setSpeed(float)
     上层完全不需要知道 IN1/IN2/PWM/duty cycle 这些细节

核心方法：
  setSpeed(float speed)   speed: -1.0（反转）~ +1.0（正转）
  stop()                  自由停转（断电，自然减速）
  brake()                 主动刹车（两相接高电平，停得更快）

内部转换逻辑：
  speed > 0 → IN1=HIGH, IN2=LOW, PWM=speed×255（正转）
  speed < 0 → IN1=LOW,  IN2=HIGH, PWM=|speed|×255（反转）
  speed = 0 → IN1=LOW,  IN2=LOW,  PWM=0（停转）
```

#### `chassis_controller.h/.cpp` — 底盘语义层：管理四个电机

```
作用：知道什么是"前进""左转"，把语义命令翻译成四轮速度

核心逻辑：
  moveForward(speed)       → 左右轮都以 speed 正转
  moveBackward(speed)      → 左右轮都以 speed 反转
  turnLeft(speed)          → 左轮反转、右轮正转（原地左转）
  turnRight(speed)         → 左轮正转、右轮反转（原地右转）
  drive(linear, angular)   → 差速公式（摇杆模式）：
                              左轮 = linear - angular
                              右轮 = linear + angular

安全机制：
  emergencyStop()          → 四轮 brake() + 进入 EMERGENCY_STOP 状态
                              此后所有运动指令被拦截，必须显式解除
  update()                 → 每次 loop() 调用，检查心跳超时
  refreshHeartbeat()       → 每次收到指令，刷新最后活跃时间戳
```

#### `chassis.ino` — 固件主程序：网络 + 协议解析

```
作用：把 WiFi / WebSocket / JSON 和底盘控制器粘合起来

启动流程：
  setup() → chassis.begin() → connectWiFi() → wsServer.begin()

主循环：
  loop() → wsServer.loop()       // 轮询 WebSocket 新消息
         → chassis.update()      // 检查心跳超时
         → broadcastStatus()     // 每秒推送一次状态给所有客户端

指令处理（handleMessage）：
  收到 JSON → 解析 "skill" 字段 → 分发：
    "chassis.move"          → moveForward / moveBackward / turnLeft / turnRight
    "chassis.drive"         → drive(linear, angular)（摇杆）
    "chassis.stop"          → stop()
    "system.emergency_stop" → emergencyStop()
    "system.heartbeat"      → refreshHeartbeat()（保活）
    "system.get_status"     → 返回当前状态 JSON

状态上报格式（每秒广播）：
  {"type":"status","state":"moving_forward","linear":0.5,"emergency":false,...}
```

---

### 📁 drivers/（上位机底层通信）

#### `esp32_driver.py` — WebSocket 客户端驱动

```
作用：封装所有网络通信细节，上层只需调用 send_skill()

核心特性：

1. request_id 请求追踪
   每次 send_skill() 生成唯一 ID
   后台接收循环匹配响应，用 asyncio.Future 等待结果
   → 把 WebSocket 双向流包装成"请求-响应"模式

2. 自动重连（指数退避）
   断线后按 1s→2s→4s→8s→...→30s 间隔自动重试
   重连期间所有等待中的 Future 立即失败返回

3. 状态推送回调
   ESP32 主动广播的 status 消息走 on_status(callback) 回调
   与 send_skill() 的请求响应走不同的处理路径，互不干扰

4. 心跳辅助
   send_heartbeat() 定期向 ESP32 发 system.heartbeat
   由 ChassisService 在摇杆模式下自动调用，防止失联停车
```

---

### 📁 services/（业务服务层）

#### `chassis_service.py` — 底盘服务

```
作用：把"发 JSON 给 ESP32"封装成"底盘语义方法"
     上层调用 move_forward()，不需要知道任何 JSON 或网络细节

关键方法：
  move_forward(speed)                      → 前进
  move_backward(speed)                     → 后退
  turn_left(speed)                         → 左转
  turn_right(speed)                        → 右转
  move_timed(direction, speed, duration_ms)→ 限时运动后自动停止
  drive(linear, angular)                   → 摇杆差速模式
  stop()                                   → 停止
  emergency_stop()                         → 急停
  clear_emergency_stop()                   → 解除急停
  get_status()                             → 获取本地缓存状态
  fetch_status()                           → 主动拉取 ESP32 实时状态

特殊机制 - 摇杆心跳自动管理：
  调用 drive()  → 自动启动后台心跳 Task（每 500ms 发送一次）
  调用 stop()   → 自动停止心跳 Task
  → 保证摇杆模式下 ESP32 绝不触发失联停车
```

---

### 📁 core/（核心调度逻辑）

#### `skill_dispatcher.py` — 技能路由中心

```
作用：系统的"路由表"，所有技能调用都经过这里分发
     解耦了"谁发出命令"和"命令如何执行"

关键设计：

1. 服务注册（依赖注入）
   register_chassis_service(svc)  → 注入底盘服务
   register_arm_service(svc)      → 注入机械臂服务（预留）
   register_audio_service(svc)    → 注入音频服务（预留）
   → 新增模块只需注册，不修改调度器代码

2. 急停最高优先级
   system.emergency_stop 不走普通路由
   直接设置全局 _emergency_stopped = True
   所有后续 dispatch() 先检查此标志，急停状态下全部拦截

3. 统一响应格式
   无论路由到哪个服务，返回格式始终是：
   {"request_id":..., "success":..., "skill":..., "data":...}

初始化后的路由表：
  "chassis.move"       → chassis_service.move_forward/backward/...
  "chassis.drive"      → chassis_service.drive()
  "chassis.stop"       → chassis_service.stop()
  "chassis.set_speed"  → （预留）
  "system.get_status"  → chassis_service.fetch_status()
  "arm.*"              → arm_service（预留）
```

#### `command_parser.py` — 语音文本解析器

```
作用：将 ASR 识别的自然语言文本映射到标准技能调用格式
     MVP 阶段使用规则词表，后续可替换为 LLM function_call

两种解析模式（优先级从高到低）：

1. 带时长模式（正则）
   "前进两秒" / "后退3秒" / "左转1.5s"
   → 正则提取时长数字 + 中文数字转换（"两"→2）
   → 生成带 duration_ms 的 chassis.move 调用

2. 关键词匹配
   精确匹配 > 包含匹配
   "前进"  → chassis.move {direction: forward}
   "停止"  → chassis.stop
   "急停"  → system.emergency_stop
   "挥手"  → arm.execute_preset {action: wave}（机械臂预留）

词表配置在 configs/skills.json，调用 parser.reload() 热更新
```

---

### 📁 app_api/（对外接口层）

#### `websocket_server.py` — App 端 WebSocket 服务器

```
作用：接受 App（手机/浏览器）连接，是系统对外的唯一入口

消息类型处理：
  "skill" 字段存在  → 直接 dispatch 到 SkillDispatcher
  type="asr_result" → 先经 CommandParser 解析，再 dispatch
  type="get_status" → 查询底盘状态返回
  type="ping"       → 返回 pong（保活）

安全保障：
  App 断开时（finally 块）立即触发 chassis.stop
  支持多 App 同时连接（Set<WebSocket>）
  broadcast() 向所有连接的 App 推送状态
```

---

### 📁 根目录文件

#### `main.py` — 上位机主程序入口

```
启动顺序：
  load_config → setup_logging → ESP32Driver → ChassisService
  → SkillDispatcher → CommandParser → AppWebSocketServer → 主循环

命令行参数：
  --mock        使用 MockESP32Driver（无需真实硬件，适合本地开发）
  --esp32 URL   覆盖配置文件中的 ESP32 地址
  --port 8765   覆盖 App 服务端口
  --log DEBUG   调整日志级别

主循环：每秒把底盘状态广播给所有 App 客户端
```

#### `configs/device.yaml` — 设备参数配置

```yaml
esp32:
  host: "192.168.1.100"   # ESP32 IP 地址
  ws_port: 81

app_server:
  ws_port: 8765           # App 连接端口

chassis:
  default_speed: 0.5      # 默认速度
```

#### `configs/skills.json` — 语音词表配置

```
JSON 格式的关键词 → 技能 映射表
修改此文件无需重启（parser.reload() 热更新）
```

---

## 四、一次完整的指令执行流程

以用户说 **"前进两秒"** 为例，全链路追踪：

```
Step 1  App 发送 ASR 结果
        {"type": "asr_result", "text": "前进两秒", "confidence": 0.92}
        ↓ WebSocket 8765

Step 2  AppWebSocketServer 收到
        识别 type=asr_result → 调用 CommandParser.parse("前进两秒")

Step 3  CommandParser 正则匹配
        匹配 TIMED_MOVE_PATTERNS 中的"前进"模式
        提取时长："两" → 2 → 2000ms
        返回 skill_call:
        {"skill":"chassis.move","params":{"direction":"forward","speed":0.5,"duration":2000}}

Step 4  SkillDispatcher.dispatch(skill_call)
        检查 emergency_stopped = False（允许执行）
        查路由表 → 找到 chassis_move handler
        调用 chassis_service.move_timed("forward", 0.5, 2000)

Step 5  ChassisService.move_timed()
        调用 esp32_driver.send_skill("chassis.move", {direction, speed, duration})

Step 6  ESP32Driver
        生成 request_id = "a3f2c1"
        发送 JSON 到 ESP32 WebSocket
        注册 Future 等待响应（超时 3 秒）

Step 7  ESP32 固件 chassis.ino 收到
        handleMessage 解析 skill = "chassis.move", duration = 2000
        chassis.moveForward(0.5) → 四个电机开始转动
        delay(2000ms)
        chassis.stop()           → 电机停止
        返回响应: {"request_id":"a3f2c1","success":true,"data":{"state":"stopped"}}

Step 8  ESP32Driver 收到响应
        匹配 request_id，触发 Future.set_result()
        send_skill() 返回响应字典

Step 9  AppWebSocketServer 收到结果
        向 App 发送：{"success":true,"skill":"chassis.move","raw_text":"前进两秒"}

Step 10 App 显示执行成功 ✅
```

---

## 五、数据流总结

```
方向 1：App → 机器人（指令下发）
  App JSON
    → AppWebSocketServer
    → SkillDispatcher
    → ChassisService
    → ESP32Driver
    → ESP32 WebSocket Server
    → ChassisController
    → MotorDriver × 4
    → 电机转动

方向 2：机器人 → App（状态上报）
  ESP32 每秒广播 status JSON
    → ESP32Driver 回调
    → ChassisService 本地缓存
    → main.py 主循环 broadcast
    → 所有已连接的 App

方向 3：语音路径
  ASR 文本
    → AppWebSocketServer
    → CommandParser（文本→技能调用）
    → SkillDispatcher
    → （同方向 1）
```

---

## 六、快速开始

### 第一步：准备硬件

**所需硬件：**
- ESP32 开发板（任意型号）
- L298N 电机驱动模块 × 2（或 TB6612FNG）
- 直流减速电机 × 4
- 电源：电机用 7~12V，ESP32 用 5V

**默认接线（可在 `config.h` 修改）：**

```
左前电机：IN1=GPIO25, IN2=GPIO26, ENA=GPIO32
右前电机：IN3=GPIO27, IN4=GPIO14, ENB=GPIO33
左后电机：IN1=GPIO12, IN2=GPIO13, ENA=GPIO4
右后电机：IN3=GPIO16, IN4=GPIO17, ENB=GPIO5
```

### 第二步：烧录 ESP32 固件

1. 安装 [Arduino IDE](https://www.arduino.cc/en/software)
2. Board Manager 搜索并安装 `esp32`
3. Library Manager 安装：
   - `WebSockets` by Markus Sattler
   - `ArduinoJson` by Benoit Blanchon
4. 修改 `firmware/chassis/config.h`：
   ```cpp
   #define WIFI_SSID     "你的WiFi名"
   #define WIFI_PASSWORD "你的WiFi密码"
   ```
5. 打开 `firmware/chassis/chassis.ino`，选择 COM 口，上传
6. 打开串口监视器（115200），记录显示的 IP 地址

### 第三步：配置上位机

修改 `configs/device.yaml`：
```yaml
esp32:
  host: "192.168.1.100"   # 改成 ESP32 实际 IP
```

### 第四步：运行上位机

```bash
# 创建虚拟环境（推荐）
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 正式运行（需要真实 ESP32）
python main.py

# 开发/调试模式（无需硬件）
python main.py --mock

# 指定 ESP32 地址
python main.py --esp32 ws://192.168.1.100:81

# Debug 日志
python main.py --mock --log DEBUG
```

### 第五步：运行测试

```bash
python tests/test_chassis.py
```

---

## 七、通信协议参考

### App → 上位机（WebSocket 端口 8765）

**底盘控制：**
```json
{
  "skill": "chassis.move",
  "params": { "direction": "forward", "speed": 0.5 }
}
```

**限时运动：**
```json
{
  "skill": "chassis.move",
  "params": { "direction": "forward", "speed": 0.5, "duration": 2000 }
}
```

**摇杆差速驱动（持续发送）：**
```json
{
  "skill": "chassis.drive",
  "params": { "linear": 0.6, "angular": -0.3 }
}
```

**急停：**
```json
{ "skill": "system.emergency_stop", "params": {} }
```

**语音命令（ASR 结果）：**
```json
{ "type": "asr_result", "text": "前进两秒", "confidence": 0.95 }
```

### 支持的技能清单

| 技能名 | 参数 | 说明 |
|--------|------|------|
| `chassis.move` | `direction`, `speed`, `duration`(可选ms) | 按方向运动 |
| `chassis.drive` | `linear`(-1~1), `angular`(-1~1) | 差速摇杆模式 |
| `chassis.stop` | 无 | 停止 |
| `system.emergency_stop` | 无 | 急停（需显式解除） |
| `system.clear_emergency_stop` | 无 | 解除急停 |
| `system.get_status` | 无 | 查询底盘状态 |

### 语音指令词表（默认）

| 关键词 | 映射技能 |
|--------|----------|
| 前进、向前、往前走 | `chassis.move` forward |
| 后退、向后、倒退 | `chassis.move` backward |
| 左转、向左 | `chassis.move` left |
| 右转、向右 | `chassis.move` right |
| 停止、停下、站住 | `chassis.stop` |
| 急停、紧急停止 | `system.emergency_stop` |
| 挥手、打招呼 | `arm.execute_preset` wave（预留） |
| 前进X秒 | `chassis.move` forward duration=X×1000 |

> 在 `configs/skills.json` 中可自由添加/修改词条

---

## 八、安全机制

| 机制 | 实现位置 | 触发条件 | 行为 |
|------|----------|----------|------|
| **急停** | ESP32 固件 + SkillDispatcher | 收到 `system.emergency_stop` | 主动刹车，拦截所有后续运动指令 |
| **失联停车** | ESP32 固件（1.5秒超时） | WebSocket 断开或心跳超时 | 自动 stop() |
| **App断开停车** | AppWebSocketServer | App WebSocket 断连 | 立即触发 chassis.stop |
| **速度限制** | ESP32 固件 config.h | 所有运动指令 | 最大速度 80% |
| **摇杆保活** | ChassisService | drive() 调用时 | 每 500ms 自动发心跳 |

---

## 九、开发进度

- [x] Phase 0：方案设计 + 项目结构
- [x] Phase 1：底盘控制（当前）
  - [x] ESP32 电机驱动（LEDC PWM）
  - [x] 四轮差速转向
  - [x] 急停 + 失联停车
  - [x] WebSocket JSON 指令接口
  - [x] Python 上位机服务层
  - [x] 技能调度器（统一路由）
  - [x] 语音命令解析（规则词表）
  - [x] 单元测试（17个，全部通过）
  - [x] **上位机 ↔ ESP32 全链路 WebSocket 联调通过（2026-03-31）**
- [ ] Phase 2：音视频链路（WebRTC）
- [ ] Phase 3：语音识别接入（ASR）
- [ ] Phase 4：机械臂模块（舵机）
- [ ] Phase 5：联调与稳定性优化

---

## 十、踩坑记录（避免重复踩）

> 按时间倒序，每次遇到重要问题都记录在这里。

---

### 🐛 2026-03-31 · 首次联调踩坑

#### 1. ESP32 只支持 2.4GHz WiFi，无法连接 5GHz

**现象**：`config.h` 填了 5GHz WiFi SSID，ESP32 串口不断打印 `.` 然后输出 `Connection failed`。  
**原因**：ESP32 芯片（ESP32-D0WD-V3）的无线模块硬件只支持 802.11 b/g/n（2.4GHz），**无任何方式**连接 5GHz 网络。  
**解决**：
- 查看路由器的 2.4GHz 频段 SSID（通常没有 `_5G` 后缀）
- 或用手机开 **2.4GHz 热点**，ESP32 连热点

---

#### 2. arduino-cli 上传必须用 115200 波特率

**现象**：用默认 921600 波特率上传，ESP32 串口出现 `chip stopped responding`，之后固件无法启动（串口显示 `Invalid header: 0xffffffff`，flash 被写坏）。  
**原因**：本机使用的 USB 转串口芯片在高速率下不稳定。  
**解决**：上传时强制指定低速率：
```bash
arduino-cli upload \
  --fqbn "esp32:esp32:esp32:UploadSpeed=115200" \
  -p /dev/cu.usbserial-10 \
  firmware/chassis
```
> ⚠️ **flash 写坏修复**：只需再次以 115200 正确上传即可覆盖恢复，无需额外操作。

---

#### 3. ESP32 Arduino 3.x LEDC API 变更

**现象**：编译报错 `'ledcSetup' was not declared`、`'ledcAttachPin' was not declared`。  
**原因**：ESP32 Arduino 3.x 废弃了旧 API：
```cpp
// ❌ 旧 API（2.x）
ledcSetup(channel, freq, resolution);
ledcAttachPin(pin, channel);
ledcWrite(channel, duty);

// ✅ 新 API（3.x）
ledcAttach(pin, freq, resolution);
ledcWrite(pin, duty);   // 第一个参数从 channel 改为 pin
```
**解决**：`motor_driver.cpp` 全部改为新 API，`_pwmChannel` 字段可以删掉。

---

#### 4. ArduinoJson 7.x API 变更

**现象**：编译报错 `'StaticJsonDocument' is deprecated`。  
**原因**：ArduinoJson 7.x 合并了所有 Document 类型。  
```cpp
// ❌ 旧写法（6.x）
StaticJsonDocument<256> doc;
JsonObject data = doc.createNestedObject("data");

// ✅ 新写法（7.x）
JsonDocument doc;           // 大小自动管理，不需要指定
doc["data"]["state"] = ...; // 直接用下标操作
```

---

#### 5. Mac 和 ESP32 必须在同一 WiFi 网段

**现象**：`ping 192.168.43.36` 通，但 `nc -zv 192.168.43.36 81` 超时，上位机连不上 ESP32。  
**原因**：Mac 有两个网络接口同时在线：
- `en0`（WiFi）→ 公司网络 `10.122.x.x`（默认路由）
- ESP32 在手机热点 `192.168.43.x`

路由表里没有 `192.168.43.x` 的路由，TCP 包走了公司网关，ICMP（ping）因为 OS 路由策略差异偶尔能通。  
**解决**：将 Mac WiFi 切换到与 ESP32 相同的热点网络，两者同在 `192.168.43.x` 网段后立即通。

> 💡 **日常开发建议**：使用手机热点（设置为 2.4GHz），Mac 和 ESP32 都连热点，既不占用公司网络，也不需要路由器。

---

#### 6. 磁盘空间不足导致 arduino-cli 无法安装 ESP32 平台

**现象**：`arduino-cli core install esp32:esp32` 报磁盘空间不足。  
**原因**：ESP32 平台包含多个芯片的工具链，总大小超过 3GB：
- `esp-rv32`（RISC-V 工具链）：约 2GB
- ESP32-C5/C6/H2 工具链：约 711MB  
**解决**：删除不需要的芯片工具目录（保留 ESP32-S0/S3/Classic），释放空间后重新安装。

---

### ✅ 首次成功联调日志（2026-03-31 20:58）

```
[ESP32Driver] Connected to ws://192.168.43.36:81   ✅
[ChassisService] Started, connected to ESP32        ✅
[Dispatcher] ChassisService registered              ✅
[CommandParser] Loaded 12 commands                  ✅
[AppWS] Server started on ws://0.0.0.0:8765         ✅
[Init] ✅ All modules ready!                        ✅
```

**当前硬件信息（供参考）：**
- ESP32 型号：ESP32-D0WD-V3（revision v3.1），MAC: `f4:2d:c9:9f:47:64`
- 串口：`/dev/cu.usbserial-10`
- arduino-cli 路径：`/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli`
- ESP32 IP（手机热点）：`192.168.43.36`（DHCP，重启后可能变化）
