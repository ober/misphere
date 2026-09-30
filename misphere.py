#!/usr/bin/env python3
"""Independent Mi Sphere video stitching using calibration embedded in the MP4.

Requires FFmpeg, NumPy and OpenCV. No APK or vendor native library is loaded.
"""
import argparse
from fractions import Fraction
import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

import cv2
import numpy as np

NAMES = ('r_x_int', 'r_x_min', 'r_y_int', 'r_y_min',
         'l_x_int', 'l_x_min', 'l_y_int', 'l_y_min')
CONTAINERS = {b'moov', b'udta', b'madv', b'trak', b'mdia', b'minf', b'stbl'}


def boxes(f, start, end):
    """Walk bounded BMFF boxes without reading the video payload into memory."""
    while start + 8 <= end:
        f.seek(start)
        size, kind = struct.unpack('>I4s', f.read(8))
        header = 8
        if size == 1:
            size = struct.unpack('>Q', f.read(8))[0]
            header = 16
        elif size == 0:
            size = end - start
        if size < header or start + size > end:
            raise ValueError(f'Invalid MP4 box at {start}: {kind!r}, size {size}')
        yield start, size, header, kind
        start += size


def metadata(path):
    found = {}
    with open(path, 'rb') as f:
        def walk(start, end):
            for pos, size, head, kind in boxes(f, start, end):
                if kind in CONTAINERS:
                    walk(pos + head, pos + size)
                elif kind in (b'lutz', b'fltr', b'tlyd', b'GYRA'):
                    if size > 64 * 1024 * 1024:
                        raise ValueError('Oversized camera metadata')
                    f.seek(pos + head)
                    found[kind.decode()] = f.read(size - head)
        walk(0, os.path.getsize(path))
    return found


def calibration(meta):
    data = meta.get('lutz')
    if data is None or len(data) < 64:
        raise ValueError('No embedded lens calibration (lutz). Use an original camera MP4.')
    pairs = struct.unpack_from('<16I', data)
    result = {}
    for i, name in enumerate(NAMES):
        offset, size = pairs[2*i:2*i+2]
        if offset < 64 or offset + size > len(data):
            raise ValueError('Invalid calibration PNG offset/size')
        png = data[offset:offset+size]
        if not png.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('Calibration entry is not PNG')
        image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
        if image is None or image.dtype != np.uint16 or image.ndim != 2:
            raise ValueError('Expected 16-bit grayscale calibration PNG')
        result[name] = image
    if len({a.shape for a in result.values()}) != 1:
        raise ValueError('Calibration maps differ in size')
    return result


def probe(path):
    return json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))


class VideoFrames:
    """Decode with the FFmpeg executable, including on OpenCV builds without video I/O."""
    def __init__(self, path, width, height):
        self.width, self.height = width, height
        self.process = subprocess.Popen([
            'ffmpeg', '-v', 'error', '-nostdin', '-i', str(path), '-map', '0:v:0',
            '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'
        ], stdout=subprocess.PIPE)

    def read(self):
        size = self.width * self.height * 3
        data = self.process.stdout.read(size)
        if not data:
            if self.process.wait() != 0:
                raise RuntimeError('FFmpeg decoding failed')
            return False, None
        if len(data) != size:
            raise RuntimeError('FFmpeg returned an incomplete frame')
        return True, np.frombuffer(data, np.uint8).reshape(self.height, self.width, 3)

    def release(self):
        if self.process.poll() is None:
            # Rawvideo has no trailer to flush. Stop before closing the pipe so
            # intentional short previews do not print misleading broken-pipe errors.
            self.process.kill()
        self.process.wait()
        self.process.stdout.close()


def mapping(cal, src_width, src_height, width, height, seam_degrees):
    # Stored coordinates are local to each 1728-square lens in 3.5K video.
    # Other recording modes need separate validation before using this scale.
    if (src_width, src_height) != (3456, 1728):
        raise ValueError('Currently validated only for 3456x1728 Mi Sphere recordings')
    maps = {}
    # LUT grid includes both endpoints. Match the shader's endpoint interpolation.
    lut_h, lut_w = next(iter(cal.values())).shape
    xx, yy = np.meshgrid(np.linspace(0, lut_w-1, width, dtype=np.float32),
                         np.linspace(0, lut_h-1, height, dtype=np.float32))
    for side in ('l', 'r'):
        coords = []
        for axis in ('x', 'y'):
            a = cal[f'{side}_{axis}_int'].astype(np.float32)
            a += cal[f'{side}_{axis}_min'].astype(np.float32) / 1000.0
            a = cv2.remap(a, xx, yy, cv2.INTER_LINEAR)
            if side == 'r' and axis == 'x':
                a += src_width / 2
            coords.append(a)
        maps[side] = tuple(coords)
    u = np.linspace(0, 1, width)[None, :]
    v = np.linspace(0, 1, height)[:, None]
    rxz = np.sin(np.pi*v)
    z = np.sin(np.deg2rad(seam_degrees))
    bound0 = np.arccos(np.clip(z / np.maximum(rxz, 1e-12), -1, 1))
    bound1 = np.arccos(np.clip(-z / np.maximum(rxz, 1e-12), -1, 1))
    theta = np.minimum(2*np.pi*u, 2*np.pi*(1-u))
    weight = np.clip((bound1-theta) / np.maximum(bound1-bound0, 1e-12), 0, 1)
    return maps, weight.astype(np.float32)[..., None]


