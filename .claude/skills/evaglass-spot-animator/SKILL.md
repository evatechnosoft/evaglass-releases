---
name: evaglass-spot-animator
description: evaglass 30 sn lansman spotunun yapım hattı. Bir planı test etmek, Kaggle'da render almak, B-roll üretmek, ses ve kurgu yapmak istendiğinde tetikle. Anahtar kelimeler - spot, render, Kaggle, Blender, shot, SH010, SH050, SH060, B-roll, Wan, kurgu, seslendirme.
---

# evaglass spot yapım hattı

Persona `CLAUDE.md`'de. Burada yalnızca komutlar ve kontrol listeleri var.

## 0. Ön koşullar (bir kez)
```
python -m venv venv && venv/bin/pip install bpy pillow kaggle edge-tts
# Windows: venv\Scripts\pip ...
# kaggle.json -> ~/.kaggle/  (Windows: %USERPROFILE%\.kaggle\)   kaggle.com/settings -> Create New Token
```
ffmpeg PATH'te olmalı.

## 1. Tek kare test (her değişiklikten sonra, GPU gerekmez)
```
venv/bin/python spot/blender/evaglass_spot.py --shot SH050 --frame 72 --res 30 --samples 12 --out out
venv/bin/python spot/blender/evaglass_spot.py --shot SH010 --frame 60 --res 30 --samples 12 --out out
venv/bin/python spot/blender/evaglass_spot.py --shot SH060 --frame 90 --res 30 --samples 12 --out out
```
Kareyi aç ve kontrol et: ürün silueti okunuyor mu, kamera modülü ve kırmızı anahtar görünüyor mu, cam temiz mi, metin okunuyor mu. Onay almadan 2. adıma geçme.

## 2. Kaggle'da tam render
Dataset `evaglass-assets` hazırsa `spot/kaggle/blender/01_blender_render.ipynb` içinde `SCREEN_PHONE`, `SCREEN_WATCH`, varsa `GLASSES` yollarını doldur. Sonra:
```
cd spot/kaggle && ./push.sh blender
```
Betik bitene kadar bekler, `spot/out/SH010.mp4 SH050.mp4 SH060.mp4` indirir. Plan başına T4'te 10–25 dk. Hata varsa defter loglarını oku, betiği düzelt, yeniden push et. Kaggle kotası haftalık ~30 saat; boşa harcama.

## 3. B-roll (SH020, SH040)
Önce gerçek çekim öner: telefon, 24 fps, 4K varsa, pencere ışığı, karanlık arka plan. Olmazsa:
```
cd spot/kaggle && ./push.sh broll
```
Prompt'lar `spot/prompts/wan22_prompts.json`. Referans görsel varsa defterde `REF_IMAGE` doldur. 480p çıkar; Resolve Super Scale ile 1080p.

## 4. Ses
```
venv/bin/edge-tts --voice tr-TR-EmelNeural --text "Bileğimden gözlüğüme, tek dokunuş." --write-media audio/vo_tr.mp3
venv/bin/edge-tts --voice en-US-AriaNeural --text "From my wrist to my glasses. One touch." --write-media audio/vo_en.mp3
```
Müzik: YouTube Audio Library veya Pixabay, ambient, 30 sn+, lisans notunu `audio/LICENSE.txt`'ye yaz.

## 5. Kaba kurgu (ffmpeg)
`docs/shotlist.md` sırasıyla:
```
ffmpeg -f concat -safe 0 -i edit/order.txt -i audio/music.mp3 -i audio/vo_tr.mp3 \
  -filter_complex "[1:a]volume=0.35[m];[2:a]adelay=14000|14000[v];[m][v]amix=inputs=2[a]" \
  -map 0:v -map "[a]" -c:v libx264 -crf 18 -pix_fmt yuv420p -shortest deliver/evaglass_30s_16x9.mp4
```
`edit/order.txt` her satır `file '../spot/out/SH010.mp4'`. 9:16 için `-vf "crop=ih*9/16:ih,scale=1080:1920"`.
İnce kurgu ve renk: DaVinci Resolve (ücretsiz).

## 6. Teslim kontrolü
- [ ] 30 sn tam, 24 fps, 1080p, ses -14 LUFS civarı
- [ ] Ürün her planda gerçek forma uygun (ref: `spot/assets/ref/`)
- [ ] Adres ve çağrı metni okunur (en az 2 sn ekranda)
- [ ] TR ve EN sürüm
- [ ] 15 sn 9:16 ve 6 sn bumper
- [ ] Müzik lisansı dosyada

## Sorun giderme
- `AttributeError: 'Action' object has no attribute 'fcurves'` → Blender 5 API; betikte `_fcurves()` yardımcı var, yeni kod yazarken onu kullan.
- Kaggle `403`/`401` → kaggle.json yanlış yerde veya token iptal.
- Wan siyah kare → defterde `DTYPE = torch.float32` + `enable_sequential_cpu_offload()`.
- Camlar buruşuk görünüyor → cam levhasında smooth shading kapalı olmalı.
