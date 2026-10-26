# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-26

### Added

- `makera` command with discovery over the UDP broadcast and Makera's framed protocol over TCP.
- Reading commands: `find`, `status`, `version`, `wcs`, `diagnose`.
- SD card commands: `ls`, `upload`, `download`, `config`.
- Camera `snapshot`, with an optional persistent resolution change.
- Actions behind `--yes`: `home`, `unlock`, `jog`, `goto`, `park`, `zero`, `spindle`, `light`,
  `vacuum`, `ext`, `air`, `play`, `pause`, `resume`, `abort`, `send`.
- Safety checks: machine-state gating, soft-limit pre-checks for `jog` and `goto`, a raise
  to safe Z before `goto` moves XY, spindle refusal with a probe, laser or no tool active, and a
  feed hold on Ctrl-C during a move.
- `--json` output for the reading commands.
- Offline unit tests using only the standard library.
