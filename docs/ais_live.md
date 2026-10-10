# Live AIS from aisstream.io: recorder, watchdog, reach and Sentinel-1 pass windows

> "Dark" does not mean illegal. It means only that no AIS position was matched to a radar contact. Many vessels need not carry AIS, AIS can be off for lawful reasons, and satellite and terrestrial AIS have blind spots. An AIS gap is not proof of intent. The live feed described here comes from shore receivers: where it hears nothing, a radar contact is `no_coverage`, never "dark".

Updated 2026-10-10 (UTC). Owner of this document and of the code it describes: task R1-T1 (`src/darkvessel/ais/aisstream.py`, `src/darkvessel/ais/s1_passes.py`, `scripts/26_ais_record.py`, `scripts/28_ais_reach.py`, `scripts/29_ais_watchdog.py`); the supervision of the live watcher and the CNN run and section 4.5 by task R2-T4 (`scripts/29_ais_watchdog.py`, `scripts/session_start.sh`).

## 1. Why this exists

A Sentinel-1 radar contact can be identified (matched to an MMSI with name, call sign, type and size) in the open build only if live AIS was recorded while the satellite looked. GFW AIS is research only and arrives days late. So the open build needs three things, and this workstream provides them:

1. A recorder that keeps a live AIS record of the whole AOI, unattended, for weeks (`scripts/26_ais_record.py`).
2. A watchdog that restarts it when it dies or goes silent (`scripts/29_ais_watchdog.py`). The same watchdog also keeps the live-pass watcher and the regional CNN run alive (section 4.5).
3. A list of the Sentinel-1 passes over the AOI, past and coming, with how much of each footprint the feed actually hears (`data/s1_next_passes.json`, built by `scripts/28_ais_reach.py`).

The live-pass pipeline (`src/darkvessel/live/`, task R1-T2) reads the recorded positions and static data with `darkvessel.ais.aisstream.load_positions`, `load_static` and `latest_static`, and the pass file for run naming.

## 2. What is recorded

One websocket connection to `wss://stream.aisstream.io/v0/stream` (the service allows three subscribed connections per account and three open connections per IP; the recorder uses one). The client negotiates permessage-deflate (the documentation says uncompressed connections are bandwidth-limited from September 2026), sends one subscription with the three boxes below within the required 3 seconds, and reads until stopped.

Subscription boxes (aisstream wants `[[lat_min, lon_min], [lat_max, lon_max]]`). Each box is the longitude extent of the AOI polygon (Natural Earth "South China Sea", "Gulf of Tonkin" and "Gulf of Thailand", bounds 99.16E to 122.27E, 3.22S to 23.76N) in a latitude band, plus a 0.25 degree margin. They are subscription filters, not boundaries.

| Box | Latitude | Longitude | Covers |
|---|---|---|---|
| 1 | 3.47S to 6.00N | 102.20E to 116.36E | Karimata and Natuna waters to the Sarawak coast |
| 2 | 6.00N to 14.00N | 98.91E to 121.22E | Gulf of Thailand to Palawan |
| 3 | 14.00N to 24.01N | 105.37E to 122.52E | Gulf of Tonkin, Hainan, Paracels to the Luzon Strait |

The boxes are wider than the AOI polygon: they also hear the Singapore Strait, Manila Bay and the coasts around the AOI. Statistics marked "in the AOI" use the polygon.

Message types kept as rows (every frame is also kept raw):

| aisstream `MessageType` | AIS message | Table | Fields kept |
|---|---|---|---|
| `PositionReport` | 1, 2, 3 (class A) | positions | mmsi, timestamp, lon, lat, sog_kn, cog_deg, heading, nav_status, msg_type, msg_id, ais_class, ship_name |
| `StandardClassBPositionReport` | 18 (class B) | positions | as above, ais_class B |
| `ExtendedClassBPositionReport` | 19 (class B) | positions | as above |
| `LongRangeAisBroadcastMessage` | 27 | positions | as above (no heading; SOG 63 and COG 511 mean not available) |
| `ShipStaticData` | 5 (class A) | static | mmsi, imo, name, callsign, ship_type, ship_type_label, length_m (A+B), width_m (C+D), destination, eta, seen_utc |
| `StaticDataReport` | 24 part A and B (class B) | static | name (part A); callsign, ship_type, size (part B) |

Other message types (base stations, aids to navigation, binary messages, SAR aircraft) stay in the raw log only.

Normalisation (`normalise_position`, `normalise_static`): AIS not-available codes become null (latitude 91, longitude 181, SOG 102.3, COG 360, heading 511; long-range SOG 63 and COG 511), out-of-range values become null, positions at 0, 0 are dropped, `@` padding is stripped from names, IMO numbers outside 1000000 to 9999999 are dropped. Ship type labels follow ITU-R M.1371 as reproduced by the US Coast Guard Navigation Center; navigational status labels likewise.

Time. Every row is stamped with `MetaData.time_utc`, which aisstream sets on reception (format `2026-10-08 22:38:51.527287428 +0000 UTC`; parsed to microseconds, offsets applied, always UTC). Partitions are by that UTC hour. The recorder keeps a message out of the tables, in the raw log only, when its `time_utc` is more than 1 hour from the local clock, so a clock fault cannot create stray partitions or move a vessel in time.

## 3. Storage layout

Everything sits under `data/cache/ais/aisstream/` (git-ignored; about 1 MB per recorded hour including the raw log).

| Path | Content |
|---|---|
| `positions/YYYYMMDD/HH.parquet` | one partition per UTC hour, fixed arrow schema `POSITION_SCHEMA` |
| `static/YYYYMMDD.parquet` | static rows of the day, content-deduplicated, fixed schema `STATIC_SCHEMA` |
| `raw/YYYYMMDD/HH.jsonl.gz` | every frame as received, one gzip member per flush |
| `status.json` | recorder counters, connection state, `last_message_utc`, `pid`; written at start and every flush |
| `record.log` | recorder log (each connection attempt and each flush) |
| `recorder.pid` | pid of the running recorder |
| `watchdog.pid`, `watchdog.lock`, `watchdog.log`, `watchdog_status.json` | watchdog files; `watchdog.log` also holds the `[session_start]` lines of the session hook |
| `cnn_resume.out` | answer of the last CNN resume command the watchdog ran (section 4.5) |
| `pass_refresh.log` | log of the pass-plan refreshes started by the watchdog |

Write safety. The recorder flushes every 60 s. Each partition is rewritten through a temp file in the same directory, fsynced and renamed over the target, so a kill at any moment leaves the old file or the new one, never a torn file under the final name. A partition that cannot be read is moved aside as `HH.parquet.corrupt-<time>` (never overwritten; the raw log still holds its rows). A failed write keeps the rows in memory for the next flush. A kill can truncate only the last gzip member of a raw file; `aisstream.read_raw` reads member by member and stops there.

Types. Text columns are object dtype with `None` for missing, in memory and after reading back. pandas 3 (3.0.6 in this environment) reads parquet strings as its `str` dtype with NaN for missing; `positions_frame` and `static_frame` convert every partition back on load, so partitions written before this fix (14:31 to 23:35 UTC on 2026-10-08) load exactly like new ones. This was the cause of the failing test `test_recorder_flush_round_trip` (`ship_name` NaN instead of None); the code, not the test, was wrong.

