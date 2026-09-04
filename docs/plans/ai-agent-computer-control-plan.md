# Otonom Bilgisayar Kontrol Ajanı + Oyunlaştırılmış Ajan Arayüzü — Ürün ve Mimari Planı

Tarih: 2026-09-04 · Durum: Taslak v1 · Sahip: PM / YZ Mühendisliği

---

## 0. Yönetici Özeti

**Karar: Önce Agent Builder (arka plan), ama "olay akışı + minimal canlı izleme" dikey dilimiyle.**
Oyunlaştırılmış arayüz, ajanın ürettiği olayların (ekran görüntüsü aldı, tıkladı, onay bekliyor, hata verdi…)
görsel bir yorumudur. Olay akışı olmadan animasyon edilecek bir şey yoktur. Bu yüzden:

1. **Sprint 0–2:** Çekirdek ajan döngüsü + olay sözleşmesi (Event Contract) + ham canlı izleme paneli.
2. **Sprint 2'den itibaren paralel:** Oyunlaştırılmış arayüz ekibi, sabitlenmiş olay sözleşmesi üzerinden
   kayıtlı (replay) olaylarla çalışmaya başlar; canlı ajana bağımlı değildir.
3. **Sprint 5–6:** İki hat birleşir; ilk demo: ajan gerçek bir masaüstünde görev yaparken piksel-art sahnede canlanır.

Toplam hedef: **6 sprint (~12 hafta)**, 2 mühendis + 1 tasarımcı/oyun geliştirici.

---

## 1. Görsellerden Çıkarımlar (Doğrulanmış)

| Görsel | Ne görünüyor | Gerçeklik durumu |
|---|---|---|
| 1 — "CHEFlang" salatalık kesen robot, `cutCucumberLoop()` | Yapay zekâ ile üretilmiş piksel-art **illüstrasyon**. Ekrandaki "kod" gerçek değil; ajan döngüsünü (init → move → click → increment → check) anlatan bir görselleştirme. | Gerçek ürün değil. Ancak **tasarım dilimiz için mükemmel referans**: aktif görev kartı, ilerleme çubuğu, gösterge (gauge), adım-adım vurgulanan kod satırı. |
| 2 — GitHub listesi: `xai-org/grok-prompts`, `milind-soni/OpenMausBot`, `b-nnett/grok-bot-0.18-reconstructed` | Reel sahibinin yıldızladığı repolar. | Gerçek repolar. |
| 3 — Yetenek/destek matrisi (macOS Supported / Ubuntu 24.04 Xorg Beta / Wayland Beta / "bundled Cua 0.19.3") | **OpenMausBot** dokümanındaki `computer-use-integration.md` matrisidir (Grok Bot reposu değil). | Gerçek. |
| 4 — `grok-bot-0.18-reconstructed` repo sayfası (1.4k yıldız, 1.6k fork) | Cursor ekibinin yayınladığı Grok Bot 0.18.0 masaüstü uygulamasının, **source map'ler açık unutulduğu için** kaynak koda geri çevrilmiş hâli. | Gerçek. "Grok'un computer-use yeteneğini taklit eden" değil, **orijinal uygulamanın yeniden inşası**. |
| 5 — Piksel-art "memecoin casino" (GOAT, PENGU, POPCAT ajanları; "TOOK PROFIT", "NO DICE") | Ajan-kasabası (agent-town) tarzı oyunlaştırılmış bir ajan görselleştirmesi. Yukarıdaki iki reponun parçası değil; muhtemelen ayrı bir demo/ürün. | Kaynağı doğrulanamadı. **Görsel dil referansı** olarak kullanılacak; işlevsel olarak kripto/kumar kısmı kapsam dışı (bkz. §7 riskler). |

### 1.1 İki reponun mimarisi (bizim için ders çıkarılacak yerler)

