# AgentLab — Claude Code proje bağlamı

Bu dizin, "otonom bilgisayar kontrol ajanı + oyunlaştırılmış ajan sahnesi" projesinin Sprint 0 dikey dilimidir.
Plan: `../docs/plans/ai-agent-computer-control-plan.md`. Devir-teslim özeti: `HANDOFF.md`. Kullanım: `README.md`.

## Hızlı başlangıç
```bash
./scripts/dev.sh setup        # venv + bağımlılıklar (+ Xvfb kontrolü)
./scripts/dev.sh test         # tüm testler (Linux'ta xvfb-run altında)
./scripts/dev.sh serve        # gateway + UI → http://127.0.0.1:8799
./scripts/dev.sh demo         # loop + git-push görevlerini başlatır (onay UI'dan)
./scripts/dev.sh claude "Terminali aç ve 'git status' yaz"   # canlı Claude sürücüsü (API anahtarı gerekir)
```

## Mimari (değiştirmeden önce oku)
- **Tek sözleşme:** `contracts/events.schema.json` (v1). UI yalnızca bu olay akışını tüketir; komutlar `POST /sessions/{id}/commands`.
  Şema değişirse `v` artır, testlerdeki şema doğrulaması (`tests/test_core.py`) kırılır — bu istenen davranış.
- `services/agentlab/events.py` EventBus (thread-safe) → herkes `bus.emit(session, agent, type, data)` çağırır.
- `perception.py` (mss) → `executor.py` (pynput, 17 `computer_toolset_20260801` aksiyonu) → `safety.py` (risk, allowlist, hız limiti, kill switch, ApprovalBroker).
- `orchestrator.py` gerçek Claude döngüsü: `client.beta.messages.create`, `tools=[{"type":"computer_toolset_20260801"}]`,
  `thinking={"type":"adaptive"}`, `output_config={"effort":"high"}`, `betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`.
  Varsayılan model `claude-opus-5`. Fable 5.1 yalnızca zor görevlerde opsiyonel (`--model claude-fable-5-1`).
- `drivers/scripted.py` deterministik sürücü (git-push / loop / shell): sandbox'ta **gerçek** komut çalıştırır, Claude döngüsüyle aynı olay şeklini üretir. UI ikisini ayırt edemez; bu kasıtlı.
- `gateway.py` FastAPI: `/events` (SSE), `/sessions`, komutlar, `/replay`. `DISPLAY` varsa gerçek algı bağlanır.
- `ui/index.html` tek dosya canvas sahne; olay→animasyon eşlemesi `const ANIM` tablosunda. Sunucusuz replay: `?replay=<url>` veya `window.__REPLAY__`.

## Kurallar
- Testler yeşil kalmadan push yok: `./scripts/dev.sh test`.
- Yeni olay türü = önce şema (`$defs` dahil), sonra üretici, sonra `ANIM` tablosu.
- Gerçek masaüstünde koşarken: `SafetyGuard` allowlist'i boş bırakma, `--auto-approve` kullanma, kill switch = UI'daki DURDUR.
- Kimlik bilgisi asla prompt'a girmez; `ANTHROPIC_API_KEY` yalnızca ortam değişkeni.
- Commit mesajları Türkçe, kısa başlık + madde gövde.

## Açık işler (öncelik sırasıyla)
1. Canlı Claude sürücüsünü gerçek anahtarla bir kez koştur, `runs/` altındaki NDJSON'u fixture olarak kaydet.
2. Gateway `start` komutu executor'ı bağlamıyor (`executor=None`); Claude sürücüsü gateway'den başlatılamıyor — `_cmd_start`'a `driver: "claude"` ekle.
3. Sanal masaüstünde uygulama yok (monitör siyah): Docker imajına xterm/basit uygulama ekle, `docker compose up` ile doğrula.
4. Sprint 1: macOS "bu bilgisayar" modu (TCC izinleri: Ekran Kaydı + Erişilebilirlik), Windows'ta mss/pynput doğrulaması.
5. Sprint 2 kararları: Flutter+Flame mi PixiJS mi; gateway TS'e taşınsın mı; Rust sidecar zamanı.
