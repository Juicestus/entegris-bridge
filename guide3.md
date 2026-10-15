# R3/R4 hardware test guide

Four tests, run in order, decide between each. Read-only throughout — no test in this ladder
builds a write command, and the allowlist cannot produce one. These are production pumps.

**If the call gets cut short:** test 3 is the one that matters. Its capture directory is the
deliverable, and test 4 runs later with no pump. Test 5 is worth the two minutes if you have them —
its probe mode is seconds and answers a question nothing else does.

---

## Pre-flight

Check these four lines match Carson's MMI string (`2:10.25.85.52:8888:COM8` → addr 2, COM8) in
**each** of `t1_regression.py`, `t2_fixes.py`, `t3_profile_pull.py`:

```python
HOST = "10.25.85.52"
PORT = 8888
PUMP_COM = 8
PUMP_ADDR = 2
```

They are duplicated per file on purpose — a wrong one only affects that test.

Confirm the pump has **dispensed at least once since power-up**. It does not need to be dispensing
right now — an idle pump with a stored profile is the *easier* case, because the cycle number
can't roll over mid-pull. Only a pump that has never dispensed is a problem, and the fix is to
change `PUMP_ADDR` to a neighbor on the same COM port rather than to ask for a dispense.

Capture every run's stdout to a file. `python t1_regression.py 2>&1 | tee t1.out` and so on.

---

## Test 1 — regression

```
python t1_regression.py
```

Confirms the two fixes broke nothing. Runs the full 17-command probe unchanged, bracketed by
requests-served counter reads.

| Looking for | |
|---|---|
| PASS | Same command outcomes as the Oct 13 run, all 11 struct lengths matching, served counters up per port |
| Expected change | `DISPENSE_INFO` now answers GOOD instead of gateway rc 10 |
| STOP | Any struct length mismatch, or a command that worked Oct 13 now failing |

A struct length mismatch here means something changed on the pump or gateway side since Oct 13 —
that is a finding worth more than the rest of the session. Stop and capture it.

→ Clean? Go to test 2. Broken? Stop; nothing below is interpretable.

---

## Test 2 — the two fixes

```
python t2_fixes.py
```

Two `DISPENSE_INFO` requests: one with recipe 0 (most recent dispense), one with no data.

| Verdict line | Meaning | Next |
|---|---|---|
| Fix A PASSED | 164 bytes back, 8 known fields decoded | Go |
| Fix A FAILED | Recipe number wrong, or command not supported | Go anyway — test 3 doesn't use DISPENSE_INFO |
| Fix B PASSED | Pump's bounds error came back as the pump's answer, not a transport failure | Go |
| Fix B NOT EXERCISED | Pump accepted an empty request | Fine, note it |
| Fix B FAILED | No pump answer at all | Go, but expect test 3 to hide pump errors too |

Fix B failing is worth knowing before test 3, because it means a pump-side refusal mid-pull will
surface as a transport error instead of a return code.

→ Either way, go to test 3. Neither fix gates it.

---

## Test 3 — profile pull (the one that matters)

```
python t3_profile_pull.py
```

Reads the current profile cycle, reads the trace sizes, then pulls all ten traces smallest-first
and writes each one to disk the moment it arrives. Expect roughly 14 round trips and 6.4 KB.

**Every trace file is written before any check runs.** A crash on trace 4 still leaves traces
7, 9, 8, 5 and 6 on disk and worth taking home.

| Verdict | |
|---|---|
| PASSED | Every trace matched its encoded size, profile info unchanged across the pull |
| NOT CLEAN | Something is off — read the lines above the verdict |

Watch the first trace's `asked for N, received M` line. If M ≠ N on trace 7, the reply has a
header we didn't account for, and it will be wrong on all ten. That's a 30-second finding, not a
session-ender — note the offset and move on.

→ Clean or not, **zip the capture directory**. That is the deliverable.

Worth running twice if there's time, a minute apart, to get two different cycles.

---

## Test 5 — the reference profile

Read-only, and it varies **only the cycle number** — never a command code. An out-of-range cycle
comes back as `BOUNDS_ERROR`, which is a clean answer, not a failure.

```
python t5_reference.py
```

One `GET_PROFILE_TRACE_INFO` per candidate cycle, nothing pulled, no files written. Prints a table:
which cycles the pump answers for, what profile type each reports, and how far back profiles are
retained. Takes seconds.

Read the table, then pull the one that matters:

```
python t5_reference.py --pull <cycle>
```

