# Security

## Reporting a vulnerability

Please report security problems privately through GitHub: open the
repository's **Security** tab and choose **Report a vulnerability**. Do not
open a public issue for anything that could expose users' keys or chats.

Include what you found, how to reproduce it, and what an attacker could do with
it. You'll get a reply as soon as practical; this is a personal project,
maintained occasionally.

## Supported versions

Only the latest release on the `main` branch receives fixes.

## How Conclave protects you

- **Local only.** The server binds to `127.0.0.1` (`.streamlit/config.toml` and
  the launcher both enforce it), with Streamlit's XSRF and CORS protection on.
  Conclave has no login, so it must never be exposed on a network.
- **Encrypted keys.** API keys are encrypted with Windows DPAPI under your
  Windows login. Without DPAPI available, Conclave refuses to save keys rather
  than writing them in readable form.
- **Nothing leaves except to the companies you enable.** No telemetry; Streamlit
  usage statistics are off. Questions go only to the AI providers and optional
  helpers (Tavily, Brave, Cerebras, TypeSafe) you switch on.
- **No secrets in the repository.** `api_keys.json` and the chat history files
  are in `.gitignore`. Error messages shown in the app include an error's type,
  never its text, where that text could contain key material.

## Things to keep in mind

- Anyone who can sign in to your Windows account can use your saved keys and
  read your chats. Treat the app folder like any other private data.
- Each AI provider's own data and retention policies apply to what you send.
- Set spending limits in each provider's billing console; Conclave's cost
  figures are estimates.
