
const POLL_MS = 3000;
const LOW_LIMIT = 20;
const HIGH_LIMIT = 90;
const LEVEL_FIELD = "level";
const API_KEY = "paste-your-key-here";

const $ = (id) => document.getElementById(id);

const el = {
  conn: $("connection"),
  water: $("water"),
  tank: $("tank"),
  markLow: $("markLow"),
  markHigh: $("markHigh"),
  level: $("levelValue"),
  badge: $("tankBadge"),
  updated: $("lastUpdated"),
  motorDot: $("motorDot"),
  motorText: $("motorText"),
  refillCard: $("refillCard"),
  refillMsg: $("refillMessage"),
  approve: $("approveBtn"),
  reject: $("rejectBtn"),
  feedback: $("feedback"),
  slider: $("testSlider"),
  sliderOut: $("testValue"),
  send: $("sendBtn"),
  chart: $("chart"),
  tbody: $("historyBody"),
};

let busy = false;

function pick(obj, keys) {
  if (!obj || typeof obj !== "object") return undefined;

  for (const key of keys) {
    if (obj[key] !== undefined && obj[key] !== null) {
      return obj[key];
    }
  }

  return undefined;
}

function tankFromLevel(level) {
  if (level <= LOW_LIMIT) return "Low";
  if (level >= HIGH_LIMIT) return "Full";
  return "Medium";
}

function formatTime(value) {
  if (!value) return "Unknown";

  const date = new Date(value);
  return isNaN(date) ? String(value) : date.toLocaleString();
}

function motorIsOn(value) {
  if (typeof value === "boolean") return value;

  if (value && typeof value === "object") {
    return motorIsOn(pick(value, ["status", "state", "on"]));
  }

  const status = String(value).toUpperCase();
  return status === "ON" || status === "RUNNING";
}

function normalizeStatus(raw) {
  const data = raw.data || raw;
  const reading =
    pick(data, ["latest_reading", "latest", "reading"]) || {};

  const rawLevel = pick(data, [
    "level",
    "water_level",
    "level_percent",
    "latest_level",
  ]) ?? pick(reading, [
    "level",
    "water_level",
    "level_percent",
  ]);

  const level =
    rawLevel === undefined || rawLevel === null
      ? NaN
      : Number(rawLevel);

  const tankStatus = pick(data, ["tank_status", "tank"]);

  return {
    level: Number.isFinite(level) ? level : null,
    tank: Number.isFinite(level)
      ? tankFromLevel(level)
      : tankStatus || null,
    motor: pick(data, ["motor_status", "motor"]),
    time: pick(data, [
      "timestamp",
      "last_updated",
      "updated_at",
      "created_at",
    ]) ?? pick(reading, ["timestamp", "created_at"]),
    pending:
      pick(data, [
        "refill_requested",
        "refill_pending",
        "request_pending",
        "pending_request",
      ]) === true,
    alert: pick(data, ["alert", "message"]),
  };
}

function normalizeReadings(raw) {
  const list = Array.isArray(raw)
    ? raw
    : raw.data || raw.readings || raw.history || [];

  return list.map((reading) => {
    const rawLevel = pick(reading, [
      "level",
      "water_level",
      "level_percent",
    ]);

    const level =
      rawLevel === undefined || rawLevel === null
        ? NaN
        : Number(rawLevel);

    return {
      level,
      time: pick(reading, [
        "timestamp",
        "created_at",
        "time",
      ]),
      tank: Number.isFinite(level)
        ? tankFromLevel(level)
        : "Unknown",
    };
  }).filter((reading) => Number.isFinite(reading.level));
}

// ---------- Connection ----------

function setConnection(ok) {
  el.conn.className = "pill " + (ok ? "pill-ok" : "pill-bad");
  el.conn.textContent = ok
    ? "Backend connected"
    : "Backend not reachable";
}

// ---------- Dashboard status ----------

function renderStatus(status) {
  if (status.level === null) return;

  const level = Math.max(0, Math.min(100, status.level));
  const low = level <= LOW_LIMIT;
  const full = level >= HIGH_LIMIT;
  const motorOn = motorIsOn(status.motor);

  el.water.style.height = level + "%";
  el.water.classList.toggle("is-low", low);
  el.level.textContent = Math.round(level);
  el.updated.textContent = formatTime(status.time);

  const tank = low ? "Low" : full ? "Full" : "Medium";

  el.badge.textContent = tank + (full ? "" : " level");
  el.badge.className = "badge badge-" + tank.toLowerCase();

  el.motorText.textContent = motorOn ? "ON (simulated)" : "OFF";
  el.motorDot.className = "dot " + (motorOn ? "dot-on" : "dot-off");

  const needsRefill = low || status.pending;

  el.refillCard.classList.toggle(
    "needs-action",
    low && !status.pending
  );

  if (full) {
    el.refillMsg.textContent =
      "Tank is full. The simulated motor stays OFF.";
  } else if (low) {
    el.refillMsg.textContent =
      status.alert ||
      "⚠️ LOW WATER LEVEL! Refill needed. Please approve or reject the refill request.";
  } else if (status.pending && motorOn) {
    el.refillMsg.textContent =
      "Refill approved. The simulated motor is filling the tank.";
  } else if (status.pending) {
    el.refillMsg.textContent =
      "Refill request is pending. Please approve or reject.";
  } else {
    el.refillMsg.textContent =
      "The tank level is fine. No refill needed.";
  }

  const canAct = needsRefill && !busy && !motorOn && !full;

  el.approve.disabled = !canAct;
  el.reject.disabled = !canAct;
}

