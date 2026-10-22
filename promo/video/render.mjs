// Render the promo frame by frame (deterministic) and pipe PNGs into ffmpeg.
// usage: node render.mjs <index.html> <out.mp4> [cue.json] [fps]
import { chromium } from 'playwright';
import { spawn } from 'child_process';
import fs from 'fs';
const [,, page, out, cueFile, fpsArg] = process.argv;
const fps = +(fpsArg || 30);
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const p = await b.newPage({ viewport: { width: 1080, height: 1920 } });
await p.goto('file://' + page);
await p.evaluate(() => document.fonts.ready);
if (cueFile) await p.evaluate(c => { window.CUE = c; }, JSON.parse(fs.readFileSync(cueFile)));
const end = await p.evaluate(() => CUE.end);
const n = Math.round(end * fps);
const ff = spawn('ffmpeg', ['-loglevel', 'error', '-y', '-f', 'image2pipe', '-framerate', String(fps), '-i', '-',
  '-c:v', 'libx264', '-preset', 'slow', '-crf', '17', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', out], { stdio: ['pipe', 'inherit', 'inherit'] });
for (let i = 0; i < n; i++) {
  await p.evaluate(t => render(t), i / fps);
  const png = await p.screenshot({ type: 'png' });
  if (!ff.stdin.write(png)) await new Promise(r => ff.stdin.once('drain', r));
  if (i % 60 === 0) process.stdout.write(`${i}/${n} `);
}
ff.stdin.end();
await new Promise(r => ff.on('close', r));
await b.close();
console.log('\ndone', out);
