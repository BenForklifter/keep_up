// keep_up MCU sketch: reads the Modulino Knob and the optional Modulino Movement
// and notifies the Python app (python/main.py) over the Bridge.
//
//   knob_turned(steps)  the knob moved by `steps` detents, positive = clockwise
//   knob_pressed()      the knob was pushed down, once per press
//   moved(milli_g)      the table was bumped (someone sits down, puts down a glass),
//                       at most once per BUMP_COOLDOWN_MS; wakes keep_up from sleep
//
// A bump is a sudden change of acceleration: the reading differs from its recent
// average, which is gravity however the board is tilted, by more than BUMP_THRESHOLD_G.

#include <Arduino_Modulino.h>
#include <Arduino_RouterBridge.h>

constexpr uint32_t POLL_INTERVAL_MS = 20;
constexpr float BUMP_THRESHOLD_G = 0.02f;    // sensor noise is ~0.001 g; raise if it wakes by itself
constexpr float BASELINE_SMOOTHING = 0.04f;  // per poll: the average follows within ~0.5 s
constexpr uint32_t BUMP_COOLDOWN_MS = 1000;

struct Acceleration {
  float x;
  float y;
  float z;
};

static ModulinoKnob knob;
static int16_t lastKnobPosition = 0;
static bool knobWasPressed = false;

static ModulinoMovement movement;
static bool hasMovement = false;
static bool hasBaseline = false;
static Acceleration baseline = {0.0f, 0.0f, 0.0f};  // g
static uint32_t lastBumpMs = 0;

static void pollKnob() {
  const int16_t position = knob.get();
  if (position != lastKnobPosition) {
    Bridge.notify("knob_turned", static_cast<int>(position - lastKnobPosition));
    lastKnobPosition = position;
  }

  const bool pressed = knob.isPressed();
  if (pressed && !knobWasPressed) {
    Bridge.notify("knob_pressed");
  }
  knobWasPressed = pressed;
}

static void pollMovement() {
  if (!hasMovement || !movement.update()) {
    return;
  }
  const Acceleration reading = {movement.getX(), movement.getY(), movement.getZ()};
  if (!hasBaseline) {
    baseline = reading;
    hasBaseline = true;
    return;
  }

  const float dx = reading.x - baseline.x;
  const float dy = reading.y - baseline.y;
  const float dz = reading.z - baseline.z;
  baseline.x += dx * BASELINE_SMOOTHING;
  baseline.y += dy * BASELINE_SMOOTHING;
  baseline.z += dz * BASELINE_SMOOTHING;

  const float change = sqrtf(dx * dx + dy * dy + dz * dz);
  const uint32_t now = millis();
  if (change > BUMP_THRESHOLD_G && now - lastBumpMs >= BUMP_COOLDOWN_MS) {
    Bridge.notify("moved", static_cast<int>(change * 1000.0f));
    lastBumpMs = now;
  }
}

void setup() {
  Bridge.begin();
  Modulino.begin(Wire1);  // the Qwiic connector of the UNO Q
  knob.begin();
  lastKnobPosition = knob.get();
  hasMovement = movement.begin();
}

void loop() {
  pollKnob();
  pollMovement();
  delay(POLL_INTERVAL_MS);
}
