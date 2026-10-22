"""Original soundtrack: a punchy beat plus sound effects on every cue, ducked under the voice."""
import json
import sys

import numpy as np
import soundfile as sf

SR = 48000
rng = np.random.default_rng(7)


def env(n, a=0.002, d=0.2):
    t = np.arange(n) / SR
    return np.minimum(1, t / max(a, 1e-4)) * np.exp(-t / d)


def kick(dur=0.45):
    n = int(SR * dur); t = np.arange(n) / SR
    f = 45 + 110 * np.exp(-t * 28)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(n, .001, .16) * 1.0


def snare(dur=0.25):
    n = int(SR * dur); t = np.arange(n) / SR
    noise = rng.standard_normal(n) * env(n, .001, .07)
    tone = np.sin(2 * np.pi * 190 * t) * env(n, .001, .05)
    return (noise * .55 + tone * .5)


def hat(dur=0.06):
    n = int(SR * dur); x = rng.standard_normal(n)
    x = x - np.convolve(x, np.ones(4) / 4, "same")  # crude high-pass
    return x * env(n, .0005, .018) * .35


def bass(freq, dur):
    n = int(SR * dur); t = np.arange(n) / SR
    x = np.sin(2 * np.pi * freq * t) + .35 * np.sin(2 * np.pi * freq * 2 * t)
    return np.tanh(x * 1.6) * env(n, .005, dur * .6) * .32


def whoosh(dur=0.45):
    n = int(SR * dur); t = np.arange(n) / SR
    x = rng.standard_normal(n)
    k = np.sin(np.pi * t / dur) ** 2
    # sweeping smoothing window imitates a filter sweep
    out = np.zeros(n); w = 2
    for i in range(0, n, 256):
        w = int(2 + 30 * (1 - i / n))
        seg = x[max(0, i - w): i + 256]
        out[i:i + 256] = np.convolve(seg, np.ones(w) / w, "same")[-len(out[i:i + 256]):]
    return out * k * 1.4


def hit():
    k = kick(.5) * 1.1
    n = len(k); noise = rng.standard_normal(n) * env(n, .0005, .03) * .5
    return k + noise


def boom():
    n = int(SR * 1.2); t = np.arange(n) / SR
    f = 38 + 90 * np.exp(-t * 10)
    sub = np.sin(2 * np.pi * np.cumsum(f) / SR) * env(n, .002, .45)
    noise = rng.standard_normal(n) * env(n, .001, .12) * .4
    return (sub * 1.2 + noise)


def pop():
    n = int(SR * .12); t = np.arange(n) / SR
    f = 700 + 900 * np.exp(-t * 60)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(n, .001, .03) * .5


def click():
    n = int(SR * .05); x = rng.standard_normal(n) * env(n, .0003, .006)
    t = np.arange(n) / SR
    return (x * .7 + np.sin(2 * np.pi * 2200 * t) * env(n, .0003, .01) * .4) * .8


def shatter():
    n = int(SR * .9); out = np.zeros(n)
    for _ in range(40):  # many tiny glassy pings
        st = int(rng.uniform(0, .35) * SR); m = int(SR * .25); t = np.arange(m) / SR
        f = rng.uniform(2500, 7000)
        ping = np.sin(2 * np.pi * f * t) * env(m, .0005, rng.uniform(.02, .08))
        e = min(n, st + m); out[st:e] += ping[:e - st] * rng.uniform(.05, .18)
    out += rng.standard_normal(n) * env(n, .001, .09) * .45
    out[:len(boom())] += boom()[:n] * .7
    return out


def ding():
    n = int(SR * 1.1); t = np.arange(n) / SR
    x = sum(a * np.sin(2 * np.pi * f * t) for f, a in ((1318.5, .5), (1975.5, .3), (2637, .15)))
    x2 = np.zeros(n); d = int(.09 * SR)
    t2 = np.arange(n - d) / SR
    x2[d:] = sum(a * np.sin(2 * np.pi * f * t2) for f, a in ((1760, .45), (2637, .25)))
    return (x * env(n, .002, .25) + x2 * env(n, .002, .35)) * .45


def rise(dur):
    n = int(SR * dur); t = np.arange(n) / SR
    f = 200 + 900 * (t / dur) ** 2
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * (t / dur) ** 2 * .18


def add(buf, x, t, gain=1.0):
    s = int(t * SR)
    if s >= len(buf):
        return
    e = min(len(buf), s + len(x)); buf[s:e] += x[:e - s] * gain


def build(events_file, voice_file, out_full, out_music_only=None, bpm=124):
    cue = json.load(open(events_file))
    end = cue["end"]; T1, T2, T3, T4 = cue["T"]
    n = int(SR * (end + .05))
    music = np.zeros(n); sfx = np.zeros(n)
    beat = 60 / bpm
    # groove: starts after the hook, drops out briefly before the features for impact
    notes = [55, 55, 65.4, 49]  # A1 A1 C2 G1
    b = 0; t = T1
    while t < end - .25:
        bar_pos = b % 4
        add(music, kick(), t, .9)
        if bar_pos in (1, 3):
            add(music, snare(), t, .55)
        add(music, hat(), t + beat / 2, 1.0)
        add(music, hat(), t + beat / 4, .5); add(music, hat(), t + 3 * beat / 4, .5)
        add(music, bass(notes[(b // 4) % 4], beat * .9), t, 1.0)
        b += 1; t += beat
    # hook: heavy hits only, no groove; CTA: half-time feel
    gain = {"hit": .9, "boom": 1.0, "pop": .9, "shatter": 1.0, "whoosh": .6, "click": .9, "ding": 1.0}
    for e in cue["events"]:
        k, t = e["type"], e["t"]
        if k == "rise":
            add(sfx, rise(max(.2, e["end"] - t)), t)
        else:
            add(sfx, {"hit": hit, "boom": boom, "pop": pop, "shatter": shatter, "whoosh": whoosh, "click": click, "ding": ding}[k](), t, gain[k])
    # fade music at the end for a clean loop
    fade = np.ones(n); fe = int((end - .35) * SR); fade[fe:] = np.linspace(1, 0, n - fe)
    music *= fade

    voice = np.zeros(n)
    if voice_file:
        v, vsr = sf.read(voice_file)
        if v.ndim > 1: v = v.mean(1)
        if vsr != SR:
            idx = np.arange(0, len(v), vsr / SR); v = np.interp(idx, np.arange(len(v)), v)
        voice[:min(n, len(v))] = v[:n]
        # sidechain: duck the music while the voice is speaking
        win = int(.05 * SR)
        envv = np.sqrt(np.convolve(voice ** 2, np.ones(win) / win, "same"))
        duck = 1 - .55 * np.clip(envv / (envv.max() * .25 + 1e-9), 0, 1)
        music *= duck
    mix = music * .42 + sfx * .5 + voice * 1.0
    mix = np.tanh(mix * 1.1) / np.tanh(1.1)
    peak = np.abs(mix).max(); mix = mix / peak * .95
    sf.write(out_full, np.stack([mix, mix], 1), SR)
    if out_music_only:  # voice + SFX, no music, for adding a trending sound in-app
        alt = sfx * .5 + voice
        alt = alt / np.abs(alt).max() * .95
        sf.write(out_music_only, np.stack([alt, alt], 1), SR)


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "-" else None,
          sys.argv[3] if len(sys.argv) > 3 else "mix.wav", sys.argv[4] if len(sys.argv) > 4 else None)
