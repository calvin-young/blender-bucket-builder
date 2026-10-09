"""Helpers shared by the tests that run in a Blender window."""

import os
import struct
import zlib

import bpy
import numpy as np

# With software OpenGL on a virtual display Blender cannot read its own front
# buffer back (the screenshot operator returns black).  When the display is an
# Xvfb started with -fbdir, its framebuffer file is read instead: that is
# exactly what a monitor would show.  tests/gui_on_xvfb.sh sets this up.
XVFB_FB = os.environ.get('BUCKET_BUILDER_XVFB_FB', '')


def xwd_pixels(src):
    """An X window dump (24 or 32 bits per pixel) as an array (height, width, 3)
    of red, green, blue; the first row is the top of the screen."""
    with open(src, 'rb') as f:
        data = f.read()
    hdr = struct.unpack('>25I', data[:100])
    header_size, width, height = hdr[0], hdr[4], hdr[5]
    byte_order, bpp, bytes_per_line, ncolors = hdr[7], hdr[11], hdr[12], hdr[19]
    raw = np.frombuffer(data, dtype=np.uint8, count=bytes_per_line * height,
                        offset=header_size + ncolors * 12).reshape(height, bytes_per_line)
    px = raw[:, :width * (bpp // 8)].reshape(height, width, bpp // 8)
    return np.ascontiguousarray(px[:, :, [2, 1, 0]] if byte_order == 0 else px[:, :, 1:4])


def screen_pixels():
    """What the virtual display shows right now, or None without one."""
    if XVFB_FB and os.path.exists(XVFB_FB):
        return xwd_pixels(XVFB_FB)
    return None


def xwd_to_png(src, dst):
    """Convert an X window dump to a PNG file."""
    rgb = xwd_pixels(src)
    height, width = rgb.shape[:2]
    rows = b''.join(b'\x00' + rgb[y].tobytes() for y in range(height))

    def chunk(tag, d):
        c = struct.pack('>I', len(d)) + tag + d
        return c + struct.pack('>I', zlib.crc32(tag + d) & 0xffffffff)

    with open(dst, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
        f.write(chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)))
        f.write(chunk(b'IDAT', zlib.compress(rows, 6)))
        f.write(chunk(b'IEND', b''))


def screenshot(window, path):
    """Save what the window shows."""
    if XVFB_FB and os.path.exists(XVFB_FB):
        xwd_to_png(XVFB_FB, path)
    else:
        with bpy.context.temp_override(window=window, screen=window.screen):
            bpy.ops.screen.screenshot(filepath=path)
