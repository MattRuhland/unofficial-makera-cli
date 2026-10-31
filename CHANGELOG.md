# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `send --wait`: wait for the machine to start moving and settle back to Idle, for firmware
  macros that pass through Idle between steps.
- `wcs` reports the active work coordinate system from `get wcs`, as `active`.
- `probe-laser on|off`: the probe alignment laser (`M494.1` / `M494.2`).
- `limits`: the feed limits the firmware is using now, from `M203.1` and `M203`.

### Changed

- The Alarm error no longer sends you straight to `unlock`. It says the position may be wrong and
  that the machine needs homing. `unlock` prints a warning, and `status` adds a note during Alarm.
- After a move, the final line reads `reported` and a note says the position is commanded, not
  measured.
- `config` prints a note on stderr that its values come from the file and can differ from live ones.

### Fixed

- `snapshot --resolution` could save a frame at the old size with no error. It now waits for the
  requested size and fails if it does not arrive.
- `snapshot` could save the garbled frame the camera sends during a size change. It now skips the
  first frames and saves only a frame that matches the one before it.

## [0.1.1] - 2026-10-26

### Changed

- Ctrl-C while waiting for a move no longer sends a feed hold. It stops waiting and sends
  nothing, because a feed hold does not stop motion on Z1 firmware 1.1.3.0.1.13.
- `play`, `pause`, `send` and `spindle on` print a warning with their plan, in dry runs too.

### Documentation

- README: hardware test results for `light`, `jog`, `goto --machine`, `upload`, `send`,
  `play`, `pause` and `resume`.
- README: new "Known firmware behaviour" section. Feed hold does not stop motion, status
  misreports position during Hold, automatic bed cleaning after a job can spin the cutter, and
  `$J` needs spaces between axis words.

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
