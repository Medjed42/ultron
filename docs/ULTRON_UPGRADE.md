# Ultron upgrade — 8 October 2026

The assistant is now named **Ultron**. Its existing Gemini Live connection, model fallback ladder, credentials and voice configuration remain in place. Startup connected successfully using `gemini-3.1-flash-live-preview`.

## Start and use

- Double-click **Ultron** on the desktop, or run `start_ultron.bat` in this project. The launcher avoids duplicate assistant processes. `run.bat` also uses this launcher.
- The existing optional tray/wake launcher is available through `start_launcher.bat`.
- Drag the new sphere to rotate it, scroll to zoom, and double-click to reset. Settings still offer the previous face and core styles.
- The left panel displays the actual Gemini model, connection state, plugin count and last tool. Its Chrome, audio, mail and news buttons send requests to Gemini.
- Use Settings → Plugins to inspect or toggle plugins. Toggling requests a Gemini reconnect so the tool list updates. Gmail and SmartThings connection buttons now resolve correctly.

Example requests:

- “Look at my existing Chrome tabs and switch to YouTube.”
- “Search YouTube for ambient music, then select the video titled …”
- “Pause this YouTube video.” / “Set YouTube volume to 40 percent.”
- “List my audio outputs.” / “Switch audio to [observed device name].”
- “Show recent Gmail messages.” / “Draft a reply to this message.”
- “Preview promotional emails.” / “Give me a world news briefing with source links.”
- “Check Ultron's health.”

## Implemented changes

| Area | Result |
| --- | --- |
| Identity and interface | Ultron name in the main assistant, prompt, launchers and remote dashboard; native animated amber orb, model/status cards, quick commands, smaller-window scrolling and preserved settings. Internal tool names and storage paths remain compatible. |
| Chrome | Existing-window control through Windows accessibility, tab observation/switching, navigation, searches, visible-element clicking, literal typing and bounded inspection. Unsupported actions no longer silently start another Chrome session. No extension or debugging port required. |
| YouTube | Explicit observed play/pause, mute/unmute, fullscreen, volume slider, player shortcuts and settings-menu control. Videos are selected by observed title; arbitrary numbered hyperlinks are refused. |
| Audio | Output enumeration, named switching and previous-device switching verify the resulting endpoint. Ambiguous partial device names are refused. |
| Prompts | Ultron identity, observe/action/verify flow, bounded recovery, truthful completion, untrusted web/mail handling, draft-first Gmail and evidence-based research. |
| Gmail | Existing OAuth connection reused; inbox/search/thread/MIME reading, drafts/reply drafts, labels, archive/trash and promotion previews. Sending and supported one-click unsubscribe use the existing desktop confirmation mechanism. The standalone MCP adapter shares this implementation and supports the installed MCP 2 API. |
| News | New world/finance RSS briefing plugin with source links, timestamps and partial-feed failure reporting. |
| Research | Existing research service uses Gemini, a separate local port, source retrieval, freshness/quotation checks, conflict handling and review states. It saves briefs and drafts; the repaired acquisition workflow does not automatically send outreach emails. Website generation reports missing output instead of claiming a site exists. |
| Plugin loading | Resolved the game-updater tool-name collision, repaired settings callbacks, and deferred Midea cloud login until requested. 11 plugins and 18 built-in tools load successfully. |
| Diagnostics and launch | New health plugin, secret redaction, UTF-8 launch logs, duplicate-process avoidance and desktop shortcut. Embedded Telegram credentials were moved to the existing ignored environment file. |
| Dependencies | Repaired the corrupted requirements file and declared Vosk. The dependency dry run found the required packages already installed. |

Claude PC access, gesture/webcam tracking and the Friday project's separate voice stack were not initialized. Existing unrelated integrations were preserved. The research service responds locally on `127.0.0.1:8766`; it is also started on demand by its plugin. No acquisition workflow or external outreach was run for validation.

