"""Blur the "Users/<username>" home-folder path wherever it shows in the Claude panel.

blurpath.py in.mp4 out.mp4 [start dur]   (start/dur for a test run)
Pass 1 finds the text 5 times a second (half resolution, left 760 px, on the GPU)
by normalised cross-correlation against two snippets: the approval box's font and
the expanded JSON's monospace. Pass 2 blurs every 60 fps frame with each box found
at the samples either side, plus the hull of a box and its moved self in the next
sample, so scrolling text is covered between samples.
"""
import subprocess, sys
from pathlib import Path
import numpy as np, torch, torch.nn.functional as F
from PIL import Image

S = str(Path(__file__).resolve().parent.parent / "work/precut/blur-templates")
W, H, X1, FPS, STEP = 1920, 1080, 760, 60, 12       # sample every 12th frame = 5 a second
THR, PAD, BATCH = 0.7, 8, 64
dev = "mps" if torch.backends.mps.is_available() else "cpu"
src, out = sys.argv[1], sys.argv[2]
seek = ["-ss", sys.argv[3], "-t", sys.argv[4]] if len(sys.argv) > 4 else []

def tmpl(name, x0, x1):
    g = np.asarray(Image.open(f"{S}/{name}").convert("L"), dtype=np.float32)[::2, x0:x1:2]
    t = torch.from_numpy(g - g.mean())
    return (t / t.norm()).to(dev), g.shape
temps = [tmpl("t1.png", 35, 190), tmpl("t2.png", 37, 200)]

def find(batch):                      # batch: (B, 1, h, w) float on dev
    found = [[] for _ in range(batch.shape[0])]
    for t, (th, tw) in temps:
        n = th * tw
        s1 = F.avg_pool2d(batch, (th, tw), stride=1) * n
        s2 = F.avg_pool2d(batch * batch, (th, tw), stride=1) * n
        var = s2 - s1 * s1 / n
        ncc = F.conv2d(batch, t[None, None]) / var.clamp(min=1e-3).sqrt()
        ncc[var < n * 15 ** 2] = -1   # flat patches (no text) can't match
        ncc = ncc[:, 0].cpu().numpy()
        for b in np.nonzero(ncc.reshape(len(ncc), -1).max(1) >= THR)[0]:
            m = ncc[b]
            while True:
                y, x = np.unravel_index(m.argmax(), m.shape)
                if m[y, x] < THR: break
                found[b].append((2 * x - PAD, 2 * y - PAD, 2 * (x + tw) + PAD, 2 * (y + th) + PAD))
                m[max(y - th, 0):y + th, max(x - tw, 0):x + tw] = -1
    return found

# Pass 1
hw, hh = X1 // 2, H // 2
dec = subprocess.Popen(["ffmpeg", "-v", "error", *seek, "-i", src, "-vf",
                        f"select='not(mod(n,{STEP}))',crop={X1}:{H}:0:0,scale={hw}:{hh},format=gray",
                        "-fps_mode", "passthrough", "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
samples, buf = [], []
def flush():
    if buf:
        samples.extend(find(torch.from_numpy(np.stack(buf)[:, None].astype(np.float32)).to(dev)))
        buf.clear()
while True:
    raw = dec.stdout.read(hw * hh)
    if len(raw) < hw * hh: break
    buf.append(np.frombuffer(raw, np.uint8).reshape(hh, hw))
    if len(buf) == BATCH: flush()
    if len(samples) % 1500 == 0 and samples: print(f"pass 1: {len(samples) / 5:.0f}s", flush=True)
flush()
print("pass 1 done:", len(samples), "samples,", sum(map(bool, samples)), "with the path", flush=True)

def boxes_for(i):
    k = i // STEP
    near = [b for j in (k - 1, k, k + 1, k + 2) if 0 <= j < len(samples) for b in samples[j]]
    for j in (k, k + 1):              # hull of a box and its moved self in the next sample
        if 0 <= j < len(samples) - 1:
            for a in samples[j]:
                for b in samples[j + 1]:
                    if abs(a[0] - b[0]) < 40 and abs(a[1] - b[1]) < 300:
                        near.append((min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])))
    return near

def blur(f, b):
    x0, y0, x1, y1 = max(b[0], 0), max(b[1], 0), min(b[2], W), min(b[3], H)
    r = f[y0:y1, x0:x1].astype(np.float32)
    k = 10   # pixelate in 10 px blocks, then mix in the box's mean colour
    h, w = (y1 - y0) // k * k, (x1 - x0) // k * k
    if h and w:
        blk = r[:h, :w].reshape(h // k, k, w // k, k, 3).mean((1, 3))
        r[:h, :w] = np.repeat(np.repeat(blk, k, 0), k, 1)
    f[y0:y1, x0:x1] = (0.5 * r + 0.5 * r.mean((0, 1))).astype(np.uint8)

# Pass 2
dec = subprocess.Popen(["ffmpeg", "-v", "error", *seek, "-i", src, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       stdout=subprocess.PIPE, bufsize=W * H * 3 * 4)
enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                        "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                        "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)
log, i = open(out + ".boxes.txt", "w"), 0
while True:
    raw = dec.stdout.read(W * H * 3)
    if len(raw) < W * H * 3: break
    bs = boxes_for(i)
    if bs:
        f = np.frombuffer(raw, np.uint8).reshape(H, W, 3).copy()
        for b in bs: blur(f, b)
        raw = f.tobytes()
        log.write(f"{i / FPS:.2f} {len(bs)}\n")
    enc.stdin.write(raw)
    i += 1
    if i % (FPS * 300) == 0: print(f"pass 2: {i / FPS:.0f}s", flush=True)
enc.stdin.close(); enc.wait(); log.close()
print("frames", i)
