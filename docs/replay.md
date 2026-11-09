# Recording and replay

A recording is a saved copy of the recentchange stream. wikihose plays one back at its recorded pace, so a demo, a bug
report or a test sees exactly the same edits every time. The code is in `src/wikihose/sources/recording.py` (the
format) and `src/wikihose/sources/replay.py` (playback).

## File format, version 1

A gzipped UTF-8 JSONL file. The first line is the header:

```json
{"format": "wikihose-recording", "version": 1, "recorded_at": "2026-11-07T12:00:00+00:00", "stream": "recentchange", "wiki": "all"}
```

- `recorded_at` is the UTC time the recording started. A replay reports `recorded_at` plus the time played so far as its
  current moment.
- `wiki` is a wiki database name such as `enwiki` when the recording was filtered to one wiki, otherwise `all`.
  `--record` always filters to the selected wiki. `--replay` plays a one-wiki recording in that wiki; for `all`
  it uses `--wiki` or the configured wiki. The wiki cannot be changed during a replay.

Every later line is one server-sent event:

```json
{"t": 1.234, "id": "[{\"topic\":\"codfw.mediawiki.recentchange\", ...}]", "data": "{\"type\":\"edit\", ...}"}
```

- `t` is seconds since the recording started (a float).
- `id` is the event's SSE `id` string, or null. It is stored as sent, so a recording keeps Wikimedia's Kafka positions.
- `data` is the raw SSE data string, not parsed. Replays parse it with the same code as the live stream.

A reader checks the header, then skips lines that are not valid records and counts them. A recording cut off mid-write
(a crash) reads up to the cut. A file with a different `format` or `version` is refused.

`src/wikihose/data/sample.jsonl.gz` is a small real recording (48 event lines, one of them
deliberately broken). It ships in the wheel and is used by the tests and by `--selftest`.

## Recording

`Recorder(path, clock, wiki)` writes the file. It flushes once per second of clock time and when closed, so a crash
loses at most a second. With a `wiki` it keeps only events whose JSON `wiki` field matches. The live source hands it
every raw event before parsing, so a recording holds events the app does not use, such as `categorize`, too.

`--record FILE` opens a `Recorder` and gives it to the live source. It refuses an existing file unless `--force` is
also given, and cannot be combined with `--replay`. `--wiki NAME` picks the wiki to record.

## Replaying

`ReplaySource(path, clock, speed=1.0)` is a `DataSource`, like the live source. Before each event it waits
`(t - elapsed) / speed` seconds on the injected clock, so tests run on a `VirtualClock` without waiting. It starts with
an `ok` status and ends with a `down` status whose detail is `replay finished`.

Controls, for the key bindings to call:

- `pause()`, `resume()` and `paused`
- `speed`, set between 0.5 and 16 (values outside are clamped)
- `skip(seconds)` jumps forward in recorded time. Events in the skipped span are still yielded, without waiting, so
  counts and aggregates stay right.
- `moment()` is the recorded time reached so far, for the clock the screen shows.

`--replay FILE` runs the app on a recording. `--speed N` sets the starting speed. The top bar says "Recorded data, not
live" with the state (paused, a speed, or replay finished). Keys, shown only in a replay:

- `space` pauses and resumes
- `+` and `-` double and halve the speed (0.5x to 16x)
- `]` skips ahead 60 seconds

`--headless-seconds N` plays the recording with no screen and prints a one-line summary.

## The demo

`wikihose --demo` plays `src/wikihose/data/demo-enwiki.jsonl.gz`, which ships in the wheel: six minutes of the real
English Wikipedia (`enwiki`) stream, recorded on 7 November 2026 (about 00:59 to 01:05 UTC on 8 November), 1,822 events.
It runs at 1x through `ReplaySource` and never touches the network: no HTTP client and no live source are built. It
uses your theme but always watches `enwiki` (the one setting it does not save). What you do is saved as in live mode,
the watchlist and the settings, but nothing the recording holds is: no edits or counts reach the database. Pressing `w`
there needs no connection; the page is kept under a placeholder ID that the next live start replaces with the real one.
The top bar says
"Recorded data, not live". When the recording ends it starts again from the top with an empty log and fresh numbers, so
a demo left open keeps moving (a `--replay FILE` run stops at "replay finished" instead). `--demo` cannot be combined
with `--replay`, `--record`, `--wiki` or `--speed`; the replay keys (space, `+`, `-`, `]`) work as in any replay.

The recording is Wikimedia data under CC BY-SA 4.0; see [NOTICE](../NOTICE).
