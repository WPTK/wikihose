# wikihose

A terminal dashboard for watching Wikipedia live: what people are editing right now, what people are suddenly reading,
where the two overlap, and edits to the pages you choose to track.

**Status: 0.1.0, the first release. All five tabs work.**

![The wikihose Live tab playing the bundled demo recording: a scrolling edit log on the left, edits per minute, a split of people and bots, and the most edited articles on the right.](https://raw.githubusercontent.com/WPTK/wikihose/v0.1.0/docs/images/demo.svg)

*The Live tab playing the bundled demo recording (recorded data, not live).*

## What it shows

- **Live:** every edit as it happens, with the size of the change, edits per minute, the most edited articles of the
  last 15 minutes and the split between people and bots. Filter by wiki, bots, articles only and change size.
- **Reading:** the articles whose reading jumped most on the last full day, against their usual 30 days, with a
  sparkline and the article summary.
- **Watchlist:** pages you track. Press `w` on any article row, or `W` to type a title or paste a link. Edits to them
  are marked in the Live log and counted on the tab, including edits made while wikihose was closed.
- **Diff:** Enter on a watched page shows what changed since you last looked, removed text in red and added text in
  green.
- **Convergence:** articles hot in both: edited by people in the last 15 minutes and read at least twice their usual.
  An article that suddenly gets 10 edits has its own reading history fetched.

`?` lists every key. Every key in the footer is also a button, and rows and tabs take clicks. It fits an 80x24
terminal.

## Install

With [pipx](https://pipx.pypa.io) (Windows, macOS or Linux, Python 3.12 or later):

```powershell
pipx install wikihose
wikihose
```

Or download a ready-made binary from the [releases page](https://github.com/WPTK/wikihose/releases). No Python is
needed. Binaries are published from version 0.2.0; 0.1.0 has the Windows exe only.

| Your computer | File |
| --- | --- |
| Windows (Intel or AMD) | `wikihose-<version>-windows-x86_64.exe` |
| Mac with Apple silicon (M1 or later) | `wikihose-<version>-macos-arm64.tar.gz` |
| Mac with an Intel chip | `wikihose-<version>-macos-x86_64.tar.gz` |
| Linux, Intel or AMD | `wikihose-<version>-linux-x86_64.tar.gz` |
| Linux, ARM (a Raspberry Pi 4 or 5 on 64-bit Linux, for example) | `wikihose-<version>-linux-arm64.tar.gz` |

On macOS and Linux, unpack the archive and run the file inside it:

```sh
tar -xzf wikihose-<version>-macos-arm64.tar.gz
./wikihose
```

The Linux binaries need glibc 2.31 or newer (Ubuntu 20.04, Debian 11 or newer). On a distribution with musl, such as
Alpine, use `pipx`.

**macOS blocks the first run.** The binary is not signed with an Apple Developer ID, so a browser download carries a
quarantine flag and macOS says it cannot verify the app. Remove the flag once, then run it:

```sh
xattr -d com.apple.quarantine ./wikihose
```

(Or open System Settings, Privacy & Security, and choose Allow Anyway after the first blocked run.) Downloading with
`curl` or `gh release download` sets no flag, so no step is needed. Windows may show a SmartScreen warning for the same
reason: More info, then Run anyway.

Each release lists `SHA256SUMS`, and this checks that a file was built here (see
[docs/release.md](https://github.com/WPTK/wikihose/blob/v0.1.0/docs/release.md)); use the name of the file you downloaded:

```sh
gh attestation verify ./wikihose-0.2.0-linux-x86_64.tar.gz --repo WPTK/wikihose --signer-workflow WPTK/wikihose/.github/workflows/release.yml
```

To run it from a clone instead, see [CONTRIBUTING.md](https://github.com/WPTK/wikihose/blob/v0.1.0/CONTRIBUTING.md) for the setup.

## Run it

```powershell
wikihose --demo        # a six-minute recording of English Wikipedia, with a recorded day of reading numbers; no network
wikihose               # live, English Wikipedia
wikihose --wiki dewiki # another wiki
wikihose --version
wikihose --selftest    # checks the install without the network
```

`wikihose --help` lists the rest (`--replay`, `--record`). `q` quits.

wikihose is an unofficial project. It is not affiliated with, endorsed by, or sponsored by the Wikimedia Foundation.
Wikipedia is a trademark of the Wikimedia Foundation. It reads public Wikimedia feeds and APIs and keeps well inside
their rate limits.

See [docs/design.md](https://github.com/WPTK/wikihose/blob/v0.1.0/docs/design.md) for how it works and [CONTRIBUTING.md](https://github.com/WPTK/wikihose/blob/v0.1.0/CONTRIBUTING.md) to help.

## License

Code: [MIT](https://github.com/WPTK/wikihose/blob/v0.1.0/LICENSE). Recorded Wikimedia data in the test fixtures and the demo is CC BY-SA 4.0; see [NOTICE](https://github.com/WPTK/wikihose/blob/v0.1.0/NOTICE).
