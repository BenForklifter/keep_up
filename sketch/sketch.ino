// Modulino Knob and Modulino Movement -> Python (python/main.py) over the Bridge.
//
//   knob_turned(steps)  knob moved by `steps` detents (+ clockwise, - counter-clockwise)
//   knob_pressed()      knob pushed down (once per press)
//   moved(milli_g)      the table was bumped (someone sits down, puts down a glass):
//                       wakes keep_up from its sleep mode; at most once per BUMP_REPEAT_MS
//
// A bump is a sudden change of acceleration: the reading differs from its recent
// average (gravity, however the table and board are tilted) by more than BUMP_G.
// Without a Modulino Movement the knob works as before.

#include <Arduino_Modulino.h>
#include <Arduino_RouterBridge.h>

const unsigned long POLL_MS = 20;
const float BUMP_G = 0.02;                  // sensor noise is ~0.001 g; raise if it wakes by itself
const float AVERAGE_FOLLOW = 0.04;          // per poll: the average follows within ~0.5 s
const unsigned long BUMP_REPEAT_MS = 1000;

ModulinoKnob knob;
int16_t lastPosition = 0;
bool wasPressed = false;

ModulinoMovement movement;
bool hasMovement = false;
bool hasAverage = false;
float averageX, averageY, averageZ;  // g
unsigned long lastBump = 0;

void setup() {
  Bridge.begin();
  Modulino.begin(Wire1);  // the Qwiic connector of the UNO Q
  knob.begin();
  lastPosition = knob.get();
  hasMovement = movement.begin();
}

void checkBump() {
  if (!hasMovement || !movement.update()) {
    return;
  }
  float x = movement.getX(), y = movement.getY(), z = movement.getZ();
  if (!hasAverage) {
    averageX = x;
    averageY = y;
    averageZ = z;
    hasAverage = true;
    return;
  }
  float dx = x - averageX, dy = y - averageY, dz = z - averageZ;
  float change = sqrt(dx * dx + dy * dy + dz * dz);
  averageX += dx * AVERAGE_FOLLOW;
  averageY += dy * AVERAGE_FOLLOW;
  averageZ += dz * AVERAGE_FOLLOW;

  unsigned long now = millis();
  if (change > BUMP_G && now - lastBump >= BUMP_REPEAT_MS) {
    Bridge.notify("moved", (int)(change * 1000));
    lastBump = now;
  }
}

void loop() {
  int16_t position = knob.get();
  if (position != lastPosition) {
    Bridge.notify("knob_turned", (int)(position - lastPosition));
    lastPosition = position;
  }

  bool pressed = knob.isPressed();
  if (pressed && !wasPressed) {
    Bridge.notify("knob_pressed");
  }
  wasPressed = pressed;

  checkBump();

  delay(POLL_MS);
}
