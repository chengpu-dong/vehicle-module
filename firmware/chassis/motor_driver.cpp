/**
 * motor_driver.cpp
 * ESP32 底盘控制固件 - 电机驱动层实现
 */

#include "motor_driver.h"

MotorDriver::MotorDriver(uint8_t in1Pin, uint8_t in2Pin, uint8_t pwmPin,
                         uint8_t pwmChannel, uint32_t pwmFreq,
                         uint8_t pwmResolution, bool inverted)
    : _in1Pin(in1Pin), _in2Pin(in2Pin), _pwmPin(pwmPin),
      _pwmChannel(pwmChannel), _pwmFreq(pwmFreq),
      _pwmResolution(pwmResolution), _inverted(inverted),
      _currentSpeed(0.0f) {
    // 计算最大占空比值：2^resolution - 1
    _maxDuty = (1 << pwmResolution) - 1;  // 8bit = 255
}

void MotorDriver::begin() {
    // 配置方向控制引脚为输出
    pinMode(_in1Pin, OUTPUT);
    pinMode(_in2Pin, OUTPUT);

    // 初始化 ESP32 LEDC PWM（ESP32 Arduino 3.x 新 API）
    // ledcAttach(pin, freq, resolution) 自动分配通道
    ledcAttach(_pwmPin, _pwmFreq, _pwmResolution);

    // 确保电机初始状态为停止
    stop();
}

void MotorDriver::setSpeed(float speed) {
    // 限制输入范围在 -1.0 ~ +1.0
    if (speed > 1.0f)  speed = 1.0f;
    if (speed < -1.0f) speed = -1.0f;

    _currentSpeed = speed;

    if (speed == 0.0f) {
        stop();
        return;
    }

    // 处理方向反转标志
    bool forward = (speed > 0);
    if (_inverted) forward = !forward;

    // 将 0~1.0 映射到 0~maxDuty（8bit: 0~255）
    uint8_t duty = (uint8_t)(abs(speed) * _maxDuty);

    _drive(forward, duty);
}

void MotorDriver::stop() {
    // 自由停转：两引脚都拉低，关闭 PWM
    digitalWrite(_in1Pin, LOW);
    digitalWrite(_in2Pin, LOW);
    ledcWrite(_pwmPin, 0);      // 3.x: 用 pin 代替 channel
    _currentSpeed = 0.0f;
}

void MotorDriver::brake() {
    // 主动刹车：两引脚都拉高（L298N 支持此模式）
    // 会产生制动力矩，停止比 stop() 更快
    digitalWrite(_in1Pin, HIGH);
    digitalWrite(_in2Pin, HIGH);
    ledcWrite(_pwmPin, _maxDuty);  // 3.x: 用 pin 代替 channel
    _currentSpeed = 0.0f;
}

void MotorDriver::_drive(bool forward, uint8_t duty) {
    if (forward) {
        // 正转：IN1=HIGH, IN2=LOW
        digitalWrite(_in1Pin, HIGH);
        digitalWrite(_in2Pin, LOW);
    } else {
        // 反转：IN1=LOW, IN2=HIGH
        digitalWrite(_in1Pin, LOW);
        digitalWrite(_in2Pin, HIGH);
    }
    // 通过 PWM 设置速度（3.x: 用 pin 代替 channel）
    ledcWrite(_pwmPin, duty);
}