**grok-bot-0.18-reconstructed** (Electron + TypeScript, yalnızca macOS arm64):
- Katmanlar: `electron-main` (yaşam döngüsü, auth, box bağlayıcıları) · `electron-preload` (güvenli UI köprüsü) · `host` (çıkarım, araçlar, MCP, tur yürütme) · `node-agent-coordinator` (yönlendirme, streaming, reaksiyonlar) · `shared` (protokol/sözleşmeler) · `frontend` (React renderer).
- Bilgisayar kontrolü: **uzak "box" (barındırılan VM)** varsayılan; **yerel Docker modu** loopback portlara bağlı bir kutu-host + yürütme daemon'u.
- Çıkarım yönlendiricisi: Cursor / Claude Code / Codex / OpenRouter sağlayıcıları, MCP üzerinden araç yürütme.
- **Ders:** Host (LLM + araçlar) ile Coordinator (yönlendirme + akış) ayrımı ve tek bir protokol paketi (`shared`) — bizim mikro mimarimizle birebir örtüşüyor.

**OpenMausBot** (Node 24 + pnpm, React + Tailwind, Electron; macOS/Windows/Ubuntu):
- İki süreç: **Harness sunucusu** (127.0.0.1:8799; ajan süreçlerini, sürücü kayıt defterini, olay veriyolunu ve **onay aracısını** sahiplenir) + **React UI** (tek bir SSE olay akışını tüketir).
- Ajanlar yerel CLI'lar (`claude`, `codex`, `grok`) üzerinden çalışır; her sağlayıcının protokolü (stream-JSON / JSON-RPC / ACP) **kanonik bir olay akışına** normalize edilir.
- Masaüstü kontrolü: **`cua-driver`** — Electron main sürecinden spawn edilen, Rust ile yazılmış, MCP stdio proxy'si üzerinden 23+ araç (fare/klavye/pencere/erişilebilirlik ağacı/ekran görüntüsü) sunan imzalı bir ikili. macOS TCC izinleri uygulamaya atfedilsin diye yalnızca main süreçten başlatılıyor.
- Tarayıcı: Playwright yok; Electron `WebContentsView` + `webContents.debugger` (CDP) ile yönetim.
- Güvenlik kapıları: Wayland'da kontrol **fail-closed** kapalı; Xorg'da iki adımlı açık onay (ayar + "Bu bilgisayar"a bot atama); her aksiyon için onay; sabitlenmiş ikili hash'leri.
- **Ders:** Olay veriyolu + SSE + onay aracısı üçlüsü, oyunlaştırılmış UI için hazır bir "besleme" katmanıdır. Bunu kopyalamayacağız ama aynı deseni kuracağız.

### 1.2 Model notu
Görsel 1'deki "Claude 3.5 Sonnet – Computer Use Trained" 2024 dönemine ait. Bugün (Eylül 2026) doğru hedef:
- Sunucu tanımlı araç: **`computer_toolset_20260801`** (beta başlığı gerektirmez; `display_width_px/height/number` parametreleri **kaldırıldı**, koordinat uzayı döndürdüğünüz ekran görüntüsünün piksel uzayıdır).
- Modeller: `claude-opus-5` (ana sürücü), `claude-sonnet-5` (ucuz/hızlı tur), `claude-fable-5-1` (en zor görevler).
- 17 üye aksiyon: `screenshot, zoom, left/right/middle/double/triple_click, left_click_drag, mouse_move, left_mouse_down/up, cursor_position, scroll, type, key, hold_key, wait`.
- Tavsiye: 1024×768 – 1366×768 çözünürlük, en fazla 1920×1080; Retina'da 2× ölçekle; her batch'i `screenshot` ile bitir; istek başına ≤20 görüntü; prompt cache ile ekran görüntüsü geçmişini toplu buda.

---

## 2. Ürün Tanımı

**Vizyon:** "Bilgisayarı kullanan ajanı, bir oyun sahnesindeki karakter gibi izle ve yönet."

