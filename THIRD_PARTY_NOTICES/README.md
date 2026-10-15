# Third-party notices — Ultron 2.0

Ultron 2.0 includes or adapts the open-source work listed
below. Their licences require these notices to travel with the code, so they
are kept here, in one place, rather than in the application.

| Component | Used for | Licence | Notice |
| --- | --- | --- | --- |
| Ultron by Sagar Builds — [pinned commit `a65306f`](https://github.com/SAGAR-TAMANG/ultron-by-sagar-builds/tree/a65306f5a9568655551ec27445f773f20273223a) | The particle sphere (Qt/NumPy adaptation, `core/ultron_orb.py`, `assets/orb/`) | MIT | [`ultron-MIT.txt`](ultron-MIT.txt), [`assets/orb/LICENSE.reference.txt`](../assets/orb/LICENSE.reference.txt) |
| ethanplusai/jarvis — `frontend/src/orb.ts` | Orb physics and state behaviour the sphere adapts | See that project | — |
| Friday Tony Stark Demo — [pinned commit `4d85750`](https://github.com/SAGAR-TAMANG/friday-tony-stark-demo/tree/4d85750ae4c60c88850a6c69107e91bf02300673) | Reviewed reference (source audit only) | See that project | — |
| Holo (Zubair Trabzada / AI Workshop Studio LLC), MediaPipe, three.js | Holo Hands view (`assets/holo/`) | MIT, Apache 2.0, MIT | [`assets/holo/LICENSE`](../assets/holo/LICENSE) |
| three.js | Orb bundle (`assets/orb/orb.bundle.js`) | MIT | [`assets/orb/orb.bundle.js.LEGAL.txt`](../assets/orb/orb.bundle.js.LEGAL.txt) |
| Leaflet | Workspace maps (`core/workspace/web/vendor/leaflet/`) | BSD 2-Clause | [`LICENSE`](../core/workspace/web/vendor/leaflet/LICENSE) |
| MediaPipe face mesh | Avatar head model (`core/face_model.obj`) | Apache 2.0 | header of `core/face_model.obj` |
| Vosk US English small model (Alpha Cephei) | Wake word, welcome-recording analysis (`vosk_model/`) | Apache 2.0 | `vosk_model/model/README` |

The source audit of the two pinned repositories above is described in
[`docs/ULTRON_UPGRADE.md`](../docs/ULTRON_UPGRADE.md) and
[`docs/PHASE2_ENGINEERING_REPORT.md`](../docs/PHASE2_ENGINEERING_REPORT.md);
local source hashes are in `.audit/upstream-manifest.json`.