def stitch(frame, maps, weight):
    left = cv2.remap(frame, *maps['l'], cv2.INTER_LINEAR)
    right = cv2.remap(frame, *maps['r'], cv2.INTER_LINEAR)
    return np.clip(left * weight + right * (1-weight), 0, 255).astype(np.uint8)


def tag_spherical(path):
    """Add Google's spherical v1 UUID to the video trak of a moov-at-end MP4."""
    xml = b'''<?xml version="1.0"?><rdf:SphericalVideo
 xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:GSpherical="http://ns.google.com/videos/1.0/spherical/">
 <GSpherical:Spherical>true</GSpherical:Spherical>
 <GSpherical:Stitched>true</GSpherical:Stitched>
 <GSpherical:StitchingSoftware>Independent Mi Sphere converter</GSpherical:StitchingSoftware>
 <GSpherical:ProjectionType>equirectangular</GSpherical:ProjectionType>
 <GSpherical:StereoMode>mono</GSpherical:StereoMode></rdf:SphericalVideo>'''
    payload = bytes.fromhex('ffcc8263f8554a938814587a02521fdd') + xml
    uuid = struct.pack('>I4s', len(payload)+8, b'uuid') + payload
    with open(path, 'r+b') as f:
        top = list(boxes(f, 0, os.path.getsize(path)))
        pos, size, head, kind = next(b for b in top if b[3] == b'moov')
        if head != 8 or pos + size != os.path.getsize(path):
            raise ValueError('Spherical tagging requires a terminal, 32-bit moov')
        f.seek(pos)
        moov = f.read(size)
        track = None
        for p, n, h, t in boxes(io.BytesIO(moov), 8, len(moov)):
            if t == b'trak':
                # Handler type follows version/flags and pre_defined in hdlr.
                for mp, mn, mh, mt in boxes(io.BytesIO(moov), p+h, p+n):
                    if mt == b'mdia':
                        for hp, hn, hh, ht in boxes(io.BytesIO(moov), mp+mh, mp+mn):
                            if ht == b'hdlr' and moov[hp+hh+8:hp+hh+12] == b'vide':
                                track = (p, n)
        if track is None:
            raise ValueError('No video track to tag')
        p, n = track
        new = bytearray(moov[:p+n] + uuid + moov[p+n:])
        struct.pack_into('>I', new, 0, len(new))
        struct.pack_into('>I', new, p, n+len(uuid))
        f.seek(pos)
        f.write(new)
        f.truncate()


