# Promo reel

The 15-second vertical (1080×1920) promo video, built in code.

| File | What it is |
|---|---|
| `index.html` | The animation. `render(t)` draws any moment; `CUE` holds the line and word timings |
| `render.mjs` | Renders it frame by frame with Playwright and encodes it with FFmpeg |
| `soundtrack.py` | An original beat and sound effects, placed on the cues from `EVENTS()` |
| `fonts/` | Anton and Inter (SIL Open Font License) |

```
npm i playwright
node render.mjs "$PWD/index.html" reel.mp4
```
