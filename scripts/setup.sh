#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
platform=$(uname -s)
python_command=${PYTHON:-python3}

admin() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    elif command -v doas >/dev/null 2>&1; then
        doas "$@"
    else
        printf '%s\n' 'Installing system dependencies needs root, sudo, or doas.' >&2
        exit 1
    fi
}

linux_packages() {
    if command -v apt-get >/dev/null 2>&1; then
        admin apt-get update
        admin apt-get install -y "$@"
    else
        printf '%s\n' 'Install Python 3, its venv support, and FFmpeg with your package manager, then rerun make.' >&2
        exit 1
    fi
}

if ! command -v "$python_command" >/dev/null 2>&1; then
    case "$platform" in
        FreeBSD) admin pkg install -y python3 ;;
        Darwin)
            if ! command -v brew >/dev/null 2>&1; then
                printf '%s\n' 'Install Python 3 and FFmpeg, or install Homebrew, then rerun make.' >&2
                exit 1
            fi
            brew install python
            ;;
        Linux) linux_packages python3 python3-venv ;;
        *) printf '%s\n' 'Install Python 3 and FFmpeg, then rerun make.' >&2; exit 1 ;;
    esac
fi

"$python_command" -c 'import sys; sys.exit("Python 3.9 or newer is required") if sys.version_info < (3, 9) else None'

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    case "$platform" in
        FreeBSD) admin pkg install -y ffmpeg ;;
        Darwin)
            if ! command -v brew >/dev/null 2>&1; then
                printf '%s\n' 'Install FFmpeg or Homebrew, then rerun make.' >&2
                exit 1
            fi
            brew install ffmpeg
            ;;
        Linux) linux_packages ffmpeg ;;
        *) printf '%s\n' 'Install FFmpeg and FFprobe on PATH, then rerun make.' >&2; exit 1 ;;
    esac
fi

if [ "$platform" = FreeBSD ]; then
    # Use native bindings; PyPI's OpenCV wheels do not target FreeBSD.
    if ! "$python_command" -c 'import numpy, cv2' >/dev/null 2>&1; then
        python_flavor=$("$python_command" -c 'import sys; print("py%d%d" % sys.version_info[:2])')
        admin pkg install -y "$python_flavor-numpy" "$python_flavor-opencv-python-headless"
    fi
    "$python_command" -m venv --system-site-packages .venv
else
    if ! "$python_command" -m venv .venv; then
        if [ "$platform" = Linux ]; then
            linux_packages python3-venv
            "$python_command" -m venv .venv
        else
            exit 1
        fi
    fi
    .venv/bin/python -m pip install -r requirements.txt
fi

.venv/bin/python - <<'PY'
import shutil
import subprocess
import sys

import cv2
import numpy as np

def version(value):
    return tuple(int(n) for n in value.split('.')[:2])

if not (version(np.__version__) >= (1, 24) and version(np.__version__) < (3, 0)):
    sys.exit('NumPy must be >=1.24 and <3')
if not (version(cv2.__version__) >= (4, 8) and version(cv2.__version__) < (6, 0)):
    sys.exit('OpenCV must be >=4.8 and <6')
for name in ('ffmpeg', 'ffprobe'):
    if shutil.which(name) is None:
        sys.exit(f'{name} is missing from PATH')
encoders = subprocess.check_output(['ffmpeg', '-hide_banner', '-encoders'], stderr=subprocess.STDOUT, text=True)
if not any(len(line.split()) > 1 and line.split()[1] == 'libx264' for line in encoders.splitlines()):
    sys.exit('FFmpeg must include the libx264 encoder')
if 'FFMPEG:                      YES' not in cv2.getBuildInformation():
    sys.exit('OpenCV must have FFmpeg video decoding support')
print(f'Ready: Python {sys.version.split()[0]}, NumPy {np.__version__}, OpenCV {cv2.__version__}, FFmpeg.')
print('Run: make convert INPUT=original.mp4 (or make help).')
PY
touch .venv/.misphere-ready