## Review of the two supplied repositories

Reviewed source checkouts: the two upstream repositories at the pinned commits
listed in [THIRD_PARTY_NOTICES/README.md](../THIRD_PARTY_NOTICES/README.md).

No obvious RAT, credential-stealing routine or persistence mechanism was found in the reviewed first-party source. This is a source review, **not a guarantee of safety** or a full dependency/binary audit. Upstream applications and installation scripts were not executed. Local source hashes are retained in `.audit/upstream-manifest.json`.

The Ultron lockfile audit reported **6 affected packages: 1 critical, 4 high and 1 moderate**. These included Next.js and transitive packages. The audit is saved at `.audit/ultron-npm-audit.json`. None of that web dependency stack was installed into this assistant. The sphere was adapted using the existing Qt/NumPy runtime; its MIT notice is retained in `THIRD_PARTY_NOTICES/ultron-MIT.txt`.

The Ultron reference also has optional webcam/hand tracking and external model downloads. Those features were excluded. Friday depends on a separate LiveKit/Sarvam/OpenAI voice path, and its prompt includes fabricated market-commentary behavior; that behavior and stack were excluded. The news-monitor idea was implemented using source-labelled RSS results instead.

## Validation and practical limits

- **35 automated tests passed**, including safe Chrome routing, ambiguous clicks, literal typing, YouTube state handling, verified audio switching, Gmail message parsing and send gating, promotion filtering, evidence rejection and redaction.
- **155 local Python source files parsed without syntax errors** at the source-inventory check. The subsequently revised standalone Gmail adapter also imports and registers its tools successfully.
- All **29 discovered tool declarations** validate against the installed Gemini SDK. No plugins were rejected; both settings connection callbacks are callable.
- Read-only live checks succeeded for an existing Chrome window, two audio outputs, the existing Gmail OAuth profile and 12 news headlines from the briefing feeds.
- Research `/health` returned HTTP 200 with service `ultron-research`.
- Actual desktop startup connected to Gemini, opened microphone/output streams, loaded all plugins and produced no startup traceback. The desktop was later closed; use the shortcut to reopen it.
- The UI was rendered and inspected at **1280×800** and **1000×660**. The included preview is an isolated layout render; its initial connection/plugin placeholders are not live telemetry.

Chrome and YouTube control depend on what the page exposes through Windows accessibility. Hidden controls, advertisements, localization and site changes can require another observation or manual intervention. Dispatched keystrokes are not reported as verified completion. Quality/speed selections return observations for follow-up verification. These controls have unit and read-only integration coverage; every possible live website interaction was not exercised.

The downloaded Vosk vocabulary lacks the literal word “Ultron”, so wake detection includes phonetic variants such as “all tron”. Real microphone wake accuracy still needs a spoken check. Its existing optional enable/disable setting was preserved.

Smart-home commands, email sending/unsubscribe, Telegram messaging, autonomous coding and client acquisition were not exercised. Standalone Gmail MCP reads/drafts work without the desktop; send/reply cannot obtain desktop confirmation from an independent headless process and return that limitation instead of sending.

The global Python environment has an existing protobuf dependency conflict involving `google-antigravity`. Its native `agy` executable responds to help, but its coding workflow was not run. A global protobuf upgrade was avoided because it would conflict with other installed Google packages and could disrupt the working Gemini environment.

## Recovery and local records

The pre-upgrade project/configuration snapshot is in `backups/ULTRON_BEFORE_UPGRADE_20261008`. It contains private configuration and should remain local. Source review checkouts, manifests, audit results and preview renders are in `.audit/`. Both directories are ignored by Git. Existing uncommitted work was preserved; nothing was committed or pushed.

Startup logs are in `logs/main.log` and `logs/research.log`. Runtime logs are ignored by Git and known credential values are redacted, but conversational content should still be treated as private.

The local-session screenshot is excluded from the public source export.
