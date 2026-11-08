# wikihose

A terminal dashboard for watching Wikipedia live: what people are editing right now, what people are suddenly reading,
where the two overlap, and edits to the pages you choose to track.

**Status: in development: the Live tab works; the other tabs arrive in later versions.**

![The wikihose Live tab playing the bundled demo recording: a scrolling edit log on the left, edits per minute, a split of people and bots, and the most edited articles on the right.](docs/images/demo.svg)

*Recorded data, not live*

## Try it

From a development checkout (see [CONTRIBUTING.md](CONTRIBUTING.md) for setup), in PowerShell:

```powershell
.venv\Scripts\python -m wikihose --demo
.venv\Scripts\python -m wikihose
```

The first plays a six-minute recording with no network. The second goes live. Installing from PyPI arrives with the
first release.

wikihose is an unofficial project. It is not affiliated with, endorsed by, or sponsored by the Wikimedia Foundation.
Wikipedia is a trademark of the Wikimedia Foundation.

See [docs/design.md](docs/design.md) for what it will do and [CONTRIBUTING.md](CONTRIBUTING.md) to help.

## License

Code: [MIT](LICENSE). Recorded Wikimedia data in the test fixtures and demo recording is CC BY-SA 4.0; see
[NOTICE](NOTICE).
