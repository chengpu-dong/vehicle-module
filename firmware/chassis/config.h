/**
 * config.h
 * ESP32 底盘控制固件 - 配置文件
 *
 * 说明：
 *   修改此文件以适配你的硬件接线和网络环境
 *   注意：敏感信息（WiFi密码等）建议不要提交到 git
 */

#pragma once

// ==================== WiFi 配置 ====================
// 方案A：Mac 热点（推荐，Mac 不断网，信道选 1/6/11 = 2.4GHz）
// #define WIFI_SSID     "robot-dev"
// #define WIFI_PASSWORD "robot1234"

// 方案B：手机热点（ESP32 和 Mac 都连手机热点）
#define WIFI_SSID     "12345678"
#define WIFI_PASSWORD "qwerasdf"

// ==================== 服务端口 ====================
// ESP32 WebSocket 服务器端口（上位机连接此端口下发指令）
#define WS_PORT       81
// ESP32 HTTP 服务器端口（状态查询、调试用）
#define HTTP_PORT     80

// ==================== 安全配置 ====================
// 失联停车超时（毫秒）：超过此时间未收到指令则自动停车
#define HEARTBEAT_TIMEOUT_MS  1500
// 最大速度百分比限制 (0~100)，防止全速失控
#define MAX_SPEED_PERCENT     80

// ==================== 电机引脚配置（L298N/L293D）====================
//
// 典型接线（TB6612FNG 同理，调整引脚即可）：
//
//   左前电机 (Motor A)
//     IN1 -> GPIO 25    方向控制 A
//     IN2 -> GPIO 26    方向控制 B
//     ENA -> GPIO 32    PWM 速度控制 (需接到 ENA)
//
//   右前电机 (Motor B)
//     IN3 -> GPIO 27    方向控制 A
//     IN4 -> GPIO 14    方向控制 B
//     ENB -> GPIO 33    PWM 速度控制 (需接到 ENB)
//
//   左后电机 (Motor C)
//     IN1 -> GPIO 12    方向控制 A
//     IN2 -> GPIO 13    方向控制 B
//     ENA -> GPIO 4     PWM 速度控制
//
//   右后电机 (Motor D)
//     IN3 -> GPIO 16    方向控制 A
//     IN4 -> GPIO 17    方向控制 B
//     ENB -> GPIO 5     PWM 速度控制
//
// ⚠️ 提示：GPIO 34/35/36/39 仅为输入，不可用于输出

// 左前电机
#define MOTOR_FL_IN1  25
#define MOTOR_FL_IN2  26
#define MOTOR_FL_PWM  32

// 右前电机
#define MOTOR_FR_IN1  27
#define MOTOR_FR_IN2  14
#define MOTOR_FR_PWM  33

// 左后电机
#define MOTOR_RL_IN1  12
#define MOTOR_RL_IN2  13
#define MOTOR_RL_PWM  4

// 右后电机
#define MOTOR_RR_IN1  16
#define MOTOR_RR_IN2  17
#define MOTOR_RR_PWM  5

// ==================== PWM 配置 ====================
// ESP32 LEDC（PWM）通道分配（0~15，每个电机占一个通道）
#define PWM_CH_FL     0   // 左前
#define PWM_CH_FR     1   // 右前
#define PWM_CH_RL     2   // 左后
#define PWM_CH_RR     3   // 右后

#define PWM_FREQ      5000   // PWM频率 Hz（电机驱动一般 1kHz~20kHz）
#define PWM_RESOLUTION 8     // PWM分辨率 bit（8位 = 0~255）

// ==================== 差速转向配置 ====================
// 转向时外侧轮速度系数（1.0 = 全速，0.5 = 半速）
// 增大此值转向更急，减小更缓
#define TURN_OUTER_RATIO  1.0f
// 转向时内侧轮速度系数（负值=反转，0=停转，正值=同向慢速）
#define TURN_INNER_RATIO  -0.5f

// ==================== 急停引脚（可选）====================
// 如果你有硬件急停按钮，接到此引脚（低电平触发）
// 设为 -1 则不启用硬件急停引脚
#define EMERGENCY_STOP_PIN  -1

// ==================== 调试配置 ====================
#define DEBUG_SERIAL        true    // 是否启用串口调试输出
//Ariduino Ide中需要设施相同的波特率
#define SERIAL_BAUD         115200 
