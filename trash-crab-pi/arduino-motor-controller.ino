/*
  arduino-motor-controller.ino

  Runs on the Arduino Nano, connected to the Raspberry Pi over USB serial.
  Owns all real-time motor control: PWM generation, ESC arming, and smooth
  speed ramping. The Pi only sends high-level target speeds; this sketch
  is responsible for actually reaching them safely.

  Motor layout (tank/differential drive, 2 pairs):
    LEFT  pair = PIN_LEFT_FRONT, PIN_LEFT_REAR
    RIGHT pair = PIN_RIGHT_FRONT, PIN_RIGHT_REAR

  Serial protocol (from the Pi, one command per line, newline terminated):
    ARM              -> re-run the ESC arming sequence (neutral hold)
    STOP             -> immediate hard cut to neutral, bypasses the ramp
    T:<left>,<right> -> set target speed for each side, -100..100 (percent)

  PWM convention (ESC standard, in microseconds):
    1500us = neutral / stopped
    2000us = full forward
    1000us = full reverse
  MAX_SPEED_PCT caps how far from neutral we will ever command, no matter
  what target is requested.
*/

#include <Servo.h>

// =====================================================================
// CONFIGURATION - update the pin numbers below if your ESCs are wired
// to different pins. Defaults shown are what our Trash Crab unit uses.
// =====================================================================
const uint8_t PIN_LEFT_FRONT  = 3;
const uint8_t PIN_LEFT_REAR   = 5;
const uint8_t PIN_RIGHT_FRONT = 6;
const uint8_t PIN_RIGHT_REAR  = 9;

const int PULSE_NEUTRAL      = 1500;
const int PULSE_FORWARD_MAX  = 2000;
const int PULSE_REVERSE_MAX  = 1000;
const float MAX_SPEED_PCT    = 85.0;   // hard safety cap, mirrors the Pi-side cap
const unsigned long RAMP_MS  = 1500;   // time to sweep the full -100..100 range

Servo escLF, escLR, escRF, escRR;

// Current and target speeds, in percent (-100..100, later clamped to MAX_SPEED_PCT).
float currentLeft = 0, currentRight = 0;
float targetLeft  = 0, targetRight = 0;
unsigned long lastUpdate = 0;

String inputBuffer = "";

// Converts a -100..100 percent value into a microsecond pulse and writes it.
void writeMotor(Servo &esc, float percent) {
  percent = constrain(percent, -MAX_SPEED_PCT, MAX_SPEED_PCT);
  int pulse;
  if (percent >= 0) {
    pulse = PULSE_NEUTRAL + (percent / 100.0) * (PULSE_FORWARD_MAX - PULSE_NEUTRAL);
  } else {
    pulse = PULSE_NEUTRAL + (percent / 100.0) * (PULSE_NEUTRAL - PULSE_REVERSE_MAX);
  }
  esc.writeMicroseconds(pulse);
}

// ESCs need a neutral signal held for a few seconds before they'll accept
// throttle commands. Runs at boot and again any time "ARM" is received.
void armEscs() {
  Serial.println("Arming ESCs (neutral hold)...");
  escLF.writeMicroseconds(PULSE_NEUTRAL);
  escLR.writeMicroseconds(PULSE_NEUTRAL);
  escRF.writeMicroseconds(PULSE_NEUTRAL);
  escRR.writeMicroseconds(PULSE_NEUTRAL);
  delay(3000);
  currentLeft = currentRight = 0;
  targetLeft  = targetRight  = 0;
  Serial.println("Armed.");
}

void setup() {
  Serial.begin(57600);
  escLF.attach(PIN_LEFT_FRONT);
  escLR.attach(PIN_LEFT_REAR);
  escRF.attach(PIN_RIGHT_FRONT);
  escRR.attach(PIN_RIGHT_REAR);

  armEscs();
  lastUpdate = millis();
}

// Moves currentLeft/currentRight toward their targets at a fixed rate, so a
// full -85..85 sweep takes about RAMP_MS. Non-blocking, called every loop() tick.
void applyRamp() {
  unsigned long now = millis();
  unsigned long dt = now - lastUpdate;
  lastUpdate = now;

  float maxStep = (2.0 * MAX_SPEED_PCT) * (float)dt / (float)RAMP_MS;

  if (currentLeft < targetLeft)       currentLeft = min(currentLeft + maxStep, targetLeft);
  else if (currentLeft > targetLeft)  currentLeft = max(currentLeft - maxStep, targetLeft);

  if (currentRight < targetRight)      currentRight = min(currentRight + maxStep, targetRight);
  else if (currentRight > targetRight) currentRight = max(currentRight - maxStep, targetRight);

  writeMotor(escLF, currentLeft);
  writeMotor(escLR, currentLeft);
  writeMotor(escRF, currentRight);
  writeMotor(escRR, currentRight);
}

void handleCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  if (cmd == "STOP") {
    // Emergency stop: cut immediately, do not ramp down.
    targetLeft = 0;
    targetRight = 0;
    currentLeft = 0;
    currentRight = 0;
  } else if (cmd == "ARM") {
    armEscs();
  } else if (cmd.startsWith("T:")) {
    int commaIdx = cmd.indexOf(',');
    if (commaIdx > 2) {
      float l = cmd.substring(2, commaIdx).toFloat();
      float r = cmd.substring(commaIdx + 1).toFloat();
      targetLeft  = constrain(l, -MAX_SPEED_PCT, MAX_SPEED_PCT);
      targetRight = constrain(r, -MAX_SPEED_PCT, MAX_SPEED_PCT);
    }
  }
}

// Called automatically by the Arduino core after each loop() pass whenever
// serial data is available. Standard on the Uno/Nano.
void serialEvent() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      handleCommand(inputBuffer);
      inputBuffer = "";
    } else if (c != '\r') {
      inputBuffer += c;
    }
  }
}

void loop() {
  applyRamp();
  delay(20); // ~50Hz control loop
}
