import { createOrb, type OrbState } from "./orb";

window.addEventListener("DOMContentLoaded", () => {
  const container = document.getElementById("orb") || document.body;
  let canvas = container.querySelector("canvas") as HTMLCanvasElement | null;
  if (!canvas) {
    canvas = document.createElement("canvas");
    container.appendChild(canvas);
  }

  const orb = createOrb(canvas);

  // Expose global control APIs
  (window as any).setOrbState = (state: OrbState) => {
    orb.setState(state);
  };

  (window as any).orb = orb;

  // Listen for postMessage or custom events if embedded
  window.addEventListener("message", (event) => {
    if (event.data && typeof event.data === "object") {
      if (event.data.type === "SET_STATE" && event.data.state) {
        orb.setState(event.data.state);
      }
    }
  });

  // Hotkey reset
  window.addEventListener("keydown", (e) => {
    if (e.key === "r" || e.key === "R") {
      orb.setState("idle");
    }
  });
});
