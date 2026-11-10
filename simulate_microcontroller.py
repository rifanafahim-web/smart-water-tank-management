"""
simulate_microcontroller.py - pretends to be the tank's microcontroller.

It sends a fake water level to the backend every 2 seconds, exactly like a
real ESP32/Arduino would, and reads the answer.

NOTHING REAL IS CONTROLLED HERE. "Motor ON" below is only a message on the screen.

Run it in a SECOND terminal while app.py is running:
    python simulate_microcontroller.py
Press Ctrl+C to stop.
"""
import json
import random
import time
import urllib.error
import urllib.request

import config

SERVER_URL = "http://127.0.0.1:5000/api/readings"
SEND_EVERY_SECONDS = 2

level = 55.0                 # fake starting water level in %
motor_on = False             # the simulated motor (changes only when the server says so)


def send_reading(level_percent):
    """Sends one reading to the backend. Returns the server's JSON reply, or None."""
    body = json.dumps({"device_id": config.DEFAULT_DEVICE_ID, "level_percent": level_percent})
    req = urllib.request.Request(
        SERVER_URL,
        data=body.encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": config.DEVICE_API_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        # The server answered with an error (400, 401, ...). It still sends JSON.
        return json.loads(error.read().decode("utf-8"))
    except urllib.error.URLError:
        return None           # server not running


print("Simulated microcontroller started (no real hardware). Press Ctrl+C to stop.")
try:
    while True:
        reply = send_reading(round(level, 1))

        if reply is None:
            print("Cannot reach the server. Is app.py running?")
        elif not reply.get("success"):
            print("Server rejected the reading:", reply.get("message"))
        else:
            motor_on = reply["motor_command"] == "ON"
            print(f"Sent {level:5.1f}%  |  tank: {reply['tank_status']:<6}  |  "
                  f"simulated motor: {'ON ' if motor_on else 'OFF'}  |  refill: {reply['refill_state']}")
            if reply["alert"]:
                print("   ALERT:", reply["alert"]["message"])

        # Fake the physical world: water is used, or the (simulated) motor fills the tank.
        if motor_on:
            # A real tank would also have its own float switch that cuts the motor.
            # We copy that idea here by never filling above the high-level limit.
            level = min(level + random.randint(3, 5), config.HIGH_LEVEL_THRESHOLD)
        else:
            level = max(level - random.randint(1, 3), 0)

        time.sleep(SEND_EVERY_SECONDS)
except KeyboardInterrupt:
    print("\nStopped.")
