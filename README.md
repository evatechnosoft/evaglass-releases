# evaitec — İndirme Merkezi

Tum evaitec uygulamalarinin ortak dagitim kanali. Kod depolari ayri ve ozeldir;
burada yalnizca **imzali release APK** ve surum metadatasi bulunur.

- Indirme merkezi: <https://evatechnosoft.github.io/evaglass-releases/>
- APK'lar: [Releases](https://github.com/evatechnosoft/evaglass-releases/releases)

## Dosyalar

| Dosya | Ne ise yarar |
|---|---|
| `index.html` | Indirme merkezi sayfasi. `apps.json`'i okuyup kart izgarasi cizer. |
| `apps.json` | Sayfanin veri kaynagi. Uygulama listesi. |
| `latest.json` | **evaglass OTA kanali.** Kurulu uygulamalar guncelleme icin bunu yoklar. Adi, konumu ve alanlari degistirilemez. |
| `assets/` | Uygulama ikonlari ve ekran goruntuleri. |
| `logs.html` | Cihaz log monitoru (ayri arac). |

> `latest.json` ile `apps.json` birbirinin yerine gecmez. `latest.json` yalnizca evaglass
> OTA'si icindir ve URL'i APK'ya derleme zamaninda gomulmustur.

## `apps.json` semasi

```jsonc
{
  "updated": "2026-09-10",          // son yayin tarihi (bilgi amacli)
  "apps": [
    {
      "id": "evaglass-phone",       // benzersiz kimlik; yayin betigi kaydi bununla bulur
      "packageName": "com.evaglass.app", // Android paket adi. evaitecOTA kurulu surumu
                                    // bununla sorar; yoksa uygulama hep "kurulu degil" gorunur
      "versionCode": 806,           // sayisal surum; "version" metnine gore daha kesin karsilastirma
      "name": "evaglass",           // kartta gorunen ad
      "tagline": "Tek satir aciklama.",
      "platform": "phone",          // "phone" | "watch" | "both" -> platform rozeti
      "icon": "assets/x.svg",       // repo ici goreli yol; yoksa/yuklenmezse ad bas harfi cizilir
      "screenshots": [],            // goreli yol listesi; tiklaninca buyur. Bos birakilabilir.
      "version": "0.8.6",
      "releaseDate": "2026-09-10",  // YYYY-MM-DD
      "sizeBytes": 22809138,        // sayi; kartta MB olarak gosterilir
      "downloadUrl": "https://...", // bos ise dugme "Yayinlanmadi" olur
      "sha256": "",                 // bos ise sha satiri hic cizilmez
      "notes": ""                   // karttaki kucuk not; bos birakilabilir
    }
  ]
}
```

Tum alanlar eksik/bos degere dayaniklidir: eksik alan kartta gosterilmez, kart kirilmaz.

## Yeni uygulama ekleme

1. Ikonu `assets/` altina koy (SVG tercih edilir).
2. `apps.json` icindeki `apps` dizisine yukaridaki semaya uygun tek bir kayit ekle.
3. APK'yi bu deponun Releases bolumune yukle ve `downloadUrl` alanina o baglantiyi yaz.
4. Degisikligi push et. Sayfa GitHub Pages'ten otomatik guncellenir.

Kod tarafinda degisiklik gerekmez — `index.html` listeyi `apps.json`'dan okur.

## Yayin betigi

evaglass telefon ve saat kayitlari `deploy_ota_github.ps1` tarafindan yayin sirasinda
otomatik guncellenir (surum, tarih, boyut, sha256, indirme URL'i). Betik diger
uygulamalarin kayitlarina dokunmaz; `apps.json` yoksa olusturur.

Release APK'da saglayici anahtari **gomulu degildir** (anahtarlar yalnizca debug build'de seed'lenir).
