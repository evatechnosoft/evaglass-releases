# AgentLab — Sprint 0 dikey dilimi

Planın (`../docs/plans/ai-agent-computer-control-plan.md`) ilk uygulanabilir parçası:
**olay sözleşmesi → sidecar'lar (algı/aksiyon/güvenlik) → orkestratör → gateway (SSE) → piksel-art sahne.**

```
agent-lab/
├── contracts/            events.schema.json (v1) + README  — iki hattı bağlayan tek sözleşme
├── services/agentlab/
│   ├── events.py         EventBus (thread-safe), NDJSON store, SSE kuyruk adaptörü
│   ├── perception.py     ScreenPerceptionService: mss yakalama, ölçek, zoom, hash/"değişti"
│   ├── executor.py       ActionExecutorService: computer_toolset_20260801 aksiyonları (pynput)
│   ├── safety.py         SafetyGuard (risk kuralları, allowlist, hız limiti, kill switch) + ApprovalBroker
│   ├── orchestrator.py   LLMOrchestrator: claude-opus-5 + computer_toolset döngüsü, budama, fallback
│   ├── drivers/scripted.py  Deterministik sürücü: git-push / loop / shell — gerçek komut çalıştırır
│   ├── gateway.py        FastAPI: /events (SSE), /sessions, komutlar, /replay
│   └── cli.py            python -m agentlab.cli run --driver scripted --task git-push
├── ui/index.html         Oyunlaştırılmış sahne (canvas, tek dosya, sunucusuz replay modu)
├── fixtures/             Kayıtlı olay akışları (replay-git-push.ndjson, replay-loop.ndjson)
├── tests/                pytest
├── Dockerfile, docker-compose.yml   Xvfb 1280x800 + xterm hedef masaüstü + gateway
└── proof/                Kanıt ekran görüntüleri (Playwright ile alınır)
```

## Çalıştırma

```bash
cd agent-lab
pip install -e .            # veya: pip install fastapi "uvicorn[standard]" sse-starlette mss pynput pillow anthropic jsonschema
export PYTHONPATH=services

# 1) Testler (sanal ekran altında)
xvfb-run -a -s "-screen 0 1280x800x24" python3 -m pytest -q

# 2) Gateway + UI (scripted sürücü, otomatik onay)
xvfb-run -a -s "-screen 0 1280x800x24" python3 -m agentlab.gateway --port 8799 --auto-approve
#    → http://127.0.0.1:8799   (▶ git-push / ▶ loop / ▶ replay)

# 3) Canlı Claude sürücüsü (ANTHROPIC_API_KEY gerekir; hedef masaüstü izole olmalı)
python3 -m agentlab.cli run --driver claude --task "Terminali aç ve 'git status' yaz" --with-display

# 4) Docker (hedef masaüstü konteynerde)
docker compose up --build
```

## Model kararı (PM notu)
| Rol | Model | Gerekçe |
|---|---|---|
| Ana orkestratör (görsel algı + aksiyon) | `claude-opus-5` | `computer_toolset_20260801` yerleşik; maliyet/doğruluk dengesi |
| Zor/uzun görevler (opsiyonel yükseltme) | `claude-fable-5-1` | 2× fiyat; yalnızca opus'un takıldığı görevlerde, `effort: high` |
| Ucuz turlar (bekleme, "değişti mi") | hash kısa devresi → `claude-sonnet-5` | Görüntü göndermeden |
| Risk sınıflandırma | kural motoru → `claude-haiku-4-5` | Deterministik önce |

Sprint 0'da gateway TypeScript yerine Python'da (tek dil, hızlı kanıt). TS'e taşıma Sprint 2 kararı.

## Kanıt (Sprint 0 uçtan uca koşu, 2026-09-04)

Xvfb 1280×800 altında gateway + scripted sürücü, Playwright ile izlendi (`scripts/proof.mjs`):

| Dosya | Ne gösteriyor |
|---|---|
| `proof/01-loop-running.png` | `loop` görevi: GOAT masasında 10 dilim, her dilimde gerçek `left_click` + gerçek ekran görüntüsü (sha1 hash) |
| `proof/04-approval-pending.png` | `git-push` görevi: PENGU `git push` öncesi **ONAY BEKLİYOR** (risk=medium), sarı ünlem |
| `proof/05-git-push-done.png` | Onay sonrası gerçek push: `f79cf61 feat: agentlab scripted smoke` bare origin'de |
| `proof/13-scene.png` | Sahne yakın plan |
| `proof/sessions.json` | `/sessions` çıktısı: iki oturum `session.finished`, 74 + 42 olay |
| `fixtures/replay-demo.ndjson` | İki görevin birleşik, sunucusuz oynatılabilir kaydı (artifact bunu gömer) |

Tekrar üretmek için:

```bash
export PYTHONPATH=$PWD/services
nohup xvfb-run -a -s "-screen 0 1280x800x24" python3 -m agentlab.gateway --port 8799 --store-dir "" &
NODE_PATH=$(npm root -g) node scripts/proof.mjs http://127.0.0.1:8799 proof
```
