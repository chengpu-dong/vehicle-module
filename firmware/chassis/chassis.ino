/**
 * chassis.ino
 * ESP32 底盘控制固件 - 主程序入口
 *
 * 定位：
 *   本模块是具身智能机器人的执行边缘（Edge），负责接收来自大脑（Brain/Edge Agent）
 *   的指令并驱动底盘硬件，不包含任何决策逻辑。
 *
 * 功能：
 *   1. 连接 WiFi，启动 WebSocket Server（端口 81）
 *   2. 解析 D-C-C 协议指令，控制底盘运动
 *   3. 定期上报底盘状态
 *   4. 心跳超时自动停车（失联保护）
 *   5. 急停 / 解除急停
 *
 * D-C-C 通信协议（Domain-Command-Content）：
 *   相比传统 JSON 技能格式，D-C-C 更紧凑，适配 WiFi/BT/USB 多传输层。
 *
 *   发送格式：
 *     {"d":"chassis","c":"fwd","v":0.5,"rid":"001"}      前进
 *     {"d":"chassis","c":"bwd","v":0.5,"rid":"002"}      后退
 *     {"d":"chassis","c":"lt", "v":0.5,"rid":"003"}      左转
 *     {"d":"chassis","c":"rt", "v":0.5,"rid":"004"}      右转
 *     {"d":"chassis","c":"drv","l":0.6,"a":-0.3,"rid":"005"}  差速摇杆
 *     {"d":"chassis","c":"stp","rid":"006"}              停止
 *     {"d":"sys","c":"estop","rid":"007"}                急停
 *     {"d":"sys","c":"clr","rid":"008"}                  解除急停
 *     {"d":"sys","c":"stat","rid":"009"}                 查询状态
 *     {"d":"sys","c":"hb","rid":"010"}                   心跳
 *
 *   响应格式（仅回应请求发起方）：
 *     {"rid":"001","ok":true,"s":"fwd"}
 *     {"rid":"001","ok":false,"err":"estop_active"}
 *
 *   状态广播（每秒主动推送，所有客户端）：
 *     {"t":"s","s":"fwd","l":0.5,"a":0.0,"em":false,"up":12345}
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WebSocketsServer.h>
#include <ArduinoJson.h>

#include "config.h"
#include "motor_driver.h"
#include "chassis_controller.h"

// ==================== 全局对象 ====================
ChassisController chassis;
WebSocketsServer  wsServer(WS_PORT);

// 状态上报定时器
unsigned long lastStatusReportMs = 0;
const unsigned long STATUS_REPORT_INTERVAL_MS = 1000;  // 每秒上报一次

// ==================== D-C-C 响应 ====================

/**
 * 发送 D-C-C 响应给指定客户端
 * {"rid":"001","ok":true,"s":"fwd"}
 */
void sendDCC(uint8_t clientNum, const String& rid, bool ok,
             const String& state = "", const String& err = "") {
    JsonDocument doc;
    doc["rid"] = rid;
    doc["ok"]  = ok;
    if (state.length() > 0) doc["s"] = state;
    if (err.length()   > 0) doc["err"] = err;
    String output;
    serializeJson(doc, output);
    wsServer.sendTXT(clientNum, output);
    if (DEBUG_SERIAL) Serial.println("[WS] → " + output);
}

/**
 * 广播底盘状态（每秒一次，紧凑格式）
 * {"t":"s","s":"fwd","l":0.5,"a":0.0,"em":false,"up":12345}
 */
void broadcastStatus() {
    JsonDocument doc;
    doc["t"]  = "s";
    doc["s"]  = chassis.getStateName();
    doc["l"]  = chassis.getLinearSpeed();
    doc["a"]  = chassis.getAngularSpeed();
    doc["em"] = chassis.isEmergencyStopped();
    doc["up"] = millis();
    String output;
    serializeJson(doc, output);
    wsServer.broadcastTXT(output);
}

// ==================== D-C-C 指令处理 ====================

/**
 * 解析并执行 D-C-C 格式指令
 *
 * 支持的指令：
 *   d=chassis: c=fwd/bwd/lt/rt/stp/drv
 *   d=sys:     c=estop/clr/stat/hb
 */
