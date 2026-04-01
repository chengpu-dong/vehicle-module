/**
 * motor_driver.h
 * ESP32 底盘控制固件 - 电机驱动层头文件
 *
 * 职责：
 *   直接操作 GPIO 和 PWM（LEDC），控制单个电机的方向和速度
 *   是最底层的硬件抽象，不了解"底盘概念"，只了解"电机"
 *
 * 使用方式：
 *   MotorDriver motor(IN1, IN2, PWM_PIN, PWM_CHANNEL);
 *   motor.begin();
 *   motor.setSpeed(0.8f);     // 正方向 80% 速度
 *   motor.setSpeed(-0.5f);    // 反方向 50% 速度
 *   motor.stop();
 *   motor.brake();            // 主动刹车（两线接高电平）
 */

#pragma once
#include <Arduino.h>

class MotorDriver {
public:
    /**
     * 构造函数
     * @param in1Pin       方向控制引脚 IN1
     * @param in2Pin       方向控制引脚 IN2
     * @param pwmPin       PWM 速度控制引脚（接 ENA/ENB）
     * @param pwmChannel   ESP32 LEDC 通道编号（0~15，每个电机用不同通道）
     * @param pwmFreq      PWM 频率 Hz
     * @param pwmResolution PWM 分辨率 bit（8 = 0~255）
     * @param inverted     是否反转方向（电机接反时用 true 纠正）
     */
    MotorDriver(uint8_t in1Pin, uint8_t in2Pin, uint8_t pwmPin,
                uint8_t pwmChannel, uint32_t pwmFreq = 5000,
                uint8_t pwmResolution = 8, bool inverted = false);

    /**
     * 初始化引脚和 PWM 通道，必须在 setup() 中调用
     */
    void begin();

    /**
     * 设置电机速度
     * @param speed  范围 -1.0 ~ +1.0
     *               正值 = 正转（通常为前进方向）
     *               负值 = 反转
     *               0    = 自由停转（非主动刹车）
     */
    void setSpeed(float speed);

    /**
     * 自由停转（电机断电，自然减速）
     */
    void stop();

    /**
     * 主动刹车（两相同时接高/低，产生制动力矩，停止更快）
     */
    void brake();

    /**
     * 获取当前设置的速度值
     */
    float getSpeed() const { return _currentSpeed; }

private:
    uint8_t  _in1Pin;
    uint8_t  _in2Pin;
    uint8_t  _pwmPin;
    uint8_t  _pwmChannel;
    uint32_t _pwmFreq;
    uint8_t  _pwmResolution;
    bool     _inverted;
    float    _currentSpeed;

    int      _maxDuty;  // 根据分辨率计算，8bit = 255

    /**
     * 直接写方向和 PWM 占空比
     */
    void _drive(bool forward, uint8_t duty);
};
