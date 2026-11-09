# Changelog

This file follows [Common Changelog](https://common-changelog.org/), with a [gitmoji](https://gitmoji.dev) prefix on each
entry. Versions follow [ZeroVer](https://0ver.org).

## [Unreleased]

### Changed

- 💥 Move the Live size filter from `m` to `s`, so `m` means Seen only on the Watchlist (#38)
- 💥 Move the Live edit details from Enter to `e`, because Enter now opens the page (#38)
- 💥 Move Watchlist Remove from `d` to `x` (Delete still works), so `d` is Diff on every tab (#38)
- 🚸 Move the Watchlist diff from Enter to `d`, because Enter now opens the page (#38)

### Added

- ✨ Open the selected page on Wikipedia with Enter or a double click, on the Live log and Most edited, Reading, Watchlist and Convergence, with an `enter Open` footer label where it fits (#38)
- ✨ Open the change on Wikipedia with Enter or a double click on the Diff tab (#38)
- ✨ Show the page's address in a notice when no browser can be opened, over SSH or with a text browser (#38)

### Removed

- 🔥 Remove the `b` alias for going back from the Diff tab; Escape goes back (#38)

### Fixed

- 🐛 Save the watchlist and the settings in `--demo` and `--replay` as in live mode, without storing the recording's edits (#39)
- 🐛 Let `w` watch a page in `--demo` and `--replay` with no connection, under a placeholder ID the next live start replaces (#39)
- 🐛 Save a theme picked from the command palette, and the Reading tab's row count (#39)

## [0.1.0] - 2026-11-08

_Known issue: the Live log can show rows slightly out of time order, as the feed sends them (#27)._

### Added

- ✨ Add the project scaffold and `--version` (#6)
- ✨ Add `--selftest`, which checks the install without the network (#7)
- ✨ Add the config file, `--no-config` to ignore it and `--debug` to write a log file (#7)
- ✨ Add the Live tab: a scrolling edit log, edits per minute, the most edited articles and the people/bots split (#8)
- ✨ Add the five-tab shell with `?` help, mouse support on every footer key, row and tab, a bar that names a failing feed, and a message when the window is smaller than 80x24 (#8)
- ✨ Add `--replay` with `--speed`, `--record` with `--force`, and `--wiki` (#8)
- ✨ Add the Reading tab: the day's biggest reading spikes with a 30-day sparkline and the article summary (#15)
- ✨ Add `--demo`, a six-minute recording of English Wikipedia that runs with no network (#10)
- ✨ Show a recorded day of Reading numbers in `--demo`, so the Reading and Convergence tabs are filled with no network (#25)
- ✨ Add the Watchlist tab, live matching of watched pages, the Diff tab and the startup catch-up; `w` watches the selected page and `W` types a title or link (#18)
- ✨ Add a 30-day pageview sparkline to each watched page (#21)
- ✨ Add the Convergence tab: articles hot in both Live and Reading, ranked by edits by people times the reading spike, with a surge trigger that fetches a busy page's own reading history (#22)
- ✨ Publish each release to PyPI and as a single Windows exe, with `SHA256SUMS` and build attestations (#11)

### Fixed

- 🐛 Show `new` instead of a huge multiple for a page read under 100 times a day before; such pages rank last in Reading and are never hot in Convergence (#26, #34)
- 🐛 Name what space will do in a replay (`space Pause` while playing, `space Play` while paused), and call the Live log's `p` key `Hold` so it is not a second Pause (#34)
