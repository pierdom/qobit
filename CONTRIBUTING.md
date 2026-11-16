# Contributing

Bug reports, ideas and pull requests are welcome.

## Reporting a bug

Open an issue with:

- what you did, what you expected, what happened
- `qobit --version`, your OS, your terminal
- your audio output (`qobit devices`), if it's about playback

For a security issue, don't open a public issue: see [SECURITY.md](SECURITY.md).

## Setting up

```bash
git clone https://github.com/pierdom/qobit && cd qobit
uv sync --group dev        # Linux: install the system packages from the README first
uv run qobit
```

## Before opening a pull request

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

- Don't add anything that changes the audio samples (resampling, DSP, software
  volume) unless it's opt-in and off by default.
- Add or update tests for logic changes. For UI changes, include a screenshot.
- Update the docs your change affects: README, `docs/`, and `CLAUDE.md`
  (architecture notes).
- Write commit messages in the [Conventional Commits](https://www.conventionalcommits.org)
  style: `feat(ui): …`, `fix(connect): …`, `docs: …`.
- Keep one topic per pull request. CI must pass.

`CLAUDE.md` explains how the code is organised and the patterns to follow.

## License

Contributions are released under the project's [GPL-3.0](LICENSE) license.
