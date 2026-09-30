"""Synthetic checks: no private recordings or vendor calibration files needed."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import misphere


def box(kind, payload):
    return struct.pack('>I4s', len(payload) + 8, kind) + payload


def synthetic_lut():
    """Both maps sample the center of their lens, independent of real cameras."""
    payloads = []
    for name in misphere.NAMES:
        values = np.full((8, 16), 864 if name.endswith('_int') else 0, np.uint16)
        ok, encoded = cv2.imencode('.png', values)
        if not ok:
            raise RuntimeError('Cannot encode synthetic calibration')
        payloads.append(encoded.tobytes())
    index = []
    offset = 64
    for png in payloads:
        index.extend((offset, len(png)))
        offset += len(png)
    return struct.pack('<16I', *index) + b''.join(payloads)


class FormatTests(unittest.TestCase):
    def test_4k_scales_calibration_and_offsets_right_lens(self):
        cal = misphere.calibration({'lutz': synthetic_lut()})
        maps, _ = misphere.mapping(cal, 3840, 1920, 32, 16, 1)
        np.testing.assert_allclose(maps['l'][0], 960)
        np.testing.assert_allclose(maps['l'][1], 960)
        np.testing.assert_allclose(maps['r'][0], 2880)
        np.testing.assert_allclose(maps['r'][1], 960)

    def test_box_bounds_and_extended_size(self):
        extended = struct.pack('>I4sQ', 1, b'free', 19) + b'abc'
        self.assertEqual(list(misphere.boxes(io.BytesIO(extended), 0, len(extended))),
                         [(0, 19, 16, b'free')])
        bad = struct.pack('>I4s', 100, b'lutz') + b'abc'
        with self.assertRaisesRegex(ValueError, 'Invalid MP4 box'):
            list(misphere.boxes(io.BytesIO(bad), 0, len(bad)))

    def test_calibration_is_read_from_nested_boxes(self):
        lut = synthetic_lut()
        content = box(b'moov', box(b'udta', box(b'madv', box(b'lutz', lut))))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'synthetic.mp4'
            source.write_bytes(content)
            maps = misphere.calibration(misphere.metadata(source))
        self.assertEqual(maps['r_x_int'].shape, (8, 16))
        self.assertEqual(int(maps['l_y_int'][0, 0]), 864)

    def test_invalid_calibration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'No embedded lens calibration'):
            misphere.calibration({})
        invalid_index = struct.pack('<16I', *([64, 1000000] * 8))
        with self.assertRaisesRegex(ValueError, 'Invalid calibration PNG offset/size'):
            misphere.calibration({'lutz': invalid_index})

    def test_both_hemispheres_are_sampled(self):
        cal = misphere.calibration({'lutz': synthetic_lut()})
        source = np.zeros((1728, 3456, 3), np.uint8)
        source[:, :1728] = (0, 0, 255)
        source[:, 1728:] = (255, 0, 0)
        maps, weights = misphere.mapping(cal, 3456, 1728, 320, 160, 1)
        panorama = misphere.stitch(source, maps, weights)
        np.testing.assert_array_equal(panorama[80, 0], (0, 0, 255))
        np.testing.assert_array_equal(panorama[80, 160], (255, 0, 0))
        with self.assertRaisesRegex(ValueError, 'Unsupported recording dimensions'):
            misphere.mapping(cal, 2304, 1152, 320, 160, 1)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),
                     'FFmpeg and FFprobe are required for integration checks')
class VideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.input_dir = cls.root / 'originals'
        cls.input_dir.mkdir()
        cls.source = cls.input_dir / 'synthetic.mp4'
        subprocess.run([
            'ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
            'color=c=red:s=1728x1728:r=30', '-f', 'lavfi', '-i',
            'color=c=blue:s=1728x1728:r=30', '-f', 'lavfi', '-i',
            'sine=frequency=440:sample_rate=48000', '-filter_complex',
            '[0:v][1:v]hstack=inputs=2[v]', '-map', '[v]', '-map', '2:a',
            '-t', '0.2', '-c:v', 'libx264', '-preset', 'ultrafast',
            '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(cls.source)
        ], check=True)
        # Append synthetic camera metadata to a terminal moov, keeping media offsets.
        with cls.source.open('r+b') as f:
            top = list(misphere.boxes(f, 0, cls.source.stat().st_size))
            pos, size, header, kind = next(b for b in top if b[3] == b'moov')
            if pos + size != cls.source.stat().st_size or header != 8:
                raise RuntimeError('Synthetic FFmpeg input must have a terminal moov')
            f.seek(pos)
            moov = f.read(size)
            new = bytearray(moov + box(b'udta', box(b'madv', box(b'lutz', synthetic_lut()))))
            struct.pack_into('>I', new, 0, len(new))
            f.seek(pos)
            f.write(new)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_cli(self, *arguments):
        return subprocess.run([sys.executable, str(Path(misphere.__file__)), *map(str, arguments)],
                              capture_output=True, text=True, timeout=60)

    def audio_hash(self, path):
        return subprocess.check_output([
            'ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0',
            '-c', 'copy', '-f', 'hash', '-hash', 'sha256', '-'
        ])

    def test_conversion_preserves_audio_and_marks_spherical_video(self):
        output = self.root / 'stitched.mp4'
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result = self.run_cli('convert', self.source, output, '--width', 320)
        self.assertEqual(result.returncode, 0, result.stderr)
        info = misphere.probe(output)
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        self.assertEqual((video['width'], video['height'], int(video['nb_frames'])),
                         (320, 160, 6))
        spherical = next(s for s in video['side_data_list']
                         if s['side_data_type'] == 'Spherical Mapping')
        self.assertEqual(spherical['projection'], 'equirectangular')
        self.assertEqual(self.audio_hash(self.source), self.audio_hash(output))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), source_hash)
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(output), '-f', 'null', '-'], check=True)
        cap = misphere.VideoFrames(output, 320, 160)
        try:
            ok, frame = cap.read()
        finally:
            cap.release()
        self.assertTrue(ok)
        self.assertGreater(int(frame[80, 0, 2]), 200)
        self.assertGreater(int(frame[80, 160, 0]), 200)
        before = output.read_bytes()
        result = self.run_cli('convert', self.source, output, '--width', 320)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refusing to overwrite', result.stderr)
        self.assertEqual(output.read_bytes(), before)

    def test_batch_continues_records_failure_and_skips_existing_output(self):
        output_dir = self.root / 'batch'
        bad = self.input_dir / 'broken.mp4'
        bad.write_bytes(b'not an MP4 recording')
        second = self.input_dir / 'VID_002.mp4'
        shutil.copyfile(self.source, second)
        try:
            result = self.run_cli('batch', self.input_dir, output_dir, '--width', 320)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertTrue((output_dir / 'synthetic_s.mp4').exists())
            self.assertTrue((output_dir / 'VID_002_s.mp4').exists())
            self.assertFalse((output_dir / 'synthetic.mp4').exists())
            failures = json.loads((output_dir / 'failures.json').read_text())
            self.assertEqual(len(failures), 1)
            self.assertEqual(Path(failures[0]['input']).name, 'broken.mp4')
        finally:
            bad.unlink()
            second.unlink()
        result = self.run_cli('batch', self.input_dir, output_dir, '--width', 320)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Skip existing', result.stdout)

    def test_nested_batch_output_is_rejected(self):
        result = self.run_cli('batch', self.input_dir, self.input_dir / 'converted')
        self.assertEqual(result.returncode, 2)
        self.assertIn('outside the input directory', result.stderr)

    def test_automatic_file_identifies_converts_and_skips_tagged_output(self):
        output = self.source.with_name('synthetic_s.mp4')
        try:
            checked = self.run_cli('auto', '--check', self.source)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertIn('dual-fisheye', checked.stdout)
            self.assertFalse(output.exists())
            converted = self.run_cli('auto', self.source, '--width', 320)
            self.assertEqual(converted.returncode, 0, converted.stderr)
            self.assertTrue(output.exists())
            tagged = self.run_cli('auto', output)
            self.assertEqual(tagged.returncode, 0, tagged.stderr)
            self.assertIn('equirectangular', tagged.stdout)
            self.assertFalse(output.with_name('synthetic_s_s.mp4').exists())
        finally:
            if output.exists():
                output.unlink()

    def test_automatic_folder_preserves_names_and_reports_unknown_files(self):
        folder = self.root / 'automatic'
        nested = folder / 'subfolder'
        nested.mkdir(parents=True)
        shutil.copyfile(self.source, folder / 'VID_001.mp4')
        shutil.copyfile(self.source, nested / 'VID_002.mp4')
        unknown = folder / 'untagged.mp4'
        # Remuxing intentionally removes the synthetic camera-specific LUT.
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(self.source), '-c', 'copy',
                        '-map_metadata', '-1', str(unknown)], check=True)
        result = self.run_cli('auto', folder, '--width', 320)
        self.assertEqual(result.returncode, 1, result.stderr)
        outputs = folder.with_name('automatic_s')
        self.assertTrue((outputs / 'VID_001_s.mp4').exists())
        self.assertTrue((outputs / 'subfolder' / 'VID_002_s.mp4').exists())
        self.assertFalse((outputs / 'untagged_s.mp4').exists())
        self.assertIn('unknown', result.stdout)
        errors = json.loads((outputs / 'failures.json').read_text())
        self.assertEqual(len(errors), 1)
        self.assertEqual(Path(errors[0]['input']).name, 'untagged.mp4')


if __name__ == '__main__':
    unittest.main()
