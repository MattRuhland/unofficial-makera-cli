# Vendored files

These files are copied unmodified from the
[Carvera Community controller](https://github.com/Carvera-Community/Carvera_Controller)
at commit `63da3c0a8ab6bca2563ff4c2aadb7ab5323335bf`:

| File here | Upstream path |
|---|---|
| `XMODEM.py` | `carveracontroller/XMODEM.py` |
| `protocols/framing.py` | `carveracontroller/protocols/framing.py` |

The upstream project is licensed under the GNU General Public License v2.0. A copy is in
`LICENSE` next to this file. `XMODEM.py` is derived from
[tehmaze/xmodem](https://github.com/tehmaze/xmodem) (MIT, Copyright (c) 2010 Wijnand Modderman),
with Makera framed-transfer support added by the Carvera Community.

The two `__init__.py` files are stand-ins written for this package, so the relative import in
`XMODEM.py` resolves without the rest of the upstream controller (which needs Kivy).

To update, copy the same two files from a newer upstream commit, record the commit here, and
run the test suite.
