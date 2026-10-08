#!/usr/bin/env bash
# Build dist/AutoEdit-<version>-arm64.dmg from a clean checkout.
#   scripts/build-mac.sh            full build
#   SKIP_FFMPEG=1 scripts/build-mac.sh   reuse build/bin from a previous run
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
BUILD=$ROOT/build
PY_VERSION=3.12
FFMPEG_VERSION=9.0.2
mkdir -p "$BUILD"

echo "==> 1/5 web UI"
npm --prefix web ci --silent
npm --prefix web run build --silent

echo "==> 2/5 Python $PY_VERSION (python-build-standalone via uv)"
rm -rf "$BUILD/python"
uv python install "$PY_VERSION" --quiet
PY_SRC=$(dirname "$(dirname "$(uv python find "$PY_VERSION")")")
# uv's "cpython-3.12-..." folder is a symlink; copying through it leaves a Python that still
# believes it lives in uv's folder. Copy the real versioned folder instead.
PY_SRC=$(cd "$PY_SRC" && pwd -P)
cp -R "$PY_SRC" "$BUILD/python"
find "$BUILD/python" -name EXTERNALLY-MANAGED -delete
PY="$BUILD/python/bin/python3"
uv pip install --python "$PY" --quiet -r requirements-app.txt
uv pip install --python "$PY" --quiet --no-deps mlx-whisper
"$PY" - <<'PYCHECK'
import importlib
for m in ["mlx_whisper", "mlx.core", "numba", "scipy.signal", "fastapi", "uvicorn", "anthropic", "PIL", "Vision", "huggingface_hub"]:
    importlib.import_module(m)
print("python deps ok")
PYCHECK
# Slim it down: tests, caches, headers, the unused tk/idle stack.
find "$BUILD/python" -type d \( -name __pycache__ -o -name tests -o -name test \) -prune -exec rm -rf {} +
rm -rf "$BUILD/python/include" "$BUILD/python/share" "$BUILD/python/lib/tcl"* "$BUILD/python/lib/tk"* \
       "$BUILD/python/lib/python$PY_VERSION/idlelib" "$BUILD/python/lib/python$PY_VERSION/tkinter"

echo "==> 3/5 ffmpeg $FFMPEG_VERSION (static, only what AutoEdit uses)"
if [[ "${SKIP_FFMPEG:-}" != 1 || ! -x "$BUILD/bin/ffmpeg" ]]; then
  SRC=$BUILD/ffmpeg-src
  if [[ ! -d $SRC ]]; then
    curl -fsSL "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz" -o "$BUILD/ffmpeg.tar.xz"
    mkdir -p "$SRC" && tar -xf "$BUILD/ffmpeg.tar.xz" -C "$SRC" --strip-components 1
  fi
  # Link x264 statically: give the linker a folder with only the .a in it.
  X264=$BUILD/x264-static
  rm -rf "$X264" && mkdir -p "$X264/lib" "$X264/include"
  cp "$(brew --prefix x264)/lib/libx264.a" "$X264/lib/"
  cp "$(brew --prefix x264)"/include/x264*.h "$X264/include/"
  mkdir -p "$X264/lib/pkgconfig"
  cat > "$X264/lib/pkgconfig/x264.pc" <<PC
prefix=$X264
libdir=\${prefix}/lib
includedir=\${prefix}/include
Name: x264
Description: H.264 encoder (static)
Version: $(sed -n 's/^#define X264_POINTVER "\(.*\)"/\1/p' "$X264/include/x264_config.h" | cut -d' ' -f1)
Libs: -L\${libdir} -lx264 -lpthread -lm
Cflags: -I\${includedir}
PC
  export PKG_CONFIG_PATH="$X264/lib/pkgconfig"
  (cd "$SRC" && ./configure --prefix="$BUILD/ffmpeg-install" \
      --enable-gpl --enable-static --disable-shared --disable-autodetect \
      --enable-videotoolbox --enable-audiotoolbox --enable-libx264 --enable-zlib \
      --disable-doc --disable-ffplay --disable-network --disable-debug \
      --pkg-config-flags=--static \
      --extra-cflags="-I$X264/include" --extra-ldflags="-L$X264/lib" >/dev/null \
    && make -j"$(sysctl -n hw.ncpu)" >/dev/null && make install >/dev/null)
  mkdir -p "$BUILD/bin"
  cp "$BUILD/ffmpeg-install/bin/ffmpeg" "$BUILD/ffmpeg-install/bin/ffprobe" "$BUILD/bin/"
fi
# Fail the build if anything AutoEdit relies on is missing, or if it links Homebrew libraries.
# (Capture the lists first: under pipefail, `ffmpeg | grep -q` fails when grep exits early.)
ENCODERS=$("$BUILD/bin/ffmpeg" -hide_banner -encoders)
FILTERS=$("$BUILD/bin/ffmpeg" -hide_banner -filters)
for e in h264_videotoolbox libx264 aac pcm_s16le mjpeg; do grep -qw "$e" <<<"$ENCODERS" || { echo "missing encoder $e"; exit 1; }; done
for f in overlay scale_vt hwdownload sendcmd afade crop scale fps split; do grep -qw "$f" <<<"$FILTERS" || { echo "missing filter $f"; exit 1; }; done
if otool -L "$BUILD/bin/ffmpeg" | grep -q /opt/homebrew; then echo "ffmpeg links Homebrew libraries"; exit 1; fi
echo "ffmpeg ok"

echo "==> 4/5 app code"
rm -rf "$BUILD/app" && mkdir -p "$BUILD/app"
rsync -a --exclude __pycache__ server "$BUILD/app/"
cp -R web/dist "$BUILD/app/web-dist"
cp THIRD_PARTY_NOTICES.md "$BUILD/app/"

echo "==> 5/5 package"
# Package outside the repo: if ~/Documents is synced by iCloud Drive, the sync keeps adding
# Finder metadata to the .app, which invalidates its code signature.
OUT="$HOME/Library/Caches/AutoEdit-build/dist"
rm -rf "$OUT"
npm --prefix desktop ci --silent
(cd desktop && npx electron-builder --mac dmg --arm64 --publish never -c.directories.output="$OUT")
mkdir -p dist
cp "$OUT"/*.dmg dist/
ls -lh dist/*.dmg
