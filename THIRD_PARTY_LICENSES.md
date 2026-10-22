# Third-party software in VidFetch

VidFetch's own code is MIT-licensed (see `LICENSE`). The Windows download
bundles the following programs and libraries, each under its own licence.
Their source code is available from the links below.

## Separate programs (in their own folders)

| Component | Licence | Source |
|---|---|---|
| FFmpeg and FFprobe (`ffmpeg/`) | LGPL-2.1-or-later; GPL-2.0-or-later if `ffmpeg/SOURCE.txt` says "gpl build". Full text in `ffmpeg/LICENSE.txt`. | https://github.com/FFmpeg/FFmpeg, built by https://github.com/BtbN/FFmpeg-Builds |
| Deno (`deno/`) | MIT, full text in `deno/LICENSE.md` | https://github.com/denoland/deno |

VidFetch only runs these as separate programs. They are unmodified official
builds. The exact FFmpeg build used for a release is recorded in that
release's build log on GitHub Actions.

## Libraries packaged inside VidFetch.exe

| Component | Licence | Source |
|---|---|---|
| Python | PSF License | https://www.python.org |
| yt-dlp | Unlicense | https://github.com/yt-dlp/yt-dlp |
| yt-dlp-ejs | Unlicense | https://github.com/yt-dlp/ejs |
| pywebview | BSD-3-Clause | https://github.com/r0x0r/pywebview |
| pythonnet | MIT | https://github.com/pythonnet/pythonnet |
| requests | Apache-2.0 | https://github.com/psf/requests |
| urllib3 | MIT | https://github.com/urllib3/urllib3 |
| certifi | MPL-2.0 | https://github.com/certifi/python-certifi |
| websockets | BSD-3-Clause | https://github.com/python-websockets/websockets |
| Brotli | MIT | https://github.com/google/brotli |
| pycryptodomex | BSD-2-Clause and public domain | https://github.com/Legrandin/pycryptodome |
| PyInstaller bootloader | GPL-2.0 with an exception that allows bundling into any program | https://github.com/pyinstaller/pyinstaller |

When yt-dlp is updated from inside the app, the newer copy is downloaded from
PyPI (https://pypi.org/project/yt-dlp/) under the same licence.
