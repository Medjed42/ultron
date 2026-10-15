# Public source setup

This source export contains no configured owner account. API keys, OAuth grants,
passwords, linked sessions, private certificates, account-status caches, personal
memory, conversations, lead databases, generated sites, logs, local-session
screenshots and recorded test audio have been excluded. The previous Git history
and remotes are not part of this export. Third-party attribution is preserved.

## Set up your own installation

1. Install the dependencies described in the main README.
2. Start `start_ultron.bat` on Windows and complete the application's first-run
   setup with your own name and Gemini key.
3. Use `ULTRON_SETUP_ALL_ACCOUNTS.bat` or **Connections & permissions** for optional
   Google, Maps, calendar and other integrations. Supply your own OAuth desktop
   client file when requested.
4. For optional Telegram or Midea support, copy `.env.example` to `.env` and fill
   in only the integrations you use. Telegram requires both a bot token and an
   allowed private chat ID. Midea starts in dry-run mode in the example.
5. The two Omni Agent projects have separate `.env.example` files. Their secrets
   and generated work are local data and are excluded by `.gitignore`.

Claude CLI and existing-browser integrations use the accounts signed in on the
computer running the application. Those accounts are managed outside this source
folder; every user must provide their own sign-ins. Do not start this application
on a shared computer unless that is intended.

The MCP configuration examples under `.agents/plugins/` use paths relative to
the repository root. Configure the MCP host to launch them with that working
directory, or set an absolute path for your own checkout locally.

## Before publishing changes

- Review the files Git will include. `.gitignore` does not protect files that
  have already been committed or explicitly force-added.
- Keep `.env.example` files blank or limited to non-secret defaults.
- Do not commit runtime folders, personal screenshots, recordings, generated
  customer work, saved account status, or copies of an older installation.
- Preserve the third-party notices. The main README states the project's license.

Bundled Vosk model files are available in this local copy but are ignored by Git.
The application can download its wake-word model through its existing setup flow.
The published UI bundles remain included; `node_modules` is not needed to use them.

No GitHub repository has been created or uploaded by preparing this folder.
