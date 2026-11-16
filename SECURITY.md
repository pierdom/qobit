# Security policy

## Supported versions

Only the latest release gets security fixes. Upgrade with
`uv tool upgrade qobit` (or `pipx upgrade qobit`).

## Reporting a vulnerability

Please report it privately, not in a public issue:
[open a security advisory](https://github.com/pierdom/qobit/security/advisories/new)
on GitHub. Include what an attacker can do, the steps to reproduce it, and the
qobit version and OS.

You should hear back within a week. Once a fix is released, the advisory is
published with credit to you, unless you'd rather stay anonymous.

## Scope

qobit holds a Qobuz session token and shows text written by other Qobuz
users (public playlists). Reports in these areas are especially welcome:

- the session token: where it is stored (system keyring, else
  `~/.config/qobit/config.json`), and anywhere it could leak (logs, crash
  screens, URLs, process arguments)
- the browser login: the local redirect server and the code it accepts
- text from the Qobuz API reaching the terminal (escape sequences)
- files qobit writes (`~/.config/qobit`, `~/.local/share/qobit`,
  `~/.cache/qobit`) and the mpv control socket
- Qobuz Connect: messages from the Qobuz servers

Out of scope: vulnerabilities in Qobuz's own services or apps (report them to
Qobuz), and in mpv, Python or other dependencies, unless qobit uses them in an
unsafe way.