| Looking for | |
|---|---|
| A candidate with a **different profile type** than the live cycle | That's the reference profile. Pull it — it's the best target we have |
| A candidate that answers GOOD but reports a *different cycle than asked for* | That value is an alias, not a literal cycle. Most interesting result in the test. **Pull the value you asked for first; if the traces refuse or come back the wrong size, pull the cycle the reply reported instead** — we don't know which one a real pump serves traces for |
| The deepest `N - k` that answers | Retention depth — a *range*, not a number, since the candidates step 1, 2, 5, 10, 50. A range is enough to size the historian's polling slack; don't spend call time narrowing it |
| Everything but `N` returning `BOUNDS_ERROR` | Fine. Means profiles aren't addressable by arbitrary cycle, which is itself the finding |

A static profile should pull byte-identical twice. If you have time, `--pull` the same cycle twice
and `diff` the directories — that checks the whole transport path in a way a live profile can't.

→ Capture directories from test 5 decode with test 4 unchanged.

---

## Test 4 — offline decode

```
python t4_decode.py captures/profile_<cycle>_<timestamp>
```

No pump, no gateway, no network. Run it on the call if there's time, or afterwards. Decodes test 5's
capture directories too, unchanged.

| Result | |
|---|---|
| CSV written, 2144 rows, no blank columns | Done. Compare a few numbers against the MMI's own export |
| Some columns blank | Those traces failed to decode or came out the wrong length — listed in the `NOT_DECODED` header line |
| All columns blank | Decoder is wrong for this data, not a capture problem. Files are still good |

First row's time column should read `-2.000`.

---

## Triage

### Gateway return codes

| Code | Means | Do |
|---|---|---|
| 1015 `SERIAL_READ_TIMEOUT` | Pump didn't answer. Wrong address, wrong COM, or bus busy | Confirm addr/COM with Carson. The code already waits out the timeout |
| 1016 `SERIAL_OUTPUT_INVALID_CRC` | Pump's reply was corrupted on the bus | Retry once. Repeating = bus or wiring |
| 1006 `INPUT_INVALID_CRC` | **Our** CRC was rejected before the bus | Shouldn't happen — CRC is verified against vendor vectors. Capture and stop |
| 1001–1005, 1007+ | See `ReturnCode` in `ncs_protocol.py` | Capture the hex dump |

### Pump return codes

| Code | Means | Do |
|---|---|---|
| 6 `BUSY` | Mid-dispense | Wait a cycle, retry |
| 10 `BOUNDS_ERROR` | A request field is out of range — the next two bytes give the field index | Usually a stale cycle number. Rerun test 3 |
| 36 `NOT_SUPPORTED` | Firmware doesn't have this command | Note the firmware version from test 1 |
| 13 `UNKNOWN_SVID` | One SVID in the list isn't on this firmware | Expected on some; the probe's tiered fallback handles it |
| 1 `SIZE_ERROR` | Our request length is wrong for the command | Capture the request hex dump — that's a code bug |

### Test 3 specifics

| Symptom | Means | Do |
|---|---|---|
| `asked for N, received M`, M < N consistently | Reply carries a header | Note the byte offset, continue |
| `received 0 words, stopping` | Trace empty or cycle gone | Check whether the pump is still dispensing |
| `THE PROFILE INFO CHANGED DURING THE PULL` | Cycle rolled over mid-pull | Traces may be from two profiles. Rerun |
| `ENCODING IS n, NOT THE COMPRESSION THE DECODER KNOWS` | Different encoding | Raw bytes still saved. Test 4 won't decode. Big finding |
| No profile cycle from either status command | Pump hasn't dispensed since power-up | Change `PUMP_ADDR` to another pump on COM8 — up to 12 are there and one will have dispensed. Only ask Carson for a dispense if none has |

### Test 4 specifics

| Symptom | Means |
|---|---|
| Short sample count on the big traces (0, 1, 4, 2, 3) only | Delta path. Most likely a delta count not a multiple of three mid-trace |
| Garbage values, correct count | Delta bit position |
| Small traces (7, 9, 8, 5, 6) fine, big ones not | Confirms it's the delta coding and nothing else |
| `bit 15 set` warning | One delta *may* be wrong — only if that word's first delta was negative, otherwise harmless. Note the trace, don't chase it |

The small-traces-first ordering exists for exactly this split — run-coded traces decode correctly
even if the delta path is broken, so the pattern of failures localizes the bug.

---

## Hard stop

Stop the session and capture everything if:

- Any command returns a code not in `ReturnCode` or `PumpReturnCode`
- A struct length mismatch appears in test 1 that wasn't there Oct 13
- Carson reports anything unusual on the tool or the pump

---

## Take home

1. `t1.out` through `t4.out`
2. The whole `captures/` directory — every run, tests 3 and 5 both, even failed ones
3. The MMI's own CSV export of the same cycle if Carson can produce one — that's the ground truth
   for test 4
4. Firmware version and pump serial from test 1's output