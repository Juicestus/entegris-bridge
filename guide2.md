# Hardware test run — quick guide

Read-only. No write, control, reset or self test command can be sent.
~4 seconds of actual bus time across all commands.

---

## Before sending the zip

- [ ] `HOST` / `PORT` / `PUMP_COM` / `PUMP_ADDR` set in `pump_probe.py`, confirmed with Carson
- [ ] `HOST` / `PORT` set in `ncs_probe.py`
- [ ] Extract the zip to an **empty folder** and run both scripts against the mock from there
      (this is what catches a missing file)
- [ ] Files present: `ncs_probe.py` `pump_probe.py` `ncs_client.py` `ncs_protocol.py`
      `pump_client.py` `pump_protocol.py` `pump_structs.py` `svid.py` `units.py`
      `crc16.py` `util.py`
- [ ] Mock **not** included

Prefer a pump on COM3–7. Those showed 0 requests served; COM8 showed 972,388.

---

## Run order

```
python3 ncs_probe.py  > ncs.txt  2>&1
python3 pump_probe.py > pump.txt 2>&1
```

Get **both .txt files** back as files, not pasted text.

**1. `ncs_probe.py` first** — 10 seconds, proves the box is reachable before spending
the session on pump traffic. Also: compare `served` to yesterday
(COM1 75,358 · COM2 129,056 · COM8 972,388). The delta is the bus traffic rate,
which is the Phase 3 capacity number.

**2. `pump_probe.py`** — under a minute. If it looks frozen it's waiting on a
timeout; give it 30 seconds.

If `FIRMWAREINFO` (the first pump command) fails, **stop and fix addressing** —
everything after it will fail the same way.

---

## What good looks like

| Check | Expect |
|---|---|
| `FIRMWAREINFO` | ProductQualifier **1234**, Major **1011**, Minor **1536** |
| `CS_RTSTATUS` | Model and PumpSerialNumber as readable text |
| Summary | `17 of 17 commands answered GOOD` |

Then the four answers at the bottom — these are the run's real output:

- **SVIDs** — do they work, and do they reach password-gated fields
- **DISPENSE_INFO** — `BAD_PASSWORD` or not
- **Profile** — present, and `UnencodedSize` (the Phase 3 bandwidth number)
- **Alarms** — did records come back

---

## Triage

| You see | Means | Do |
|---|---|---|
| `ConnectError`, first command | wrong IP/port or firewall | open `http://<ip>/` in a browser — NCS serves its own web page |
| gateway **1015** on every pump command | nothing answering at that address/COM | **most likely failure.** Wrong COM or address — get it from the MMI Details column or any profile CSV header |
| gateway **1004** | COM out of range | must be 1–8 |
| gateway **1005** | port disabled | pick another COM |
| gateway **1006** | our CRC rejected before the bus | surprising, CRC is vector-verified. Malformed frame |
| gateway **1016** | pump's reply corrupted | noisy bus, rerun |
| pump **6** BUSY | mid-dispense | wait a minute, rerun |
| pump **36** NOT_SUPPORTED | command absent on this model | fine, note it, move on |
| **LENGTH MISMATCH** | my transcription was wrong | **not a failure** — raw bytes are logged, fixable offline |
| first works, rest time out | 10s lockout cascading | slow but completes, let it run |

**A failed run is still a useful run.** Every command prints raw bytes whether it
succeeds or not. The only outcome that costs a second session is not reaching the box.

---

## Raise with Carson on the call if…

**`DISPENSE_INFO` = BAD_PASSWORD *and* the SVID password pair also fails**
→ part of Phase 2 needs credentials or another route. He can find out whether
passwords were deliberately enabled.

**Everything times out**
→ ask him to confirm the address with the MMI's Scan button on that COM port.

**It all works**
→ ask what's on COM1, COM2, COM8 (the busy ones), and whether COM3–7 pumps are
in production or spare.