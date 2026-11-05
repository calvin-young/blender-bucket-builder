# Demo reels

Scripted demo videos of Bucket Builder. Every frame is a real screenshot of Blender running the
add-on, played by a script, then edited into a 1920x1080 MP4 with a title card and captions.

| Reel | Length | What it shows |
| --- | --- | --- |
| `check/` | about 64 s | Build volume, collisions, clearance and wall gap live while parts move, the problem list, ignoring a problem, the Build Report |
| `pack/` | about 2 min 15 s | Parts packed by the real packer (the fill replayed), the result checked, the density limit compared, then the chips below and wedges, in one continuous shot |
| `chips/` | about 1 min | A can of 100 different chips: packed by hand, dumped out, a simulated poor packer, then the real packer's stacks. Material Preview, not solid shading |

```
demo/check/make.sh capture [segment ...]   # play the segments in Blender, save frames
demo/check/make.sh edit                    # cards, captions, cross-fades -> demo/out/check.mp4
```

`demo/pack/make.sh` works the same way (its `make.sh` lists its extra steps). `lib/` is the capture
and edit library, with synthetic mechanical parts. `cache/` and `out/` are gitignored. A capture
uses a private Blender config, so your own is untouched, and needs a real display, Pillow, numpy
and ffmpeg. Only synthetic parts are filmed. The add-on is linked from this working tree.

The poor packer on screen is simulated: chips tumbled at random and dropped in by our own packer with nesting off.