// ---------- History ----------

function renderHistory(rows) {
  el.tbody.innerHTML = "";

  if (!rows.length) {
    el.tbody.innerHTML =
      '<tr><td colspan="3" class="muted">No readings yet.</td></tr>';
    el.chart.innerHTML = "";
    return;
  }

  rows.forEach((reading) => {
    const row = document.createElement("tr");

    [
      formatTime(reading.time),
      reading.level + "%",
      reading.tank,
    ].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    });

    el.tbody.appendChild(row);
  });

  drawChart(rows);
}

function drawChart(rows) {
  const data = [...rows].sort(
    (a, b) => new Date(a.time) - new Date(b.time)
  );

  const W = 700;
  const H = 200;
  const pad = 32;

  const x = (index) =>
    pad +
    (data.length === 1
      ? (W - 2 * pad) / 2
      : (index * (W - 2 * pad)) / (data.length - 1));

  const y = (value) =>
    H - pad - (value / 100) * (H - 2 * pad);

  const points = data
    .map((reading, index) =>
      `${x(index)},${y(reading.level)}`
    )
    .join(" ");

  let svg =
    `<svg viewBox="0 0 ${W} ${H}" role="img" ` +
    `aria-label="Water level history chart">`;

  [0, 50, 100].forEach((value) => {
    svg +=
      `<line x1="${pad}" x2="${W - pad}" ` +
      `y1="${y(value)}" y2="${y(value)}" stroke="#d9e4ea"/>`;

    svg +=
      `<text x="4" y="${y(value) + 4}" ` +
      `font-size="11" fill="#4a6372">${value}%</text>`;
  });

  svg +=
    `<line x1="${pad}" x2="${W - pad}" ` +
    `y1="${y(LOW_LIMIT)}" y2="${y(LOW_LIMIT)}" ` +
    `stroke="#c8402f" stroke-dasharray="5 4"/>`;

  svg +=
    `<polyline points="${points}" fill="none" ` +
    `stroke="#146b80" stroke-width="3" ` +
    `stroke-linejoin="round" stroke-linecap="round"/>`;

  data.forEach((reading, index) => {
    svg +=
      `<circle cx="${x(index)}" cy="${y(reading.level)}" ` +
      `r="4" fill="#146b80"/>`;
  });

  svg += "</svg>";
  el.chart.innerHTML = svg;
}

// ---------- Feedback ----------

function showFeedback(message, ok) {
  el.feedback.textContent = message;
  el.feedback.className = "feedback " + (ok ? "ok" : "err");
}

// ---------- API requests ----------

async function api(path, options = {}) {
  options.headers = {
    "X-API-Key": API_KEY,
    ...(options.headers || {}),
  };

  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));

  if (!response.ok || body.success === false) {
    throw new Error(
      body.message || body.error || "Request failed (" + response.status + ")"
    );
  }

  return body;
}

async function refresh() {
  try {
    const [status, history] = await Promise.all([
      api("/api/status"),
      api("/api/readings?limit=10"),
    ]);

    setConnection(true);
    renderStatus(normalizeStatus(status));
    renderHistory(normalizeReadings(history));
  } catch (error) {
    setConnection(false);
    console.error("Dashboard refresh failed:", error);
  }
}

async function refillAction(kind) {
  if (busy) return;

  busy = true;
  el.approve.disabled = true;
  el.reject.disabled = true;

  try {
    const body = await api("/api/refill/" + kind, {
      method: "POST",
    });

    showFeedback(
      body.message ||
        (kind === "approve"
          ? "Refill approved."
          : "Refill request rejected."),
      true
    );
  } catch (error) {
    showFeedback(error.message, false);
  } finally {
    busy = false;
    await refresh();
  }
}

async function sendTestReading() {
  if (busy) return;

  el.send.disabled = true;

  try {
    const payload = {
      [LEVEL_FIELD]: Number(el.slider.value),
      device_id: "web-dashboard",
    };

    const body = await api("/api/readings", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    showFeedback(body.message || "Reading sent.", true);
  } catch (error) {
    showFeedback(error.message, false);
  } finally {
    el.send.disabled = false;
    await refresh();
  }
}

// ---------- Start ----------

el.markLow.style.bottom = LOW_LIMIT + "%";
el.markHigh.style.bottom = HIGH_LIMIT + "%";

el.approve.addEventListener("click", () => refillAction("approve"));
el.reject.addEventListener("click", () => refillAction("reject"));
el.send.addEventListener("click", sendTestReading);

el.slider.addEventListener("input", () => {
  el.sliderOut.textContent = el.slider.value + "%";
});

refresh();
setInterval(refresh, POLL_MS);