**Kullanıcı hikâyeleri (MVP):**
1. Operatör olarak bir görevi doğal dille veririm ("Bu CSV'yi aç, toplamları hesapla, raporu PDF olarak kaydet"), ajan izole bir masaüstünde bunu yapar, ben her adımı izler ve gerektiğinde onaylarım.
2. Riskli aksiyonlarda (dosya silme, ödeme, form gönderme) ajan durur, sahnedeki karakterin başında "onay bekliyor" balonu çıkar; tek tıkla onaylarım/reddederim.
3. Görev bittiğinde sahnede "TOOK PROFIT / DONE" tarzı bir sonuç kartı görürüm; olay kaydını (replay) geri oynatabilirim.
4. Aynı sahnede birden fazla ajan (çoklu-ajan) farklı masalarda/pencerelerde çalışır; her biri kendi görev kartına sahiptir.

**MVP dışı (v2+):** ses (TTS), mobil companion, Composio tarzı SaaS entegrasyonları, ajanlar arası pazarlık.

---

## 3. Hedef Mimari (mikro servis + SOLID)

```
┌──────────────────────────────────────────────────────────────────────┐
│  Gamified UI  (Flutter + Flame — web/desktop)                        │
│  sadece EventStream tüketir; komutları CommandAPI'ye yazar           │
└───────────────▲───────────────────────────────┬──────────────────────┘
                │ SSE / WebSocket (olaylar)      │ HTTP (komutlar, onaylar)
┌───────────────┴───────────────────────────────▼──────────────────────┐
│  Harness / Gateway  (TypeScript, Node 24)                            │
│  EventBus · SessionRegistry · ApprovalBroker · ReplayStore (NDJSON)  │
└───────▲───────────────────────────────────────▲──────────────────────┘
        │ gRPC / stdio                          │
┌───────┴────────────────┐          ┌───────────┴──────────────────────┐
│ LLMOrchestrator (Py)   │          │ Perception + Executor sidecar     │
│ AgentLoop · Router     │◄────────►│ (Python v1 → Rust v2)             │
│ Policy · Memory        │ MCP/     │ ScreenPerceptionService           │
│ Claude/Gemini/Azure    │ JSON-RPC │ ActionExecutorService             │
└────────────────────────┘          │ SafetyGuard (allowlist, kill sw.) │
                                    └──────────────┬────────────────────┘
                                                   │ OS API
                                    ┌──────────────▼────────────────────┐
                                    │ Hedef masaüstü: Docker/VM (Xvfb)  │
                                    │ veya opt-in "bu bilgisayar"       │
                                    └───────────────────────────────────┘
```

### 3.1 Servisler ve sorumluluklar (tek sorumluluk ilkesi)

