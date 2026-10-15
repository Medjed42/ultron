/* ULTRON Workspace — result components.
   Each renderer takes a validated component {id, kind, data} and a context
   {lang, conversationId, messageId, inPanel} and returns a DOM node. The
   backend decides WHAT to show; these trusted renderers decide HOW. */
(function () {
  "use strict";
  const W = window.W;
  const { el, icon, button, fmt } = W;

  // ── maps ────────────────────────────────────────────────────────────────
  const live = new Set();
  const observer = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
    entries.forEach((e) => { if (e.isIntersecting && e.target.__init) { const f = e.target.__init; e.target.__init = null; observer.unobserve(e.target); f(); } });
  }, { rootMargin: "200px" }) : null;

  W.maps = {
    available: () => typeof window.L !== "undefined",
    /** Creates a Leaflet map when the container first scrolls into view. */
    lazy(container, build) {
      if (!W.maps.available()) {
        container.appendChild(el("div", { class: "map-fallback" }, icon("map", 22),
          el("span", { text: W.t("Map library unavailable.", "Biblioteka mape nije dostupna.") })));
        return;
      }
      const init = () => {
        const map = L.map(container, { zoomControl: false, attributionControl: true, scrollWheelZoom: true,
          zoomAnimation: !W.reducedMotion(), fadeAnimation: !W.reducedMotion(), markerZoomAnimation: !W.reducedMotion() });
        L.control.zoom({ position: "bottomright" }).addTo(map);
        // OpenStreetMap's own tiles (tile usage policy: identify the app,
        // keep volume light, credit OSM). Darkened in CSS to match the UI.
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19, referrerPolicy: "strict-origin-when-cross-origin", crossOrigin: false,
          attribution: "© OpenStreetMap contributors",
        }).addTo(map);
        map.attributionControl.setPrefix("");
        live.add({ container, map });
        container.__map = map;
        build(map);
        setTimeout(() => map.invalidateSize(), 60);
      };
      if (observer) { container.__init = init; observer.observe(container); } else init();
    },
    sweep() { live.forEach((m) => { if (!m.container.isConnected) { try { m.map.remove(); } catch (e) { /* gone */ } live.delete(m); } }); },
    refresh() { live.forEach((m) => { if (m.container.isConnected) m.map.invalidateSize(); }); },
    marker(lat, lon, node, opts) {
      return L.marker([lat, lon], Object.assign({ icon: L.divIcon({ html: node, className: "mk-wrap", iconSize: null }), keyboard: true }, opts || {}));
    },
    fit(map, points, maxZoom) {
      const pts = points.filter((p) => p && isFinite(p[0]) && isFinite(p[1]));
      if (!pts.length) return;
      if (pts.length === 1) { map.setView(pts[0], maxZoom || 14); return; }
      map.fitBounds(L.latLngBounds(pts), { padding: [36, 36], maxZoom: maxZoom || 16, animate: !W.reducedMotion() });
    },
  };

  function pinNode(cls, label, title) {
    return el("div", { class: "mk " + cls, title: title || "", "aria-label": title || label }, el("span", { text: label }));
  }

  // ── shared bits ─────────────────────────────────────────────────────────
  function T(ctx) { return (en, sr) => (ctx.lang === "sr" ? sr : en); }
  function cardShell(kind, iconName, title, subtitle, actions) {
    const head = el("div", { class: "card-head" },
      el("div", { class: "card-ico" }, icon(iconName, 18)),
      el("div", { class: "card-titles" }, el("div", { class: "card-title", text: title }),
        subtitle ? el("div", { class: "card-sub", text: subtitle }) : null),
      el("div", { class: "card-actions" }, actions || []));
    return el("section", { class: "rcard rcard-" + kind, "aria-label": title }, head);
  }
  function save(kind, title, data, source) {
    W.api("saved.add", { kind, title, data, source }).then((r) =>
      W.toast(r.ok ? W.t("Saved to Saved Results", "Sačuvano u Sačuvane rezultate") : (r.error || "Not saved"), r.ok ? "success" : "error"));
  }
  function action(ctx, comp, name, args) {
    return W.api("component.action", { conversation_id: ctx.conversationId, message_id: ctx.messageId,
      component_id: comp.id, action: name, args });
  }
  function busy(node, on, label) {
    node.classList.toggle("is-busy", !!on);
    let ov = node.querySelector(":scope > .busy-overlay");
    if (on && !ov) node.appendChild(el("div", { class: "busy-overlay", role: "status" }, el("div", { class: "spinner" }), el("span", { text: label || "" })));
    if (!on && ov) ov.remove();
  }
  W.cardBusy = busy;
  function expandable(card, mapBox) {
    return button(W.t("Expand", "Proširi"), { icon: "expand", iconOnly: true, kind: "ghost", small: true,
      onClick: (e) => {
        const on = !card.classList.contains("is-full");
        card.classList.toggle("is-full", on);
        document.body.classList.toggle("has-fullcard", on);
        e.currentTarget.replaceChildren(icon(on ? "shrink" : "expand", 16));
        setTimeout(() => { if (mapBox && mapBox.__map) mapBox.__map.invalidateSize(); }, 220);
      } });
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { const f = W.$(".rcard.is-full"); if (f) { f.classList.remove("is-full"); document.body.classList.remove("has-fullcard"); W.maps.refresh(); } }
  });
  function sourceLine(text) { return el("div", { class: "src-line" }, icon("database", 13), el("span", { text })); }

  // ── RouteMapCard ────────────────────────────────────────────────────────
  const MODE = { driving: ["car", "Car", "Kolima"], walking: ["walk", "Walk", "Peške"], cycling: ["bike", "Bike", "Bicikl"], transit: ["bus", "Transit", "Prevoz"] };

  function routeMap(comp, ctx) {
    const d = comp.data, t = T(ctx);
    const o = d.origin || {}, de = d.destination || {};
    const mapBox = el("div", { class: "map " + (ctx.inPanel ? "map-tall" : "") });
    let selected = 0;
    const card = cardShell("route", "route", `${o.name || "?"} → ${de.name || "?"}`, d.provider);
    const stats = el("div", { class: "stats" });
    const modes = el("div", { class: "seg", role: "radiogroup", "aria-label": t("Travel mode", "Način putovanja") });
    const alts = el("div", { class: "alts" });
    let layers = [];

    function drawStats() {
      W.clear(stats);
      const r = d.routes[selected];
      if (!r) { stats.appendChild(el("div", { class: "muted", text: t("No route data.", "Nema podataka o ruti.") })); return; }
      stats.append(
        el("div", { class: "stat" }, el("div", { class: "stat-k", text: t("Distance", "Razdaljina") }), el("div", { class: "stat-v", text: fmt.km(r.distance_m, ctx.lang) })),
        el("div", { class: "stat" }, el("div", { class: "stat-k", text: t("Travel time", "Vreme putovanja") }), el("div", { class: "stat-v", text: fmt.dur(r.duration_s) })),
        el("div", { class: "stat" }, el("div", { class: "stat-k", text: t("Mode", "Prevoz") }),
          el("div", { class: "stat-v stat-mode" }, icon(MODE[d.mode][0], 18), el("span", { text: t(MODE[d.mode][1], MODE[d.mode][2]) }))));
    }
    function drawModes() {
      const avail = d.available_modes && d.available_modes.length ? d.available_modes : ["driving", "walking", "cycling"];
      ["driving", "walking", "cycling", "transit"].forEach((m) => {
        const ok = avail.includes(m);
        const b = el("button", { type: "button", class: "seg-btn" + (m === d.mode ? " on" : ""), role: "radio",
          "aria-checked": String(m === d.mode), disabled: !ok,
          title: ok ? t(MODE[m][1], MODE[m][2]) : t("Public transport needs Google Routes (MCP Hub → Credentials)", "Javni prevoz zahteva Google Routes (MCP Hub → Kredencijali)"),
          onclick: () => {
            if (m === d.mode) return;
            busy(card, true, t("Calculating route…", "Računam rutu…"));
            action(ctx, comp, "route.mode", { mode: m }).then((r) => { if (!r.ok) { busy(card, false); W.toast(r.error || "Failed", "error"); } });
          } }, icon(MODE[m][0], 16), el("span", { text: t(MODE[m][1], MODE[m][2]) }));
        modes.appendChild(b);
      });
    }
    function drawAlts() {
      W.clear(alts);
      if (d.routes.length < 2) return;
      d.routes.forEach((r, i) => {
        alts.appendChild(el("button", { type: "button", class: "alt" + (i === selected ? " on" : ""), "aria-pressed": String(i === selected),
          onclick: () => { selected = i; drawStats(); drawAlts(); paint(mapBox.__map); } },
          el("span", { class: "alt-dot" }), el("span", { class: "alt-main", text: (i === 0 ? t("Fastest", "Najbrža") : t("Alternative", "Alternativa") + " " + i) + (r.summary ? " · " + r.summary : "") }),
          el("span", { class: "alt-v", text: fmt.km(r.distance_m, ctx.lang) + " · " + fmt.dur(r.duration_s) })));
      });
    }
    function paint(map) {
      if (!map) return;
      layers.forEach((l) => map.removeLayer(l)); layers = [];
      const pts = [];
      d.routes.forEach((r, i) => {
        if (!r.geometry || r.geometry.length < 2) return;
        if (i !== selected) {
          const alt = L.polyline(r.geometry, { color: "#5b7fb8", weight: 4, opacity: 0.55, dashArray: "6 8" }).addTo(map);
          alt.on("click", () => { selected = i; drawStats(); drawAlts(); paint(map); });
          layers.push(alt);
        }
      });
      const r = d.routes[selected];
      if (r && r.geometry && r.geometry.length > 1) {
        layers.push(L.polyline(r.geometry, { color: "#21D4FD", weight: 11, opacity: 0.16 }).addTo(map));
        layers.push(L.polyline(r.geometry, { color: "#258CFF", weight: 5, opacity: 0.95 }).addTo(map));
        layers.push(L.polyline(r.geometry, { color: "#bff3ff", weight: 1.5, opacity: 0.8 }).addTo(map));
        r.geometry.forEach((p) => pts.push(p));
      }
      if (o.lat !== undefined) { layers.push(W.maps.marker(o.lat, o.lon, pinNode("mk-a", "A", o.name)).addTo(map)); pts.push([o.lat, o.lon]); }
      if (de.lat !== undefined) { layers.push(W.maps.marker(de.lat, de.lon, pinNode("mk-b", "B", de.name)).addTo(map)); pts.push([de.lat, de.lon]); }
      W.maps.fit(map, pts);
    }

    drawStats(); drawModes(); drawAlts();
    const actions = card.querySelector(".card-actions");
    actions.append(
      button(t("Save route", "Sačuvaj rutu"), { icon: "bookmark", iconOnly: true, kind: "ghost", small: true,
        onClick: () => save("route", `${o.name} → ${de.name}`, { origin: o, destination: de, mode: d.mode,
          distance_m: (d.routes[0] || {}).distance_m, duration_s: (d.routes[0] || {}).duration_s, provider: d.provider, open_url: d.open_url }, d.provider) }),
      expandable(card, mapBox));
    card.append(stats, modes, mapBox, alts,
      el("div", { class: "card-foot" },
        el("div", { class: "foot-notes" }, (d.notes || []).map((n) => el("div", { class: "note", text: n })),
          sourceLine(t("Route data: ", "Podaci o ruti: ") + d.provider + (d.fetched_at ? " · " + d.fetched_at.slice(11, 16) : ""))),
        d.open_url ? button(t("Open in Google Maps", "Otvori u Google Maps"), { icon: "external", kind: "primary", small: true, onClick: () => W.open(d.open_url) }) : null));
    W.maps.lazy(mapBox, paint);
    return card;
  }

  // ── PlaceResultsPanel ───────────────────────────────────────────────────
  const CAT_ICON = { restaurant: "utensils", cafe: "coffee", bar: "coffee", fast_food: "utensils", parking: "parking",
    fuel: "fuel", pharmacy: "pill", hospital: "plus", atm: "euro", supermarket: "cart", attraction: "landmark", charging: "zap" };

  function placeResults(comp, ctx) {
    const d = comp.data, t = T(ctx);
    const center = d.center || {};
    const label = (d.label && (ctx.lang === "sr" ? d.label.sr : d.label.en)) || center.name || "";
    const title = d.category ? `${label} · ${center.name || ""}` : (center.name || label);
    const card = cardShell("places", CAT_ICON[d.category] || "pin", title,
      d.category ? `${d.places.length} · ${d.provider} · ${t("straight-line distances", "vazdušne udaljenosti")}` : (center.label || d.provider));
    const mapBox = el("div", { class: "map " + (ctx.inPanel ? "map-tall" : "map-mid") });
    const list = el("ol", { class: "plist" });
    const markers = new Map();
    function focus(i) {
      W.$$(".pitem", list).forEach((n, j) => n.classList.toggle("on", j === i));
      const m = markers.get(i); if (m && mapBox.__map) { mapBox.__map.setView(m.getLatLng(), Math.max(mapBox.__map.getZoom(), 16), { animate: !W.reducedMotion() }); }
    }
    d.places.forEach((p, i) => {
      const meta = [p.cuisine, p.opening_hours].filter(Boolean).join(" · ");
      list.appendChild(el("li", { class: "pitem", tabindex: "0", onclick: () => focus(i), onkeydown: (e) => { if (e.key === "Enter") focus(i); } },
        el("span", { class: "pnum", text: String(i + 1) }),
        el("div", { class: "pmain" }, el("div", { class: "pname", text: p.name }),
          el("div", { class: "pmeta", text: [fmt.km(p.distance_m, ctx.lang), p.address, meta].filter(Boolean).join(" · ") })),
        el("div", { class: "pacts" },
          p.website ? button(t("Website", "Sajt"), { icon: "globe", iconOnly: true, kind: "ghost", small: true, onClick: (e) => { e.stopPropagation(); W.open(p.website); } }) : null,
          button(t("Open in Maps", "Otvori u mapama"), { icon: "external", iconOnly: true, kind: "ghost", small: true, onClick: (e) => { e.stopPropagation(); W.open(p.maps_url); } }),
          button(t("Save", "Sačuvaj"), { icon: "bookmark", iconOnly: true, kind: "ghost", small: true, onClick: (e) => { e.stopPropagation(); save("place", p.name, p, d.provider); } }))));
    });
    card.querySelector(".card-actions").append(expandable(card, mapBox));
    card.append(mapBox);
    if (d.places.length) card.append(list);
    card.append(el("div", { class: "card-foot" }, sourceLine(d.provider + (d.category ? " · © OpenStreetMap contributors" : ""))));
    W.maps.lazy(mapBox, (map) => {
      const pts = [];
      if (center.lat !== undefined) { W.maps.marker(center.lat, center.lon, pinNode("mk-b", "●", center.name)).addTo(map); pts.push([center.lat, center.lon]); }
      d.places.forEach((p, i) => {
        const m = W.maps.marker(p.lat, p.lon, pinNode("mk-dot", String(i + 1), p.name)).addTo(map);
        m.on("click", () => { focus(i); const n = list.children[i]; if (n) n.scrollIntoView({ block: "nearest", behavior: W.reducedMotion() ? "auto" : "smooth" }); });
        markers.set(i, m); pts.push([p.lat, p.lon]);
      });
      W.maps.fit(map, pts, d.places.length ? 16 : 12);
    });
    return card;
  }

  // ── AccommodationResultsPanel ───────────────────────────────────────────
  const AMEN = { wifi: ["wifi", "Wi-Fi", "Wi-Fi"], parking: ["parking", "Parking", "Parking"], kitchen: ["kitchen", "Kitchen", "Kuhinja"],
    ac: ["snow", "Air conditioning", "Klima"], tv: ["tv", "TV", "TV"], washer: ["washer", "Washer", "Veš mašina"],
    balcony: ["balcony", "Balcony", "Terasa"], elevator: ["building", "Elevator", "Lift"], pets: ["paw", "Pets", "Ljubimci"],
    bathroom: ["bath", "Bathroom", "Kupatilo"], heating: ["heat", "Heating", "Grejanje"], breakfast: ["coffee", "Breakfast", "Doručak"],
    pool: ["sparkles", "Pool", "Bazen"], jacuzzi: ["sparkles", "Jacuzzi", "Đakuzi"] };
  const TYPE = { apartment: ["Apartment", "Apartman"], room: ["Room", "Soba"], house: ["House", "Kuća"], hotel: ["Hotel", "Hotel"],
    guest_house: ["Guest house", "Pansion"], hostel: ["Hostel", "Hostel"], other: ["Other", "Ostalo"] };
  const STATUS = { verified: ["Verified price", "Potvrđena cena", "ok"], indicative: ["Indicative price", "Indikativna cena", "warn"],
    unavailable: ["No price published", "Cena nije objavljena", "mute"] };

  function applyFilters(list, f) {
    return list.filter((x) => {
      if (f.max_night != null && (x.night_price == null || x.night_price > f.max_night + 1e-9)) return false;
      if (f.max_total != null && (x.total == null || x.total > f.max_total + 1e-9)) return false;
      if (f.min_rating != null) { if (x.rating == null || (x.rating / (x.rating_scale || 5) * 5) < f.min_rating) return false; }
      if (f.types && f.types.length && !f.types.includes(x.property_type)) return false;
      if (f.amenities && f.amenities.length && !f.amenities.every((a) => (x.amenities || []).includes(a))) return false;
      if (f.max_distance_m != null && (x.distance_center_m == null || x.distance_center_m > f.max_distance_m)) return false;
      if (f.free_cancellation && !/free cancellation/i.test(x.cancellation || "")) return false;
      if (f.priced_only && x.price_status === "unavailable") return false;
      return true;
    });
  }
  function sortList(list, sort) {
    const rank = { verified: 0, indicative: 1, unavailable: 2 };
    const by = {
      price: (a, b) => (rank[a.price_status] - rank[b.price_status]) || ((a.total == null) - (b.total == null)) || ((a.total || 0) - (b.total || 0)),
      distance: (a, b) => ((a.distance_center_m == null) - (b.distance_center_m == null)) || ((a.distance_center_m || 0) - (b.distance_center_m || 0)),
      landmark: (a, b) => ((a.distance_landmark_m == null) - (b.distance_landmark_m == null)) || ((a.distance_landmark_m || 0) - (b.distance_landmark_m || 0)),
      rating: (a, b) => ((b.rating || 0) / (b.rating_scale || 5)) - ((a.rating || 0) / (a.rating_scale || 5)),
    }[sort] || (() => 0);
    return list.slice().sort(by);
  }

  function propertyCard(x, ctx, opts) {
    const t = T(ctx);
    const st = STATUS[x.price_status] || STATUS.unavailable;
    const media = el("div", { class: "prop-media" });
    if (x.image) {
      const img = el("img", { src: x.image, alt: x.title, loading: "lazy", decoding: "async", referrerpolicy: "no-referrer" });
      img.addEventListener("error", () => { img.remove(); media.classList.add("noimg"); media.appendChild(icon("building", 30)); });
      media.appendChild(img);
    } else { media.classList.add("noimg"); media.appendChild(icon(x.property_type === "hotel" ? "building" : "home", 30)); }
    if ((x.flags || []).includes("lowest_verified")) media.appendChild(el("span", { class: "badge badge-best" }, icon("sparkles", 13), el("span", { text: t("Lowest price", "Najniža cena") })));
    else if ((x.flags || []).includes("lowest_indicative")) media.appendChild(el("span", { class: "badge badge-best soft" }, icon("sparkles", 13), el("span", { text: t("Lowest indicative price", "Najniža indikativna cena") })));
    media.appendChild(el("span", { class: "badge badge-src", text: x.provider_name }));

    const meta = [];
    meta.push(el("span", { class: "chip-s" }, icon(x.property_type === "hotel" ? "building" : "home", 13), el("span", { text: t(...(TYPE[x.property_type] || TYPE.other)) })));
    if (x.capacity) meta.push(el("span", { class: "chip-s", title: t("Capacity", "Kapacitet") }, icon("users", 13), el("span", { text: String(x.capacity) })));
    if (x.beds) meta.push(el("span", { class: "chip-s", title: t("Beds", "Ležaja") }, icon("bed", 13), el("span", { text: String(x.beds) })));
    if (x.rating != null) meta.push(el("span", { class: "chip-s", title: t("Guest rating", "Ocena gostiju") }, icon("star", 13),
      el("span", { text: `${x.rating.toFixed(1)}/${x.rating_scale || 5}` + (x.review_count ? ` (${x.review_count})` : "") })));
    else if (x.stars) meta.push(el("span", { class: "chip-s stars", title: t("Category as advertised", "Kategorija iz oglasa"), text: "★".repeat(Math.round(x.stars)) }));

    const price = el("div", { class: "prop-price" });
    if (x.night_price != null) {
      price.append(el("div", { class: "pp-main" }, el("span", { class: "pp-v", text: fmt.money(x.night_price, x.total_currency, ctx.lang) }),
        el("span", { class: "pp-u", text: t("/ night", "/ noć") })),
        el("div", { class: "pp-total", text: t("Total ", "Ukupno ") + fmt.money(x.total, x.total_currency, ctx.lang) +
          (x.price_basis === "from" ? t(" · price from", " · cena od") : "") }));
    } else price.append(el("div", { class: "pp-none", text: t("No published price", "Bez objavljene cene") }));
    price.append(el("span", { class: "pill pill-" + st[2], title: x.price_status === "indicative" ? t("Advertised “price from”; not a quote for your dates", "Oglašena „cena od”; nije ponuda za vaše datume") : "" }, el("span", { text: t(st[0], st[1]) })));

    const dist = [];
    if (x.distance_center_m != null) dist.push(t("Centre ", "Centar ") + fmt.km(x.distance_center_m, ctx.lang));
    if (x.distance_landmark_m != null && opts.landmark) dist.push((opts.landmark.name || t("Landmark", "Lokacija")) + " " + fmt.km(x.distance_landmark_m, ctx.lang));

    const amen = el("div", { class: "amen" }, (x.amenities || []).filter((a) => AMEN[a]).slice(0, 8).map((a) =>
      el("span", { class: "amen-i", title: t(AMEN[a][1], AMEN[a][2]), "aria-label": t(AMEN[a][1], AMEN[a][2]) }, icon(AMEN[a][0], 14))));

    const node = el("article", { class: "prop", tabindex: "0", "aria-label": x.title, dataset: { id: x.id },
      onclick: (e) => { if (!e.target.closest("button")) opts.onSelect && opts.onSelect(x); },
      onkeydown: (e) => { if (e.key === "Enter" && !e.target.closest("button")) opts.onSelect && opts.onSelect(x); } },
      media,
      el("div", { class: "prop-body" },
        el("h4", { class: "prop-title", text: x.title }),
        el("div", { class: "prop-addr" }, icon("pin", 13), el("span", { text: x.address || x.city || "" })),
        el("div", { class: "prop-meta" }, meta),
        amen,
        el("div", { class: "prop-avail" + (x.availability === "available" ? " ok" : ""),
          text: x.availability === "available" ? t("Offer available for your dates", "Ponuda dostupna za vaše datume")
            : t("Availability for your dates not confirmed", "Dostupnost za vaše datume nije potvrđena") }),
        dist.length ? el("div", { class: "prop-dist" }, icon("target", 13), el("span", { text: dist.join(" · ") + t(" (straight line)", " (vazdušno)") })) : null,
        (x.also_on || []).length ? el("div", { class: "prop-also", text: t("Also on: ", "Takođe na: ") + x.also_on.map((a) => a.provider).join(", ") }) : null),
      el("div", { class: "prop-side" }, price,
        el("div", { class: "prop-acts" },
          x.url ? button(t("View property", "Pogledaj oglas"), { icon: "external", kind: "primary", small: true, onClick: () => W.open(x.url) }) : null,
          x.lat != null ? button(t("Show on map", "Na mapi"), { icon: "map", kind: "ghost", small: true, onClick: () => opts.onMap && opts.onMap(x) }) : null,
          button(t("Save", "Sačuvaj"), { icon: "bookmark", iconOnly: true, kind: "ghost", small: true,
            onClick: () => save("property", x.title, x, x.provider_name) }),
          x.address ? button(t("Copy address", "Kopiraj adresu"), { icon: "copy", iconOnly: true, kind: "ghost", small: true, onClick: () => W.copy(x.address) }) : null)));
    return node;
  }
  W.propertyCard = propertyCard;

  function stayResults(comp, ctx) {
    const d = comp.data, t = T(ctx), q = d.query;
    const listings = d.listings || [];
    const state = {
      // The context panel shows the map beside the thread, so cards in the
      // thread start as a list; without the panel (narrow screens) too.
      view: ctx.inPanel ? "split" : (window.innerWidth >= 1280 || window.innerWidth < 760 ? "list" : "split"),
      sort: d.sort || "price",
      filters: Object.assign({}, d.filters || {}),
      selected: null, showFilters: false,
    };
    const answered = (d.answered || []).map((a) => `${a.name} ${a.count}`).join(" · ");
    const card = cardShell("stays", "bed", t("Stays in ", "Smeštaj: ") + q.city,
      answered || t("Filtered from the previous search", "Filtrirano iz prethodne pretrage"));

    // summary / query editor
    const ci = el("input", { type: "date", class: "inp inp-date", value: q.check_in, "aria-label": t("Check-in", "Dolazak") });
    const co = el("input", { type: "date", class: "inp inp-date", value: q.check_out, "aria-label": t("Check-out", "Odlazak") });
    let guests = q.guests;
    const gVal = el("span", { class: "stepper-v", text: String(guests), "aria-live": "polite" });
    const apply = button(t("Update search", "Ažuriraj pretragu"), { icon: "refresh", kind: "primary", small: true, class: "hidden",
      onClick: () => {
        if (co.value <= ci.value) { W.toast(t("Check-out must be after check-in.", "Odlazak mora biti posle dolaska."), "error"); return; }
        busy(card, true, t("Searching all sources…", "Pretražujem sve izvore…"));
        action(ctx, comp, "stays.update", { check_in: ci.value, check_out: co.value, guests }).then((r) => {
          if (!r.ok) { busy(card, false); W.toast(r.error || "Failed", "error"); } });
      } });
    const dirty = () => apply.classList.toggle("hidden", ci.value === q.check_in && co.value === q.check_out && guests === q.guests);
    ci.addEventListener("change", dirty); co.addEventListener("change", dirty);
    const stepper = el("div", { class: "stepper", role: "group", "aria-label": t("Guests", "Gosti") },
      button("−", { kind: "ghost", small: true, aria: t("Fewer guests", "Manje gostiju"), onClick: () => { guests = Math.max(1, guests - 1); gVal.textContent = guests; dirty(); } }),
      gVal,
      button("+", { kind: "ghost", small: true, aria: t("More guests", "Više gostiju"), onClick: () => { guests = Math.min(16, guests + 1); gVal.textContent = guests; dirty(); } }));
    const sortSel = el("select", { class: "inp", "aria-label": t("Sort", "Sortiraj"), onchange: (e) => { state.sort = e.target.value; draw(); sync(); } },
      [["price", t("Lowest total price", "Najniža ukupna cena")], ["distance", t("Closest to centre", "Najbliže centru")],
        d.landmark ? ["landmark", t("Closest to ", "Najbliže: ") + (d.landmark.name || "")] : null,
        listings.some((x) => x.rating != null) ? ["rating", t("Best rated", "Najbolje ocenjeno")] : null]
        .filter(Boolean).map(([v, l]) => el("option", { value: v, text: l, selected: v === state.sort })));
    const summary = el("div", { class: "qbar" },
      el("label", { class: "qf" }, el("span", { class: "qk", text: t("Destination", "Mesto") }), el("span", { class: "qv", text: q.city })),
      el("label", { class: "qf" }, el("span", { class: "qk", text: t("Dates", "Datumi") }), el("span", { class: "qv qdates" }, ci, el("span", { text: "→" }), co)),
      el("div", { class: "qf" }, el("span", { class: "qk", text: t("Guests", "Gosti") }), stepper),
      el("label", { class: "qf" }, el("span", { class: "qk", text: t("Sort", "Redosled") }), sortSel),
      apply);
    const assumptions = (d.assumptions || []).length ? el("div", { class: "assume" }, icon("info", 14),
      el("span", { text: t("Assumed: ", "Pretpostavke: ") + d.assumptions.map((a) => ASSUME[a] ? t(...ASSUME[a]) : a).join(" · ") + t(" — edit above.", " — promenite iznad.") })) : null;

    // toolbar
    const views = el("div", { class: "seg seg-sm", role: "radiogroup", "aria-label": t("View", "Prikaz") });
    [["list", "list", t("List", "Lista")], ["map", "map", t("Map", "Mapa")], ["split", "split", t("Split", "Podeljeno")]].forEach(([v, ic, l]) => {
      views.appendChild(el("button", { type: "button", class: "seg-btn" + (state.view === v ? " on" : ""), role: "radio", "aria-checked": String(state.view === v),
        onclick: (e) => { state.view = v; W.$$(".seg-btn", views).forEach((b) => { b.classList.remove("on"); b.setAttribute("aria-checked", "false"); });
          e.currentTarget.classList.add("on"); e.currentTarget.setAttribute("aria-checked", "true"); layout(); } }, icon(ic, 15), el("span", { text: l })));
    });
    const fBtn = button(t("Filters", "Filteri"), { icon: "sliders", kind: "ghost", small: true, onClick: () => { state.showFilters = !state.showFilters; fPanel.classList.toggle("open", state.showFilters); fBtn.setAttribute("aria-expanded", String(state.showFilters)); } });
    fBtn.setAttribute("aria-expanded", "false");
    const count = el("span", { class: "count", "aria-live": "polite" });
    const toolbar = el("div", { class: "toolbar" }, views, fBtn, count);

    // filters (only those the data can actually satisfy)
    const fPanel = el("div", { class: "filters" });
    const priced = listings.filter((x) => x.night_price != null);
    if (priced.length) {
      const maxP = Math.ceil(Math.max(...priced.map((x) => x.night_price)));
      const cur = priced[0].total_currency;
      const val = el("span", { class: "fval" });
      const range = el("input", { type: "range", min: "0", max: String(maxP), step: String(maxP > 200 ? 10 : 1), class: "range",
        value: String(state.filters.max_night != null ? Math.min(state.filters.max_night, maxP) : maxP), "aria-label": t("Max price per night", "Maks. cena po noći") });
      const showVal = () => { val.textContent = Number(range.value) >= maxP && state.filters.max_night == null ? t("Any", "Bilo koja") : fmt.money(Number(range.value), cur, ctx.lang); };
      range.addEventListener("input", () => { state.filters.max_night = Number(range.value) >= maxP ? null : Number(range.value); showVal(); draw(); });
      range.addEventListener("change", sync);
      showVal();
      fPanel.appendChild(el("div", { class: "fgroup" }, el("div", { class: "fk" }, el("span", { text: t("Max price / night", "Maks. cena / noć") }), val), range));
    }
    const types = [...new Set(listings.map((x) => x.property_type))];
    if (types.length > 1) {
      const g = el("div", { class: "chips" });
      types.forEach((ty) => {
        const on = !state.filters.types || state.filters.types.includes(ty);
        g.appendChild(el("button", { type: "button", class: "chip" + (on ? " on" : ""), "aria-pressed": String(on), onclick: (e) => {
          const cur = state.filters.types ? state.filters.types.slice() : types.slice();
          const i = cur.indexOf(ty); if (i >= 0) cur.splice(i, 1); else cur.push(ty);
          state.filters.types = cur.length === types.length ? null : cur;
          e.currentTarget.classList.toggle("on"); e.currentTarget.setAttribute("aria-pressed", String(e.currentTarget.classList.contains("on"))); draw(); sync();
        } }, el("span", { text: t(...(TYPE[ty] || TYPE.other)) })));
      });
      fPanel.appendChild(el("div", { class: "fgroup" }, el("div", { class: "fk", text: t("Property type", "Tip smeštaja") }), g));
    }
    const amenAll = [...new Set(listings.flatMap((x) => x.amenities || []))].filter((a) => AMEN[a]);
    if (amenAll.length) {
      const g = el("div", { class: "chips" });
      amenAll.forEach((a) => {
        const on = (state.filters.amenities || []).includes(a);
        g.appendChild(el("button", { type: "button", class: "chip" + (on ? " on" : ""), "aria-pressed": String(on), onclick: (e) => {
          const cur = (state.filters.amenities || []).slice(); const i = cur.indexOf(a);
          if (i >= 0) cur.splice(i, 1); else cur.push(a);
          state.filters.amenities = cur; e.currentTarget.classList.toggle("on"); e.currentTarget.setAttribute("aria-pressed", String(i < 0)); draw(); sync();
        } }, icon(AMEN[a][0], 13), el("span", { text: t(AMEN[a][1], AMEN[a][2]) })));
      });
      fPanel.appendChild(el("div", { class: "fgroup" }, el("div", { class: "fk", text: t("Amenities (per listing)", "Sadržaji (prema oglasu)") }), g));
    }
    if (listings.some((x) => x.rating != null)) {
      const sel = el("select", { class: "inp", onchange: (e) => { state.filters.min_rating = e.target.value ? Number(e.target.value) : null; draw(); sync(); } },
        ["", "3", "3.5", "4", "4.5"].map((v) => el("option", { value: v, text: v ? "≥ " + v + " / 5" : t("Any", "Bilo koja") })));
      fPanel.appendChild(el("div", { class: "fgroup" }, el("div", { class: "fk", text: t("Minimum rating", "Minimalna ocena") }), sel));
    }
    const dists = listings.filter((x) => x.distance_center_m != null);
    if (dists.length) {
      const maxD = Math.ceil(Math.max(...dists.map((x) => x.distance_center_m)) / 500) * 500;
      const val = el("span", { class: "fval" });
      const range = el("input", { type: "range", min: "500", max: String(maxD), step: "500", value: String(state.filters.max_distance_m || maxD), class: "range", "aria-label": t("Max distance from centre", "Maks. udaljenost od centra") });
      const show = () => { val.textContent = Number(range.value) >= maxD ? t("Any", "Bilo koja") : fmt.km(Number(range.value), ctx.lang); };
      range.addEventListener("input", () => { state.filters.max_distance_m = Number(range.value) >= maxD ? null : Number(range.value); show(); draw(); });
      range.addEventListener("change", sync);
      show();
      fPanel.appendChild(el("div", { class: "fgroup" }, el("div", { class: "fk" }, el("span", { text: t("Distance from centre", "Udaljenost od centra") }), val), range));
    }
    const toggles = el("div", { class: "chips" });
    const pricedOnly = el("button", { type: "button", class: "chip" + (state.filters.priced_only ? " on" : ""), "aria-pressed": String(!!state.filters.priced_only),
      onclick: (e) => { state.filters.priced_only = !state.filters.priced_only; e.currentTarget.classList.toggle("on"); e.currentTarget.setAttribute("aria-pressed", String(state.filters.priced_only)); draw(); sync(); } },
      icon("euro", 13), el("span", { text: t("Only with a price", "Samo sa cenom") }));
    toggles.appendChild(pricedOnly);
    if (listings.some((x) => /free cancellation/i.test(x.cancellation || ""))) {
      toggles.appendChild(el("button", { type: "button", class: "chip" + (state.filters.free_cancellation ? " on" : ""),
        onclick: (e) => { state.filters.free_cancellation = !state.filters.free_cancellation; e.currentTarget.classList.toggle("on"); draw(); sync(); } },
        icon("check", 13), el("span", { text: t("Free cancellation (verified)", "Besplatno otkazivanje (potvrđeno)") })));
    }
    fPanel.appendChild(el("div", { class: "fgroup" }, el("div", { class: "fk", text: t("Show", "Prikaži") }), toggles));
    fPanel.appendChild(button(t("Reset filters", "Poništi filtere"), { kind: "ghost", small: true, icon: "refresh",
      onClick: () => { state.filters = {}; sync(); const fresh = stayResults(Object.assign({}, comp, { data: Object.assign({}, d, { filters: {}, sort: state.sort }) }), ctx); card.replaceWith(fresh); W.maps.sweep(); } }));

    // body
    const listBox = el("div", { class: "plist-stays", role: "list" });
    const mapBox = el("div", { class: "map map-stays" });
    const body = el("div", { class: "stays-body" }, listBox, mapBox);
    const markers = new Map();
    let mapRef = null;

    function select(x, fromMap) {
      state.selected = x.id;
      W.$$(".prop", listBox).forEach((n) => n.classList.toggle("on", n.dataset.id === x.id));
      markers.forEach((m, id) => { const node = m.getElement && m.getElement(); if (node) node.classList.toggle("active", id === x.id); });
      if (fromMap) { const n = W.$(`.prop[data-id="${CSS.escape(x.id)}"]`, listBox); if (n) n.scrollIntoView({ block: "nearest", behavior: W.reducedMotion() ? "auto" : "smooth" }); }
      else if (mapRef && x.lat != null) { mapRef.setView([x.lat, x.lon], Math.max(mapRef.getZoom(), 15), { animate: !W.reducedMotion() }); }
      if (ctx.conversationId) action(ctx, comp, "stays.select", { listing_id: x.id });
    }
    function draw() {
      const shown = sortList(applyFilters(listings, state.filters), state.sort);
      count.textContent = `${shown.length} / ${listings.length}`;
      W.clear(listBox);
      if (!shown.length) listBox.appendChild(el("div", { class: "empty-s" }, icon("filter", 20), el("span", { text: t("No results match these filters.", "Nijedan rezultat ne odgovara filterima.") })));
      const cap = ctx.inPanel ? 120 : 60;
      shown.slice(0, cap).forEach((x) => listBox.appendChild(propertyCard(x, ctx, { landmark: d.landmark, onSelect: (p) => select(p), onMap: (p) => {
        if (state.view === "list") { state.view = "split"; W.$$(".seg-btn", views).forEach((b, i) => { b.classList.toggle("on", i === 2); b.setAttribute("aria-checked", String(i === 2)); }); layout(); }
        select(p);
      } })));
      if (shown.length > cap) listBox.appendChild(el("div", { class: "muted small", text: t(`Showing ${cap} of ${shown.length}.`, `Prikazano ${cap} od ${shown.length}.`) }));
      paintMarkers(shown);
    }
    function paintMarkers(shown) {
      if (!mapRef) return;
      markers.forEach((m) => mapRef.removeLayer(m)); markers.clear();
      const pts = [];
      if (d.center && d.center.lat != null) pts.push([d.center.lat, d.center.lon]);
      shown.filter((x) => x.lat != null).forEach((x) => {
        const label = x.night_price != null ? fmt.money(x.night_price, x.total_currency, ctx.lang) : "•";
        const cls = "mk-price" + (x.price_status === "verified" ? " v" : x.price_status === "indicative" ? " i" : " n") + ((x.flags || []).some((f) => f.startsWith("lowest")) ? " best" : "");
        const m = W.maps.marker(x.lat, x.lon, pinNode(cls, label, x.title), { riseOnHover: true, title: x.title }).addTo(mapRef);
        m.on("click", () => select(x, true));
        markers.set(x.id, m); pts.push([x.lat, x.lon]);
      });
      if (!pts.length) return;
      W.maps.fit(mapRef, pts, 15);
    }
    function layout() {
      body.className = "stays-body view-" + state.view;
      if (mapRef) setTimeout(() => mapRef.invalidateSize(), 80);
    }
    let syncTimer = null;
    function sync() {
      clearTimeout(syncTimer);
      syncTimer = setTimeout(() => { if (ctx.conversationId) action(ctx, comp, "stays.filters", { filters: state.filters, sort: state.sort }); }, 400);
    }

    // footer: other sources, honesty notes
    const foot = el("div", { class: "card-foot stays-foot" });
    const linkRow = el("div", { class: "other-src" });
    (d.links || []).forEach((l) => linkRow.appendChild(button(l.name, { icon: "external", kind: "ghost", small: true, title: t("Opens the provider's own search with these dates; ULTRON does not read its prices.", "Otvara pretragu provajdera sa ovim datumima; ULTRON ne čita njegove cene."), onClick: () => W.open(l.url) })));
    if ((d.links || []).length) foot.appendChild(el("div", { class: "src-grp" }, el("span", { class: "fk", text: t("Search elsewhere (prices not retrieved)", "Pretraži drugde (cene se ne preuzimaju)") }), linkRow));
    const notes = [];
    (d.not_configured || []).forEach((n) => notes.push(t(`${n.name}: not configured`, `${n.name}: nije podešen`)));
    (d.failed || []).forEach((f) => notes.push(`${f.name}: ${f.error}`));
    if (d.fx) notes.push(d.fx);
    (d.notes || []).forEach((n) => notes.push(n));
    notes.forEach((n) => foot.appendChild(el("div", { class: "note", text: n })));
    foot.appendChild(sourceLine(t("Indicative prices are advertised “from” prices, not quotes. Nothing is booked. Sources are credited on each card.",
      "Indikativne cene su oglašene „od” cene, ne ponude. Ništa se ne rezerviše. Izvor je naveden na svakoj kartici.")));

    card.querySelector(".card-actions").append(expandable(card, mapBox));
    card.append(...[summary, assumptions, toolbar, fPanel, body, foot].filter(Boolean));
    layout();
    draw();
    W.maps.lazy(mapBox, (map) => {
      mapRef = map;
      if (d.center && d.center.lat != null) W.maps.marker(d.center.lat, d.center.lon, pinNode("mk-center", "", (d.center.name || "") + " · " + t("centre", "centar")), { interactive: false }).addTo(map);
      if (d.landmark && d.landmark.lat != null && d.landmark.kind !== "center") W.maps.marker(d.landmark.lat, d.landmark.lon, pinNode("mk-b", "★", d.landmark.name)).addTo(map);
      paintMarkers(sortList(applyFilters(listings, state.filters), state.sort));
    });
    return card;
  }
  const ASSUME = { one_night: ["1 night", "1 noćenje"], default_guests: ["1 guest", "1 gost"], date_tonight: ["check-in tonight", "dolazak večeras"],
    city_from_route: ["town from your route", "mesto iz rute"], city_from_previous_search: ["same town as before", "isto mesto kao ranije"],
    dates_from_previous_search: ["same dates as before", "isti datumi kao ranije"], weekend_one_night: ["weekend = Sat → Sun", "vikend = sub → ned"],
    city_from_context: ["town from the conversation", "mesto iz razgovora"] };

  // ── PropertyFocus ───────────────────────────────────────────────────────
  function propertyFocus(comp, ctx) {
    const d = comp.data, x = d.listing, t = T(ctx);
    const card = cardShell("focus", "pin", x.title, [x.address, x.provider_name].filter(Boolean).join(" · "));
    const mapBox = el("div", { class: "map " + (ctx.inPanel ? "map-tall" : "map-mid") });
    card.querySelector(".card-actions").append(expandable(card, mapBox));
    card.append(mapBox, propertyCard(x, ctx, { onMap: () => mapBox.__map && mapBox.__map.setView([x.lat, x.lon], 17) }));
    W.maps.lazy(mapBox, (map) => {
      const pts = [];
      if (x.lat != null) { W.maps.marker(x.lat, x.lon, pinNode("mk-price i best", x.night_price != null ? fmt.money(x.night_price, x.total_currency, ctx.lang) : "●", x.title)).addTo(map); pts.push([x.lat, x.lon]); }
      if (d.center && d.center.lat != null) { W.maps.marker(d.center.lat, d.center.lon, pinNode(d.reason === "landmark" ? "mk-b" : "mk-center", d.reason === "landmark" ? "★" : "", d.center.name)).addTo(map); pts.push([d.center.lat, d.center.lon]); }
      if (pts.length === 2) L.polyline(pts, { color: "#21D4FD", weight: 2, opacity: 0.7, dashArray: "4 6" }).addTo(map);
      W.maps.fit(map, pts, 16);
    });
    return card;
  }

  // ── links, research, connected services ────────────────────────────────
  function linkCard(comp, ctx) {
    const d = comp.data;
    const card = cardShell("link", "link", d.title, "");
    card.append(el("div", { class: "link-list" }, d.links.map((l, i) =>
      el("div", { class: "link-row" }, el("div", { class: "link-meta" }, el("div", { class: "link-l", text: l.label }), el("div", { class: "link-u", text: l.url })),
        button(W.t("Open", "Otvori"), { icon: "external", kind: i === 0 ? "primary" : "ghost", small: true, onClick: () => W.open(l.url) }),
        button(W.t("Copy link", "Kopiraj link"), { icon: "copy", iconOnly: true, kind: "ghost", small: true, onClick: () => W.copy(l.url) })))));
    if (d.note) card.append(el("div", { class: "note note-box" }, icon("shield", 14), el("span", { text: d.note })));
    return card;
  }

  function research(comp, ctx) {
    const d = comp.data, t = T(ctx);
    const card = cardShell("research", "globe", d.query, d.provider);
    card.querySelector(".card-actions").append(button(t("Save", "Sačuvaj"), { icon: "bookmark", iconOnly: true, kind: "ghost", small: true,
      onClick: () => save("research", d.query, { query: d.query, answer: d.answer.slice(0, 4000), sources: d.sources }, d.provider) }));
    const list = el("ol", { class: "sources" }, d.sources.map((s, i) => el("li", { class: "source" },
      el("span", { class: "snum", text: String(i + 1) }),
      el("div", { class: "smain" }, el("div", { class: "stitle", text: s.title }), el("div", { class: "shost", text: s.host }),
        s.snippet ? el("div", { class: "ssnip", text: s.snippet }) : null),
      button(t("Open", "Otvori"), { icon: "external", iconOnly: true, kind: "ghost", small: true, onClick: () => W.open(s.url) }))));
    card.append(el("div", { class: "fk pad", text: t("Sources", "Izvori") }), list,
      el("div", { class: "card-foot" }, sourceLine(t("Search results are untrusted text; verify important facts.", "Rezultati pretrage nisu provereni; proverite važne činjenice."))));
    return card;
  }

  function emailSummary(comp, ctx) {
    const d = comp.data, t = T(ctx);
    const card = cardShell("email", "mail", t("Email", "Pošta"), d.provider);
    if (d.items.length) card.append(el("ul", { class: "rows" }, d.items.map((m) => el("li", { class: "row" + (m.unread ? " unread" : "") },
      el("span", { class: "dot", "aria-label": m.unread ? t("Unread", "Nepročitano") : "" }),
      el("div", { class: "row-main" }, el("div", { class: "row-t", text: m.subject || t("(no subject)", "(bez naslova)") }),
        el("div", { class: "row-s", text: [m.from, m.date].filter(Boolean).join(" · ") }),
        m.snippet ? el("div", { class: "row-x", text: m.snippet }) : null)))));
    else card.append(el("div", { class: "muted pad", text: t("No messages listed.", "Nema poruka.") }));
    card.append(el("div", { class: "card-foot" }, sourceLine(t("Read-only through Claude's Gmail connector. Sending or deleting always needs your review.", "Samo čitanje preko Claude Gmail konektora. Slanje ili brisanje uvek traži vašu potvrdu."))));
    return card;
  }

  function driveFiles(comp, ctx) {
    const d = comp.data, t = T(ctx);
    const card = cardShell("drive", "drive", "Google Drive", d.provider);
    if (d.items.length) card.append(el("ul", { class: "rows" }, d.items.map((f) => el("li", { class: "row" },
      icon("file", 16),
      el("div", { class: "row-main" }, el("div", { class: "row-t", text: f.name }),
        el("div", { class: "row-s", text: [f.type, f.modified && f.modified.slice(0, 10), f.owner, f.size != null ? fmt.bytes(f.size) : ""].filter(Boolean).join(" · ") })),
      f.url ? button(t("Open", "Otvori"), { icon: "external", iconOnly: true, kind: "ghost", small: true, onClick: () => W.open(f.url) }) : null))));
    else card.append(el("div", { class: "muted pad", text: t("No files listed.", "Nema fajlova.") }));
    return card;
  }

  function calendarAgenda(comp, ctx) {
    const d = comp.data, t = T(ctx);
    const card = cardShell("calendar", "calendar", t("Schedule", "Raspored") + (d.range ? " · " + d.range : ""), d.provider);
    if (!d.configured) {
      card.append(el("div", { class: "empty-s" }, icon("calendar", 22), el("span", { text: d.note })),
        el("div", { class: "card-foot" }, button("MCP Hub", { icon: "hub", kind: "primary", small: true, onClick: () => W.bus.emit("navigate", { view: "mcp" }) })));
      return card;
    }
    if (!d.events.length) { card.append(el("div", { class: "empty-s" }, icon("checkCircle", 20), el("span", { text: t("Nothing scheduled.", "Ništa zakazano.") }))); return card; }
    const groups = {};
    d.events.forEach((e) => { const k = (e.start || "").slice(0, 10); (groups[k] = groups[k] || []).push(e); });
    Object.keys(groups).sort().forEach((k) => {
      card.append(el("div", { class: "day-h", text: fmt.day(k, ctx.lang) }),
        el("ul", { class: "rows" }, groups[k].map((e) => el("li", { class: "row" },
          el("span", { class: "time", text: e.start && e.start.length > 10 ? e.start.slice(11, 16) : t("All day", "Ceo dan") }),
          el("div", { class: "row-main" }, el("div", { class: "row-t", text: e.title }),
            el("div", { class: "row-s", text: [e.location, e.calendar].filter(Boolean).join(" · ") }))))));
    });
    return card;
  }

  function mcpStatus(comp, ctx) {
    const d = comp.data;
    const card = cardShell("mcp", "hub", "MCP", "");
    card.append(el("div", { class: "mini-grid" }, d.servers.map((s) => el("div", { class: "mini" },
      el("span", { class: "sdot s-" + s.status }), el("span", { class: "mini-n", text: s.name }),
      el("span", { class: "mini-s", text: W.statusLabel(s.status) + (s.tools ? ` · ${s.tools}` : "") })))),
      el("div", { class: "card-foot" }, button(W.t("Open MCP Hub", "Otvori MCP Hub"), { icon: "hub", kind: "primary", small: true, onClick: () => W.bus.emit("navigate", { view: "mcp" }) })));
    return card;
  }

  function errorRecovery(comp, ctx) {
    const d = comp.data;
    const card = cardShell("error", "alert", d.title, "");
    if (d.detail) card.append(el("div", { class: "err-detail", text: d.detail }));
    card.append(el("div", { class: "card-foot" }, d.actions.map((a, i) => button(a.label, {
      icon: a.kind === "view" ? "arrowRight" : a.kind === "url" ? "external" : "refresh", kind: i === 0 ? "primary" : "ghost", small: true,
      onClick: () => { if (a.kind === "view") W.bus.emit("navigate", { view: a.value }); else if (a.kind === "url") W.open(a.value); else W.bus.emit("send", a.value); } }))));
    return card;
  }

  function notice(comp) {
    const d = comp.data;
    return el("div", { class: "notice notice-" + d.tone, role: "note" }, icon(d.tone === "warning" ? "alert" : d.tone === "success" ? "checkCircle" : "info", 16),
      el("div", null, el("strong", { text: d.title }), el("div", { text: d.text })));
  }

  function toolResult(comp) {
    const d = comp.data;
    const det = el("details", { class: "rcard rcard-tool" }, el("summary", null, icon("terminal", 15), el("span", { text: `${d.server} · ${d.tool}` })),
      el("pre", { class: "codeblock", text: d.body }));
    return det;
  }

  W.cards = { route_map: routeMap, place_results: placeResults, stay_results: stayResults, property_focus: propertyFocus,
    link_card: linkCard, research, email_summary: emailSummary, drive_files: driveFiles, calendar_agenda: calendarAgenda,
    mcp_status: mcpStatus, error_recovery: errorRecovery, notice, tool_result: toolResult };
  W.renderComponent = function (comp, ctx) {
    const fn = W.cards[comp && comp.kind];
    if (!fn) return el("div", { class: "notice notice-warning", text: "Unsupported result type." });
    try { const n = fn(comp, ctx); n.dataset.componentId = comp.id; return n; }
    catch (e) { console.error(e); return el("div", { class: "notice notice-warning", text: "This result could not be displayed." }); }
  };
  W.mapCapable = (kind) => ["route_map", "place_results", "stay_results", "property_focus", "research"].includes(kind);
})();
