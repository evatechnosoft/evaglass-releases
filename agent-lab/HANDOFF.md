# Devir-Teslim — AgentLab Sprint 0 (2026-09-04)

Yerelde devam etmek için tek kaynak. Claude Code'u `agent-lab/` içinde aç; `CLAUDE.md` otomatik yüklenir.

## 1. Yerelde kaldığın yerden devam
```bash
git clone https://github.com/evatechnosoft/evaglass-releases.git
cd evaglass-releases
git checkout claude/ai-agent-computer-control-9ntdm5
cd agent-lab
./scripts/dev.sh setup && ./scripts/dev.sh test
./scripts/dev.sh serve            # başka terminalde: ./scripts/dev.sh demo
export ANTHROPIC_API_KEY=sk-ant-...   # canlı ajan için
./scripts/dev.sh claude "Terminali aç ve 'git status' yaz"
```
Dal: `claude/ai-agent-computer-control-9ntdm5` · 8 commit · testler: 59 yeşil.
Sahne (sunucusuz demo): https://claude.ai/code/artifact/698b482c-b0d0-4429-a0dc-e5e16e184f40

> Bu depo aslında `evaglass` release deposu. Asıl geliştirme için `agent-lab/` dizinini ayrı bir repoya taşı
> (`git subtree split -P agent-lab -b agentlab-main` ile geçmiş korunur).

## 2. Ne var (dosya haritası)
| Yol | Görev | Sahip/Durum |
|---|---|---|
| `../docs/plans/ai-agent-computer-control-plan.md` | Ürün + mimari plan, 6 sprint yol haritası, riskler | tamam |
| `contracts/events.schema.json`, `contracts/README.md` | Olay sözleşmesi v1 (18 tür, `$defs` ile `data` şemaları), komut API | tamam |
| `services/agentlab/events.py` | EventBus, NDJSON store, SSE kuyruk adaptörü | tamam |
| `services/agentlab/perception.py` | mss yakalama, ölçek faktörü (Anthropic kuralı), zoom, hash/changed, thumb | tamam |
| `services/agentlab/executor.py` | 17 `computer_toolset_20260801` aksiyonu (pynput, tembel import) | tamam, gerçek masaüstünde test edilmedi |
| `services/agentlab/safety.py` | `classify()` risk kuralları, allowlist (fail-closed), hız limiti, kill switch, ApprovalBroker | tamam |
| `services/agentlab/orchestrator.py` | Claude döngüsü: batch hata semantiği, görüntü budama, refusal→failed, maliyet metriği | sahte istemciyle test edildi, **canlı koşulmadı** |
| `services/agentlab/drivers/scripted.py` | git-push / loop / shell; sandbox'ta gerçek komut; kill switch | tamam |
| `services/agentlab/gateway.py` | FastAPI SSE + komutlar + replay; DISPLAY varsa algı bağlanır | tamam; executor bağlanmıyor (açık iş #2) |
| `services/agentlab/cli.py` | `python -m agentlab.cli run --driver scripted|claude --task ...` | tamam |
| `ui/index.html` | Piksel-art sahne, HUD, komut akışı, onay/abort, sunucusuz replay | tamam |
| `fixtures/replay-{loop,git-push,demo}.ndjson` | Gerçek ekran görüntüsü hash'leriyle kaydedilmiş akışlar | tamam |
| `tests/test_core.py`, `tests/test_gateway.py` | 46 + 13 test | yeşil |
| `scripts/dev.sh`, `scripts/proof.mjs`, `scripts/screenshot_ui.mjs` | Yerel yardımcı, Playwright kanıt/video | tamam |
| `proof/*.png`, `proof/sessions.json` | Canlı koşu kanıtları | tamam (`demo.webm` git dışı) |
| `Dockerfile`, `docker-compose.yml` | Xvfb hedef masaüstü + gateway | yazıldı, **build edilmedi** |

## 3. Kanıtlanan
- Xvfb 1280×800 altında gateway + iki scripted görev: loop 10/10 gerçek tıklama + gerçek ekran görüntüsü; git-push onayda durdu, API'den onaylandı, bare origin'e commit `f79cf61` gitti.
- UI canlı SSE'de iki masa (GOAT, PENGU), ONAY BEKLİYOR → DONE geçişi; sunucusuz replay `file://` altında sıfır konsol hatası.
- Kill switch: döngü 2. dilimde `session.failed{reason:"aborted"}` ile durdu (test).

## 4. Kararlar (gerekçeleriyle)
| Karar | Neden |
|---|---|
| Önce Agent Builder, UI paralelde sözleşme üzerinden | UI olay akışının görselleştirmesi; akış olmadan animasyon yok |
| Ana model `claude-opus-5`, Fable 5.1 opsiyonel | Computer Use aracı yerleşik; Fable 2× fiyat, yalnızca opus'un takıldığı görevlerde |
| Hibrit router yalnızca metin turlarında | Gemini/Azure'a görsel-aksiyon turu vermek koordinat doğruluğunu sıfırdan kanıtlamak demek |
| OpenCV v1'de yok | Görsel algıyı model yapıyor; OpenCV v2'de "bilinen buton" hızlı yolu |
| Gateway Python (plan TS diyordu) | Sprint 0'da tek dil, hızlı kanıt; TS'e taşıma Sprint 2 kararı |
| Scripted sürücü Claude ile aynı olay şeklini üretir | UI'ı canlı ajana bağımlı olmadan geliştirmek ve demo yapmak için |
| Finansal/kumar otomasyonu kapsam dışı | Görsel 5'teki memecoin sahnesi yalnızca görsel referans; finansal aksiyon her zaman insan onaylı |

## 5. Açık işler
1. **Canlı Claude koşusu** (anahtarla): `./scripts/dev.sh claude "..."`; çıkan `runs/*.ndjson`'u fixture yap; `orchestrator.py`'deki `tool_result` şekillerini gerçek yanıtla doğrula.
2. **Gateway'den Claude sürücüsü**: `_cmd_start` içinde `driver=="claude"` → `LLMOrchestrator` + `ScreenPerceptionService` + `ActionExecutorService` kur, thread'de çalıştır.
3. **Docker imajı**: `docker compose up --build`; xterm'in Xvfb'de göründüğünü ve thumb'ların siyah olmadığını doğrula.
4. **macOS/Windows gerçek masaüstü**: mss+pynput yerli çalışır; macOS'ta Ekran Kaydı + Erişilebilirlik izni. Allowlist'i doldurmadan koşma.
5. **UI**: monitörde thumb yoksa "algı yok" etiketi; olay paneline filtre; 4+ ajan için kaydırma.
6. **Sprint 1 backlog** plan §6'da; Sprint 2 kararları `CLAUDE.md`'de.

## 6. Referanslar
- Anthropic Computer Use: https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool
- b-nnett/grok-bot-0.18-reconstructed (Electron host/coordinator ayrımı) · milind-soni/OpenMausBot (harness + cua-driver + onay aracısı deseni)
