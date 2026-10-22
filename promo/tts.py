"""Generate the promo voiceover with Kokoro (Apache-2.0) and save word timings."""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro import KPipeline

SR = 24000
# Spoken forms (spelled for pronunciation); captions use the display text.
LINES = [
    "Stop paying for video downloaders.",
    "Ads. Limits. Nope.",
    "This is Vid Fetch. Paste a link, pick a quality... done.",
    "Four K. M P three. Playlists. No ads, no tracking.",
    "Vid Fetch. Free for Windows. Link in bio.",
]
voices = sys.argv[1].split(",")
speed = float(sys.argv[2]) if len(sys.argv) > 2 else 1.1
out = Path("promo/out")
out.mkdir(parents=True, exist_ok=True)
pipes, timings = {}, {}

for v in voices:
    lang = "b" if v.startswith("b") else "a"
    pipe = pipes.setdefault(lang, KPipeline(lang_code=lang))
    timings[v] = []
    full = []
    for i, line in enumerate(LINES):
        chunks, words, offset = [], [], 0.0
        for r in pipe(line, voice=v, speed=speed):
            a = r.audio.numpy() if hasattr(r.audio, "numpy") else np.asarray(r.audio)
            for t in r.tokens or []:
                if getattr(t, "start_ts", None) is not None:
                    words.append({"w": t.text, "s": round(offset + t.start_ts, 3),
                                  "e": round(offset + (t.end_ts or t.start_ts), 3)})
            chunks.append(a)
            offset += len(a) / SR
        audio = np.concatenate(chunks)
        sf.write(out / f"{v}_{i}.wav", audio, SR)
        timings[v].append({"line": i, "text": line, "dur": round(len(audio) / SR, 3), "words": words})
        full += [audio, np.zeros(int(0.15 * SR), dtype=audio.dtype)]
    sf.write(out / f"sample_{v}.wav", np.concatenate(full), SR)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(out / f"sample_{v}.wav"),
                    "-b:a", "160k", str(out / f"sample_{v}.mp3")], check=True)
    print(v, "total", round(sum(t["dur"] for t in timings[v]), 2), "s")

(out / "timings.json").write_text(json.dumps(timings, indent=1))
