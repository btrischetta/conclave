# Contributing to Conclave

Thanks for your interest. Conclave is a personal project maintained
occasionally, so small, focused pull requests are the easiest to review.

## Getting set up

```bash
git clone https://github.com/btrischetta/conclave.git
cd conclave
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m unittest discover -s tests -v
```

Conclave targets Windows (keys are protected with Windows DPAPI) and Python
3.14. The whole app is one file, `conclave.py`, built on Streamlit.

## Ground rules

- **No paid calls in tests.** The test suite must stay free: it never calls a
  real AI provider and never reads real keys or chats (Streamlit tests run with
  `CONCLAVE_TEST_DATA_DIR` pointing at an empty temporary folder).
- **Never commit keys or chats.** `api_keys.json` and `ai_history*.json` are
  ignored; keep it that way.
- **Model and price changes need a source.** Link the provider's official
  documentation page in your pull request. See
  [MODEL_MAINTENANCE.md](MODEL_MAINTENANCE.md).
- **Keep the app local-only.** Don't change the `127.0.0.1` binding or weaken
  how keys are stored.
- **Bump the version.** Update `APP_VERSION` in `conclave.py` and add an entry
  to [CHANGELOG.md](CHANGELOG.md).

## Good first contributions

- Model updates from **Settings → Check models**.
- Streamlit upgrades: Conclave's look relies on custom CSS that targets
  Streamlit's internal page structure, which can change between versions.
- Support for storing keys securely on macOS and Linux.

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).
