# Third-party software in the AutoEdit app

The AutoEdit desktop app bundles the following programs. AutoEdit's own code is not covered by
these licenses (it has no open-source license; all rights reserved).

## FFmpeg 9.0.2 with x264 (GPL v2 or later)

`ffmpeg` and `ffprobe` in `AutoEdit.app/Contents/Resources/bin` are FFmpeg built with libx264,
which makes the binaries GPL-licensed. They run as separate programs.

- FFmpeg source: https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz
- x264 source (revision r3222): https://code.videolan.org/videolan/x264
- Exact build steps and configure flags: `scripts/build-mac.sh` in this repository
  (`--enable-gpl --enable-static --disable-shared --disable-autodetect --enable-videotoolbox
  --enable-audiotoolbox --enable-libx264 --enable-zlib --disable-doc --disable-ffplay
  --disable-network --disable-debug`)

If you'd like a copy of the corresponding source for a release you received, open an issue in
this repository.

## Python 3.12 (PSF License)

A standalone CPython build from https://github.com/astral-sh/python-build-standalone, with
packages listed in `requirements-app.txt` (each under its own license: MIT, BSD, Apache-2.0)
and `mlx-whisper` (MIT).

## Electron (MIT) and Chromium (BSD-style)

The app shell. https://www.electronjs.org

## Whisper large-v3-turbo model (MIT)

Downloaded on first run from https://huggingface.co/mlx-community/whisper-large-v3-turbo.