Credentials. `AISSTREAM_API_KEY` is read from the git-ignored `.env` at the repo root and registered with `aisstream.redact`, which removes its exact value, any 32-or-more hex string and any `api key = ...` or `Authorization:` pattern from every log line, error string and status field. The subscription JSON, which must carry the key, is never logged.

## 4. Keeping it alive: recorder retries and the watchdog

### What happened at 15:20 UTC on 2026-10-08

The first recorder ran from 14:31 UTC. Its log ends with:

```
2026-10-08 15:20:29Z [aisstream] ConnectionClosedError: no close frame received or sent -> retry in 1 s
2026-10-08 15:20:30Z [aisstream] ConnectionRefusedError: [Errno 111] Connect call failed ('127.0.0.1', 39767) -> retry in 2 s
```

then nothing until a new recorder was started by hand at 22:38:51 UTC. The retry loop was working (it logged two retries); the process itself was gone. At 22:50 UTC the machine reported 16 minutes of uptime (`uptime`), so it booted at about 22:34 UTC and the new recorder got pid 515. The container went down after 15:20:30: first the local proxy on 127.0.0.1 stopped (the refused connection), then the processes. The recorder did not hang and did not exit on its own; there was nothing left to restart it. No log line of the recorder's own shutdown exists because it was killed, not stopped.

Lesson: in-process retries cover proxy drops and server disconnects; only a separate process covers a dead recorder; and nothing inside the container survives a container restart. The watchdog must be started again at every session start (section 4.4).

### 4.1 Recorder: retry forever

`aisstream.stream` treats every failure (network error, refused proxy, failed handshake, a socket silent for 120 s, a service error frame) as a reason to reconnect. It waits 1 s after the first failure and doubles to a cap of 60 s, with plus or minus 30 % jitter (the documentation asks for "exponential backoff and jitter"), resets after the first good frame, and logs every failed attempt with its number and the wait (`... -> attempt 3 failed, retry in 4 s`, then `connecting, attempt 4`). It stops only when told to, or when the service rejects the key (retrying cannot help; exit code 4). Websocket pings every 30 s detect a dead link. A frame the normaliser cannot handle is counted (`handler_errors`) and skipped; it never drops the connection. A failing flush is logged and retried at the next tick; the flusher cannot die. The reader task is restarted if it ever crashes.

### 4.2 Watchdog

`scripts/29_ais_watchdog.py` runs detached (own session, `watchdog.pid`, output appended to `watchdog.log`) and checks the recorder every 60 s:

1. It lists recorder processes from `/proc` (command line ends in `26_ais_record.py`, not `--status` or `--stop`, not a zombie), and reads `recorder.pid` and `status.json`. A pid in `recorder.pid` that now belongs to another program (pid reuse after a reboot) does not count.
2. It keeps the recorder in `recorder.pid` (or the one that wrote `status.json`) and SIGTERMs any second recorder: never two connections.
3. Healthy means: the pid is alive and `status.json`, written by that pid, has `last_message_utc` (or `started_utc` before the first message) less than 5 minutes old. A recorder the watchdog has just started gets a 3 minute grace before `status.json` must be its own.
4. When unhealthy, it SIGTERMs the stale process (SIGKILL after 20 s), checks that no recorder is left, starts a new one with the same command line (`python scripts/26_ais_record.py --hours 0`, new session, stdin `/dev/null`, output appended to `record.log`) and writes the new pid to `recorder.pid`.
5. Consecutive restarts wait 1, 2, 5, then 10 minutes after the previous one; 15 minutes of health resets the streak. A healthy recorder is never restarted, so the watchdog never interrupts a pass.
6. An exclusive `flock` on `watchdog.lock` keeps a second watchdog out (it exits with code 3).
7. Every 6 hours (age of `generated_utc` in `data/s1_next_passes.json`) it refreshes the pass plan in the background with `nice -n 10 python scripts/28_ais_reach.py --passes-only --fetch-plan`; a failed refresh is retried after 1 hour.

8. Since 2026-10-09 it also supervises the live-pass watcher (`scripts/30_live_pass.py --watch`) and the regional CNN run (`scripts/32_cnn_regional.py`) while that run is incomplete; section 4.5 gives the rules. `--no-live-watcher` and `--no-cnn-resume` turn either off.

`watchdog_status.json` holds checks, cumulative restarts, the current streak, the last restart and its reason, the last decision and a short history, with a `watcher` and a `cnn` section for the other two processes and `supervise` for the flags; the counts carry over when the watchdog itself is restarted.

### 4.3 Test on the live system, 2026-10-08

The watchdog was started at 23:06 UTC and adopted the running recorder (pid 515) without restarting it; a second watchdog started by hand exited at once with "another watchdog holds watchdog.lock" (exit code 3). At 23:35:10 UTC, 30 minutes after the 22:58 to 23:05 pass, the recorder was stopped by hand (`26_ais_record.py --stop`, SIGTERM; it flushed and exited at 23:35:11). The watchdog log then shows:

```
2026-10-08 23:35:47Z [watchdog] restart 1 (streak 1): no recorder process; new recorder pid 6290; next restart no sooner than 1 min
```

and the new recorder logged `connected and subscribed` at 23:35:48 and resumed flushing positions every minute. One recorder process ran before and after. The restart also loaded the fixed recorder code (sections 3 and 4.1).

### 4.4 Commands

```
python scripts/29_ais_watchdog.py --ensure     # start detached unless one holds the lock (run at every session start)
python scripts/29_ais_watchdog.py --status     # watchdog, recorder, live watcher and CNN run
python scripts/29_ais_watchdog.py --stop       # stop the watchdog only; what it supervises keeps running
python scripts/26_ais_record.py --status       # what has been recorded, per hour
python scripts/26_ais_record.py --stop         # stop the recorder (the watchdog restarts it within about a minute)
python scripts/30_live_pass.py --stop          # stop the live watcher (the watchdog restarts it within about a minute)
python scripts/32_cnn_regional.py --status     # CNN run: alive or not, scene checkpoints per phase, last log lines
bash scripts/session_start.sh                  # what the session hook runs; safe to run by hand at any time
```

To stop everything: `--stop` the watchdog first, then the recorder, the live watcher and the CNN run (`python scripts/32_cnn_regional.py --stop`). With the watchdog still running, each of them comes back within about a minute (the CNN run within 30 minutes).

### 4.5 Operations

#### What supervises what

