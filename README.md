# Unofficial Makera CLI

A command line interface for the [Makera Z1](https://www.makera.com/) desktop CNC machine. It talks to the machine over WiFi with the same framed protocol Makera Studio uses, so you can check status, manage files on the SD card, read the machine configuration, grab a camera frame, and (with explicit confirmation) move the machine, switch outputs and start or stop jobs. All of it works from a terminal or a script.

> [!IMPORTANT]
> This project is not affiliated with, endorsed by, or supported by Makera. It is built from the open-source [Carvera Community controller](https://github.com/Carvera-Community/Carvera_Controller) and from observing how the machine behaves. A CNC machine can injure people and damage itself, the tooling and the work. Read [Safety](#safety) before you use any action command.

## Contents

- [Why](#why)
- [Status](#status)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [How it connects](#how-it-connects)
- [Global options](#global-options)
- [Command reference](#command-reference)
  - [Reading the machine](#reading-the-machine)
  - [Files on the SD card](#files-on-the-sd-card)
  - [Camera](#camera)
  - [Actions](#actions)
- [Safety](#safety)
- [JSON output and scripting](#json-output-and-scripting)
- [Exit status](#exit-status)
- [Protocol notes](#protocol-notes)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Credits](#credits)
- [License](#license)

## Why

Makera Studio is a desktop application with no scripting interface. This CLI exposes the machine itself, so that you can:

- Upload a batch of G-code files after posting them from your CAM software.
- Check what the machine is doing from another room, or from a script.
- Read work offsets, tool length offsets and the last probe point as numbers.
- Pull `config.txt` to see soft limits and other settings.
- Save a camera frame to check stock and fixtures before a job.
- Let an automation tool or AI agent read machine state, with motion kept behind an explicit `--yes`.

## Status

This is an early release. The table shows what has been exercised against a real Z1 (firmware `1.1.3.0.1.13`) and what has only been tested offline.

| Area | Commands | Tested on a Z1 |
|---|---|---|
| Discovery | `find` | Yes |
| Reading | `status`, `version`, `wcs`, `diagnose` | Yes |
| Files | `ls`, `download`, `config` | Yes. A downloaded file matched the original's MD5. |
| Files | `upload` | Yes. Size and the machine's `upload success` confirmed. |
| Camera | `snapshot` | Yes |
| Actions | `light` | Yes. On and off confirmed with `diagnose`. |
| Actions | `jog` | Yes, single and multi-axis, with soft-limit refusals confirmed. |
| Actions | `goto --machine` | Yes, including the raise to safe Z before an XY move. |
| Actions | `send` | Yes. `send --wait` is tested offline only. |
| Actions | `play`, `pause`, `resume` | Yes. See [Known firmware behaviour](#known-firmware-behaviour). `pause` does not stop motion, and `play` triggers automatic bed cleaning at the end of the job. |
| Actions | `goto` in work coordinates, `home`, `unlock`, `park`, `zero`, `spindle`, `vacuum`, `ext`, `air`, `abort` | No. Only the planning and safety checks are tested, offline. |

Other Makera machines (Carvera, Carvera Air) use related protocols but are untested. The CLI refuses machines that answer in the older plain-text protocol.

## Requirements

- Python 3.10 or newer. There are no third-party dependencies.
- A Makera Z1 on the same local network as the computer running the CLI.
- No other client connected to the machine. The Z1 accepts one client at a time, so disconnect Makera Studio first.

## Installation

With [pipx](https://pipx.pypa.io/) (recommended for command line tools):

```bash
pipx install git+https://github.com/MattRuhland/unofficial-makera-cli
```

With pip, into a virtual environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install git+https://github.com/MattRuhland/unofficial-makera-cli
```

From a clone, without installing:

```bash
git clone https://github.com/MattRuhland/unofficial-makera-cli
cd unofficial-makera-cli
PYTHONPATH=src python3 -m makera_cli --help
```

Any of these gives you the `makera` command. Check it with `makera --version`.

## Quick start

```console
$ makera find
Makera_Z1 at 192.0.2.10:2222, state Idle

$ makera status
state     Idle
mpos      X   -11.600  Y   -14.600  Z    -3.000  A     0.000
wpos      X    90.688  Y    92.132  Z    65.486  A   -50.000
tool      T1  TLO 6.525
feed      0 mm/min  (target 2000, override 100%)
spindle   0 rpm  (target 10000, override 100%)

$ makera upload part-T1.cnc part-T2.cnc --prefix job42-
$ makera ls
```

Each command finds the machine by its broadcast, connects, does its work and disconnects. To skip discovery, pass the address:

```bash
makera --host 192.0.2.10 status
```

## How it connects

1. Discovery. The Z1 broadcasts `Makera_Z1,<ip>,<port>,<busy>,<state>` on UDP port 3333 every second. The CLI listens for up to 6 seconds and uses the first broadcast it hears. If `busy` is 1, another client holds the machine and the CLI stops with a message.
2. Connection. TCP to port 2222. The CLI sends `echo echo` and expects silence, which marks Makera's framed protocol. A plain-text echo means the older Smoothie protocol, which this CLI does not support.
3. Status first. Every connected command reads the machine status before it does anything else, and uses the state to decide whether it may continue.

The machine's address comes from DHCP and can change between power cycles. Discovery handles that. A DHCP reservation in your router makes `--host` reliable.

## Global options

Global options go before the command name, for example `makera --json status` or `makera --yes jog X10`.

| Option | Meaning |
|---|---|
| `--host HOST` | Machine address. Skips discovery. |
| `--port PORT` | Machine TCP port. Default 2222. |
| `--json` | Machine-readable output for `find`, `status`, `wcs`, `diagnose`, `ls` and `config`. |
| `--yes` | Send an action. Without it, actions only print what they would send. |
| `--force` | Skip the machine-state check. Use with care. |
| `--timeout SECONDS` | How long to wait for a move to finish. Default 120. |
| `--version` | Print the CLI version. |

## Command reference

### Reading the machine

Reading commands never change machine state. Apart from `status`, they run only when the machine is Idle, Sleep or Alarm.

#### `find`

Listens for the discovery broadcast and prints what it hears. It does not connect, so it is safe at any time, including during a job.

```console
$ makera find
Makera_Z1 at 192.0.2.10:2222, state Run
```

#### `status`

Runs in any state, including during a job. Shows the state (`Idle`, `Run`, `Hold`, `Alarm`, `Home` and so on), machine and work positions, the active tool and its length offset (TLO), feed and spindle with overrides, and job progress while a file plays.

#### `version`

Prints the firmware version, for example `version = 1.1.3.0.1.13`.

#### `wcs`

Sends `$#` and `get wcs`, and prints the work coordinate systems G54 to G59.3, G28, G30, G92, the tool length offset, the last probe point and the active work coordinate system (`active`, also in the JSON output).

```console
$ makera wcs
G54    X  -102.288  Y  -106.732  Z   -75.011  A    50.000  B     0.000
G55    X     0.000  Y     0.000  Z     0.000  A     0.000  B     0.000
...
TLO    6.5247
PRB    X    -9.300  Y   -12.510  Z   -70.714  (hit)
active G54
```

`PRB` is in machine coordinates. The tool length measurement also probes, so after a tool change `PRB` shows the tool setter's position.

#### `diagnose`

Prints the switch and sensor report as raw values: spindle (`S`), laser (`L`), vacuum (`V`), spindle fan (`F`), light (`G`), tool sensor (`T`), inputs (`E`), WiFi signal strength (`RSSI`) and others. The meaning of each field follows the community controller's diagnose screen.

### Files on the SD card

#### `ls [DIR]`

Lists a directory. The default is `/sd/gcodes`. Directories come first and show `<dir>`. Dates are as the machine reports them. A machine with an unset clock stamps uploads as 1980-01-01.

```console
$ makera ls
     <dir>  20260601084912  Examples/
    837428  20260914171848  part-T1.cnc
```

#### `upload FILE... [--dir DIR] [--prefix PREFIX]`

Uploads one or more files to `/sd/gcodes` (or `--dir`). `--prefix` is added to each remote name, which helps keep jobs apart. An upload overwrites a file with the same name without asking. The CLI requires the machine's own `upload success` message before it reports a file as sent.

The Z1 answers `md5sum` with a fixed placeholder value, so compare sizes with `makera ls` to check an upload.

#### `download REMOTE [LOCAL]`

Downloads a file and prints its size and MD5. The default local name is the remote base name. The file is written to `LOCAL.part` first and renamed when the transfer completes.

#### `config [KEY...] [--save PATH]`

Downloads `/sd/config.txt` and prints every setting, or only the keys you name. `--save` keeps the raw file.

```console
$ makera config soft_endstop.enable soft_endstop.x_min soft_endstop.y_min soft_endstop.z_min
soft_endstop.enable                      true
soft_endstop.x_min                       -207.0
soft_endstop.y_min                       -206.0
soft_endstop.z_min                       -102.0
```

### Camera

#### `snapshot [OUT.jpg] [--resolution N]`

Saves one frame from the built-in camera. The default file is `makera-snapshot.jpg`.

The camera serves one stream and gives it to the newest client. A snapshot cuts off a live camera view open in Makera Studio, and Studio may not reconnect until you reopen its camera view.

`--resolution` sets the camera's frame size before capture. The setting persists on the camera and affects every viewer.

| Value | Size | Approximate frame rate |
|---|---|---|
| 10 | 640x480 | 20 fps |
| 11 | 800x600 | |
| 12 | 1024x768 | |
| 13 | 1280x720 | |
| 14 | 1280x1024 | |
| 15 | 1600x1200 | 10 fps |

### Actions

Actions change the machine's state. Each one prints the console lines it would send and stops, unless you pass `--yes`:

```console
$ makera goto X0 Y0 Z5
would send G53 G0 Z-2
would send G90 G0 X0Y0
would send G90 G0 Z5
dry run: add --yes to send

$ makera --yes goto X0 Y0 Z5
send G53 G0 Z-2
send G90 G0 X0Y0
send G90 G0 Z5
state Idle  machine X  -102.288  Y  -106.732  Z   -63.486  A     0.000
```

The second output is illustrative, because `goto` in work coordinates has not yet run against a real machine. `goto --machine` has (see [Status](#status)).

After any move, the CLI polls status until the machine is Idle and then prints the final machine position. Pressing Ctrl-C while it waits stops the CLI and sends nothing to the machine. The machine keeps moving. Use its stop button to stop it.

Some actions print a warning with their plan, in dry runs and real runs alike: `play`, `pause`, `send` and `spindle on`.

Actions need the machine Idle, with three exceptions. `pause`, `resume` and `abort` run in any state, and `unlock` runs in Alarm.

| Command | Sends | Notes |
|---|---|---|
| `home` | `$H` | Homes all axes. |
| `unlock` | `$X` | Clears an alarm. Find the cause first. |
| `jog AXIS... [--feed F]` | `$J X10`, one line per axis | Relative moves, for example `jog X10 Y-5 Z2`. Each target is checked against the soft limits. Axes move one after another. |
| `goto AXIS... [--machine] [--direct]` | `G90 G0 ...` or `G53 G0 ...` | Rapid move in work coordinates, or machine coordinates with `--machine`. Raises Z to machine Z-2 first unless `--direct`, then moves XY, then Z. Checked against the soft limits. |
| `park clearance\|origin\|anchor1\|anchor2` | `M496.1` to `M496.4` | Moves to the clearance position, the work origin, or anchor 1 or 2. |
| `zero AXES [--value N]` | `G10L20P0X0Y0` | Sets the current position to N (default 0) on the named axes of the active work coordinate system, for example `zero xy` or `zero z`. Does not move. |
| `spindle on RPM` / `spindle off` | `M3 S12000` / `M5` | Refuses `on` while the active tool is the 3D probe (0, 9999, 999990 and up), the laser (8888) or none (-1). |
| `light on\|off` | `M821` / `M822` | Work light. |
| `vacuum on\|off` | `M801 S100` / `M802` | Untested on a Z1. |
| `ext on\|off` | `M851 S100` / `M852` | External output. |
| `air on\|off` | `M7` / `M9` | Untested on a Z1. |
| `play NAME [--dir DIR]` | `play /sd/gcodes/NAME` | Checks that the file exists first. Starts the job. When the job ends, automatic bed cleaning can spin the loaded cutter, even if the file has no `M3`. |
| `pause` | `!` (realtime) | Feed hold. Does not stop motion on firmware 1.1.3.0.1.13. |
| `resume` | `~` (realtime) | Cycle start, clears a hold. |
| `abort` | `abort` | Stops the running job. |
| `send 'LINE' [--wait]` | the line as given | Any console command or G-code. No checks beyond the state check. With `--wait`, the CLI waits for the machine to start moving and then to stay Idle for three polls in a row. That suits firmware macros such as probe cycles, which pass through Idle between steps. `--timeout` applies. |

Manual tool changes (`M6`) are not included yet. During a manual change the machine waits for confirmation from the connected client, and the CLI cannot answer that prompt.

## Safety

The CLI adds checks in front of the firmware. It never replaces your attention at the machine.

What the CLI does before sending an action:

- Prints the exact lines and sends nothing without `--yes`.
- Refuses unless the machine is in a suitable state (see above). `--force` overrides this, so use it only when you understand why the check failed.
- For `jog` and `goto`, downloads `config.txt`, reads `soft_endstop.<axis>_min`, and refuses any target outside the box from that minimum to 0. Home is 0 on every axis and travel runs negative. Work coordinates are converted to machine coordinates with the offset and rotation from the status report, which already include the active tool length offset. If soft limits are off in the configuration, the CLI warns and skips this check.
- For `goto`, raises Z to machine Z-2 before any XY move, unless `--direct`.
- For `spindle on`, refuses when a probe, the laser or no tool is active.
- For `play`, `pause`, `send` and `spindle on`, prints a warning about the risk the checks cannot cover.
- During a move, stops with an error if the machine enters Alarm.

What the CLI cannot know:

- Clamps, vises, fixtures and the stock itself. The soft limits describe only the machine's travel.
- Whether a tool is really in the collet, or which tool it is.
- Whether the machine has been homed since power-on, or whether homing has drifted.
- Anything about `send`. Raw lines go straight to the machine.
- What the firmware does on its own, such as the end-of-job routine below.

Before the first real use, run each action without `--yes` and read the plan. Stand at the machine with the emergency stop in reach for every move. The CLI has no command that reliably stops motion. The stop button on the machine does.

The firmware's own soft limits stay enabled as the last line of defence.

### Known firmware behaviour

Observed on a Z1 with firmware `1.1.3.0.1.13`:

| Behaviour | Detail |
|---|---|
| Feed hold does not stop motion | After `!` (the `pause` command), the state reads `Hold`, but the axes keep moving at the programmed feed and the queued moves end on schedule. This was measured by polling position every 0.9 seconds over one connection, for a console `G1` move and during file playback. `resume` returns the state to `Run`. |
| Position is misreported during Hold | While the state is `Hold`, `MPos` in the status report shows the end point of the queued moves. The true position is unknown until the state leaves `Hold`. |
| Automatic bed cleaning after a job | When playback of any file ends, the firmware runs its bed-cleaning routine if `bed_cleaning.enable` is `true` in `config.txt`. The machine moves to the front of the bed, lowers the brush and can spin the loaded cutter. This happens even when the file contains no spindle command. Check the setting with `makera config bed_cleaning.enable`. |
| `$J` needs spaces between axes | `$J X-3 Y-3` moves diagonally. `$J X-3Y-3` moves only X and reports no error. The `jog` command sends one axis per line, so it is not affected. Watch for this when you use `send`. |

## JSON output and scripting

`--json` prints one JSON document on stdout for `find`, `status`, `wcs`, `diagnose`, `ls` and `config`. Messages from the machine go to stderr, so stdout stays parseable.

```bash
# Current machine position
makera --json status | jq '.mpos'

# Wait until the machine is idle
until [ "$(makera --json status | jq -r .state)" = "Idle" ]; do sleep 10; done

# Last probe point in machine coordinates
makera --json wcs | jq '.PRB'

# Soft limits
makera --json config soft_endstop.x_min soft_endstop.y_min soft_endstop.z_min
```

`status --json` contains `state`, `mpos`, `wpos`, `tool`, `tlo`, `feed`, `spindle`, `playing` (only while a job runs) and `raw`, which holds every field of the status report as reported.

For an AI agent or other automation, a practical rule is to allow every reading command freely and require a person to approve any command that uses `--yes`.

## Exit status

| Code | Meaning |
|---|---|
| 0 | Success, including a dry run of an action |
| 1 | A reported failure: no machine found, connection refused, wrong state, a safety check refused the action, a transfer failed |
| 2 | Invalid command line |
| 130 | Interrupted with Ctrl-C |

## Protocol notes

The Z1 uses Makera's framed binary protocol on TCP port 2222. Each frame is:

```
0x86 0x68 | length (2 bytes) | type (1 byte) | payload | CRC16-CCITT (2 bytes) | 0x55 0xAA
```

Commands go out as `CTRL_MULTI` frames (type `0xA2`) with no trailing newline. Realtime characters such as `?`, `!` and `~` go out as `CTRL_SINGLE` frames (type `0xA1`). Replies come back by type:

| Reply | Frame type | Content |
|---|---|---|
| Status | `STATUS_RES` (`0x81`) | One `<State\|MPos:...\|WPos:...\|...>` report |
| Diagnose | `DIAG_RES` (`0x82`) | One `{S:...\|L:...\|...}` report |
| Directory listing | `LOAD_INFO` (`0x83`), then `LOAD_FINISH` (`0x84`) | `name size YYYYmmddHHMMSS` rows |
| Text | `NORMAL_INFO` (`0x90`) | Fragments, joined into lines at newlines |

File transfers use the `FILE_*` frame types (`0xB0` to `0xB6`) with 8192-byte packets that the machine requests by sequence number. Paths in commands escape spaces as `0x01` and the realtime characters `? & ! ~` as `0x02` to `0x05`.

The frame encoder and the transfer code are vendored unmodified from the Carvera Community controller. See [`src/makera_cli/_vendor/carvera/SOURCE.md`](src/makera_cli/_vendor/carvera/SOURCE.md).

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `no Makera broadcast on UDP 3333` | The machine is off, still booting (it can take several minutes to join WiFi), or on another network or VLAN. A firewall may block incoming UDP. Use `--host`. |
| `the machine reports a client already connected` | Makera Studio or another tool is connected. Disconnect it in that application. |
| `cannot connect to ...:2222` | Same as above, or the address changed. Run `makera find`. |
| `machine state is Run` | A job is running. Reading and file commands wait for it to end, because the machine serves the SD card to the job. `status`, `pause` and `abort` still work. |
| `no "upload success"` | The transfer finished but the machine did not confirm it. Check the size with `makera ls` and upload again. |
| Studio's camera view froze | `makera snapshot` took the camera stream. Reopen the camera view in Studio. |
| The machine moved to the front and spun the tool after a job | Automatic bed cleaning. See [Known firmware behaviour](#known-firmware-behaviour). |
| `pause` showed Hold but the machine kept moving | Feed hold does not stop motion on this firmware. Use the machine's stop button. |

## Development

```bash
git clone https://github.com/MattRuhland/unofficial-makera-cli
cd unofficial-makera-cli
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The tests use only the standard library and need no machine. They cover frame splitting, discovery parsing, every reply parser, the WebSocket framing, coordinate conversion, soft-limit checks and the plan for every action.

Project layout:

```
src/makera_cli/
  cli.py        argument parsing and command dispatch
  link.py       TCP link, discovery broadcast, protocol detection
  protocol.py   command session and reply parsers
  transfer.py   upload and download
  camera.py     snapshot over the camera WebSocket
  motion.py     action planning and safety checks
  _vendor/      unmodified files from the Carvera Community controller
tests/          offline unit tests
```

Contributions are welcome, especially reports from other firmware versions and other Makera machines. When you report an issue, include `makera version` output and, where relevant, the `--json` output of the command involved.

## Credits

- The [Carvera Community](https://github.com/Carvera-Community) and their [controller](https://github.com/Carvera-Community/Carvera_Controller), whose protocol implementation, command strings and reply parsers this CLI builds on.
- [tehmaze/xmodem](https://github.com/tehmaze/xmodem), the origin of the transfer code.

## License

GNU General Public License v2.0. See [LICENSE](LICENSE). The vendored files keep their upstream license and attribution, listed in [NOTICE](NOTICE).

"Makera", "Makera Z1", "Makera Studio" and "Carvera" are trademarks of their respective owners. They are used here only to describe compatibility.
