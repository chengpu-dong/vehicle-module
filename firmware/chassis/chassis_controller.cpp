/**
 * chassis_controller.cpp
 * ESP32 底盘控制固件 - 底盘控制器实现
 */

#include "chassis_controller.h"

ChassisController::ChassisController()
    : _state(ChassisState::STOPPED),
      _lastHeartbeatMs(0),
      _heartbeatEnabled(false) {

    _lastVector = {0.0f, 0.0f};
    _maxSpeed = MAX_SPEED_PERCENT / 100.0f;

    // 创建四个电机驱动实例
    // 参数：IN1, IN2, PWM引脚, PWM通道, PWM频率, PWM分辨率, 是否反转
    _motorFL = new MotorDriver(MOTOR_FL_IN1, MOTOR_FL_IN2, MOTOR_FL_PWM,
                                PWM_CH_FL, PWM_FREQ, PWM_RESOLUTION, false);
    _motorFR = new MotorDriver(MOTOR_FR_IN1, MOTOR_FR_IN2, MOTOR_FR_PWM,
                                PWM_CH_FR, PWM_FREQ, PWM_RESOLUTION, false);
    _motorRL = new MotorDriver(MOTOR_RL_IN1, MOTOR_RL_IN2, MOTOR_RL_PWM,
                                PWM_CH_RL, PWM_FREQ, PWM_RESOLUTION, false);
    _motorRR = new MotorDriver(MOTOR_RR_IN1, MOTOR_RR_IN2, MOTOR_RR_PWM,
                                PWM_CH_RR, PWM_FREQ, PWM_RESOLUTION, false);
}

void ChassisController::begin() {
    _motorFL->begin();
    _motorFR->begin();
    _motorRL->begin();
    _motorRR->begin();

    // 初始化硬件急停引脚（如果配置了）
#if EMERGENCY_STOP_PIN >= 0
    pinMode(EMERGENCY_STOP_PIN, INPUT_PULLUP);
#endif

    if (DEBUG_SERIAL) {
        Serial.println("[Chassis] Initialized. Max speed: " + String(_maxSpeed));
    }
}

// ==================== 语义化运动指令 ====================

void ChassisController::moveForward(float speed) {
    if (_state == ChassisState::EMERGENCY_STOP) {
        if (DEBUG_SERIAL) Serial.println("[Chassis] BLOCKED: Emergency stop active");
        return;
    }
    refreshHeartbeat();
    _lastVector = {speed, 0.0f};
    _setWheelSpeeds(speed, speed);
    _state = ChassisState::MOVING_FORWARD;
    if (DEBUG_SERIAL) Serial.println("[Chassis] Move forward, speed=" + String(speed));
}

void ChassisController::moveBackward(float speed) {
    if (_state == ChassisState::EMERGENCY_STOP) {
        if (DEBUG_SERIAL) Serial.println("[Chassis] BLOCKED: Emergency stop active");
        return;
    }
    refreshHeartbeat();
    _lastVector = {-speed, 0.0f};
    _setWheelSpeeds(-speed, -speed);
    _state = ChassisState::MOVING_BACKWARD;
    if (DEBUG_SERIAL) Serial.println("[Chassis] Move backward, speed=" + String(speed));
}

void ChassisController::turnLeft(float speed) {
    if (_state == ChassisState::EMERGENCY_STOP) return;
    refreshHeartbeat();
    _lastVector = {0.0f, -speed};
    // 原地左转：左轮反转，右轮正转
    _setWheelSpeeds(-speed, speed);
    _state = ChassisState::TURNING_LEFT;
    if (DEBUG_SERIAL) Serial.println("[Chassis] Turn left, speed=" + String(speed));
}

void ChassisController::turnRight(float speed) {
    if (_state == ChassisState::EMERGENCY_STOP) return;
    refreshHeartbeat();
    _lastVector = {0.0f, speed};
    // 原地右转：左轮正转，右轮反转
    _setWheelSpeeds(speed, -speed);
    _state = ChassisState::TURNING_RIGHT;
    if (DEBUG_SERIAL) Serial.println("[Chassis] Turn right, speed=" + String(speed));
}

void ChassisController::stop() {
    _motorFL->stop();
    _motorFR->stop();
    _motorRL->stop();
    _motorRR->stop();
    _lastVector = {0.0f, 0.0f};
    // 注意：stop() 不改变 EMERGENCY_STOP 状态
    if (_state != ChassisState::EMERGENCY_STOP) {
        _state = ChassisState::STOPPED;
    }
    if (DEBUG_SERIAL) Serial.println("[Chassis] Stopped");
}

