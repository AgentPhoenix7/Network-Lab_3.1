#!/usr/bin/env python3
"""Generate genuine, playable offline test media without padding or huge buffers.

WAV and raw AVI are each at least --mib MiB. Optional MP4 is a genuinely
compressed counterpart of the AVI, so its smaller size is expected. These are
generated signals/test patterns, not recordings. Only ffmpeg/ffprobe are needed.
"""

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

MIB = 1024 * 1024


def generate(output, mib=128, mp4=False):
    """Generate synthetic WAV, raw AVI, and optional H.264 MP4 media fixtures.

    What it does:
        Synthesizes standard multi-megabyte media test files using FFmpeg lavfi filters
        without padding bytes or artificial disk bloat, saving probe metadata and SHA-256 digests.

    How it does:
        1. Validates requested size (0 < mib <= 4096) and verifies presence of ffmpeg/ffprobe.
        2. Calculates frame counts for 48 kHz stereo PCM audio and 640x360 raw yuv420p video.
        3. Spawns FFmpeg commands to generate audio.wav and video.avi (and optionally video.mp4).
        4. Inspects media formats via ffprobe, computes sha256 checksums, and writes manifest.json.
    """
    if not math.isfinite(mib) or not 0 < mib <= 4096:
        raise ValueError('--mib must be finite and between 0 and 4096')
    for executable in ('ffmpeg', 'ffprobe'):
        if shutil.which(executable) is None:
            raise ValueError(executable + ' must be installed')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    names = ['audio.wav', 'video.avi'] + (['video.mp4'] if mp4 else [])
    for name in names + ['manifest.json']:
        if (output / name).exists():
            raise ValueError('Output already exists: ' + str(output / name))
    if shutil.disk_usage(output).free < mib * MIB * 2.5 + 32 * MIB:
        raise ValueError('Insufficient free space for the requested media fixtures')
    temporary = Path(os.environ.get('TMPDIR', str(Path(__file__).resolve().parents[1] / 'build' / 'tmp')))
    temporary.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TMPDIR=str(temporary), TMP=str(temporary), TEMP=str(temporary))
    base = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-n', '-filter_threads', '1']
    # 48 kHz, signed 16-bit, two-channel PCM is 192,000 bytes of audio per second.
    audio_frames = math.ceil(mib * MIB / 4)
    audio_duration = audio_frames / 48000
    # A yuv420p 640x360 raw frame is 345,600 bytes; frame count rounds upward.
    frames = math.ceil(mib * MIB / (640 * 360 * 3 // 2))
    jobs = [('audio.wav', base + ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:beep_factor=2',
                                 '-t', f'{audio_duration:.9f}', '-ac', '2', '-c:a', 'pcm_s16le',
                                 '-threads', '1', str(output / 'audio.wav')]),
            ('video.avi', base + ['-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24',
                                 '-frames:v', str(frames), '-an', '-c:v', 'rawvideo', '-pix_fmt', 'yuv420p',
                                 '-vtag', 'I420', '-threads', '1', str(output / 'video.avi')])]
    if mp4:
        jobs.append(('video.mp4', base + ['-i', str(output / 'video.avi'), '-an', '-c:v', 'libx264',
                                          '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p',
                                          '-threads', '2', '-movflags', '+faststart', str(output / 'video.mp4')]))
    manifest = {'description': 'Offline synthesized audio and generated moving video test pattern; no padding',
                'requested_uncompressed_mib': mib, 'files': []}
    for name, command in jobs:
        print('Generating ' + name, flush=True)
        started = time.perf_counter()
        try:
            subprocess.run(command, check=True, env=env)
        except BaseException:
            (output / name).unlink(missing_ok=True)
            raise
        elapsed = time.perf_counter() - started
        inspected = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(output / name)],
                                   check=True, capture_output=True, text=True, env=env)
        digest = hashlib.sha256()
        with (output / name).open('rb') as stream:
            while chunk := stream.read(MIB):
                digest.update(chunk)
        size = (output / name).stat().st_size
        manifest['files'].append({'name': name, 'bytes': size, 'sha256': digest.hexdigest(),
                                  'generation_seconds': elapsed, 'command': command,
                                  'probe': json.loads(inspected.stdout)})
        print(f'{name}: {size} bytes ({size / MIB:.2f} MiB), {elapsed:.2f}s generation', flush=True)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def main(argv=None):
    """Run the media fixture generation CLI.

    What it does:
        Parses CLI parameters (--output, --mib, --mp4) and calls generate.

    How it does:
        Builds ArgumentParser, parses args, executes generate(), and handles errors cleanly.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mib', type=float, default=128, help='Minimum WAV and AVI sizes, each (default 128 MiB)')
    parser.add_argument('--mp4', action='store_true', help='Also create a smaller H.264/MP4 counterpart with two encoder threads')
    args = parser.parse_args(argv)
    try:
        generate(args.output, args.mib, args.mp4)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print('Media generation failed: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
