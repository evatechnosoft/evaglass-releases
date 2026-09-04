# AgentLab Event Contract v1

Tek NDJSON/SSE akışı. Her satır `events.schema.json`'a uyar. UI **yalnızca** bu akışı tüketir;
komutlar ayrı bir HTTP yüzeyinden gider (`POST /sessions/{id}/commands`).

| Komut | Gövde | Etki |
|---|---|---|
| `start` | `{ "task": "...", "driver": "scripted|claude", "agent": "goat" }` | Yeni oturum başlatır, `session.started` yayınlar |
| `approve` / `reject` | `{ "approval_id": "..." }` | Bekleyen `approval.requested`'ı çözer |
| `abort` | `{}` | Kill switch; `session.failed{reason:"aborted"}` |

Olay → UI animasyon eşlemesi `../ui/index.html` içindeki `ANIM` tablosundadır; şema değişince
`v` artırılır, UI eski `v`'yi replay'den okuyabilmelidir.