void handleMessage(uint8_t clientNum, const String& payload) {
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, payload);

    if (err) {
        if (DEBUG_SERIAL) Serial.println("[WS] Parse error: " + String(err.c_str()));
        wsServer.sendTXT(clientNum, "{\"ok\":false,\"err\":\"parse_err\"}");
        return;
    }

    String rid = doc["rid"] | "?";
    String d   = doc["d"]   | "";   // domain: chassis / sys
    String c   = doc["c"]   | "";   // command

    if (DEBUG_SERIAL) {
        Serial.println("[WS] ← d=" + d + " c=" + c + " rid=" + rid);
    }

    // ===== domain: chassis =====
    if (d == "chassis") {
        float v = doc["v"] | 0.5f;   // speed（单向指令用）
        int   t = doc["t"] | 0;       // duration ms，0=持续

        if (c == "fwd") {
            chassis.moveForward(v);
            if (t > 0) { delay(t); chassis.stop(); }

        } else if (c == "bwd") {
            chassis.moveBackward(v);
            if (t > 0) { delay(t); chassis.stop(); }

        } else if (c == "lt") {
            chassis.turnLeft(v);
            if (t > 0) { delay(t); chassis.stop(); }

        } else if (c == "rt") {
            chassis.turnRight(v);
            if (t > 0) { delay(t); chassis.stop(); }

        } else if (c == "stp") {
            chassis.stop();

        } else if (c == "drv") {
            // 差速摇杆：l=linear, a=angular
            float l = doc["l"] | 0.0f;
            float a = doc["a"] | 0.0f;
            chassis.drive(l, a);

        } else {
            sendDCC(clientNum, rid, false, "", "unknown_cmd");
            return;
        }
        sendDCC(clientNum, rid, true, chassis.getStateName());
    }

    // ===== domain: sys =====
    else if (d == "sys") {
        if (c == "estop") {
            chassis.emergencyStop();
            sendDCC(clientNum, rid, true, "estop");

        } else if (c == "clr") {
            chassis.clearEmergencyStop();
            sendDCC(clientNum, rid, true, "ok");

        } else if (c == "stat") {
            JsonDocument resp;
            resp["rid"] = rid;
            resp["ok"]  = true;
            resp["s"]   = chassis.getStateName();
            resp["l"]   = chassis.getLinearSpeed();
            resp["a"]   = chassis.getAngularSpeed();
            resp["em"]  = chassis.isEmergencyStopped();
            resp["up"]  = millis();
            String output;
            serializeJson(resp, output);
            wsServer.sendTXT(clientNum, output);

        } else if (c == "hb") {
            chassis.refreshHeartbeat();
            sendDCC(clientNum, rid, true, "pong");

        } else {
            sendDCC(clientNum, rid, false, "", "unknown_cmd");
        }
    }

    // ===== 未知 domain =====
    else {
        if (DEBUG_SERIAL) Serial.println("[WS] Unknown domain: " + d);
        sendDCC(clientNum, rid, false, "", "unknown_domain");
    }
}

// ==================== WebSocket 事件回调 ====================

void onWebSocketEvent(uint8_t clientNum, WStype_t type,
                      uint8_t* payload, size_t length) {
    switch (type) {
        case WStype_DISCONNECTED:
            if (DEBUG_SERIAL) {
                Serial.printf("[WS] Client #%u disconnected\n", clientNum);
            }
            // 客户端断开后立即停车（失联保护）
            // 心跳超时机制也会兜底，这里做主动停车
            chassis.stop();
            break;

        case WStype_CONNECTED: {
            IPAddress ip = wsServer.remoteIP(clientNum);
            if (DEBUG_SERIAL) {
                Serial.printf("[WS] Client #%u connected from %s\n",
                              clientNum, ip.toString().c_str());
            }
            // 发送欢迎消息 + 当前状态
            String welcome = "{\"type\":\"connected\",\"message\":\"ESP32 Chassis Ready\","
                             "\"state\":\"" + String(chassis.getStateName()) + "\"}";
            wsServer.sendTXT(clientNum, welcome);
            break;
        }

        case WStype_TEXT:
            handleMessage(clientNum, String((char*)payload));
            break;

        case WStype_ERROR:
            if (DEBUG_SERIAL) {
                Serial.printf("[WS] Error on client #%u\n", clientNum);
            }
            break;

        default:
            break;
    }
}

// ==================== WiFi 连接 ====================

void connectWiFi() {
    Serial.print("[WiFi] Connecting to ");
    Serial.print(WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    int retries = 0;
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
        retries++;
        if (retries > 40) {  // 20秒超时
            Serial.println("\n[WiFi] Connection failed! Restarting...");
            ESP.restart();
        }
    }

    Serial.println();
    Serial.println("[WiFi] Connected! IP: " + WiFi.localIP().toString());
    Serial.println("[WS]   WebSocket server: ws://" +
                   WiFi.localIP().toString() + ":" + String(WS_PORT));
}

// ==================== setup / loop ====================

void setup() {
    if (DEBUG_SERIAL) {
        Serial.begin(SERIAL_BAUD);
        delay(100);
        Serial.println("\n=== ESP32 Chassis Controller ===");
    }

    // 初始化底盘
    chassis.begin();

    // 连接 WiFi
    connectWiFi();

    // 启动 WebSocket 服务器
    wsServer.begin();
    wsServer.onEvent(onWebSocketEvent);
    Serial.println("[WS] WebSocket server started on port " + String(WS_PORT));
}

void loop() {
    // WebSocket 轮询（必须持续调用）
    wsServer.loop();

    // 底盘状态更新（检查心跳超时等）
    chassis.update();

    // 定时状态上报
    unsigned long now = millis();
    if (now - lastStatusReportMs >= STATUS_REPORT_INTERVAL_MS) {
        lastStatusReportMs = now;
        broadcastStatus();
    }
}
