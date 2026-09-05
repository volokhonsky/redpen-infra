"""Ограничение частоты запросов к API — защита от простого залива.

Зачем это здесь, а не в прокси: Caddy у нас один на оба хоста, и заливать он
будет одинаково; а плагина ограничения частоты в сборке нет. Ограничитель
внутри приложения дешевле любого варианта с новой зависимостью и защищает то
единственное, что действительно можно перегрузить, — API.

Почему сайт при этом не страдает: просмотрщик не делает ни одного запроса к API
(инвариант офлайна), поэтому даже полностью выведенный из строя API читателя не
касается. Ограничивать нужно ровно API.

Алгоритм — «дырявое ведро»: у каждого ключа есть запас токенов, он тратится по
одному на запрос и пополняется со временем. Всплеск переживается, ровный залив —
нет. Без внешних зависимостей и без фонового потока.
"""

import threading
import time
from typing import Dict, Optional, Tuple


class TokenBucket:
    """Разделяемое хранилище вёдер. Потокобезопасно, чистится само."""

    #: Больше этого числа ключей не храним: иначе залив с меняющихся адресов
    #: превратил бы защиту в утечку памяти.
    MAX_KEYS = 20000

    def __init__(self, rate_per_minute: int, burst: int):
        self.rate = rate_per_minute / 60.0
        self.burst = float(burst)
        self._buckets: Dict[str, Tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        """True — запрос пропускаем, False — отвечаем 429."""
        if self.rate <= 0:
            return True
        now = time.monotonic() if now is None else now
        with self._lock:
            tokens, last = self._buckets.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1.0, now)
            if len(self._buckets) > self.MAX_KEYS:
                self._evict(now)
            return True

    def _evict(self, now: float) -> None:
        """Освободить место, не забывая тех, кто прямо сейчас тратит токены.

        Сначала выбрасываются ключи, чьи вёдра успели наполниться до краёв: они
        ничего не помнят, и заново заведённое ведро от них не отличается.

        Если места всё равно не хватило, выбрасываются самые давние по времени
        последнего запроса — но ровно столько, сколько нужно, чтобы уложиться в
        MAX_KEYS. Полная очистка словаря здесь была бы худшим из решений:
        именно во время залива она возвращала бы полный запас токенов всем
        заливающим сразу, то есть снимала бы ограничение в тот момент, когда
        оно единственно и нужно.

        Вызывается под замком, из allow().
        """
        full_after = self.burst / self.rate if self.rate else 0
        stale = [k for k, (_tokens, last) in self._buckets.items()
                 if now - last > full_after]
        for key in stale:
            self._buckets.pop(key, None)
        if len(self._buckets) <= self.MAX_KEYS:
            return
        # Дальше — по давности последнего запроса, начиная с самых давних.
        # Отметка обновляется и у отбитого запроса, поэтому тот, кто стучится
        # прямо сейчас, оказывается в конце очереди на вылет и своего предела
        # не теряет. Считать «полнее — значит ненужнее» здесь нельзя: отбитое
        # ведро успевает накопить долю токена и выглядит полнее, чем только что
        # опустошённое.
        by_age = sorted(self._buckets.items(), key=lambda item: item[1][1])
        for key, _bucket in by_age[:len(self._buckets) - self.MAX_KEYS]:
            self._buckets.pop(key, None)


def client_key(request) -> str:
    """Адрес клиента. За прокси берём ПОСЛЕДНИЙ адрес из X-Forwarded-For.

    Почему последний, а не первый. Прокси не заменяет этот заголовок, а
    дописывает адрес соединения в конец уже пришедшего значения. Значит первый
    элемент пишет сам клиент, и до 2026-09-05 ограничение частоты читало именно
    его: приславший `X-Forwarded-For: <любая строка>` назначал себе ключ ведра
    сам и, меняя строку на каждом запросе, не встречал предела вовсе. Последний
    элемент дописан нашим собственным прокси, и подделать его нельзя.

    Второй замок стоит в Caddy: `header_up X-Forwarded-For` на хосте API
    перезаписывает заголовок целиком, так что до сюда доезжает ровно один
    адрес. Здесь берётся последний на случай, если эта строка конфигурации
    когда-нибудь потеряется при правке.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"
