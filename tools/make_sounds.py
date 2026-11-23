#!/usr/bin/env python3
"""Genera los sonidos propios de SensorPulse (WAV mono 16 bits, 44,1 kHz) sin dependencias.

  sensorpulse-nuclear  alarma de central nuclear: tres «whoop» de klaxon que suben de tono,
                       con timbre de bocina (armónicos impares), un poco de saturación y eco
                       de nave industrial. Friki a propósito.
  sensorpulse-sonar    «ping» de sonar con su eco
  sensorpulse-beeps    tres pitidos digitales, como un reloj de pulsera
  sensorpulse-chime    campanilla suave (pensada para los avisos)

Uso: python3 tools/make_sounds.py [carpeta]   (por defecto sensorpulse/data/sounds)
Los ficheros salen siempre iguales (sin azar), así el paquete es reproducible.
"""

import math
import os
import struct
import sys
import wave

RATE = 44100
TAU = 2 * math.pi


def write(path, samples, peak=0.85):
    m = max(1e-9, max(abs(s) for s in samples))
    k = peak / m
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s * k)) * 32767))
                               for s in samples))


def env(i, n, attack, release):
    """Envolvente lineal de ataque y caída (en muestras)."""
    a = min(1.0, i / max(1, attack))
    r = min(1.0, (n - i) / max(1, release))
    return a * r


def echo(x, delay_s, gain, times=3):
    d = int(delay_s * RATE)
    out = list(x) + [0.0] * (d * times)
    for t in range(1, times + 1):
        g = gain ** t
        off = d * t
        for i, s in enumerate(x):
            out[i + off] += s * g
    return out


def nuclear():
    """Klaxon «whoop»: barrido de 380 a 1150 Hz en 0,75 s, tres veces, con armónicos impares."""
    out = []
    whoop, gap = 0.78, 0.16
    for rep in range(3):
        n = int(whoop * RATE)
        ph = 0.0
        for i in range(n):
            t = i / float(n)
            f = 380 + (1150 - 380) * (t ** 0.75)
            ph += TAU * f / RATE
            # bocina: fundamental + 3.º, 5.º y 7.º armónicos (onda casi cuadrada, sin aliasing)
            s = (math.sin(ph) + math.sin(3 * ph) / 3.2 + math.sin(5 * ph) / 6.0 +
                 math.sin(7 * ph) / 11.0)
            # vibrato leve de altavoz viejo y saturación suave
            s *= 1 + 0.12 * math.sin(TAU * 31 * i / RATE)
            s = math.tanh(1.8 * s)
            out.append(s * env(i, n, 0.03 * RATE, 0.06 * RATE))
        out.extend([0.0] * int(gap * RATE))
    return echo(out, 0.19, 0.28, 3)


def sonar():
    n = int(0.9 * RATE)
    out = []
    for i in range(n):
        t = i / RATE
        e = math.exp(-t * 5.5)
        s = math.sin(TAU * 1480 * t) * e + 0.25 * math.sin(TAU * 2960 * t) * e * math.exp(-t * 8)
        out.append(s * env(i, n, 0.004 * RATE, 0.05 * RATE))
    return echo(out, 0.33, 0.35, 2)


def beeps():
    out = []
    for _k in range(3):
        n = int(0.09 * RATE)
        for i in range(n):
            t = i / RATE
            s = 1.0 if math.sin(TAU * 2700 * t) >= 0 else -1.0     # zumbador piezoeléctrico
            s = 0.6 * s + 0.4 * math.sin(TAU * 2700 * t)
            out.append(s * env(i, n, 0.002 * RATE, 0.004 * RATE))
        out.extend([0.0] * int(0.07 * RATE))
    return out


def chime():
    n = int(1.1 * RATE)
    out = []
    for i in range(n):
        t = i / RATE
        s = 0.0
        for f, a, d in ((880, 1.0, 3.2), (1320, 0.45, 4.5), (1760, 0.25, 6.0), (2640, 0.1, 9.0)):
            s += a * math.sin(TAU * f * t) * math.exp(-t * d)
        out.append(s * env(i, n, 0.006 * RATE, 0.08 * RATE))
    return out


def main():
    dest = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sensorpulse", "data", "sounds")
    os.makedirs(dest, exist_ok=True)
    for name, fn, peak in (("nuclear", nuclear, 0.85), ("sonar", sonar, 0.8),
                           ("beeps", beeps, 0.6), ("chime", chime, 0.7)):
        p = os.path.join(dest, "sensorpulse-%s.wav" % name)
        write(p, fn(), peak)
        print(p, os.path.getsize(p))


if __name__ == "__main__":
    main()