def convert(source, output, width, seam_degrees, seconds=None):
    if output.exists():
        raise FileExistsError(f'Refusing to overwrite {output}')
    if width < 4 or width % 4:
        raise ValueError('Width must be positive and divisible by four')
    meta = metadata(source)
    info = probe(source)
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    sw, sh = video['width'], video['height']
    fps = video['avg_frame_rate']
    rate = Fraction(fps)
    if rate <= 0:
        raise ValueError('Input must have a positive constant frame rate')
    if Fraction(video['r_frame_rate']) != rate or float(video.get('start_time', 0)) != 0:
        raise ValueError('Currently supported only for constant-frame-rate video starting at zero')
    maps, weight = mapping(calibration(meta), sw, sh, width, width//2, seam_degrees)
    output.parent.mkdir(parents=True, exist_ok=True)
    cap = VideoFrames(source, sw, sh)
    with tempfile.TemporaryDirectory(prefix='.misphere-', dir=output.parent) as temp:
        temp_output = Path(temp) / 'stitched.mp4'
        cmd = ['ffmpeg', '-v', 'error', '-nostdin', '-f', 'rawvideo', '-pixel_format', 'bgr24',
               '-video_size', f'{width}x{width//2}', '-framerate', fps, '-i', '-', '-i', str(source),
               '-map', '0:v:0', '-map', '1:a?', '-c:v', 'libx264', '-preset', 'fast',
               '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-map_metadata', '-1']
        if seconds is not None:
            cmd += ['-t', str(seconds)]
        cmd += [str(temp_output)]
        n, d = rate.numerator, rate.denominator
        frames = 0
        try:
            with subprocess.Popen(cmd, stdin=subprocess.PIPE) as encoder:
                try:
                    while seconds is None or frames*d/n < seconds:
                        ok, frame = cap.read()
                        if not ok:
                            break
                        encoder.stdin.write(stitch(frame, maps, weight).tobytes())
                        frames += 1
                        if frames % 100 == 0:
                            print(f'{source.name}: {frames} frames', flush=True)
                finally:
                    encoder.stdin.close()
                if encoder.wait() != 0:
                    raise RuntimeError('FFmpeg encoding failed')
        finally:
            cap.release()
        if frames == 0:
            raise ValueError('No frames decoded')
        tag_spherical(temp_output)
        # Atomic create avoids replacing an output written by another process.
        os.link(temp_output, output)
    audio = 'audio copied' if any(s['codec_type'] == 'audio' for s in info['streams']) else 'no audio'
    print(f'{output}: {frames} frames, embedded calibration, {audio}, spherical tag')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    inspect = subs.add_parser('inspect')
    inspect.add_argument('input', type=Path)
    single = subs.add_parser('convert')
    single.add_argument('input', type=Path)
    single.add_argument('output', type=Path)
    batch = subs.add_parser('batch')
    batch.add_argument('input_dir', type=Path)
    batch.add_argument('output_dir', type=Path)
    for p in (single, batch):
        p.add_argument('--width', type=int, default=3456,
                       help='Output width, divisible by four; height is half (default: 3456)')
        p.add_argument('--seam-degrees', type=float, default=1,
                       help='Blend half-width in degrees, greater than 0 through 10 (default: 1)')
        p.add_argument('--seconds', type=float, help='Convert only the beginning of the clip')
    args = parser.parse_args()
    for executable in ('ffmpeg', 'ffprobe') if args.command != 'inspect' else ('ffprobe',):
        if shutil.which(executable) is None:
            parser.error(f'{executable} is not on PATH; install FFmpeg first')
    if args.command == 'inspect':
        meta = metadata(args.input)
        cal = calibration(meta)
        gyro = meta.get('tlyd', meta.get('GYRA', b''))
        matrices = np.frombuffer(gyro[:len(gyro)//36*36], '<f4').reshape(-1, 9)
        valid = int(np.argmax(~np.any(matrices != 0, axis=1))) if len(matrices) else 0
        if len(matrices) and np.all(np.any(matrices != 0, axis=1)):
            valid = len(matrices)
        print(json.dumps({'metadata_bytes': {k: len(v) for k,v in meta.items()},
                          'lut_shape': list(next(iter(cal.values())).shape),
                          'gyro_valid_matrices': valid,
                          'gyro_first_matrix': matrices[0].tolist() if valid else None,
                          'probe': probe(args.input)}, indent=2))
    else:
        if not 0 < args.seam_degrees <= 10:
            parser.error('--seam-degrees must be between 0 and 10')
        if args.seconds is not None and (not math.isfinite(args.seconds) or args.seconds <= 0):
            parser.error('--seconds must be positive')
        if args.width < 4 or args.width % 4:
            parser.error('--width must be positive and divisible by four')
        if args.command == 'convert':
            convert(args.input.resolve(), args.output.resolve(), args.width, args.seam_degrees, args.seconds)
        else:
            input_root = args.input_dir.resolve()
            output_root = args.output_dir.resolve()
            if output_root == input_root or input_root in output_root.parents:
                parser.error('Output directory must be outside the input directory')
            failures = []
            inputs = sorted(p for p in args.input_dir.rglob('*') if p.suffix.lower() == '.mp4')
            if not inputs:
                parser.error('No MP4 inputs found')
            for source in inputs:
                output = args.output_dir / source.relative_to(args.input_dir)
                if output.exists():
                    print(f'Skip existing: {output}')
                    continue
                try:
                    convert(source.resolve(), output.resolve(), args.width, args.seam_degrees, args.seconds)
                except Exception as error:
                    failures.append({'input': str(source), 'error': str(error)})
                    print(f'FAILED {source}: {error}', flush=True)
            if failures:
                args.output_dir.mkdir(parents=True, exist_ok=True)
                (args.output_dir/'failures.json').write_text(json.dumps(failures, indent=2))
                raise SystemExit(1)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, RuntimeError) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
