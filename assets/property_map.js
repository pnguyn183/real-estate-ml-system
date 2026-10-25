/* HomeNest map: local Leaflet engine, bounded fallback, no invented location. */
(() => {
  "use strict";
  const config = JSON.parse(document.getElementById("homenest-map-config").textContent);
  const root = document.getElementById("homenest-map-root");
  const message = document.getElementById("map-message");
  const messageText = document.getElementById("map-message-text");
  const retryButton = document.getElementById("map-retry");
  const homeButton = document.getElementById("map-home");
  const map = L.map("homenest-map", { zoomControl: false, minZoom: 2, maxZoom: 19,
    scrollWheelZoom: false, attributionControl: true });
  L.control.zoom({ zoomInTitle: "Phóng to", zoomOutTitle: "Thu nhỏ" }).addTo(map);
  L.control.scale({ imperial: false }).addTo(map);
  const home = () => {
    if (config.point) map.setView(config.point, config.area ? 13 : 16);
    else map.fitBounds(config.bounds, { padding: [12, 12] });
  };
  home();
  const pin = L.divIcon({ className: "hn-map-pin", iconSize: [32, 43], iconAnchor: [16, 42],
    tooltipAnchor: [0, -35], html: '<svg viewBox="0 0 32 43" width="32" height="43" aria-hidden="true">' +
      '<path d="M16 1C7.7 1 1 7.6 1 15.8c0 10.7 15 25.7 15 25.7s15-15 15-25.7C31 7.6 24.3 1 16 1Z" fill="#a84a21" stroke="white" stroke-width="2"/>' +
      '<circle cx="16" cy="15.5" r="5.5" fill="white"/></svg>' });
  const marker = config.point ? L.marker(config.point, { icon: pin,
    title: "Vị trí bất động sản", alt: "Vị trí bất động sản" }).addTo(map)
    .bindTooltip(config.point.map(n => n.toFixed(6)).join(", ")) : null;
  const diagnostics = window.HomeNestMap = { map, marker, state: "loading",
    providerIndex: -1, currentProvider: null, loadedTiles: 0, failedTiles: 0, attempts: [] };
  let layer = null;
  let generation = 0;
  let timer = null;
  let fallbackTimer = null;

  const status = (state, text, canRetry = false) => {
    root.dataset.state = diagnostics.state = state;
    messageText.textContent = text;
    message.hidden = state === "ready";
    retryButton.hidden = !canRetry;
  };
  const stopTimers = () => {
    clearTimeout(timer);
    clearTimeout(fallbackTimer);
    timer = fallbackTimer = null;
  };

  function useProvider(index) {
    stopTimers();
    const token = ++generation;
    if (layer) {
      map.removeLayer(layer);
      // Leaflet's attribution control listens for the layer's remove event.
      // Let it clean up its credit before removing our obsolete listeners.
      layer.off();
      layer = null;
    }
    diagnostics.providerIndex = index;
    diagnostics.loadedTiles = diagnostics.failedTiles = 0;
    if (index >= config.providers.length) {
      diagnostics.currentProvider = null;
      status("error", "Chưa tải được nền bản đồ. Hãy kiểm tra kết nối rồi thử lại. Bạn vẫn có thể mở vị trí bằng các liên kết bên dưới.", true);
      return;
    }
    const provider = config.providers[index];
    diagnostics.currentProvider = provider.name;
    diagnostics.attempts.push(provider.name);
    status("loading", index ? "Đang tải bản đồ từ nguồn dự phòng…" : "Đang tải bản đồ…");
    let loaded = 0;
    let failed = 0;
    let switching = false;
    const current = () => token === generation;
    const next = () => {
      if (!current() || switching) return;
      switching = true;
      // Let Leaflet finish dispatching events before removing this layer.
      fallbackTimer = setTimeout(() => { if (current()) useProvider(index + 1); }, 0);
    };
    const watch = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (!current()) return;
        if (!loaded) next();
        else status("partial", "Một phần bản đồ chưa tải xong. Bạn có thể thử tải lại.", true);
      }, config.timeoutMs);
    };
    layer = L.tileLayer(provider.url, { minZoom: 2, maxZoom: provider.maxZoom,
      maxNativeZoom: provider.maxNativeZoom, attribution: provider.attribution,
      keepBuffer: 1, noWrap: true });
    layer.on("loading", () => {
      if (!current()) return;
      loaded = failed = 0;
      diagnostics.loadedTiles = diagnostics.failedTiles = 0;
      status("loading", "Đang tải bản đồ…");
      watch();
    });
    layer.on("tileload", () => {
      if (!current()) return;
      diagnostics.loadedTiles = ++loaded;
    });
    layer.on("tileerror", () => {
      if (!current()) return;
      diagnostics.failedTiles = ++failed;
      if (failed >= 3 && !loaded) next();
    });
    layer.on("load", () => {
      if (!current()) return;
      clearTimeout(timer);
      if (!loaded) next();
      else if (failed) status("partial", "Một phần bản đồ chưa tải được. Hãy thử tải lại để xem đầy đủ.", true);
      else status("ready", "Bản đồ đã tải xong.");
    });
    watch();
    layer.addTo(map);
  }
  diagnostics.retry = retryButton.onclick = () => {
    diagnostics.attempts = [];
    useProvider(0);
  };
  homeButton.onclick = home;
  homeButton.title = homeButton.ariaLabel = config.point ? "Về vị trí đã chọn" : "Xem toàn Việt Nam";
  const observer = new ResizeObserver(() => map.invalidateSize({ pan: false }));
  observer.observe(root);
  window.addEventListener("pagehide", () => { stopTimers(); observer.disconnect(); map.remove(); }, { once: true });
  useProvider(0);
})();
