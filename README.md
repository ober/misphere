# MiSphere Converter

**Give your Xiaomi Mi Sphere recordings a life beyond the Android app.**

Convert original **Xiaomi Mijia Mi Sphere / Madventure 360** dual-fisheye videos
into stitched, equirectangular 360° MP4s on your computer. MiSphere Converter
reads the lens calibration embedded in each recording, processes both lenses,
and preserves the original audio. No Android app, vendor runtime, account, or
cloud upload is required to convert your videos.

This is an early command-line converter, validated on a real **3456×1728**
Mi Sphere recording. Horizon stabilization is not implemented yet. See
[limitations](#current-limitations) before processing your archive.

## One command

```sh
./misphere video.mp4
```

That's it: setup is automatic, original camera files are identified from their
embedded calibration, and the output is `video_s.mp4`. No environment activation
or output filename is needed. Already tagged equirectangular 360 videos are
recognized and skipped. Existing outputs are preserved.

Process hundreds of recordings with the same command:

```sh
./misphere /path/to/videos
```

This scans subdirectories and writes `/path/to/videos_s/`, retaining the folder
layout and adding `_s.mp4` to each original stem. Problems are reported in
`failures.json`; one failed file does not stop the rest. Multiple file or folder
arguments are supported.

Identify a recording from a remote terminal without converting it:

```sh
./misphere --check video.mp4
```

The result is `dual-fisheye` (original camera calibration), `equirectangular`
(360 projection metadata), or `unknown`. These are metadata-based checks, not
a guarantee about arbitrary transcoded footage: if calibration/tags were
stripped or conflict, the script leaves the file unchanged instead of guessing.
No GUI is needed. The first run installs missing dependencies and may request
your sudo/doas password for system packages.

Optional preview: `./misphere --width 1440 --seconds 3 video.mp4`. Advanced
Makefile and Python commands remain available below.

## What it does

- **Uses the recording's own lens calibration** rather than guessing a generic
  fisheye projection or assuming every camera has identical lenses.
- **Stitches both lens views** into a standard 2:1 equirectangular panorama,
  with a narrow blend at the hemisphere boundaries.
- **Copies audio without re-encoding** while encoding the stitched video as
  H.264.
- **Adds 360° metadata** so compatible players recognize the output as
  spherical video.
- **Processes entire folders recursively**, preserves their directory layout,
  skips existing outputs, and records failed conversions.
- **Inspects hidden camera metadata**, including calibration and orientation
  records.
- **Keeps originals untouched** and refuses to overwrite an existing output.

Record with the camera's physical buttons, copy the original MP4s from its
microSD card, and convert them locally. Keep those original files: normal
transcoding tools can discard the camera calibration and orientation data.

## Install

You need **Python 3.9 or newer**, **FFmpeg/FFprobe** on your `PATH`, and an FFmpeg
build with the `libx264` encoder. The two Python dependencies are NumPy and
OpenCV. **The Makefile handles dependencies and the virtual environment.**

```sh
git clone https://github.com/ober/misphere.git
cd misphere
make
```

`make` and `make setup` install/check dependencies. Conversion and test targets
also run setup automatically when needed; there is no environment activation
step. Setup installs missing system packages with `sudo` or `doas` as needed.
It supports FreeBSD `pkg`, macOS Homebrew, and Debian/Ubuntu `apt-get`. On other
systems, install Python and FFmpeg with your package manager first.

**FreeBSD:** use the built-in `make`; GNU make is not required. Setup installs
native `pyXY-numpy` and `pyXY-opencv-python-headless` packages matching your
Python version, then makes them available in the virtual environment. It avoids
building OpenCV from PyPI. Video decoding uses the FFmpeg executable, so OpenCV
does not need its own video decoder. The tested FreeBSD packages use Python 3.12; select
a different installed version with `make PYTHON=python3.12` if needed.

Run `make help` for all targets. The underlying Python CLI remains available
as `.venv/bin/python misphere.py ...`.

## Convert a video

```sh
make convert INPUT=original.mp4
```

This writes `original_s.mp4` beside the input. Every default output keeps its
original filename and adds `_s` before `.mp4`. Choose another name with
`OUTPUT=stitched.mp4`. The default output is **3456×1728** at the input's frame rate. Open the result
in a 360°-capable video player. An ordinary player displays a flat panorama;
that alone does not mean the conversion failed.

Make a short preview before committing to a large batch:

```sh
make convert INPUT=original.mp4 OUTPUT=preview.mp4 WIDTH=1440 SECONDS=3
```

Options:

| Make variable | Default | Purpose |
| --- | --- | --- |
| `WIDTH` | `3456` | Output width; height is half the width. Must be divisible by four. |
| `SECONDS` | Entire clip | Convert only the beginning of the video. |
| `SEAM_DEGREES` | `1` | Angular blend half-width at each hemisphere boundary, from greater than 0 through 10 degrees. |

Quote paths containing spaces.

## Convert an archive

```sh
make batch INPUT=/path/to/originals OUTPUT=/path/to/stitched
```

For example, `VID_001.mp4` becomes `VID_001_s.mp4` and `VID_002.mp4` becomes
`VID_002_s.mp4`, preserving subdirectories. Omit `OUTPUT` to use a sibling
directory named `originals_s`. Use a
separate output directory outside the input directory. The converter
finds `.mp4` files recursively, preserves relative paths, and skips outputs
that already exist. A failed file does not stop the remaining batch. If any
file fails, the command exits with status 1 and writes `failures.json` in the
output directory.

Inspect an existing output before relying on skip-existing behavior: the
converter does not verify files that were already present.

## Inspect camera metadata

```sh
make inspect INPUT=original.mp4
```

This prints JSON describing the calibration grid, orientation records, and
the FFprobe stream information. Orientation records are exposed for research;
their count is not necessarily the video's frame count.

## How it works

The original recording contains two circular fisheye views side by side. It
also contains a camera-specific `lutz` section under `moov/udta/madv` in the
MP4 container. That section holds eight 16-bit PNG lookup maps: integer and
fractional X/Y coordinates for each lens.

For each output panorama pixel, the converter interpolates these maps to find
the corresponding position in each lens image. OpenCV samples those positions
and blends the views around their boundaries. FFmpeg encodes the result,
copies the audio, and the converter adds Google's spherical-video metadata.

The implementation was developed by inspecting original recordings and
studying the legacy Android app's file parser and renderer behavior. It
implements the recovered format independently in Python. **No APKs,
decompiled application source, extracted vendor libraries, or private sample
footage are included in this repository.**

For the file layout, equations, provenance, and unresolved questions, read
[the format notes](docs/format.md).

## Current limitations

- Input support is currently restricted to **original 3456×1728 and 3840×1920 camera MP4s
  with embedded calibration**. Other recording modes are rejected pending
  validation. Video timing assumes constant frame rate and a zero start time.
- A Mi Sphere recording has been tested. Madventure-compatible support follows
  the shared legacy app/file format; recordings from every camera or firmware
  revision have not been verified.
- **Gyro stabilization and horizon leveling are not implemented.** The
  orientation-data layout is partly understood, but timing and coordinate
  conventions need validation.
- Visible seam exposure differences and close-object parallax can remain.
  This is calibration-based stitching with a narrow blend, not an optical-flow
  or adaptive-seam stitcher.
- Processing uses CPU remapping and software H.264 encoding. It is not a
  real-time preview or a GPU-accelerated converter.
- This is a CLI tool; there is no desktop GUI, Android app, camera-control
  client, or Wi-Fi downloader yet.
- Real camera footage has been tested on macOS arm64. FreeBSD's native setup
  and synthetic conversion tests are also checked. Other platforms have not
  been tested with real camera footage. Output storage must support hard links
  for atomic publishing.

If your Android app shows one black hemisphere, inspect the original camera
MP4 before assuming a lens was not recorded. This converter can bypass the
legacy rendering path when both views and calibration are present; it does
not repair that Android app or establish the cause on a particular phone.

## Validation and contributing

On the initial real recording, conversion preserved **380 frames**, the
**12.679333-second** video duration, and **byte-identical compressed audio**.
FFprobe recognized the output's equirectangular spherical metadata, and
FFmpeg decoded the whole output without errors. Both hemispheres were
visually checked. That footage remains private.

The repository includes synthetic tests so checks do not require personal
footage or calibration files:

```sh
make test
```

Useful contributions include validation of other recording modes, orientation
timing, stabilization, seam improvements, and tests on additional platforms.
When reporting a problem, include the command, camera/firmware version,
dependency versions, and error output. Share footage only if you are comfortable
making it public.

## License

[MIT](LICENSE). You may use, modify, redistribute, and sell this implementation
subject to the license's notice requirement. Dependency licenses remain their
own. This project is independent of Xiaomi and MADV.
