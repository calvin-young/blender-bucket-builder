# Demo reels

Scripted demo videos of Bucket Builder. Every frame is a real screenshot of Blender running the
add-on, played by a script, then edited into a 1920x1080 MP4 with a title card and captions.

| Reel | Length | What it shows |
| --- | --- | --- |
| `check/` | about 64 s | Build volume, collisions, clearance and wall gap live while parts move, the problem list, ignoring a problem, the Build Report |
| `pack/` | about 50 s | Parts packed by the real packer (the fill replayed), the result checked, the density limit compared, curved chips nested |

```
demo/check/make.sh capture [segment ...]   # play the segments in Blender, save frames
demo/check/make.sh edit                    # cards, captions, cross-fades -> demo/out/check.mp4
```

`demo/pack/make.sh` works the same way (its `make.sh` lists its extra steps). `lib/` is the capture
and edit library, with synthetic mechanical parts. `cache/` and `out/` are gitignored. A capture
uses a private Blender config, so your own is untouched, and needs a real display, Pillow, numpy
and ffmpeg. Only synthetic parts are filmed. The add-on is linked from this working tree.
