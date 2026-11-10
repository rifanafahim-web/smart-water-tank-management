

from flask import Flask, jsonify, request, send_from_directory
import os

import database

app = Flask(
    __name__,
    static_folder="static",
    static_url_path="/static"
)

LOW_WATER_THRESHOLD = 20


@app.route("/")
def home():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "index.html"
    )


@app.route("/api")
def api_info():
    return jsonify({
        "success": True,
        "message": "Smart Water Tank backend is running",
        "endpoints": [
            "GET /api/status",
            "GET /api/readings?limit=10",
            "POST /api/readings",
            "POST /api/refill/approve",
            "POST /api/refill/reject"
        ]
    })


@app.route("/api/status", methods=["GET"])
def get_status():
    row = database.get_latest_reading()

    if row:
        level = float(row["level_percent"])
        timestamp = row["created_at"]
        motor_status = row["motor_status"]
    else:
        level = 0
        timestamp = None
        motor_status = "OFF"

    return jsonify({
        "success": True,
        "level": level,
        "water_level": level,
        "tank_status": (
            "LOW" if level <= LOW_WATER_THRESHOLD else "NORMAL"
        ),
        "motor_status": motor_status,
        "timestamp": timestamp,
        "refill_requested": level <= LOW_WATER_THRESHOLD
    })


@app.route("/api/readings", methods=["GET"])
def get_readings():
    try:
        limit = int(request.args.get("limit", 10))
        limit = max(1, min(limit, 100))
    except (ValueError, TypeError):
        limit = 10

    rows = database.get_recent_readings(limit)

    readings = [
        {
            "id": row["id"],
            "level": row["level_percent"],
            "water_level": row["level_percent"],
            "timestamp": row["created_at"],
            "motor_status": row["motor_status"]
        }
        for row in rows
    ]

    return jsonify({
        "success": True,
        "readings": readings,
        "data": readings
    })


@app.route("/api/readings", methods=["POST"])
def add_reading():
    data = request.get_json(silent=True) or {}

    level = data.get(
        "level",
        data.get("water_level", data.get("waterLevel"))
    )

    if level is None:
        return jsonify({
            "success": False,
            "message": "Please provide level or water_level"
        }), 400

    try:
        level = float(level)
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "Water level must be a number"
        }), 400

    if not 0 <= level <= 100:
        return jsonify({
            "success": False,
            "message": "Water level must be between 0 and 100"
        }), 400

    device_id = str(data.get("device_id", "web-dashboard"))
    motor_status = "OFF"

    saved = database.save_reading(
        device_id,
        level,
        motor_status
    )

    return jsonify({
        "success": True,
        "message": "Water-level reading saved",
        "level": saved["level_percent"],
        "timestamp": saved["created_at"]
    }), 201


@app.route("/api/refill/approve", methods=["POST"])
def approve_refill():
    latest = database.get_latest_refill_request()

    if latest and latest["status"] == "pending":
        changed = database.decide_refill_request(
            latest["id"], "approved"
        )

        if changed:
            return jsonify({
                "success": True,
                "message": "Refill request approved. Motor control is simulated.",
                "motor_status": "ON"
            })

    return jsonify({
        "success": True,
        "message": "No pending refill request. Motor control is simulated.",
        "motor_status": "OFF"
    })


@app.route("/api/refill/reject", methods=["POST"])
def reject_refill():
    latest = database.get_latest_refill_request()

    if latest and latest["status"] == "pending":
        database.decide_refill_request(
            latest["id"], "rejected"
        )

    return jsonify({
        "success": True,
        "message": "Refill request rejected. Motor remains simulated.",
        "motor_status": "OFF"
    })


if __name__ == "__main__":
    database.init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)
