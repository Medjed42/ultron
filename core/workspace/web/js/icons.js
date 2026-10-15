/* ULTRON Workspace — line icons (24×24, 2px stroke, round joins).
   Built with DOM APIs only; no markup strings are parsed. */
(function () {
  "use strict";
  const P = {
    plusChat: ["M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z", "M12 7v6", "M9 10h6"],
    grid: ["M3 3h7v7H3z", "M14 3h7v7h-7z", "M14 14h7v7h-7z", "M3 14h7v7H3z"],
    chats: ["M14 9a2 2 0 0 1-2 2H6l-4 4V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2z", "M18 9h2a2 2 0 0 1 2 2v11l-4-4h-6a2 2 0 0 1-2-2v-1"],
    bookmark: ["M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"],
    hub: ["M12 2v4", "M12 18v4", "M4.93 4.93l2.83 2.83", "M16.24 16.24l2.83 2.83", "M2 12h4", "M18 12h4", "M4.93 19.07l2.83-2.83", "M16.24 7.76l2.83-2.83", "C12 12 3"],
    settings: ["M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z", "C12 12 3"],
    globe: ["C12 12 10", "M2 12h20", "M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"],
    pin: ["M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0z", "C12 10 3"],
    bed: ["M2 20v-8a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v8", "M4 10V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v4", "M12 4v6", "M2 18h20"],
    calendar: ["M8 2v4", "M16 2v4", "M3 10h18", "M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"],
    search: ["C11 11 8", "M21 21l-4.3-4.3"],
    send: ["M22 2L11 13", "M22 2l-7 20-4-9-9-4z"],
    stop: ["M6 6h12v12H6z"],
    x: ["M18 6L6 18", "M6 6l12 12"],
    chevRight: ["M9 18l6-6-6-6"],
    chevDown: ["M6 9l6 6 6-6"],
    chevLeft: ["M15 18l-6-6 6-6"],
    menu: ["M4 6h16", "M4 12h16", "M4 18h16"],
    panel: ["M3 3h18v18H3z", "M15 3v18"],
    expand: ["M15 3h6v6", "M9 21H3v-6", "M21 3l-7 7", "M3 21l7-7"],
    shrink: ["M4 14h6v6", "M20 10h-6V4", "M14 10l7-7", "M3 21l7-7"],
    external: ["M15 3h6v6", "M10 14L21 3", "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"],
    copy: ["M9 9h11a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2z", "M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"],
    star: ["M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z"],
    car: ["M19 17h2v-4l-2.4-5.2A2 2 0 0 0 16.8 6H7.2a2 2 0 0 0-1.8 1.2L3 13v4h2", "M3 13h18", "C7 17 2", "C17 17 2", "M9 17h6"],
    walk: ["C13 4 1.6", "M10 22l2-6 3 3v3", "M8 12l2-4 4 1 2 4 3 1", "M12 16l-1-5"],
    bike: ["C5.5 17.5 3.5", "C18.5 17.5 3.5", "M15 6h2l3 11.5", "M5.5 17.5L9 10h7l-4.5 7.5", "M9 10l-1-3H6"],
    bus: ["M8 6v6", "M16 6v6", "M4 12h16", "M6 18h12a2 2 0 0 0 2-2V6a3 3 0 0 0-3-3H7a3 3 0 0 0-3 3v10a2 2 0 0 0 2 2z", "M7 18v3", "M17 18v3"],
    route: ["C6 19 3", "M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15", "C18 5 3"],
    nav: ["M3 11l19-9-9 19-2-8z"],
    wifi: ["M5 12.55a11 11 0 0 1 14.08 0", "M1.42 9a16 16 0 0 1 21.16 0", "M8.53 16.11a6 6 0 0 1 6.95 0", "M12 20h.01"],
    parking: ["M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z", "M9 17V7h4a3 3 0 0 1 0 6H9"],
    utensils: ["M3 2v7c0 1.1.9 2 2 2h4a2 2 0 0 0 2-2V2", "M7 2v20", "M21 15V2a5 5 0 0 0-5 5v6c0 1.1.9 2 2 2h3zm0 0v7"],
    snow: ["M2 12h20", "M12 2v20", "M20 16l-4-4 4-4", "M4 8l4 4-4 4", "M16 4l-4 4-4-4", "M8 20l4-4 4 4"],
    tv: ["M3 7h18v13H3z", "M17 2l-5 5-5-5"],
    kitchen: ["M5 3h14v18H5z", "M5 10h14", "M9 6h.01", "M9 14v3"],
    washer: ["M4 2h16a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z", "C12 13 5", "M6 6h.01", "M10 6h.01"],
    balcony: ["M3 21h18", "M5 21V9h14v12", "M9 21v-8", "M15 21v-8", "M12 3l7 6H5z"],
    building: ["M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18z", "M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2", "M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2", "M10 6h4", "M10 10h4", "M10 14h4", "M10 18h4"],
    home: ["M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z", "M9 22V12h6v10"],
    filter: ["M22 3H2l8 9.46V19l4 2v-8.54z"],
    sliders: ["M4 21v-7", "M4 10V3", "M12 21v-9", "M12 8V3", "M20 21v-5", "M20 12V3", "M1 14h6", "M9 8h6", "M17 16h6"],
    list: ["M8 6h13", "M8 12h13", "M8 18h13", "M3 6h.01", "M3 12h.01", "M3 18h.01"],
    map: ["M1 6v16l7-4 8 4 7-4V2l-7 4-8-4z", "M8 2v16", "M16 6v16"],
    split: ["M3 3h18v18H3z", "M12 3v18"],
    check: ["M20 6L9 17l-5-5"],
    checkCircle: ["M22 11.08V12a10 10 0 1 1-5.93-9.14", "M22 4L12 14.01l-3-3"],
    alert: ["M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z", "M12 9v4", "M12 17h.01"],
    alertCircle: ["C12 12 10", "M12 8v4", "M12 16h.01"],
    info: ["C12 12 10", "M12 16v-4", "M12 8h.01"],
    refresh: ["M23 4v6h-6", "M1 20v-6h6", "M3.51 9a9 9 0 0 1 14.85-3.36L23 10", "M1 14l4.64 4.36A9 9 0 0 0 20.49 15"],
    power: ["M18.36 6.64a9 9 0 1 1-12.73 0", "M12 2v10"],
    key: ["C7.5 15.5 5.5", "M21 2l-9.6 9.6", "M15.5 7.5l3 3L22 7l-3-3"],
    shield: ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z", "M9 12l2 2 4-4"],
    sparkles: ["M12 3l1.9 5.8L19.7 10.7l-5.8 1.9L12 18.4l-1.9-5.8L4.3 10.7l5.8-1.9z", "M19 3v4", "M21 5h-4", "M5 17v4", "M7 19H3"],
    brain: ["M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18z", "M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18z", "M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4", "M12 5v13"],
    mail: ["M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z", "M22 6l-10 7L2 6"],
    drive: ["M22 12H2", "M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z", "M6 16h.01", "M10 16h.01"],
    file: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z", "M14 2v6h6", "M16 13H8", "M16 17H8"],
    clock: ["C12 12 10", "M12 6v6l4 2"],
    activity: ["M22 12h-4l-3 9L9 3l-3 9H2"],
    zap: ["M13 2L3 14h9l-1 8 10-12h-9z"],
    command: ["M18 3a3 3 0 0 0-3 3v12a3 3 0 0 0 3 3 3 3 0 0 0 3-3 3 3 0 0 0-3-3H6a3 3 0 0 0-3 3 3 3 0 0 0 3 3 3 3 0 0 0 3-3V6a3 3 0 0 0-3-3 3 3 0 0 0-3 3 3 3 0 0 0 3 3h12a3 3 0 0 0 3-3 3 3 0 0 0-3-3z"],
    arrowUpRight: ["M7 17L17 7", "M7 7h10v10"],
    arrowRight: ["M5 12h14", "M12 5l7 7-7 7"],
    trash: ["M3 6h18", "M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6", "M10 11v6", "M14 11v6", "M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"],
    edit: ["M12 20h9", "M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"],
    link: ["M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71", "M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"],
    users: ["M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2", "C9 7 4", "M23 21v-2a4 4 0 0 0-3-3.87", "M16 3.13a4 4 0 0 1 0 7.75"],
    minus: ["M5 12h14"],
    plus: ["M12 5v14", "M5 12h14"],
    cpu: ["M4 4h16v16H4z", "M9 9h6v6H9z", "M9 1v3", "M15 1v3", "M9 20v3", "M15 20v3", "M20 9h3", "M20 14h3", "M1 9h3", "M1 14h3"],
    database: ["E12 5 9 3", "M21 12c0 1.66-4 3-9 3s-9-1.34-9-3", "M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"],
    terminal: ["M4 17l6-6-6-6", "M12 19h8"],
    lock: ["M5 11h14v11H5z", "M7 11V7a5 5 0 0 1 10 0v4"],
    layers: ["M12 2L2 7l10 5 10-5z", "M2 17l10 5 10-5", "M2 12l10 5 10-5"],
    target: ["C12 12 10", "C12 12 6", "C12 12 2"],
    history: ["M3 3v5h5", "M3.05 13A9 9 0 1 0 6 5.3L3 8", "M12 7v5l4 2"],
    euro: ["M4 10h12", "M4 14h9", "M19 6a7.7 7.7 0 0 0-5.2-2A7.9 7.9 0 0 0 6 12c0 4.4 3.5 8 7.8 8 2 0 3.8-.8 5.2-2"],
    bath: ["M9 6L6.5 3.5a1.5 1.5 0 0 0-1-.5C4.68 3 4 3.68 4 4.5V17a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5", "M2 12h20", "M7 19v2", "M17 19v2"],
    heat: ["M14 4v10.54a4 4 0 1 1-4 0V4a2 2 0 0 1 4 0z"],
    paw: ["C11 4 2", "C18 8 2", "C20 16 2", "M9 10a5 5 0 0 1 5 5v3.5a3.5 3.5 0 0 1-6.84 1.045Q6.52 17.48 4.46 16.84A3.5 3.5 0 0 1 5.5 10z"],
    coffee: ["M18 8h1a4 4 0 0 1 0 8h-1", "M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4z", "M6 1v3", "M10 1v3", "M14 1v3"],
    fuel: ["M3 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18", "M3 22h12", "M15 13h2a2 2 0 0 1 2 2v2a2 2 0 0 0 4 0V9.83a2 2 0 0 0-.59-1.42L18 4", "M7 7h4"],
    pill: ["M10.5 20.5L3.5 13.5a4.95 4.95 0 1 1 7-7l7 7a4.95 4.95 0 1 1-7 7z", "M8.5 8.5l7 7"],
    cart: ["C8 21 1", "C19 21 1", "M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12"],
    landmark: ["M3 22h18", "M6 18v-7", "M10 18v-7", "M14 18v-7", "M18 18v-7", "M12 2l8 5H4z"],
    orb: ["C12 12 9", "M12 3a9 9 0 0 1 0 18", "M3.6 9h16.8", "M3.6 15h16.8"],
  };

  const NS = "http://www.w3.org/2000/svg";

  function shape(spec) {
    // "C cx cy r" circle · "E cx cy rx ry" ellipse · otherwise a path.
    const parts = spec.replace(/^([CE])(?=[\d-])/, "$1 ").split(" ");
    if (parts[0] === "C" && parts.length === 4) {
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", parts[1]); c.setAttribute("cy", parts[2]); c.setAttribute("r", parts[3]);
      return c;
    }
    if (parts[0] === "E" && parts.length === 5) {
      const e = document.createElementNS(NS, "ellipse");
      e.setAttribute("cx", parts[1]); e.setAttribute("cy", parts[2]);
      e.setAttribute("rx", parts[3]); e.setAttribute("ry", parts[4]);
      return e;
    }
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", spec);
    return p;
  }

  function icon(name, size) {
    const svg = document.createElementNS(NS, "svg");
    const s = String(size || 18);
    svg.setAttribute("width", s); svg.setAttribute("height", s);
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.9");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    svg.classList.add("ico");
    (P[name] || P.info).forEach((d) => svg.appendChild(shape(d)));
    return svg;
  }

  window.W = window.W || {};
  window.W.icon = icon;
  window.W.iconNames = Object.keys(P);
})();
