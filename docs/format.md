# Mi Sphere video calibration notes

These notes describe observations from one original 3456×1728 Mi Sphere MP4
and investigation of the legacy Madventure Android app. They document the
format used by this independent implementation, not a complete camera
specification. No vendor source or personal recording is reproduced here.

## From two fisheyes to one sphere

One decoded frame contains two 1728×1728 lens regions side by side. A generic
dual-fisheye projection can show both hemispheres, but it does not account for
the camera's actual lens calibration. On the investigated recording, the
embedded calibration substantially improved alignment over the generic
projection; near-object parallax and exposure differences still remained.

The output is equirectangular: horizontal position spans longitude and vertical
position spans latitude. The lookup maps supply source-image coordinates for
that output grid. No feature matching between adjacent video frames is required
for this initial calibration-based conversion.

## MP4 container

Camera metadata lives under:

```text
moov
└── udta
    └── madv
        ├── lutz   lens calibration bundle
        ├── tlyd   original orientation records
        ├── fltr   starting orientation-record repetition count
        └── ...    other camera metadata
```

The legacy parser also recognizes `GYRA` as an app-generated orientation-data
variant, and additional `cutl`, `caif`, and `gpsx` sections. Ordinary stream
inspection can expose only video and audio even when these camera sections
are present.

MP4 box lengths use big-endian integers. Calibration offsets and orientation
values inside the camera sections use little-endian encodings. The converter
walks box boundaries and skips video payloads rather than searching arbitrary
compressed-video bytes for a matching string.

## `lutz` calibration bundle

The observed payload starts with a 64-byte index: sixteen little-endian
unsigned 32-bit values representing eight `(offset, length)` pairs. Each offset
is relative to the start of the `lutz` payload, after the MP4 box header. Each
pair points to a regular PNG image within the bundle.

Observed entry order:

| Index | Map | Meaning |
| --- | --- | --- |
| 0 | `r_x_int` | Right lens, integer X coordinate |
| 1 | `r_x_min` | Right lens, fractional X coordinate |
| 2 | `r_y_int` | Right lens, integer Y coordinate |
| 3 | `r_y_min` | Right lens, fractional Y coordinate |
| 4 | `l_x_int` | Left lens, integer X coordinate |
| 5 | `l_x_min` | Left lens, fractional X coordinate |
| 6 | `l_y_int` | Left lens, integer Y coordinate |
| 7 | `l_y_min` | Left lens, fractional Y coordinate |

The extracted maps were **512×256, 16-bit grayscale PNGs**. Integer coordinates
were within the 1728-square lens image; fractional values ranged from 0 to
999. The decoder uses:

```text
coordinate = integer_map + fractional_map / 1000
```

The bundle header, PNG signatures, decoded ranges, and an actual stitched
frame support this interpretation. The converter interpolates the LUT grid
from its first through last grid points to the output resolution. Right-lens
X coordinates receive an additional 1728-pixel offset to address the right
half of the original frame. These coordinate conventions have not been
validated for other recording resolutions.

## Blending

Each hemisphere uses its corresponding calibrated lens view. A narrow angular
transition blends the two views around the hemisphere boundaries. The initial
default is a one-degree half-width on each side of the boundary.

Let `u` and `v` be normalized panorama coordinates, `a` the blend half-width in
radians, and `theta = min(2πu, 2π(1-u))`. The implementation calculates:

```text
r = sin(πv)
b0 = acos(clamp(sin(a) / r, -1, 1))
b1 = acos(clamp(-sin(a) / r, -1, 1))
left_weight = clamp((b1 - theta) / (b1 - b0), 0, 1)
output = left_view * left_weight + right_view * (1 - left_weight)
```

Small denominators are bounded numerically at the poles. This mathematical
description follows observed renderer behavior; this repository contains
independent NumPy/OpenCV implementation, not extracted shader source.

## Orientation data: understood partially, not applied

`tlyd` contains little-endian float32 3×3 matrices, 36 bytes per record, followed
by padding. The legacy parser scans nonzero records until a zero record, then
prepends copies of the first matrix according to `fltr`.

The investigated recording contained more nonzero orientation records than
video frames. Initial matrices were finite with determinants approximately
one, consistent with rotation matrices, but record-to-frame alignment and
coordinate conventions need further validation. **The converter does not
apply these records and does not currently stabilize video.**

## Output and provenance

OpenCV handles coordinate interpolation and image resampling. FFmpeg handles
H.264 encoding and compressed audio copying. The converter adds the
[Google spherical-video v1 metadata](https://github.com/google/spatial-media/blob/master/docs/spherical-video-rfc.md)
UUID `ffcc8263-f855-4a93-8814-587a02521fdd` to the video track in a terminal
`moov` box, leaving earlier media offsets unchanged.

Investigation of the Madventure app's MP4 parser identified the camera box
names and orientation-record layout. Its Java rendering interfaces and native
library's embedded shader text supplied clues about calibration lookup and
boundary blending. Direct examination of the original MP4 established the
bundle offsets, PNG encodings, coordinate ranges, and working mapping.

The same investigation found legacy GPU/driver-specific shader workarounds
and an auto-fix UI for black hemispheres. That is evidence about historical
app compatibility, not proof of any particular user's present rendering fault.
The Python converter takes a different execution path and loads none of the
app's binaries.

Further references:

- [Xiaomi camera FAQ](https://www.mi.com/mx/support/faq/details/KA-07899/):
  original H.264 MP4 recording modes and camera-app identity.
- [Legacy app archive](https://ez-team.com/xiaomi.html): historical Mi Sphere
  and Madventure releases used during investigation.
- [Mi Sphere photo stitching templates](https://github.com/RubenFro/XiaomiMijiaMi-HuginTemplate):
  independent photo workflow; not the basis of this converter's video code.
