/**
 * chassis_controller.h
 * ESP32 底盘控制固件 - 底盘控制器头文件
 *
 * 职责：
 *   管理四个 MotorDriver 实例，实现"底盘语义"：
 *     - 前进 / 后退 / 左转 / 右转 / 停止 / 急停
 *     - 差速转向计算
 *     - 失联超时停车
 *     - 速度限制
 *
 * 这一层不了解网络协议，只接受语义化命令
 */

#pragma once
#include <Arduino.h>
#include "motor_driver.h"
#include "config.h"

// 底盘运动状态枚举
enum class ChassisState {
    STOPPED,          // 停止
    MOVING_FORWARD,   // 前进
    MOVING_BACKWARD,  // 后退
    TURNING_LEFT,     // 左转
    TURNING_RIGHT,    // 右转
    EMERGENCY_STOP,   // 急停（需要显式解除才能重新运动）
};

// 原始速度向量（来自摇杆或指令解析）
struct SpeedVector {
    float linear;   // 线速度  -1.0(后退) ~ +1.0(前进)
    float angular;  // 角速度  -1.0(左转) ~ +1.0(右转)
};

class ChassisController {
public:
    ChassisController();

    /**
     * 初始化四个电机驱动，必须在 setup() 中调用
     */
    void begin();

    // ==================== 语义化运动指令 ====================

    /** 以指定速度前进（speed: 0~1.0） */
    void moveForward(float speed = 0.5f);

    /** 以指定速度后退（speed: 0~1.0） */
    void moveBackward(float speed = 0.5f);

    /** 原地左转（speed: 0~1.0） */
    void turnLeft(float speed = 0.5f);

    /** 原地右转（speed: 0~1.0） */
    void turnRight(float speed = 0.5f);

    /** 平滑停止（自由停转） */
    void stop();

    /** 急停（主动刹车 + 进入 EMERGENCY_STOP 状态，禁止后续运动） */
    void emergencyStop();

    /** 解除急停状态（急停后必须调用此方法才能重新运动） */
    void clearEmergencyStop();

    /**
     * 差速驱动（摇杆模式）
     * @param linear   线速度 -1.0~+1.0（后退~前进）
     * @param angular  角速度 -1.0~+1.0（左转~右转）
     *
     * 混合计算公式（差速转向 Skid Steering）：
     *   左轮速 = linear - angular
     *   右轮速 = linear + angular
     * 适合四轮独立驱动的坦克式底盘
     */
    void drive(float linear, float angular);

    // ==================== 心跳 / 失联停车 ====================

    /**
     * 刷新心跳时间戳，上位机每次发指令时调用
     * 超过 HEARTBEAT_TIMEOUT_MS 未刷新则自动停车
     */
    void refreshHeartbeat();

    /**
     * 主循环更新，必须在 loop() 中调用
     * 检查心跳超时并执行停车
     */
    void update();

    // ==================== 状态查询 ====================

    ChassisState getState() const { return _state; }
    const char*  getStateName() const;
    bool         isEmergencyStopped() const { return _state == ChassisState::EMERGENCY_STOP; }
    bool         isHeartbeatTimeout() const;
    float        getLinearSpeed() const  { return _lastVector.linear; }
    float        getAngularSpeed() const { return _lastVector.angular; }

private:
    MotorDriver* _motorFL;  // 左前
    MotorDriver* _motorFR;  // 右前
    MotorDriver* _motorRL;  // 左后
    MotorDriver* _motorRR;  // 右后

    ChassisState _state;
    SpeedVector  _lastVector;

    unsigned long _lastHeartbeatMs;
    bool          _heartbeatEnabled;  // 首次收到指令后才启用超时检测

    float        _maxSpeed;  // 由 MAX_SPEED_PERCENT 换算

    /**
     * 设置四轮速度的底层方法（带速度上限）
     * @param leftSpeed  左侧轮速 -1.0~+1.0
     * @param rightSpeed 右侧轮速 -1.0~+1.0
     */
    void _setWheelSpeeds(float leftSpeed, float rightSpeed);

    /** 将速度值限制在最大速度范围内 */
    float _clampSpeed(float speed) const;
};
