const grid = document.getElementById("uavGrid");
const template = document.getElementById("uavCardTemplate");
const hostStatus = document.getElementById("hostStatus");
const cards = new Map();

function cardFor(uavId) {
  if (cards.has(uavId)) {
    return cards.get(uavId);
  }
  const node = template.content.firstElementChild.cloneNode(true);
  node.querySelector(".uav-id").textContent = uavId.toUpperCase();
  node.querySelector(".video").alt = `${uavId} image`;
  grid.appendChild(node);
  cards.set(uavId, node);
  return node;
}

function render(snapshot) {
  const uavs = snapshot.uavs || [];
  hostStatus.textContent = `host monotonic ${formatMs(snapshot.host_time_monotonic_ms)}`;
  for (const entry of uavs) {
    renderUav(cardFor(entry.uav_id), entry);
  }
}

function renderUav(card, entry) {
  const status = entry.status || {};
  const host = status.host || {};
  const flight = status.flight || {};
  const battery = status.battery || {};
  const rc = status.rc || {};
  const odom = status.odom || {};
  const link = status.link || {};
  const nodes = status.nodes || {};
  const system = status.system || {};

  const statusLevel = worstLevel([host.status_level, host.image_level, host.odom_level]);
  setLevel(card.querySelector(".status-dot"), statusLevel);
  card.querySelector(".mode-pill").textContent = flight.mode || "NO MODE";
  card.querySelector(".battery").textContent = formatBattery(battery);
  card.querySelector(".flight").textContent = formatFlight(flight);
  card.querySelector(".rc").textContent = rc.rssi == null ? "missing" : `${rc.rssi}`;
  card.querySelector(".odom").textContent = `${formatMs(odom.freshness_ms)} / ${formatHz(odom.hz)}`;
  card.querySelector(".image-age").textContent = formatMs(host.image_age_ms);
  card.querySelector(".ekf-delay").textContent = "未直接测量";
  card.querySelector(".cpu").textContent = formatPercent(system.cpu_percent);
  card.querySelector(".ram").textContent = formatPercent(system.ram_percent);
  card.querySelector(".eth-rx").textContent = formatBps(link.network_rx_bps);
  card.querySelector(".eth-tx").textContent = formatBps(link.network_tx_bps);
  card.querySelector(".status-rx").textContent = formatBps(host.status_rx_bps);
  card.querySelector(".image-rx").textContent = formatBps(host.image_rx_bps);

  ensureImageLoop(card, entry);

  const row = card.querySelector(".node-row");
  row.replaceChildren();
  for (const [name, live] of Object.entries(nodes)) {
    const pill = document.createElement("span");
    pill.className = `node-pill ${live ? "ok" : "error"}`;
    pill.textContent = name;
    row.appendChild(pill);
  }
}

function setLevel(node, level) {
  node.classList.remove("ok", "warn", "error", "missing");
  node.classList.add(level || "missing");
}

function worstLevel(levels) {
  if (levels.includes("error")) return "error";
  if (levels.includes("warn")) return "warn";
  if (levels.includes("missing")) return "missing";
  return "ok";
}

function formatBattery(value) {
  const percent = value.percent;
  const voltage = value.voltage;
  if (percent == null && voltage == null) return "missing";
  const parts = [];
  if (percent != null) parts.push(`${Math.round(percent * 100)}%`);
  if (voltage != null) parts.push(`${Number(voltage).toFixed(2)}V`);
  return parts.join(" ");
}

function formatFlight(value) {
  const armed = value.armed === true ? "ARM" : value.armed === false ? "DISARM" : "UNK";
  const manual = value.manual_input === true ? "RC" : "NO RC";
  const killed = value.killed === true ? "KILLED" : "";
  return [armed, manual, killed].filter(Boolean).join(" ");
}

function formatMs(value) {
  if (value == null) return "missing";
  if (value >= 1000) return `${(value / 1000).toFixed(1)}s`;
  return `${Math.round(value)}ms`;
}

function formatHz(value) {
  if (value == null) return "missing";
  return `${Number(value).toFixed(1)}Hz`;
}

function formatBps(value) {
  if (value == null) return "missing";
  const units = ["B/s", "KB/s", "MB/s"];
  let amount = Number(value);
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatPercent(value) {
  if (value == null) return "missing";
  return `${Number(value).toFixed(1)}%`;
}

function ensureImageLoop(card, entry) {
  const image = card.querySelector(".video");
  const empty = card.querySelector(".video-empty");
  const url = `/api/mjpeg/${entry.uav_id}`;
  if (!entry.has_image || !url) {
    image.removeAttribute("src");
    image.classList.remove("has-image");
    empty.style.display = "grid";
    card.dataset.imageLoop = "";
    return;
  }
  empty.style.display = "none";
  image.classList.add("has-image");
  if (card.dataset.imageLoop === "1") return;
  card.dataset.imageLoop = "1";
  image.src = url;
  image.onerror = () => {
    card.dataset.imageLoop = "";
    setTimeout(() => ensureImageLoop(card, entry), 1000);
  };
}

function startEvents() {
  if (!window.EventSource) {
    startPolling();
    return;
  }
  const events = new EventSource("/api/events");
  events.onmessage = (event) => render(JSON.parse(event.data));
  events.onerror = () => {
    events.close();
    startPolling();
  };
}

function startPolling() {
  async function tick() {
    try {
      const response = await fetch("/api/snapshot", { cache: "no-store" });
      render(await response.json());
    } catch (error) {
      hostStatus.textContent = "connection lost";
    }
  }
  tick();
  setInterval(tick, 1000);
}

startEvents();
