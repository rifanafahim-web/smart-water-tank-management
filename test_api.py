"""
test_api.py - automatic tests for the backend.
Run with:   python test_api.py
It uses a temporary database, so your real water_tank.db is never touched.
"""
import os
import tempfile
import unittest

import config

# Use a temporary database file BEFORE importing app
config.DATABASE_PATH = os.path.join(tempfile.mkdtemp(), "test.db")

import app as app_module      # noqa: E402  (imported after the config change on purpose)
import database               # noqa: E402

KEY = {"X-API-Key": config.DEVICE_API_KEY}


class WaterTankApiTests(unittest.TestCase):

    def setUp(self):
        # A fresh, empty database for every test
        if os.path.exists(config.DATABASE_PATH):
            os.remove(config.DATABASE_PATH)
        database.init_db()
        self.original_cooldown = config.REJECT_COOLDOWN_SECONDS
        self.client = app_module.app.test_client()

    def tearDown(self):
        config.REJECT_COOLDOWN_SECONDS = self.original_cooldown

    # small helpers
    def send(self, level, **extra):
        body = {"level_percent": level}
        body.update(extra)
        return self.client.post("/api/readings", json=body, headers=KEY)

    def status(self):
        return self.client.get("/api/status").get_json()

    # ---------- validation ----------
    def test_missing_api_key_is_401(self):
        r = self.client.post("/api/readings", json={"level_percent": 50})
        self.assertEqual(r.status_code, 401)
        self.assertFalse(r.get_json()["success"])

    def test_wrong_api_key_is_401(self):
        r = self.client.post("/api/readings", json={"level_percent": 50}, headers={"X-API-Key": "nope"})
        self.assertEqual(r.status_code, 401)

    def test_body_not_json_is_400(self):
        r = self.client.post("/api/readings", data="hello", headers=KEY)
        self.assertEqual(r.status_code, 400)

    def test_missing_level_is_400(self):
        r = self.client.post("/api/readings", json={"device_id": "tank-1"}, headers=KEY)
        self.assertEqual(r.status_code, 400)
        self.assertIn("required", r.get_json()["message"])

    def test_bad_levels_are_400(self):
        for bad in ["50", None, True, [1], -1, 100.5, 101]:
            r = self.send(bad)
            self.assertEqual(r.status_code, 400, f"{bad!r} should be rejected")

    def test_nan_and_infinity_are_400(self):
        for text in ('{"level_percent": NaN}', '{"level_percent": Infinity}'):
            r = self.client.post("/api/readings", data=text, headers={**KEY, "Content-Type": "application/json"})
            self.assertEqual(r.status_code, 400, text)

    def test_bad_device_id_is_400(self):
        for bad in ["", "tank 1", "a/b", 5, "x" * 51]:
            self.assertEqual(self.send(50, device_id=bad).status_code, 400, repr(bad))

    def test_valid_reading_is_201_and_saved(self):
        r = self.send(55.46, device_id="tank-1")
        self.assertEqual(r.status_code, 201)
        body = r.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["reading"]["level_percent"], 55.5)
        self.assertEqual(body["tank_status"], "Medium")
        self.assertEqual(body["motor_command"], "OFF")
        self.assertIsNone(body["alert"])

    def test_boundary_levels_0_and_100_are_valid(self):
        self.assertEqual(self.send(0).status_code, 201)
        self.assertEqual(self.send(100).status_code, 201)

    def test_unknown_url_and_wrong_method_return_json(self):
        r = self.client.get("/api/nothing")
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.get_json()["success"])
        r = self.client.get("/api/refill/approve")
        self.assertEqual(r.status_code, 405)
        self.assertFalse(r.get_json()["success"])

    # ---------- status ----------
    def test_status_with_no_data(self):
        body = self.status()
        self.assertTrue(body["success"])
        self.assertFalse(body["has_data"])
        self.assertEqual(body["motor_status"], "OFF")
        self.assertIsNone(body["level_percent"])

    def test_status_shows_latest_reading(self):
        self.send(60)
        self.send(58)
        body = self.status()
        self.assertEqual(body["level_percent"], 58)
        self.assertEqual(body["tank_status"], "Medium")
        self.assertEqual(body["refill_state"], "none")
        self.assertIn("simulated", body["note"].lower())

    # ---------- low-water alert ----------
    def test_low_level_creates_alert_and_pending_request(self):
        body = self.send(30).get_json()      # exactly the threshold counts as low
        self.assertEqual(body["alert"]["type"], "low_water")
        self.assertTrue(body["alert"]["refill_request_created"])
        self.assertEqual(body["refill_state"], "pending")
        self.assertEqual(body["tank_status"], "Low")

    def test_second_low_reading_does_not_create_second_request(self):
        self.send(29)
        body = self.send(27).get_json()
        self.assertFalse(body["alert"]["refill_request_created"])
        with database.db_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM refill_requests").fetchone()[0]
        self.assertEqual(count, 1)

    def test_pending_request_expires_when_level_recovers(self):
        self.send(25)
        self.send(60)
        self.assertEqual(self.status()["refill_state"], "none")
        r = self.client.post("/api/refill/approve")
        self.assertEqual(r.status_code, 409)

    # ---------- approve ----------
    def test_approve_without_request_is_409(self):
        r = self.client.post("/api/refill/approve")
        self.assertEqual(r.status_code, 409)
        self.assertFalse(r.get_json()["success"])

    def test_approve_turns_simulated_motor_on(self):
        self.send(25)
        r = self.client.post("/api/refill/approve")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["motor_status"], "ON")
        self.assertIn("simulated", body["message"].lower())
        self.assertEqual(self.status()["motor_status"], "ON")
        self.assertEqual(self.send(29).get_json()["motor_command"], "ON")

    def test_repeated_approval_is_blocked(self):
        self.send(25)
        self.assertEqual(self.client.post("/api/refill/approve").status_code, 200)
        r = self.client.post("/api/refill/approve")
        self.assertEqual(r.status_code, 409)
        self.assertIn("already approved", r.get_json()["message"])
        with database.db_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM refill_requests").fetchone()[0]
        self.assertEqual(count, 1)

    def test_reject_after_approve_is_blocked(self):
        self.send(25)
        self.client.post("/api/refill/approve")
        r = self.client.post("/api/refill/reject")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.status()["motor_status"], "ON")

    def test_approve_blocked_when_tank_at_high_limit(self):
        # Force a stale pending request while the latest reading is full
        self.send(25)
        self.send(95)                       # expires the pending request
        database.create_refill_request(25)  # pretend a pending request still exists
        r = self.client.post("/api/refill/approve")
        self.assertEqual(r.status_code, 409)
        self.assertIn("high-level limit", r.get_json()["message"])
        self.assertEqual(self.status()["motor_status"], "OFF")

    # ---------- full tank ----------
    def test_motor_turns_off_when_tank_reaches_high_level(self):
        self.send(25)
        self.client.post("/api/refill/approve")
        self.assertEqual(self.send(60).get_json()["motor_command"], "ON")
        body = self.send(90).get_json()     # exactly the limit
        self.assertEqual(body["motor_command"], "OFF")
        self.assertEqual(body["tank_status"], "Full")
        self.assertEqual(body["alert"]["type"], "full_tank")
        status = self.status()
        self.assertEqual(status["motor_status"], "OFF")
        self.assertEqual(status["refill_state"], "none")
        self.assertEqual(status["refill_request"]["status"], "completed")

    def test_overshoot_reading_also_stops_motor(self):
        self.send(25)
        self.client.post("/api/refill/approve")
        self.assertEqual(self.send(97).get_json()["motor_command"], "OFF")

    # ---------- reject ----------
    def test_reject_without_request_is_409(self):
        self.assertEqual(self.client.post("/api/refill/reject").status_code, 409)

    def test_reject_cancels_request_and_keeps_motor_off(self):
        self.send(25)
        r = self.client.post("/api/refill/reject")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["refill_state"], "rejected")
        status = self.status()
        self.assertEqual(status["motor_status"], "OFF")
        self.assertEqual(status["refill_state"], "rejected")
        self.assertEqual(self.client.post("/api/refill/reject").status_code, 409)  # not twice
        self.assertEqual(self.client.post("/api/refill/approve").status_code, 409)

    def test_request_not_repeated_during_cooldown_then_repeated_after(self):
        self.send(25)
        self.client.post("/api/refill/reject")
        self.assertEqual(self.send(24).get_json()["refill_state"], "rejected")   # still cooling down
        config.REJECT_COOLDOWN_SECONDS = 0
        self.assertEqual(self.send(23).get_json()["refill_state"], "pending")    # asked again
        self.assertEqual(self.client.post("/api/refill/approve").status_code, 200)

    def test_rejected_state_clears_when_level_recovers(self):
        self.send(25)
        self.client.post("/api/refill/reject")
        self.send(70)
        self.assertEqual(self.status()["refill_state"], "none")

    # ---------- history ----------
    def test_history_newest_first_and_limit(self):
        for level in (50, 48, 46, 44):
            self.send(level)
        body = self.client.get("/api/readings?limit=3").get_json()
        self.assertTrue(body["success"])
        self.assertEqual([r["level_percent"] for r in body["readings"]], [44, 46, 48])
        self.assertIn("tank_status", body["readings"][0])
        self.assertIn("motor_status", body["readings"][0])

    def test_history_bad_limit_is_400(self):
        for bad in ["abc", "0", "101", "-5"]:
            self.assertEqual(self.client.get(f"/api/readings?limit={bad}").status_code, 400, bad)

    # ---------- misc ----------
    def test_home_page_and_cors_header(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "*")
        r = self.client.options("/api/refill/approve")
        self.assertIn("POST", r.headers["Access-Control-Allow-Methods"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