| Servis | Sorumluluk | Teknoloji (v1) | Notlar |
|---|---|---|---|
| **ScreenPerceptionService** | Ekranı yakala, ölçekle (≤1366×768), `zoom` bölgesi kırp, ekran görüntüsü hash'i ile "değişmedi" tespiti | Python: `mss` + `Pillow`; Linux: Xvfb/Xorg | OpenCV element çıkarımı **v1'de yok** — `computer_toolset` görsel algıyı modele bırakır. OpenCV yalnızca v2'de "hızlı yol" (template match ile bilinen buton) için. |
| **ActionExecutorService** | `left_click`, `type`, `key`, `scroll`, `drag`… aksiyonlarını OS seviyesinde uygula; koordinatı ölçek faktörüne böl | Python: `pynput` (pyautogui'den daha kararlı) | Her aksiyon **SafetyGuard**'dan geçer. v2'de Rust sidecar (OpenMausBot'un cua-driver deseni) — TCC/imza/hash. |
| **SafetyGuard** | Allowlist (uygulama/pencere/domain), tehlikeli tuş kombinasyonu engeli, hız limiti, kill switch (ESC ×3 / panik butonu), onay gerektiren aksiyon sınıflandırması | Python | Fail-closed: bilinmeyen hedef → dur ve onay iste. |
| **LLMOrchestrator** | Ajan döngüsü (istek → tool_use → yürüt → tool_result → tekrar), model yönlendirme, bağlam/ekran görüntüsü budama, prompt cache | Python + `anthropic` SDK | Detay §4. |
| **Harness/Gateway** | Oturum kaydı, olay veriyolu, SSE yayını, onay aracısı, NDJSON replay, komut API'si | TypeScript/Node 24, Fastify | UI ve orkestratör arasındaki **tek** sözleşme noktası. |
| **Gamified UI** | Sahne, ajan sprite'ları, görev kartları, onay diyalogları, replay oynatıcı | Flutter + Flame (ekip Flutter'a hâkim; web + masaüstü tek kod) | Alternatif: TS + PixiJS (Electron'a gömmek daha kolay). Karar Sprint 2'de. |

### 3.2 Olay Sözleşmesi (Event Contract) — iki hattı bağlayan şey

Tek bir NDJSON/SSE akışı; her satır:

```json
{ "v": 1, "ts": "2026-09-04T10:22:31.412Z", "session": "s_9f2", "agent": "goat",
  "seq": 128, "type": "action.executed",
  "data": { "action": "left_click", "coordinate": [412, 300], "ok": true, "latency_ms": 38 } }
```

Olay türleri (MVP):

| Grup | Tür | UI'daki karşılığı |
|---|---|---|
| Oturum | `session.started/finished/failed` | Karakter sahneye girer / çıkar / kızarır |
| Görev | `task.received`, `task.plan`, `task.progress{done,total}`, `task.done{summary}` | Görev kartı, ilerleme çubuğu ("slices: 6/10"), sonuç kartı |
| Algı | `perception.screenshot{thumb_url,hash}`, `perception.zoom{region}` | Karakter "bakıyor" animasyonu; küçük ekran resmi köşede |
| Karar | `llm.thinking{model,effort}`, `llm.decision{action,reason}` | Düşünme balonu; kod paneli satırı vurgulanır |
| Aksiyon | `action.requested/executed/failed` | El/fare animasyonu; hata → kıvılcım |
| Güvenlik | `approval.requested{risk,description}`, `approval.resolved{approved}` | Sarı "ONAY BEKLİYOR" balonu; masadaki kırmızı lamba |
| Sistem | `metrics.tick{tokens,cost_usd,fps}` | Sağ üst gösterge ("DİLİMLEME HIZI" grafiği) |

Kural: **UI hiçbir zaman orkestratöre doğrudan bağlanmaz.** Tüm kararlar olay olarak yayınlanır; UI'nın tek yazma yolu `POST /sessions/{id}/commands` (start, pause, approve, reject, abort).

---

## 4. LLMOrchestrator — Hibrit Yönlendirme

**Gerçekçi yönlendirme kuralı:** `computer_toolset` yalnızca Claude'da var. Gemini/Azure OpenAI'ye görsel-aksiyon turunu vermek, kendi araç şemanızı yazıp koordinat doğruluğunu sıfırdan kanıtlamanız demek. Bu yüzden:

| Tur tipi | Model | Neden |
|---|---|---|
| Görsel algı + aksiyon seçimi (döngünün kalbi) | `claude-opus-5` (varsayılan), zor görevlerde `claude-fable-5-1` | Yerleşik `computer_toolset_20260801`; koordinat doğruluğu |
| Ucuz "değişiklik var mı?" turları (aynı ekran, bekleme) | `claude-sonnet-5` veya hiç model çağırmadan hash karşılaştırma | Maliyet |
| Görev planlama / özetleme / raporlama (metin) | Router: Claude / Gemini / Azure OpenAI — mevcut AI gateway'iniz | Görsel gerekmez; mevcut altyapıyı kullanır |
| Riske sınıflandırma (onay gerekli mi?) | Kural motoru önce, belirsizse `claude-haiku-4-5` | Deterministik olmalı |

Döngü şekli (Anthropic'in önerdiği):
1. `tools=[{"type":"computer_toolset_20260801"}]` ile istek; `thinking: {type:"adaptive"}`, `output_config.effort: "high"`.
2. Dönen **tüm** `tool_use` bloklarını sırayla yürüt; biri hata verirse kalanları `is_error: true` + "Not executed…" ile işaretle.
3. Batch'i her zaman `screenshot` ile bitir.
4. Ekran görüntüsü geçmişini **toplu** buda (cache geçerliliği için), ≤20 görüntü/istek.
5. `stop_reason == "refusal"` kontrolü + `fallbacks: "default"` (server-side fallback beta) açık.
6. Sistem promptu: "Her adımdan sonra ekran görüntüsü al ve hedefe ulaşıp ulaşmadığını değerlendir; küçük yazı için `zoom` kullan; açılır menülerde klavye kısayolu tercih et."

Belleğe/bağlama: oturum başına NDJSON transcript; uzun görevlerde sunucu tarafı compaction (`compact-2026-01-12`).

---

## 5. Oyunlaştırılmış Arayüz — Tasarım Sistemi

Görsel 1 ve 5'ten damıtılan dil:

- **Sahne:** İzometrik olmayan, düz 2D piksel-art "ofis/mutfak/kasino" odaları. Her ajan = bir masa/istasyon. Tema paketleri (Mutfak: "CHEFlang", Kasino: "PIT", Ofis) sadece sprite seti değiştirir; olay → animasyon eşlemesi sabittir.
- **HUD (Görsel 1'deki gibi):** sol üst metrik grafiği (aksiyon/dk), sol orta gösterge (güven skoru / "tazelik"), üst orta **AKTİF GÖREV** kartı, sağ üst hedef ilerlemesi, sağ orta model rozeti (`claude-opus-5`), alt panel: **canlı "kod" görünümü** — aslında olay akışının okunabilir sahte-kod render'ı; aktif satır yeşil vurgulanır.
- **Durum → animasyon:** `perception.*` → kafa/bakış; `llm.thinking` → düşünce balonu + saat; `action.executed` → el/bıçak/fare hareketi; `approval.requested` → sarı ünlem + kırmızı lamba; `task.done` → "TOOK PROFIT / DONE" tabelası; `failed` → "NO DICE".
- **Etkileşim:** karaktere tık → görev detayı ve onay paneli; masaya sürükle-bırak → görev atama; zaman çizelgesi kaydırıcı → replay.
- **Erişilebilirlik:** her animasyonun metin eşdeğeri olay listesi panelinde; renk körlüğü için ikon + renk birlikte.

Teknik: Flutter + Flame (sprite sheet, `SpriteAnimationComponent`), olaylar `StreamProvider` (Riverpod) ile; web build (CanvasKit) ve masaüstü. Sprite'lar 32×32/48×48, 8 kare yürüme + 6 kare aksiyon; ilk sette 3 karakter + 1 oda.

---

## 6. Yol Haritası

| Sprint | Hat A — Agent Builder | Hat B — Gamified UI | Çıkış kriteri |
|---|---|---|---|
| **0** (1 hf) | Repo iskeleti (monorepo: `services/perception`, `services/executor`, `services/orchestrator`, `gateway`, `ui`), Docker + Xvfb hedef masaüstü, CI | Olay sözleşmesi v1 JSON Schema + 200 olaylık **sahte replay dosyası** | `docker compose up` ile boş masaüstü + ekran görüntüsü endpoint'i |
| **1** (2 hf) | Perception + Executor sidecar (screenshot/zoom/click/type/key/scroll), ölçek faktörü, SafetyGuard v0 (kill switch, allowlist) | Sahne iskeleti, 1 karakter, replay dosyasından animasyon | "Hesap makinesini aç ve 12×7 yaz" görevi elle tetiklenen aksiyonlarla geçer |
| **2** (2 hf) | LLMOrchestrator: `computer_toolset` döngüsü, batch/hata semantiği, screenshot budama, cost/latency metrikleri; Gateway: EventBus + SSE + NDJSON | HUD (görev kartı, ilerleme, kod paneli), Flame vs PixiJS kararı | Ajan 5 dakikalık web-form görevini insan müdahalesiz bitirir; UI canlı olayları oynatır |
| **3** (2 hf) | ApprovalBroker + risk sınıflandırma, hibrit router (metin turları mevcut gateway'e), refusal fallback, oturum kalıcılığı | Onay diyalogları, hata/başarı tabelaları, replay kaydırıcı | Riskli aksiyonlarda ajan durur, UI'dan onayla devam eder |
| **4** (2 hf) | Çoklu-ajan (N oturum, kaynak kilidi: aynı masaüstünde tek ajan), macOS "bu bilgisayar" opt-in modu (TCC), Windows sidecar keşfi | Çoklu karakter, tema paketi (Mutfak + Kasino), sprite üretim hattı | 2 ajan paralel iki konteynerde; sahnede iki masa |
| **5** (2 hf) | Değerlendirme seti (20 görev, başarı/maliyet/süre), prompt cache optimizasyonu, gözlemlenebilirlik (OpenTelemetry) | Cila, performans (60 fps web), erişilebilirlik | Demo: 20 görevde ≥%80 başarı, ortalama maliyet raporlanır |
| **6** (1 hf) | Sertleştirme, doküman, iç beta | Onboarding, ayarlar | İç beta yayını |

**KPI'lar:** görev başarı oranı, görev başına maliyet (USD) ve süre, insan müdahale sayısı/görev, onay bekleme süresi, UI'da olay→animasyon gecikmesi (<200 ms).

---

## 7. Riskler ve Kararlar

| Risk | Etki | Önlem |
|---|---|---|
| Ajanın gerçek bilgisayarda yıkıcı aksiyon alması | Yüksek | Varsayılan hedef **izole konteyner/VM**; "bu bilgisayar" modu iki adımlı açık onay + allowlist + kill switch (OpenMausBot deseni); Wayland'da fail-closed |
| Prompt injection (ekrandaki metin ajanı yönlendirir) | Yüksek | Anthropic'in yerleşik sınıflandırıcıları açık kalır; şüpheli işaret → onay; kimlik bilgileri asla prompt'ta değil |
| Görsel 5'teki gibi memecoin/kumar otomasyonu | Yasal/finansal | **Kapsam dışı.** Finansal aksiyonlar (`approval.risk = financial`) her zaman insan onaylı; otonom trading yok |
| Maliyet (her tur görüntü) | Orta | Hash ile "değişmedi" kısa devresi, ≤1366×768, prompt cache, ucuz model ile bekleme turları, görev bütçesi |
| Gemini/Azure ile görsel-aksiyon turu | Orta | v1'de yalnızca Claude sürer; hibrit router metin turlarıyla sınırlı |
| macOS TCC / imza / notarization | Orta | Sidecar'ı ana süreçten spawn et, imzala; Sprint 4'e kadar Linux konteyner ile ilerle |
| UI'nın orkestratöre sıkı bağlanması | Orta | Olay sözleşmesi şema versiyonlu; UI yalnızca replay dosyasıyla da çalışmalı (test edilir) |

**Açık kararlar (Sprint 2 sonunda):** Flame vs PixiJS; Rust sidecar'a geçiş zamanı; Windows desteği önceliği.

---

## 8. Hemen Yapılacaklar (Sprint 0 backlog)

1. Monorepo iskeleti + `docker-compose` (Xvfb + xdotool'lu Ubuntu 24.04 hedef masaüstü).
2. `events.schema.json` v1 + 200 olaylık sahte replay (`fixtures/replay-cucumber.ndjson`).
3. Perception sidecar: `GET /screenshot`, `POST /zoom`, ölçek faktörü.
4. Executor sidecar: `POST /action` (click/type/key/scroll), SafetyGuard v0.
5. Orkestratör: tek turluk `computer_toolset_20260801` smoke testi (`claude-opus-5`, adaptive thinking, fallbacks açık).
6. Flutter/Flame "hello scene": replay dosyasını okuyup bir karakteri yürüten 1 sahne.

---

## Kaynaklar
- b-nnett/grok-bot-0.18-reconstructed — https://github.com/b-nnett/grok-bot-0.18-reconstructed
- milind-soni/OpenMausBot — https://github.com/milind-soni/OpenMausBot (docs: `computer-use-integration.md`, `linux-desktop.md`)
- Anthropic Computer Use tool — https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool
