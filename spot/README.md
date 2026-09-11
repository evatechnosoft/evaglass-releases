# evaglass 30 sn lansman spotu — sıfır bütçe yapım hattı

Yol C + D: ürün planları Blender'da, yaşam tarzı B-roll Wan 2.2 ile, ikisi de Kaggle'ın ücretsiz GPU'sunda.
Yerel makine (Quadro K620, 2 GB VRAM) yalnızca `.blend` dosyasını açıp kamera hareketine bakmak için.

## Shot list (24 fps)

| Plan | TC | Kaynak | Nereden |
|---|---|---|---|
| SH010 | 00:00–00:03 | Makro dolly-in, camda şehir ışıkları | Blender, `01_blender_render.ipynb` |
| SH020 | 00:03–00:08 | Karakter gözlüğü takar, telefon bağlantı animasyonu | Wan 2.2 (`02_wan22_broll.ipynb`) **veya** telefonla gerçek çekim (önerilen) |
| SH030 | 00:08–00:14 | Gözlükten görünen dünya: yön, bildirim, saat kısayolu | Telefon + saat uygulamasının ekran kaydı |
| SH040 | 00:14–00:20 | Yakın plan, tek cümle | Wan 2.2 veya gerçek çekim; ses: Edge TTS |
| SH050 | 00:20–00:26 | Bullet time: gözlük + telefon + saat | Blender |
| SH060 | 00:26–00:30 | Pull-out, logo, "Uygulamayı indir" | Blender |

## Dosyalar

- `blender/evaglass_spot.py` — SH010/SH050/SH060 sahnelerini sıfırdan kurar ve render eder. Blender 4.2+ ve `pip install bpy` (5.0) ile test edildi.
- `kaggle/blender/01_blender_render.ipynb` — Kaggle'da Blender indir, OPTIX ile render, mp4 üret.
- `kaggle/broll/02_wan22_broll.ipynb` — Wan 2.2 TI2V-5B ile B-roll. **Deneysel, T4'te doğrulanmadı.**
- `kaggle/push.sh` — defteri Kaggle API ile gönderir, bitince çıktıları `spot/out/` altına indirir.
- `prompts/wan22_prompts.json` — SH020 ve SH040 prompt'ları.

## Sizden gerekenler

1. Gözlüğün 3D modeli (`.glb` tercih) ya da düz arka planda 3 açıdan fotoğraf. Model yoksa betik yer tutucu bir gözlük çizer.
2. Telefon ve saat uygulamasından 10–15 sn ekran kaydı. PNG dizisine çevirmek için:
   `ffmpeg -i phone.mp4 -vf fps=24 phone_frames/%04d.png`
3. Bunları bir Kaggle Dataset olarak yükleyip defterdeki `GLASSES`, `SCREEN_PHONE`, `SCREEN_WATCH` yollarını doldurun.

## Kaggle'a göndermek (tarayıcı gerekmez)

```
pip install kaggle
# kaggle.com/settings -> API -> Create New Token -> kaggle.json dosyasini ~/.kaggle/ (Windows: %USERPROFILE%\.kaggle\) altina koy
git clone -b claude/highfield-senior-animator-k39icv https://github.com/evatechnosoft/evaglass-releases
cd evaglass-releases/spot/kaggle && ./push.sh blender      # Git Bash / WSL
```

Defter Kaggle'da GPU ile çalışır, betik bitene kadar bekler ve mp4'leri indirir. B-roll için `./push.sh broll`.

## Yerel hızlı test (GPU gerekmez)

```
python -m venv venv && venv/bin/pip install bpy
venv/bin/python blender/evaglass_spot.py --shot SH050 --frame 72 --res 30 --samples 12 --out out
```

## Kurgu

Resolve (ücretsiz) veya Kdenlive. Müzik: YouTube Audio Library / Pixabay. Seslendirme: `edge-tts --voice tr-TR-EmelNeural`.
