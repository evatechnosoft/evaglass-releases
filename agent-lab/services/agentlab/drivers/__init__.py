"""Sürücüler: aynı olay akışını üreten farklı "beyin"ler.

`scripted` deterministik/çevrimdışıdır (UI ve replay için), `claude` gerçek LLM döngüsüdür.
UI ikisini birbirinden ayırt edemez — sözleşme aynıdır.
"""
from .scripted import TASKS, ScriptedDriver, run_task

__all__ = ["ScriptedDriver", "run_task", "TASKS"]