| Process | Started by | Kept alive by | Counts as healthy | Restart rule | Files |
|---|---|---|---|---|---|
| aisstream recorder, `scripts/26_ais_record.py --hours 0` | the watchdog | the watchdog | alive, and its own `status.json` has a message less than 5 min old | at once; then 1, 2, 5, 10 min after the previous restart; 15 min healthy resets the streak | `data/cache/ais/aisstream/recorder.pid`, `status.json`, `record.log` |
| live-pass watcher, `scripts/30_live_pass.py --watch` (nice 10) | the session hook, or the watchdog | the watchdog | alive (liveness only: one cycle on a pass can take an hour, so a quiet watcher is never restarted) | as the recorder | `data/cache/live/watch.pid`, `watch.log`, `cycle.lock` |
| regional CNN run, `scripts/32_cnn_regional.py` (nice 19) | the session hook (in the background), or the watchdog, through `--detach --if-incomplete --phases main,low` | the watchdog, until every scene has a checkpoint | alive; the watchdog acts only when no run is seen in two checks in a row, because the run re-executes itself in place to shed memory, and waits while a resume command it did not start (the hook's) is still running, up to 10 min | resumes at least 30 min apart; the gap doubles (60, 120, at most 240 min) after each resume whose run ended without a new scene checkpoint; script 32's answer "nothing to start" ends the supervision | `data/cache/regional_cnn/run.pid`, `run.log`, `main/`, `low/` |
| watchdog, `scripts/29_ais_watchdog.py --run` | the session hook (`--ensure`) | nothing inside the container; the session hook of the next session | `watchdog.pid` names a live watchdog; `watchdog.lock` held | `--ensure` starts one unless one holds the lock | `data/cache/ais/aisstream/watchdog.pid`, `watchdog.lock`, `watchdog.log`, `watchdog_status.json`, `cnn_resume.out` |

Rules for all three supervised processes:

1. A process counts only if `/proc` shows it alive (not a zombie) with the script's path as one argument of its command line (plus `--watch` for the watcher; for the CNN run none of `--status`, `--stop`, `--build`, `--detach`; a CNN command line with `--detach` is a resume command still deciding, which the watchdog waits for and never signals). A pid file is a hint, never proof: after a container restart pids start again from low numbers (the watchdog came back as pid 163 to 171, once 441), so `watch.pid` or `run.pid` can name a live process that is something else. The watchdog then starts a new watcher (it never signals the stranger) and deletes the stale `run.pid`, because script 32 itself checks only that the pid exists and would refuse to resume.
2. One copy of each. A second recorder, watcher or CNN run is SIGTERMed; the one in the pid file is kept, and a wrong pid file is corrected.
3. A healthy process is never stopped. Swapping in new watchdog code (`--stop`, then `--ensure`) does not touch the recorder, the watcher or the CNN run. Done on 2026-10-09 at 15:26:10 to 15:26:12 UTC: recorder pid 165 before and after, `connects` 3 in `status.json` before and after (last connect 15:05:54, before the swap), flushes in `record.log` at 15:25:43, 15:26:43 and every minute after, and no gap over 4.2 s between positions from 15:20 to 15:30 UTC. A second swap at 15:34:41 to 15:34:43, to load the final code, left the recorder (pid 165, 3 connects) and its minute flushes (15:33:44, 15:34:44) unchanged in the same way; the largest gap between positions from 15:30 to 15:40 UTC was 4.8 s. Two more swaps on 2026-10-10, at 00:25:16 to 00:25:17 and 00:29:03 to 00:29:05, to load the wait for a resume command started elsewhere, left the recorder (pid 178, 1 connect, last connect 00:19:14, before both) and its minute flushes (00:24:14, 00:25:14, 00:26:14, 00:27:14, 00:28:15, 00:29:15 and on) unchanged; the largest gap between positions from 00:20 to 00:30 UTC was 5.2 s.

The CNN supervision exists because the regional CNN low-class run was killed by the container restart of 07:42 UTC on 2026-10-09 (last log line 07:42:24) and nothing resumed it for 7.5 hours. It was resumed by hand at 15:10:30 UTC; its log shows it skipped the 118 checkpointed main scenes and the 27 checkpointed low scenes (`phase low: 119 scenes, 27 checkpointed, 92 to do (553919 objects)`). The outage from 16:18 UTC on 2026-10-09 killed it again (last log line 16:15:25, low scene 29 of the 92). This time the supervision brought it back without anyone: the container booted at 00:17:50 UTC on 2026-10-10, the watchdog's first check (00:19:07) saw no run, its second (00:20:07) ran the resume command (the session hook had already deleted the stale `run.pid`, whose pid 10608 no longer existed, but its own resume had been cut off by its 30 s limit; see below), which answered `started pid 803` at 00:20:58; the run logged `phase main: 118 scenes, 118 checkpointed, 0 to do (0 objects)` at 00:21:22 and `phase low: 119 scenes, 56 checkpointed, 63 to do (404359 objects)` at 00:22:38. The hook's own path was then tested the same night: the run was stopped (`python scripts/32_cnn_regional.py --stop`) at 00:44:21, the second after it checkpointed low scene 58, and `bash scripts/session_start.sh` run at once. The hook took 0.54 s, removed the stale `run.pid` (803), and its background resume answered `started pid 7314`; the new run logged `phase main: 118 scenes, 118 checkpointed, 0 to do` at 00:44:35 and `phase low: 119 scenes, 58 checkpointed, 61 to do (332050 objects)` at 00:45:33, and the watchdog logged `CNN run: watching pid 7314` at 00:45:06. Each resume first rebuilds the main-phase outputs (about 30 s), because the run builds after every phase.

#### How to check

`python scripts/29_ais_watchdog.py --status` prints, for the watchdog, the recorder, the live watcher and the CNN run: whether it is supervised (and by which flag it is not), the live processes found in `/proc`, the pid file, restarts and the last decision, resume commands of script 32 still running, and the last line of each log. Abridged, at 00:41 UTC on 2026-10-10:

```
watchdog: running, pid 3747
  last_decision: ok: ok: last message 50 s ago
recorder processes: [178]; recorder.pid 178
live watcher: supervised; processes [3523]; watch.pid 3523
  restarts: 2
  last_restart_reason: no live watcher process
  last_decision: ok: pid 3523 alive
CNN run: supervised; processes [803]; run.pid 803; scene checkpoints main 118, low 57; resume commands running none
  last_result: started (exit 0) at 2026-10-10T00:21:07Z: started pid 803; log .../data/cache/regional_cnn/run.log; stop with --stop
  last_decision: running: pid 803, 175 scene checkpoints
  next resume allowed: 2026-10-10T00:50:07Z
```

`make ais-status` runs the same command.

The watchdog log (`data/cache/ais/aisstream/watchdog.log`) prefixes the other two with `live watcher:` and `CNN run:`. Test on 2026-10-09: the watcher (pid 1852) was killed with SIGKILL at 15:26:33 UTC; the watchdog logged at 15:27:12

```
2026-10-09 15:27:12Z [watchdog] live watcher: restart 1 (streak 1): no live watcher process (watch.pid 1852 is not a watcher); new watcher pid 11665; next restart no sooner than 1 min
```

and the new watcher logged `watcher started, pid 11665` at 15:27:13: 39 s, inside one 60 s interval. Repeated with the final code on 2026-10-10: the idle watcher (pid 172) got SIGTERM at 00:27:19 UTC (it removes `watch.pid` as it exits); the watchdog logged at 00:28:17

```
2026-10-10 00:28:17Z [watchdog] live watcher: restart 2 (streak 1): no live watcher process; new watcher pid 3523; next restart no sooner than 1 min
```

and the new watcher logged `watcher started, pid 3523` at 00:28:19: 60 s after the kill, at the first check after it. Its first cycle found the 16 AOI scenes already checkpointed (5 done, 11 skipped) and started nothing again.

#### What a container restart does

It kills every process in the container: the recorder, the watcher, the CNN run and the watchdog. Files survive: the recorded partitions, the scene checkpoints and the pid files, whose pids now mean nothing. Nothing inside the container can bring anything back. In every outage so far the processes came back only when a new session started and its SessionStart hook ran `scripts/session_start.sh`, which:

1. unless `pgrep` finds any `32_cnn_regional.py` process (a run, or a resume command already deciding), deletes `data/cache/regional_cnn/run.pid` if its pid is not a `32_cnn_regional.py` process, then starts `nohup setsid timeout 600 nice -n 19 python scripts/32_cnn_regional.py --detach --if-incomplete --phases main,low` in the background (a no-op when every scene is checkpointed);
2. starts `nohup setsid nice -n 10 python scripts/30_live_pass.py --watch` unless `pgrep` finds one;
3. runs `python scripts/29_ais_watchdog.py --ensure` (capped at 45 s with `timeout`); the new watchdog's first check starts the recorder and adopts the watcher, and waits for the hook's CNN resume command before it would start its own.

The hook starts the watchdog last so that its first check finds what steps 1 and 2 started and starts nothing twice. The recorder therefore comes back a few seconds after the hook begins.

Until 2026-10-10 step 1 ran in the foreground with a 30 s limit. After the boot of 00:17:50 UTC on 2026-10-10 the cold disk made script 32's scene listing slower than that: the hook began at 00:18:21, the resume command reached its 30 s limit at 00:18:51 and was killed (it printed nothing and started no run), the watcher started at 00:18:51, the watchdog at 00:19:07, so the hook took about 46 s of its 60 s. The watchdog's own resume of 00:20:07 needed 51 s to answer (`started pid 803` at 00:20:58). Step 1 now runs in the background, so the hook does not wait for it, and the watchdog waits for it instead of starting a second resume next to it.

Recording gaps so far, measured from the position partitions in `data/cache/ais/aisstream/positions/` (last message before, first message after; `timestamp` is `MetaData.time_utc`, stamped by aisstream on reception). These are all gaps over 60 s from the start of recording (2026-10-08 14:31:47 UTC) to 00:30 UTC on 2026-10-10:

| From (UTC) | To (UTC) | Length | Cause | Sentinel-1 pass inside |
|---|---|---|---|---|
| 2026-10-08 15:19:52 | 2026-10-08 22:38:51 | 7 h 19 min | container down (section 4); no watchdog existed yet | none; recording resumed 20 min before the S1D pass of 22:58 |
| 2026-10-09 02:19:20 | 2026-10-09 04:59:43 | 2 h 40 min | container down: the watchdog's last line before was at 23:51; a new watchdog started at 04:59:36 with pid 441 | none |
| 2026-10-09 07:42:04 | 2026-10-09 07:46:48 | 4.7 min | container restart (new watchdog pid 167 at 07:46:46) | none |
| 2026-10-09 07:55:48 | 2026-10-09 07:58:55 | 3.1 min | container restart (pid 171 at 07:58:53) | none |
| 2026-10-09 08:03:55 | 2026-10-09 08:06:49 | 2.9 min | container restart (pid 170 at 08:06:48) | none |
| 2026-10-09 08:10:48 | 2026-10-09 08:17:13 | 6.4 min | container restart (pid 166 at 08:17:12) | none |
| 2026-10-09 08:21:12 | 2026-10-09 13:33:27 | 5 h 12 min | container down until the next session (pid 163 at 13:33:18; `uptime` at 15:10 put the boot at about 13:32) | S1D 11:22:31 to 11:35:34 (Gulf of Thailand and South China Sea, 241,335 km2 of AOI): no AIS recorded |
| 2026-10-09 16:17:47 | 2026-10-10 00:19:12 | 8 h 01 min | container down until the next session (boot 00:17:50 by `/proc/uptime`; hook 00:18:21; new watchdog pid 176 started a new recorder, pid 178, at 00:19:07); the watchdog then running (pid 12798) died with the container | S1D 22:04:56 to 22:09:32 (South China Sea, 51,648 km2 of AOI) and S1C 22:50:45 to 22:53:03 (Gulf of Thailand and Gulf of Tonkin, 11,264 km2): no AIS recorded |

Before every one of these, `record.log` shows the local proxy going first (`ConnectionClosedError: no close frame received or sent`, then for some `ConnectionRefusedError` on 127.0.0.1) and then nothing until a new recorder starts. Together the gaps are 23 h 30 min of the 33 h 58 min from 14:31:47 on 2026-10-08 to 00:30 on 2026-10-10: the feed was recorded for about 31 % of that time, and three of the four Sentinel-1 passes over the AOI in that time (S1D 2026-10-09 11:22, S1D 22:04, S1C 22:50; 304,247 km2 of AOI in all) fell in a gap; only the S1D pass of 2026-10-08 22:58 (149,207 km2) has AIS. A watchdog cannot close these gaps; only a host that survives the container (for example the owner's own machine or a small cloud server running the same recorder) can.

#### What the session hook needs

- `.claude/settings.json`: the SessionStart hook `bash "$CLAUDE_PROJECT_DIR"/scripts/session_start.sh 2>/dev/null || true` with `timeout` 60 (seconds).
- The project environment at `/home/user/.mamba/envs/darkvessel/bin/python` and the git-ignored `.env` with `AISSTREAM_API_KEY` at the repo root; without either the hook exits at once and starts nothing.
- Outbound network through the container proxy: the recorder to aisstream.io, the watcher and the CNN run to the AWS mirror `sentinel-s1-l1c`.
- Time: 0.65 s measured when everything is already running (2026-10-10 00:28:30 UTC; it started nothing and printed `watchdog already running`). After a container restart: the CNN resume runs in the background (it took 51 s on the cold disk of 2026-10-10 and is bounded at 600 s), so the hook waits only for `--ensure`, which took 16 s cold on 2026-10-10 (00:18:51 to 00:19:07) and is capped at 45 s. Under 60 s in every case; about 46 s was measured with the old foreground resume, which is why it moved to the background.
- Output: one `[session_start]` line per run, followed by what the three commands print, in `data/cache/ais/aisstream/watchdog.log`.

## 5. Reach maps

`scripts/28_ais_reach.py` turns everything recorded so far into two rasters on the 0.25 degree model grid (`darkvessel.ocean.grid.model_grid`, the VIIRS grid), each as a COG in EPSG:4326 and in UTM 49N (EPSG:32649):

| File | Value per cell |
|---|---|
| `data/outputs/small/ais_reach_share_4326.tif`, `ais_reach_share_utm49n.tif` | share of recorded UTC hours with at least one position in the cell (0 to 1) |
| `data/outputs/small/ais_reach_mmsi_4326.tif`, `ais_reach_mmsi_utm49n.tif` | distinct MMSI heard in the cell over the recording |

Cells outside the AOI polygon are nodata (-9999). A recorded hour is a UTC hour with at least one position anywhere; hours when the recorder was down do not count. Each file carries its period, counts, the terms note and the caveat as GeoTIFF tags. This share is the `ais_reach` column of decision D1 (`docs/PROJECT_BOARD.md`) for the open build. A cell with share 0 means the feed heard nothing there; a radar contact in such a cell is `no_coverage`, not dark.

The same script writes `data/ais_live.gpkg`:

| Layer | Content |
|---|---|
| `vessels_latest_4326`, `vessels_latest_utm49n` | last position per MMSI with the latest static data (name, call sign, IMO, type, size, destination), MID, navigational status label |
| `tracks_4326`, `tracks_utm49n` | one simplified line per MMSI over the recording (points implying more than 80 kn dropped) |
| `s1_next_passes_4326`, `s1_next_passes_utm49n` | the pass windows of section 8, footprint polygons |
| `about` | product description, period, source, terms note, recording gaps, `caveat` (DARK_CAVEAT), reach and gap caveats |

and `data/ais_live_summary.json` (counts, coverage tables, recorder and watchdog status, terms).

## 6. Measured coverage

From `data/ais_live_summary.json`, built 2026-10-08 23:37:54 UTC from everything recorded so far: 2026-10-08 14:31 to 2026-10-08 23:37 UTC (4 UTC hour(s) with data). 15,780 positions from 1,277 MMSI (1,062 class A, 215 class B); 5,227 positions and 420 MMSI inside the AOI polygon; static data for 817 MMSI. The feed heard at least one position in 42 of the 4,778 AOI cells (0.25 degree), 0.9 %; 28 cells in half the recorded hours or more.

| Area | Positions | MMSI | Positions per recorded hour | AOI cells (0.25 degree) | Cells heard | Share of cells heard |
|---|---|---|---|---|---|---|
| South China Sea (Natural Earth part of the AOI) | 10,140 | 676 | 2,535.0 | 4,226 | 42 | 1.0 % |
| Gulf of Thailand (Natural Earth part of the AOI) | 0 | 0 | 0.0 | 391 | 0 | 0.0 % |
| Gulf of Tonkin (Natural Earth part of the AOI) | 0 | 0 | 0.0 | 161 | 0 | 0.0 % |
| Pearl River mouth and Hong Kong (box 113.0E to 114.6E, 21.8 to 22.9N) | 7,676 | 493 | 1,919.0 | 17 | 8 | 47.1 % |
| Luzon west coast and Manila Bay (box 119.5E to 121.0E, 13.5 to 16.5N) | 142 | 32 | 35.5 | 40 | 2 | 5.0 % |
| Singapore Strait (box 103.4E to 104.6E, 1.0 to 1.5N) | 3,463 | 456 | 865.8 | 1 | 0 | 0.0 % |
| Bangka Strait (box 105.0E to 106.5E, -3.3 to -1.5N) | 831 | 61 | 207.8 | 17 | 8 | 47.1 % |
| Gulf of Thailand, Thai coast (box 99.5E to 102.5E, 11.5 to 13.8N) | 358 | 34 | 89.5 | 60 | 0 | 0.0 % |
| Ca Mau detail area (box 103.5E to 106.0E, 7.5 to 9.8N) | 0 | 0 | 0.0 | 74 | 0 | 0.0 % |
| Gulf of Tonkin, Vietnamese side (box 105.6E to 108.2E, 19.0 to 21.6N) | 0 | 0 | 0.0 | 72 | 0 | 0.0 % |
| Central Vietnam coast (box 107.5E to 110.0E, 11.0 to 16.5N) | 134 | 15 | 33.5 | 90 | 1 | 1.1 % |
| Paracel Islands area (box 110.5E to 113.0E, 15.5 to 17.5N) | 0 | 0 | 0.0 | 80 | 0 | 0.0 % |
| Spratly Islands area (box 111.0E to 117.0E, 7.0 to 12.0N) | 0 | 0 | 0.0 | 477 | 0 | 0.0 % |

Reporting boxes are lon/lat boxes for statistics only; they are not boundaries and take no position on any claim. A box's AOI cells are the cells of the AOI polygon inside it, so a box mostly outside the polygon (the Singapore Strait) has positions but almost no AOI cells.

| Distance to the coast | Positions | MMSI | AOI cells | Cells heard | Share of cells heard |
|---|---|---|---|---|---|
| 0 to 20 km | 12,406 | 1,033 | 534 | 15 | 2.8 % |
| 20 to 50 km | 17 | 7 | 739 | 7 | 0.9 % |
| 50 to 100 km | 48 | 9 | 1,092 | 7 | 0.6 % |
| 100 to 200 km | 9 | 6 | 1,388 | 7 | 0.5 % |
| over 200 km | 0 | 0 | 973 | 0 | 0.0 % |

Distance is from `data/outputs/small/dist_coast_km_4326.tif` (Natural Earth 10 m coastline) at each position and at each cell centre. Positions in ports and rivers fall on land cells of that grid and are in no band.

What this means:

- The feed behaves as a shore-receiver network. 12,406 of the 15,780 positions (79 %) lie within 20 km of the coast and only 74 (0.5 %) farther out; the other 3,300 fall on land cells of the 0.01 degree distance grid (ports, rivers, harbour basins). Nothing was heard more than 200 km from the coast.
- Dense: the Pearl River mouth and Hong Kong (7,676 positions, 493 MMSI, about 1,900 positions per recorded hour, 8 of 17 AOI cells heard), the Singapore Strait (3,463 positions, 456 MMSI, just outside the AOI polygon) and the Bangka Strait (831 positions, 61 MMSI, 8 of 17 cells).
- Thin: the west coast of Luzon (142 positions, 32 MMSI), the Thai coast (358 positions from 34 MMSI, all in ports and rivers outside the AOI polygon) and the central Vietnam coast (134 positions, 15 MMSI, 1 of 90 cells).
- Silent in 4 recorded hours: the Ca Mau detail area, the Vietnamese side of the Gulf of Tonkin, the Paracel and Spratly areas, and the whole Natural Earth "Gulf of Thailand" and "Gulf of Tonkin" parts of the AOI (0 positions).
- Who is heard: static data name mostly cargo ships (292 MMSI), tankers (150), tugs (94), passenger ships (40) and high-speed craft (39); only 22 MMSI report the fishing type and 21 a length under 15 m. Median reported length 78 m. The small fishing fleets of the region are almost absent from this feed.
- 15 MMSI look like fishing-gear or net beacons rather than vessels (names such as `NET-82542-84%` or `BUOY_MERAH_10-99%`, or MMSIs that are not nine digits; flag `gear_beacon_like` in `vessels_latest`, heuristic `aisstream.gear_beacon_like`). A radar contact next to one is more likely the boat tending the gear than the beacon itself.
- Consequence for P0: in the open build, a radar contact off Vietnam, including the Ca Mau detail area, can only be `no_coverage`. Live identification of radar contacts is possible only near Hong Kong and the Pearl River mouth, in the Bangka Strait at the southern edge of the AOI and along parts of the Luzon coast. Identification off Vietnam needs satellite AIS: the GFW research build now, a commercial feed for the product.
- The recording so far covers 4 UTC hours (14:31 to 15:20 and 22:38 to 23:37 UTC: late evening and early morning in local time, UTC+7 and UTC+8). The counts per hour will change as it grows; the reach of a shore receiver will not.

## 7. Recording gaps

Every gap over 60 s so far is listed with its cause in section 4.5 (What a container restart does): eight container outages between 2026-10-08 15:19 and 2026-10-10 00:19 UTC, 23 h 30 min in all. One shorter, deliberate gap is not in that table:

| From (UTC) | To (UTC) | Length | Cause |
|---|---|---|---|
| 2026-10-08 23:35:11 | 2026-10-08 23:35:48 | 37 s | deliberate restart to load the fixed recorder code: stopped by hand, restarted by the watchdog (the acceptance test of section 4.2), 30 min after the 22:58 to 23:05 pass |

Recording started at 14:31:48 UTC on 2026-10-08. Gaps over 10 minutes are listed in `recording_gaps_over_10_min` of the summary and in the `about` layer, and they do not count as recorded hours in the reach share. No Sentinel-1 pass over the AOI fell inside the gap of 2026-10-08: the passes of that day were at 09:58 and 10:48 UTC (before recording started) and 22:58 UTC (recorded). The S1D pass of 2026-10-09 11:22 to 11:35 UTC fell inside the outage of 08:21 to 13:33 UTC, and the S1D pass of 22:04 to 22:09 and the S1C pass of 22:50 to 22:53 inside the outage of 16:17 on 2026-10-09 to 00:19 on 2026-10-10: no AIS was recorded for any of them, so the live-pass pipeline cannot identify their contacts. The watcher marks such scenes `skipped_no_ais` in its checkpoints (`data/cache/live/scenes/`; all 11 scenes of the 11:22 pass on 2026-10-10 at 00:19:48) and never retries them; a skipped scene does not rebuild `data/live/`, so the product files still hold only the pass of 2026-10-08 22:58. The aisstream service itself drops messages without notice and has no replay, so short silences inside a recorded hour are possible and invisible here.

## 8. Sentinel-1 pass windows

`data/s1_next_passes.json` lists every Sentinel-1C and 1D pass over the AOI that the ESA plan or the repeat prediction gives, from 72 hours before `generated_utc` to 288 hours (12 days, one repeat cycle) after it. Past passes stay listed so the live-pass pipeline can still name its runs after them. Two sources, both kept as separate rows and tied together by `pass_group`:

1. ESA's published acquisition plan, `https://sentinels.copernicus.eu/web/sentinel/copernicus/sentinel-1/acquisition-plans` (page read on 2026-10-08; it lists one KML per mission every day or two). The files are cached in `data/cache/s1_plan/` and their URLs, windows, segment counts and download times are in `sources.esa_plan.files`. Files overlap in time; per mission the newest file (latest window start) wins inside its own window and older files are used only outside it (`s1_passes.select_plan_segments`). For this window: S1C from `s1c_mp_user_20261008t180959_20261030t202000` (and `s1c_mp_user_20261005t174134_20261027t194000` before 2026-10-08 18:09), S1D from `s1d_mp_user_20261008t182319_20261015t200700`, then `s1d_mp_user_20261007t190903_20261027t210500` after 2026-10-15 20:07; the 2026-10-06 and 2026-10-02 S1D files are used only before 2026-10-07 19:09 (one AOI pass, 2026-10-06 11:02, comes from the 2026-10-02 file).
2. The 12-day repeat cycle applied to the project's own scene archive (`data/s1_scenes.csv`, `data/s1_footprints.gpkg`): same mission, relative orbit and time of day, 12 days after the latest archived pass. ESA SentiWiki (`https://sentiwiki.copernicus.eu/web/s1-mission`): "Sentinel-1 is in a near-polar, sun-synchronous orbit with a 12 day repeat cycle and 175 orbits per cycle for a single satellite" and "The reference orbit will be maintained within an Earth-fixed orbital tube of a diameter of 120 m (RMS) during normal operation." The archive itself confirms it: 220 consecutive pass pairs, 99.1 % within 120 s of a 12-day multiple, median drift 1 s. A repeat prediction is not the plan; ESA can drop or move a segment.

The file keeps its original keys and per-pass fields (`start_utc`, `stop_utc`, `mission`, `relative_orbit`, `pass_dir`, `source`, `mode`, `polarisation`, `footprint_bbox`, `aoi_overlap_km2`, `basis_pass_start_utc`, `cycles_ahead`, `in_esa_plan`, `in_repeat_prediction`) and adds `pass_group`, `status` (past, in_progress, upcoming), `plan_file`, `aoi_parts`, `aoi_overlap_bbox`, `aoi_cells_under` and `ais_heard_share` per row, plus `pass_groups` (one row per physical pass), `lookback_hours`, `horizon_hours` and a `fields` glossary. NaN is written as null, so any JSON reader can parse it. It is written through a temp file and a rename, so a reader polling it never sees half a file. The watchdog refreshes it every 6 hours and downloads new plan files when ESA publishes them.

A pass group joins rows of one mission whose time spans lie within 10 minutes of each other (one satellite flies one pass at a time; consecutive passes over the AOI are about 100 minutes apart). The relative orbit number can change inside one ascending take (it increments at the equator crossing), so grouping by orbit number would split one pass in two.

`ais_heard_share` is the share of the 0.25 degree AOI cells under the footprint where the live feed heard at least one position during the recording. It says, before the scene arrives, how many radar contacts of that pass can possibly be matched; the rest will be `no_coverage`.

| Pass | Start to end (UTC) | Mission, rel. orbit, direction | Sources | AOI parts covered | AOI overlap bbox (W, S, E, N) | AOI overlap km2 | AIS heard under footprint |
|---|---|---|---|---|---|---|---|
| S1D_R128_20261006T1102 | 2026-10-06 11:02 to 11:10 | S1D, 128, ascending | ESA plan + 12-day repeat | Gulf of Tonkin, South China Sea | 105.62, 9.53, 108.72, 20.61 | 54,826 | 0.0 % |
| S1D_R149_20261007T2222 | 2026-10-07 22:22 to 22:25 | S1D, 149, descending | ESA plan + 12-day repeat | South China Sea | 108.04, -2.91, 111.61, 3.63 | 102,873 | 0.7 % |
| S1C_R069_20261008T0958 | 2026-10-08 09:58 to 10:00 | S1C, 69, ascending | ESA plan + 12-day repeat | South China Sea | 120.13, 18.28, 122.27, 22.99 | 26,824 | 0.0 % |
| S1D_R157_20261008T1048 | 2026-10-08 10:48 to 10:54 | S1D, 157, ascending | ESA plan + 12-day repeat | Gulf of Tonkin, South China Sea | 108.09, 18.05, 111.16, 21.90 | 49,646 | 0.0 % |
| S1D_R164_20261008T2258 | 2026-10-08 22:58 to 23:04 | S1D, 164, descending | ESA plan + 12-day repeat | Gulf of Thailand | 100.07, 6.34, 103.05, 12.69 | 149,207 | 0.0 % |
| S1D_R171_20261009T1122 | 2026-10-09 11:22 to 11:35 | S1D, 171, ascending | ESA plan + 12-day repeat | Gulf of Thailand, South China Sea | 99.82, -2.60, 105.13, 13.57 | 241,335 | 0.6 % |
| S1D_R003_20261009T2204 | 2026-10-09 22:04 to 22:09 | S1D, 3, descending | ESA plan + 12-day repeat | South China Sea | 113.54, 3.83, 116.44, 7.05 | 51,648 | 0.0 % |
| S1C_R091_20261009T2250 | 2026-10-09 22:50 to 22:53 | S1C, 91, descending | ESA plan + 12-day repeat | Gulf of Thailand, Gulf of Tonkin | 102.65, 10.78, 106.63, 19.05 | 11,264 | 0.0 % |
| S1D_R011_20261010T1032 | 2026-10-10 10:32 to 10:39 | S1D, 11, ascending | ESA plan + 12-day repeat | South China Sea | 112.15, 20.63, 114.78, 23.13 | 39,274 | 14.8 % |
| S1C_R099_20261010T1119 | 2026-10-10 11:19 to 11:21 | S1C, 99, ascending | ESA plan + 12-day repeat | Gulf of Thailand | 101.70, 10.18, 104.13, 12.69 | 29,868 | 0.0 % |
| S1C_R105_20261010T2151 | 2026-10-10 21:51 to 21:54 | S1C, 105, descending | ESA plan + 12-day repeat | South China Sea | 117.87, 14.70, 121.22, 23.23 | 120,129 | 0.0 % |
| S1D_R018_20261010T2242 | 2026-10-10 22:42 to 22:49 | S1D, 18, descending | ESA plan + 12-day repeat | Gulf of Thailand, Gulf of Tonkin, South China Sea | 102.55, -1.43, 108.56, 18.68 | 212,448 | 0.0 % |
| S1D_R025_20261011T1105 | 2026-10-11 11:05 to 11:19 | S1D, 25, ascending | ESA plan + 12-day repeat | Gulf of Thailand, South China Sea | 104.19, -2.89, 109.33, 10.55 | 57,723 | 4.5 % |
| S1D_R032_20261011T2144 | 2026-10-11 21:44 to 21:52 | S1D, 32, descending | ESA plan + 12-day repeat | South China Sea | 118.92, 10.22, 122.27, 21.17 | 83,306 | 1.8 % |
| S1C_R120_20261011T2235 | 2026-10-11 22:35 to 22:36 | S1C, 120, descending | ESA plan + 12-day repeat | South China Sea | 106.98, 10.19, 110.12, 16.24 | 45,184 | 1.7 % |
| S1D_R040_20261012T1016 | 2026-10-12 10:16 to 10:22 | S1D, 40, ascending | ESA plan + 12-day repeat | South China Sea | 116.04, 22.48, 118.53, 23.76 | 21,216 | 0.0 % |
| S1D_R047_20261012T2230 | 2026-10-12 22:30 to 22:32 | S1D, 47, descending | 12-day repeat | South China Sea | 105.95, -3.22, 109.83, 4.96 | 203,732 | 5.3 % |
| S1C_R142_20261013T1003 | 2026-10-13 10:03 to 10:07 | S1C, 142, ascending | ESA plan + 12-day repeat | South China Sea | 118.97, 12.32, 121.45, 19.13 | 66,492 | 2.2 % |
| S1D_R055_20261013T1054 | 2026-10-13 10:54 to 11:01 | S1D, 55, ascending | ESA plan + 12-day repeat | Gulf of Tonkin, South China Sea | 106.30, 10.32, 110.62, 21.70 | 208,064 | 0.4 % |
| S1D_R062_20261013T2302 | 2026-10-13 23:02 to 23:12 | S1D, 62, descending | ESA plan + 12-day repeat | Gulf of Thailand | 99.16, 7.55, 101.24, 13.57 | 70,423 | 0.0 % |
| S1D_R069_20261014T1000 | 2026-10-14 10:00 to 10:05 | S1D, 69, ascending | ESA plan + 12-day repeat | sliver | 120.12, 22.83, 120.20, 23.04 | 44 | n/a |
| S1C_R157_20261014T1048 | 2026-10-14 10:48 to 10:49 | S1C, 157, ascending | ESA plan + 12-day repeat | Gulf of Tonkin, South China Sea | 108.09, 17.88, 111.19, 21.69 | 53,673 | 0.0 % |
| S1D_R069_20261014T1131 | 2026-10-14 11:31 to 11:43 | S1D, 69, ascending | ESA plan + 12-day repeat | Gulf of Thailand | 99.16, 6.86, 101.14, 12.58 | 50,613 | 0.0 % |
| S1D_R076_20261014T2213 | 2026-10-14 22:13 to 22:17 | S1D, 76, descending | ESA plan + 12-day repeat | South China Sea | 110.06, -2.91, 114.03, 5.34 | 58,265 | 0.0 % |
| S1C_R164_20261014T2258 | 2026-10-14 22:58 to 23:00 | S1C, 164, descending | ESA plan + 12-day repeat | Gulf of Thailand | 100.82, 11.67, 103.01, 12.69 | 13,615 | 0.0 % |
| S1D_R084_20261015T1040 | 2026-10-15 10:40 to 10:46 | S1D, 84, ascending | ESA plan + 12-day repeat | South China Sea | 110.23, 18.84, 113.07, 21.98 | 66,436 | 0.0 % |
| S1D_R091_20261015T2249 | 2026-10-15 22:49 to 22:57 | S1D, 91, descending | ESA plan + 12-day repeat | Gulf of Thailand, Gulf of Tonkin, South China Sea | 101.76, 0.22, 107.08, 21.00 | 85,548 | 0.0 % |
| S1D_R098_20261016T1114 | 2026-10-16 11:14 to 11:27 | S1D, 98, ascending | ESA plan + 12-day repeat | Gulf of Thailand, South China Sea | 101.71, -3.22, 107.20, 12.69 | 386,776 | 3.8 % |
| S1D_R105_20261016T2153 | 2026-10-16 21:53 to 22:01 | S1D, 105, descending | ESA plan + 12-day repeat | South China Sea | 115.95, 5.77, 121.03, 19.30 | 198,225 | 0.0 % |
| S1C_R018_20261016T2242 | 2026-10-16 22:42 to 22:45 | S1C, 18, descending | ESA plan + 12-day repeat | South China Sea | 106.14, 9.91, 108.42, 17.94 | 21,700 | 0.0 % |
| S1D_R113_20261017T1024 | 2026-10-17 10:24 to 10:30 | S1D, 113, ascending | ESA plan + 12-day repeat | South China Sea | 114.09, 21.69, 116.63, 22.98 | 23,520 | 18.2 % |
| S1C_R026_20261017T1110 | 2026-10-17 11:10 to 11:13 | S1C, 26, ascending | ESA plan + 12-day repeat | Gulf of Thailand | 104.20, 9.97, 104.97, 10.55 | 2,174 | 0.0 % |
| S1C_R032_20261017T2144 | 2026-10-17 21:44 to 21:46 | S1C, 32, descending | 12-day repeat | South China Sea | 119.36, 12.14, 122.27, 20.12 | 62,090 | 2.4 % |
| S1D_R120_20261017T2235 | 2026-10-17 22:35 to 22:41 | S1D, 120, descending | ESA plan + 12-day repeat | South China Sea | 104.29, -3.22, 110.15, 16.46 | 243,818 | 6.4 % |
| S1D_R128_20261018T1102 | 2026-10-18 11:02 to 11:10 | S1D, 128, ascending | ESA plan + 12-day repeat | Gulf of Tonkin, South China Sea | 105.62, 9.53, 108.72, 20.61 | 54,826 | 0.0 % |
| S1D_R149_20261019T2222 | 2026-10-19 22:22 to 22:25 | S1D, 149, descending | ESA plan + 12-day repeat | South China Sea | 108.04, -2.91, 111.61, 3.63 | 102,873 | 0.7 % |
| S1C_R069_20261020T0958 | 2026-10-20 09:58 to 10:00 | S1C, 69, ascending | ESA plan + 12-day repeat | South China Sea | 120.13, 18.28, 122.27, 22.99 | 26,824 | 0.0 % |
| S1D_R157_20261020T1048 | 2026-10-20 10:48 to 10:54 | S1D, 157, ascending | ESA plan + 12-day repeat | Gulf of Tonkin, South China Sea | 108.09, 18.05, 111.16, 21.90 | 49,646 | 0.0 % |
| S1D_R164_20261020T2258 | 2026-10-20 22:58 to 23:04 | S1D, 164, descending | ESA plan + 12-day repeat | Gulf of Thailand | 100.07, 6.34, 103.05, 12.69 | 149,207 | 0.0 % |

"AIS heard under footprint" is `ais_heard_share` from the recording so far (section 6); it will change as the recording grows. Times are the ESA plan segment times where the plan has the pass, else the repeat prediction. "12-day repeat" only (no ESA plan row) means ESA's current plan does not list that segment: do not count on it.

What the list says for the live-pass work (R1-T2):

- Of the 34 passes still to come before 2026-10-21, four have AIS heard under more than 5 % of their AOI cells: S1D 2026-10-10 10:32 UTC over the Pearl River mouth and Hong Kong (14.8 %), S1D 2026-10-17 10:24 east of Hong Kong (18.2 %), S1D 2026-10-17 22:35 whose southern end reaches the Bangka Strait (6.4 %), and S1D 2026-10-12 22:30 over the Karimata and Bangka waters (5.3 %; repeat prediction only, not in ESA's current plan). These are the passes where the open build can show real identification of radar contacts. The first is 2026-10-10 10:32 UTC.
- Six more have 1 to 5 %: S1D 2026-10-11 11:05, S1D 2026-10-16 11:14, S1C 2026-10-17 21:44 (repeat only), S1C 2026-10-13 10:03, S1D 2026-10-11 21:44 and S1C 2026-10-11 22:35.
- Passes over the Ca Mau detail box (103.5E to 106.0E, 7.5N to 9.8N, more than 500 km2 of overlap): S1D 2026-10-10 22:42, S1D 2026-10-11 11:05, S1D 2026-10-15 22:49 (edge only) and S1D 2026-10-16 11:14. Over the Vietnamese side of the Gulf of Tonkin: S1D 2026-10-13 10:54, S1D 2026-10-15 22:49 and S1D 2026-10-18 11:02. The feed hears nothing there, so their contacts will be `no_coverage`; they still give radar detections, CNN scores and lights for the leads queue.
- The 2026-10-08 22:58 pass (S1D, Gulf of Thailand) had 0 % AIS heard under it.

## 9. Licence and terms of aisstream.io

Read on 2026-10-08 at about 23:00 UTC. `https://aisstream.io/`, `https://aisstream.io/documentation` and `https://aisstream.io/privacypolicy` returned HTTP 200. `https://aisstream.io/terms`, `/termsofservice`, `/terms-of-service`, `/tos` and `/legal` returned HTTP 404. The site footer links only to Documentation, Account, Privacy and Support (the GitHub issue tracker).

What the pages say (text in quotation marks is verbatim):

- Cost: "Track ship movements, monitor maritime accidents and discover ship's cargo from aisstream.io's websocket api in real-time and for free." (home page)
- Provenance: "Our global network of Automatic Identification System (AIS) stations, which power aisstream.io's tracking capabilities, allows us to stream much more than just ship positions." (home page). The site does not say whether any of these stations are satellites; the measured reach (section 6) looks like shore receivers only.
- Limits: 3 subscribed connections per account, 3 open connections per originating IP, the subscription within 3 seconds, subscription updates at most 1 per second (documentation, "Limits and operational considerations").
- Browsers: "Direct browser connections are not permitted. Connect from your own server and proxy only the information each client needs." (documentation FAQ)
- Reliability: "The service currently provides no SLA or uptime guarantee, and events are not durably replayed. Plan for interruptions, reconnect with backoff, and persist messages your application cannot afford to lose." (documentation FAQ)
- Compression: "Beginning in September 2026, uncompressed connections will be subject to per-user bandwidth limits, and messages exceeding those limits will be dropped." (documentation)
- Privacy policy: covers personal data of site visitors and account holders (IP address, browser, GitHub identity). It says nothing about the AIS data.

UNVERIFIED: the licence of the relayed AIS data. No page addresses redistribution or commercial use. Earlier project notes (`docs/data_landscape.md`, C07) cite GitHub issues in which users asked for written permission for commercial use; `https://github.com/aisstream/issues` returned HTTP 403 through this session's proxy, so those issues were not re-read here.

Consequences for the product:

- Until the operator answers in writing, aisstream-derived matches carry UNVERIFIED terms. Whether the open build may show them, and under what label, is a decision for the PM and the owner; ask through the Support link before any commercial or Viettel-facing use.
- The browser must never connect to aisstream: the key stays on the server, and the single-file page can carry only recorded, processed results, never a live feed or the key.
- An AIS gap in this feed can be the service's own outage; it is not evidence about any vessel.

## 10. Files

| File | Committed | Content |
|---|---|---|
| `data/ais_live.gpkg` | yes | vessels_latest, tracks, s1_next_passes in EPSG:4326 and UTM 49N; about |
| `data/ais_live_summary.json` | yes | counts, coverage tables, gaps, recorder and watchdog status, terms |
| `data/s1_next_passes.json` | yes | pass windows (section 8) |
| `data/outputs/small/ais_reach_{share,mmsi}_{4326,utm49n}.tif` | yes | reach COGs (section 5) |
| `data/cache/ais/aisstream/` | no | the recording itself (section 3) |
| `data/cache/s1_plan/` | no | ESA plan KMLs and `fetch_log.json` |

Rebuild: `python scripts/28_ais_reach.py` (about 20 s now; it reads every partition). Pass plan only: `python scripts/28_ais_reach.py --passes-only --fetch-plan`. Tests: `tests/test_aisstream.py`, `tests/test_ais_watchdog.py`, `tests/test_s1_passes.py` (offline; fake websocket, fake process table, synthetic KMLs).

## 11. Sources

All resolved in this session on 2026-10-08 (HTTP 200 unless stated).

- aisstream.io home page, `https://aisstream.io/`
- aisstream.io documentation, `https://aisstream.io/documentation` (limits, FAQ, message types, subscription format)
- aisstream.io privacy policy, `https://aisstream.io/privacypolicy`
- No terms page: `https://aisstream.io/terms`, `/termsofservice`, `/terms-of-service`, `/tos`, `/legal` (HTTP 404)
- aisstream issue tracker, `https://github.com/aisstream/issues` (HTTP 403 through this session's proxy; not read; UNVERIFIED here)
- ESA Sentinel-1 acquisition plans, `https://sentinels.copernicus.eu/web/sentinel/copernicus/sentinel-1/acquisition-plans`, and the plan KMLs listed in `data/s1_next_passes.json` under `sources.esa_plan.files` (each downloaded with HTTP 200; `data/cache/s1_plan/fetch_log.json` records the last page read and each file's fetch time; two S1D files were downloaded at 23:15 UTC on 2026-10-08; the other four, cached at about 14:37 UTC, were fetched again at 23:33 UTC with HTTP 200 and the same byte sizes)
- ESA SentiWiki, Sentinel-1 mission, `https://sentiwiki.copernicus.eu/web/s1-mission` (12-day repeat, 175 orbits per cycle, 120 m orbital tube)
- ITU-R Recommendation M.1371, `https://www.itu.int/rec/R-REC-M.1371` (AIS technical characteristics)
- US Coast Guard Navigation Center, AIS class A reports, `https://www.navcen.uscg.gov/ais-class-a-reports` (navigational status and ship type codes)
- websockets client reference, `https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html` (ping, open and close timeouts used by the client)
