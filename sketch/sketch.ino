// Modulino Knob -> Python (python/main.py) over the Bridge.
//
//   knob_turned(steps)  knob moved by `steps` detents (+ clockwise, - counter-clockwise)
//   knob_pressed()      knob pushed down (once per press)

#include <Arduino_Modulino.h>
#include <Arduino_RouterBridge.h>

const unsigned long POLL_MS = 20;

ModulinoKnob knob;
int16_t lastPosition = 0;
bool wasPressed = false;

void setup() {
  Bridge.begin();
  Modulino.begin(Wire1);  // the Qwiic connector of the UNO Q
  knob.begin();
  lastPosition = knob.get();
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

  delay(POLL_MS);
}