void ChassisController::emergencyStop() {
    // 急停：主动刹车（比 stop() 更快停下）
    _motorFL->brake();
    _motorFR->brake();
    _motorRL->brake();
    _motorRR->brake();
    _lastVector = {0.0f, 0.0f};
    _state = ChassisState::EMERGENCY_STOP;
    _heartbeatEnabled = false;  // 急停后禁用心跳超时（等待显式解除）
    if (DEBUG_SERIAL) Serial.println("[Chassis] ⚠️  EMERGENCY STOP!");
}

void ChassisController::clearEmergencyStop() {
    if (_state == ChassisState::EMERGENCY_STOP) {
        _state = ChassisState::STOPPED;
        _heartbeatEnabled = false;
        if (DEBUG_SERIAL) Serial.println("[Chassis] Emergency stop cleared");
    }
}

void ChassisController::drive(float linear, float angular) {
    if (_state == ChassisState::EMERGENCY_STOP) return;
    refreshHeartbeat();

    _lastVector = {linear, angular};

    // 差速转向计算（Skid Steering）
    // 左轮 = linear - angular
    // 右轮 = linear + angular
    float leftSpeed  = linear - angular;
    float rightSpeed = linear + angular;

    // 如果任一侧超出范围，等比例缩小（保持转向意图）
    float maxVal = max(abs(leftSpeed), abs(rightSpeed));
    if (maxVal > 1.0f) {
        leftSpeed  /= maxVal;
        rightSpeed /= maxVal;
    }

    _setWheelSpeeds(leftSpeed, rightSpeed);

    // 更新状态
    if (linear > 0.05f)       _state = ChassisState::MOVING_FORWARD;
    else if (linear < -0.05f) _state = ChassisState::MOVING_BACKWARD;
    else if (angular > 0.05f) _state = ChassisState::TURNING_RIGHT;
    else if (angular < -0.05f) _state = ChassisState::TURNING_LEFT;
    else                      _state = ChassisState::STOPPED;
}

// ==================== 心跳 / 失联停车 ====================

void ChassisController::refreshHeartbeat() {
    _lastHeartbeatMs = millis();
    _heartbeatEnabled = true;
}

void ChassisController::update() {
    // 检查硬件急停引脚（如果配置了）
#if EMERGENCY_STOP_PIN >= 0
    if (digitalRead(EMERGENCY_STOP_PIN) == LOW) {
        if (_state != ChassisState::EMERGENCY_STOP) {
            emergencyStop();
        }
        return;
    }
#endif

    // 检查心跳超时
    if (_heartbeatEnabled && _state != ChassisState::EMERGENCY_STOP) {
        if (isHeartbeatTimeout()) {
            if (DEBUG_SERIAL) {
                Serial.println("[Chassis] ⚠️  Heartbeat timeout! Auto stop.");
            }
            stop();
            _heartbeatEnabled = false;  // 停止后不再重复触发
        }
    }
}

bool ChassisController::isHeartbeatTimeout() const {
    if (!_heartbeatEnabled) return false;
    return (millis() - _lastHeartbeatMs) > HEARTBEAT_TIMEOUT_MS;
}

// ==================== 状态查询 ====================

const char* ChassisController::getStateName() const {
    switch (_state) {
        case ChassisState::STOPPED:          return "stopped";
        case ChassisState::MOVING_FORWARD:   return "moving_forward";
        case ChassisState::MOVING_BACKWARD:  return "moving_backward";
        case ChassisState::TURNING_LEFT:     return "turning_left";
        case ChassisState::TURNING_RIGHT:    return "turning_right";
        case ChassisState::EMERGENCY_STOP:   return "emergency_stop";
        default:                             return "unknown";
    }
}

// ==================== 私有方法 ====================

void ChassisController::_setWheelSpeeds(float leftSpeed, float rightSpeed) {
    // 应用全局速度上限
    leftSpeed  = _clampSpeed(leftSpeed);
    rightSpeed = _clampSpeed(rightSpeed);

    _motorFL->setSpeed(leftSpeed);
    _motorRL->setSpeed(leftSpeed);
    _motorFR->setSpeed(rightSpeed);
    _motorRR->setSpeed(rightSpeed);
}

float ChassisController::_clampSpeed(float speed) const {
    if (speed > _maxSpeed)  return _maxSpeed;
    if (speed < -_maxSpeed) return -_maxSpeed;
    return speed;
}
