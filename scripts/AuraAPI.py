"""
Aura: DevOps etkinliğinden oyunlaştırma — PLAN_V2.md Bölüm 6.

Akış:  DevOps (commit, PR, CI/CD) --collect--> Fact listesi --Store--> score() --> PersonCard'lar

* Olgu (Fact) saklanır, puan saklanmaz: puanlar okuma anında RULES'tan hesaplanır; kural değişince geçmiş
  kendiliğinden yeniden puanlanır.
* Kişi eşleme person_key() ile: parantezli birim atılır, Türkçe duyarlı küçük harf. 7 haneli kullanıcı
  kodları, tek sözcüklü adlar ve sistem kimlikleri kişi sayılmaz (is_person); bu kayıtlar puanlanmaz,
  yalnızca sayılır.
* Depolar: DbStore (ortak PostgreSQL; dr_aura_facts / dr_aura_sync / dr_aura_profiles, dr_meta'da kendi
  'aura_schema' anahtarı — 'schema_version'a dokunmaz, yalnız ekleyici) ve MemoryStore (veritabanı yokken
  veya demo: yalnızca bu oturum). Demo olguları ASLA veritabanına yazılmaz.
* Eşitleme collect() ile: kapsam (repo / pipeline) başına filigran, bir günlük örtüşme; kayıtlar
  ON CONFLICT DO NOTHING ile yazıldığı için aynı eşitlemenin iki kez çalışması olgu sayısını değiştirmez.

Bu dosya arayüz import etmez.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import re
import threading
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable

from scripts.fmt import TZ, parse_dt

log = logging.getLogger(__name__)

SCHEMA = 1
LOCK_ID = 7735104                 # pg advisory lock: aynı anda tek istemci eşitler
OVERLAP = timedelta(days=1)       # filigrandan geriye örtüşme
DAILY_COMMIT_CAP = 15             # günde kişi başı puanlanan commit sayısı
PAGE = 500

# ---------------------------------------------------------------------------
# Kurallar (tek yer)
# ---------------------------------------------------------------------------
RULES: dict[str, int] = {
    "commit": 10, "merge_commit": 2, "revert": -15,
    "ci_ok": 5, "ci_partial": 1, "ci_fail": -5, "ci_canceled": 0,
    "cd_ok": 15, "cd_fail": -10, "cd_canceled": 0,
    "cd_ok_prod": 25, "cd_fail_prod": -20,
    "pr_open": 5, "pr_merged": 20, "pr_abandoned": -5, "review": 8,
}
KIND_TITLES = {
    "commit": "commit", "merge_commit": "merge commit", "revert": "revert",
    "ci_ok": "CI başarılı", "ci_partial": "CI kısmen", "ci_fail": "CI başarısız", "ci_canceled": "CI iptal",
    "cd_ok": "CD başarılı", "cd_fail": "CD başarısız", "cd_canceled": "CD iptal",
    "pr_open": "PR açıldı", "pr_merged": "PR birleşti", "pr_abandoned": "PR terk", "review": "review (onay)",
}
COMMIT_KINDS = ("commit", "merge_commit", "revert")

TITLES = [   # (en düşük seviye, unvan)
    (50, "10x Efsane"), (40, "Aura Lordu"), (30, "DevOps Killer"), (20, "Programming Chad"),
    (15, "Pipeline Şövalyesi"), (10, "Merge Savaşçısı"), (6, "Commit Çırağı"), (3, "Noob Coder"), (1, "Git Noob"),
]
NEGATIVE_TITLE = "Build Kıran"

STATS = [("STR", "Güç", "Commit hacmi"), ("DEX", "Çeviklik", "CI başarı oranı"),
         ("INT", "Zekâ", "Birleştirilen PR'lar"), ("VIT", "Dayanıklılık", "Etkin gün ve en uzun seri"),
         ("LUK", "Şans", "CD başarı oranı (Prod ağırlıklı)"), ("CHA", "Karizma", "Review'lar ve farklı repolar")]


# ---------------------------------------------------------------------------
# Kişi eşleme
# ---------------------------------------------------------------------------
_PAREN = re.compile(r"\s*[\(\[<].*?[\)\]>]\s*")
_TR_LOWER = str.maketrans({"İ": "i", "I": "i", "ı": "i", "Ş": "ş", "Ğ": "ğ", "Ü": "ü", "Ö": "ö", "Ç": "ç"})
_SYSTEM_PHRASES = ("build service", "project collection", "azure pipelines", "azure devops", "team foundation",
                   "github", "microsoft.", "service account", "servis hesab")
_SYSTEM_WORDS = {"bot", "system", "sistem", "admin", "administrator", "svc", "service", "jenkins", "agent", "tfs",
                 "pipeline", "pipelines", "automation", "otomasyon", "root"}


def strip_unit(name: str) -> str:
    """'Burak ALTINIŞIK (STRATEJİ … DB)' -> 'Burak ALTINIŞIK'. Alan\\kullanıcı ve e-posta biçimleri de sadeleşir."""
    s = _PAREN.sub(" ", str(name or ""))
    s = s.split("\\")[-1]
    return " ".join(s.split())


def person_key(name: str) -> str:
    """Eşleme anahtarı: birim atılır, boşluk teklenir, Türkçe duyarlı küçük harf (İ, I, ı -> i)."""
    return strip_unit(name).translate(_TR_LOWER).casefold()


def is_person(name: str) -> bool:
    """Kişi mi? 7 haneli kullanıcı kodları / tek sözcük / rakamlı adlar / sistem kimlikleri: hayır."""
    raw = str(name or "")
    low = raw.translate(_TR_LOWER).casefold()
    if any(p in low for p in _SYSTEM_PHRASES):
        return False
    words = strip_unit(raw).split()
    if len(words) < 2:
        return False
    if any(sum(ch.isdigit() for ch in w) >= 3 for w in words) or any("@" in w for w in words):
        return False
    if {w.translate(_TR_LOWER).casefold().strip(".-_") for w in words} & _SYSTEM_WORDS:
        return False
    return all(any(ch.isalpha() for ch in w) for w in words)


# ---------------------------------------------------------------------------
# Olgular
# ---------------------------------------------------------------------------
@dataclass
class Fact:
    key: str                    # benzersiz: commit:<repo>:<sha>, build:<id>, pr:<repo>:<no>:merged …
    kind: str                   # RULES anahtarı (cd_* için Prod attrs['prod'])
    person_name: str
    app: str
    at: datetime                # tz bilgili
    attrs: dict = field(default_factory=dict)

    @property
    def person_key(self) -> str:
        return person_key(self.person_name)

    @property
    def local(self) -> datetime:
        return self.at.astimezone(TZ) if TZ else self.at.astimezone()

    def base_points(self) -> int:
        if self.kind in ("cd_ok", "cd_fail") and self.attrs.get("prod"):
            return RULES[f"{self.kind}_prod"]
        if self.kind == "commit" and self.attrs.get("merge"):
            return RULES["merge_commit"]
        return RULES.get(self.kind, 0)

    def title(self) -> str:
        t = KIND_TITLES.get(self.kind, self.kind)
        if self.kind == "commit" and self.attrs.get("merge"):
            t = KIND_TITLES["merge_commit"]
        if self.attrs.get("prod"):
            t += " (Prod)"
        return t


def _utc(value) -> datetime | None:
    d = parse_dt(value)
    return d.astimezone(timezone.utc) if d else None


# ---------------------------------------------------------------------------
# Puanlama
# ---------------------------------------------------------------------------
def level_for(aura: int) -> int:
    return int(math.isqrt(max(0, aura) // 25)) + 1 if aura > 0 else 1


def aura_for_level(level: int) -> int:
    """Seviyeye ulaşmak için gereken en az aura."""
    return 25 * (level - 1) ** 2


def title_for(level: int, aura: int) -> str:
    if aura < 0:
        return NEGATIVE_TITLE
    for low, title in TITLES:
        if level >= low:
            return title
    return TITLES[-1][1]


def _log_scale(n: float, ref: float) -> float:
    return min(1.0, math.log1p(max(0.0, n)) / math.log1p(ref))


def _ratio(ok: float, total: float) -> float:
    return (ok + 1) / (total + 2)          # Laplace: az veride uç değer yok


def _stat(x: float) -> int:
    return max(1, min(99, round(1 + 98 * x)))


@dataclass
class Totals:
    """Bir kişinin olgulardan türetilmiş sayıları (etiket ve nitelik fonksiyonlarının girdisi)."""
    commits: int = 0
    merges: int = 0
    reverts: int = 0
    ci_ok: int = 0
    ci_fail: int = 0
    ci_total: int = 0
    ci_best_streak: int = 0
    ci_worst_streak: int = 0
    cd_ok: int = 0
    cd_fail: int = 0
    cd_total: int = 0
    prod_ok: int = 0
    prod_fail: int = 0
    pr_open: int = 0
    pr_merged: int = 0
    pr_abandoned: int = 0
    reviews: int = 0
    repos: int = 0
    active_days: int = 0
    longest_streak: int = 0
    max_day_commits: int = 0
    night_commits: int = 0
    early_commits: int = 0
    weekend_commits: int = 0
    friday_prod: int = 0
    freeze_cd: int = 0
    firefighter: int = 0
    phoenix: bool = False


@dataclass
class Badge:
    id: str
    name: str
    icon: str                     # tek emoji (tema bağımsız)
    rule: str
    check: Callable[[Totals], tuple[float, float]]   # (değer, hedef)

    def progress(self, t: Totals) -> tuple[bool, float, str]:
        value, goal = self.check(t)
        return value >= goal, min(1.0, value / goal if goal else 0.0), f"{int(min(value, goal))}/{int(goal)}"


def _b(id_, name, icon, rule, fn):
    return Badge(id_, name, icon, rule, fn)


BADGES: list[Badge] = [
    _b("first_blood", "İlk Kan", "🩸", "İlk commit", lambda t: (t.commits, 1)),
    _b("green_streak", "Yeşil Seri", "🟢", "Art arda 10 başarılı CI", lambda t: (t.ci_best_streak, 10)),
    _b("flawless", "Kusursuz", "💎", "Art arda 25 başarılı CI", lambda t: (t.ci_best_streak, 25)),
    _b("red_alert", "Kırmızı Alarm", "🚨", "Art arda 5 başarısız CI", lambda t: (t.ci_worst_streak, 5)),
    _b("revert_master", "Revert Ustası", "⏪", "5 revert", lambda t: (t.reverts, 5)),
    _b("night_owl", "Gece Kuşu", "🦉", "00:00–05:00 arası 10 commit", lambda t: (t.night_commits, 10)),
    _b("early_bird", "Erkenci", "🐓", "07:00'den önce 10 commit", lambda t: (t.early_commits, 10)),
    _b("friday_courage", "Cuma Cesareti", "🍺", "Cuma 16:00'dan sonra başarılı Prod CD", lambda t: (t.friday_prod, 1)),
    _b("freeze_runner", "Freeze Kaçağı", "🥶", "Freeze döneminde CD", lambda t: (t.freeze_cd, 1)),
    _b("firefighter", "Yangın Söndürücü", "🧯", "Başarısız CD'den sonraki 1 saatte başarılı CD", lambda t: (t.firefighter, 1)),
    _b("club_100", "Yüzler Kulübü", "💯", "100 commit", lambda t: (t.commits, 100)),
    _b("club_1000", "Binler Kulübü", "🏛️", "1.000 commit", lambda t: (t.commits, 1000)),
    _b("referee", "Hakem", "⚖️", "50 review", lambda t: (t.reviews, 50)),
    _b("pr_machine", "PR Makinesi", "🏭", "25 birleştirilmiş PR", lambda t: (t.pr_merged, 25)),
    _b("one_shot", "Tek Atış", "🎯", "10 PR, hiç terk yok",
       lambda t: (t.pr_open if t.pr_abandoned == 0 else 0, 10)),
    _b("versatile", "Çok Yönlü", "🧩", "5+ farklı repoya commit", lambda t: (t.repos, 5)),
    _b("nomad", "Göçebe", "🐪", "10+ farklı repoya commit", lambda t: (t.repos, 10)),
    _b("marathon", "Maratoncu", "🏃", "30 gün aralıksız etkinlik", lambda t: (t.longest_streak, 30)),
    _b("weekend_warrior", "Hafta Sonu Savaşçısı", "⚔️", "Hafta sonu 10 commit", lambda t: (t.weekend_commits, 10)),
    _b("speed_freak", "Hız Tutkunu", "⚡", "Bir günde 20 commit", lambda t: (t.max_day_commits, 20)),
    _b("prod_conqueror", "Prod Fatihi", "🏰", "25 başarılı Prod CD", lambda t: (t.prod_ok, 25)),
    _b("deploy_king", "Deploy Kralı", "👑", "100 başarılı CD", lambda t: (t.cd_ok, 100)),
    _b("phoenix", "Anka Kuşu", "🔥", "Negatif auradan pozitife dönmek", lambda t: (1 if t.phoenix else 0, 1)),
    _b("silent_power", "Sessiz Güç", "🤫", "Hiç başarısız CD'siz 50 CD",
       lambda t: (t.cd_total if t.cd_fail == 0 else 0, 50)),
]
BADGE_BY_ID = {b.id: b for b in BADGES}


def stats_for(t: Totals) -> dict[str, int]:
    w_ok, w_total = t.cd_ok + 2 * t.prod_ok, t.cd_total + 2 * (t.prod_ok + t.prod_fail)
    return {
        "STR": _stat(_log_scale(t.commits, 1000)),
        "DEX": _stat(0.7 * _ratio(t.ci_ok, t.ci_total) * min(1.0, 0.3 + _log_scale(t.ci_total, 50))
                     + 0.3 * _log_scale(t.ci_total, 300)),
        "INT": _stat(_log_scale(t.pr_merged, 100)),
        "VIT": _stat(0.6 * _log_scale(t.active_days, 180) + 0.4 * _log_scale(t.longest_streak, 30)),
        "LUK": _stat(0.7 * _ratio(w_ok, w_total) * min(1.0, 0.3 + _log_scale(t.cd_total, 30))
                     + 0.3 * _log_scale(t.cd_total, 150)),
        "CHA": _stat(0.6 * _log_scale(t.reviews, 200) + 0.4 * _log_scale(t.repos, 15)),
    }


@dataclass
class ScoredFact:
    fact: Fact
    points: int


@dataclass
class PersonCard:
    key: str
    name: str                      # tam görünen ad (birimle, varsa)
    aura: int
    level: int
    title: str
    next_at: int                   # sonraki seviyenin aurası
    progress: float                # 0..1 bu seviyede ilerleme
    stats: dict[str, int]
    totals: Totals
    earned: list[str]              # etiket kimlikleri (kazanılma sırasıyla değil, BADGES sırasıyla)
    recent: list[ScoredFact]
    equipped: str = ""             # seçili etiket (kazanılmışsa)
    hidden: bool = False
    apps: list[str] = field(default_factory=list)
    rank: int = 0

    @property
    def label(self) -> str:
        """Rozette görünen metin: takılı etiket veya seviye unvanı."""
        b = BADGE_BY_ID.get(self.equipped) if self.equipped in self.earned else None
        return f"{b.icon} {b.name}" if b else self.title

    @property
    def featured(self) -> Badge | None:
        if self.equipped in self.earned:
            return BADGE_BY_ID[self.equipped]
        # en "zor" kazanılmış etiket: listedeki son kazanılan
        return BADGE_BY_ID[self.earned[-1]] if self.earned else None


def _display_name(names: Counter) -> str:
    """Kişinin görünen adı: birimli (parantezli) biçim varsa o, yoksa en sık görülen."""
    with_unit = [n for n, _ in names.most_common() if "(" in n]
    return with_unit[0] if with_unit else names.most_common(1)[0][0]


def _longest_run(days: set[date]) -> int:
    best = run = 0
    prev = None
    for d in sorted(days):
        run = run + 1 if prev and (d - prev).days == 1 else 1
        best, prev = max(best, run), d
    return best


def score(facts: Iterable[Fact], since: datetime | None = None, app: str | None = None,
          freezes: list | None = None, profiles: dict | None = None, recent: int = 12) -> list[PersonCard]:
    """Olgulardan sıralı kişi kartları. since/app süzgeci puan için; kişi yoksa listede yoktur."""
    profiles = profiles or {}
    by_person: dict[str, list[Fact]] = defaultdict(list)
    names: dict[str, Counter] = defaultdict(Counter)
    for f in facts:
        if not is_person(f.person_name):
            continue
        if since and f.at < since:
            continue
        if app and f.app != app:
            continue
        k = f.person_key
        by_person[k].append(f)
        names[k][f.person_name] += 1

    cards: list[PersonCard] = []
    for k, items in by_person.items():
        items.sort(key=lambda f: (f.at, f.key))
        t = Totals()
        aura, went_negative = 0, False
        per_day: Counter = Counter()
        days: set[date] = set()
        repos: set[str] = set()
        ci_run = ci_bad_run = 0
        last_cd_fail: dict[str, datetime] = {}
        scored: list[ScoredFact] = []
        for f in items:
            loc = f.local
            days.add(loc.date())
            pts = f.base_points()
            if f.kind in COMMIT_KINDS:
                per_day[loc.date()] += 1
                if f.kind != "revert" and per_day[loc.date()] > DAILY_COMMIT_CAP:
                    pts = 0                                   # günlük sınır
                if f.kind == "revert":
                    t.reverts += 1
                else:
                    t.commits += 1
                    t.merges += bool(f.attrs.get("merge"))
                    repos.add(f.app)
                    t.night_commits += loc.hour < 5
                    t.early_commits += 5 <= loc.hour < 7
                    t.weekend_commits += loc.weekday() >= 5
            elif f.kind.startswith("ci_") and f.kind != "ci_canceled":
                t.ci_total += 1
                if f.kind == "ci_ok":
                    t.ci_ok += 1
                    ci_run, ci_bad_run = ci_run + 1, 0
                elif f.kind == "ci_fail":
                    t.ci_fail += 1
                    ci_bad_run, ci_run = ci_bad_run + 1, 0
                else:
                    ci_run = ci_bad_run = 0
                t.ci_best_streak, t.ci_worst_streak = max(t.ci_best_streak, ci_run), max(t.ci_worst_streak, ci_bad_run)
            elif f.kind in ("cd_ok", "cd_fail"):
                t.cd_total += 1
                prod = bool(f.attrs.get("prod"))
                if f.kind == "cd_ok":
                    t.cd_ok += 1
                    t.prod_ok += prod
                    t.friday_prod += prod and loc.weekday() == 4 and loc.hour >= 16
                    failed = last_cd_fail.get(f.app)
                    if failed and timedelta(0) <= f.at - failed <= timedelta(hours=1):
                        t.firefighter += 1
                else:
                    t.cd_fail += 1
                    t.prod_fail += prod
                    last_cd_fail[f.app] = f.at
                if freezes and any(fr.contains(loc.date()) for fr in freezes):
                    t.freeze_cd += 1
            elif f.kind == "pr_open":
                t.pr_open += 1
            elif f.kind == "pr_merged":
                t.pr_merged += 1
            elif f.kind == "pr_abandoned":
                t.pr_abandoned += 1
            elif f.kind == "review":
                t.reviews += 1
            aura += pts
            if aura < 0:
                went_negative = True
            elif went_negative and aura > 0:
                t.phoenix = True
            scored.append(ScoredFact(f, pts))
        t.repos, t.active_days = len(repos), len(days)
        t.longest_streak = _longest_run(days)
        t.max_day_commits = max(per_day.values(), default=0)
        lvl = level_for(aura)
        floor_, next_ = aura_for_level(lvl), aura_for_level(lvl + 1)
        prof = profiles.get(k) or {}
        earned = [b.id for b in BADGES if b.progress(t)[0]]
        cards.append(PersonCard(
            key=k, name=_display_name(names[k]), aura=aura, level=lvl, title=title_for(lvl, aura), next_at=next_,
            progress=0.0 if aura <= 0 else (aura - floor_) / max(1, next_ - floor_), stats=stats_for(t), totals=t,
            earned=earned, recent=list(reversed(scored[-recent:])), equipped=prof.get("equipped") or "",
            hidden=bool(prof.get("hidden")), apps=sorted(repos)))
    cards.sort(key=lambda c: (-c.aura, c.name.casefold()))
    for i, c in enumerate(cards, 1):
        c.rank = i
    return cards


def count_ignored(facts: Iterable[Fact]) -> int:
    return sum(1 for f in facts if not is_person(f.person_name))


# ---------------------------------------------------------------------------
# Depolar
# ---------------------------------------------------------------------------
class MemoryStore:
    """Veritabanı yokken / demo: yalnızca bu oturum."""
    persistent = False

    def __init__(self):
        self._facts: dict[str, Fact] = {}
        self._marks: dict[str, tuple[str, str, str]] = {}
        self._profiles: dict[str, dict] = {}
        self._lock = threading.Lock()

    def add(self, facts: Iterable[Fact], by: str = "") -> int:
        n = 0
        with self._lock:
            for f in facts:
                if f.key not in self._facts:
                    self._facts[f.key] = f
                    n += 1
        return n

    def facts(self) -> list[Fact]:
        with self._lock:
            return list(self._facts.values())

    def count(self) -> int:
        return len(self._facts)

    def watermark(self, scope: str) -> datetime | None:
        m = self._marks.get(scope)
        return _utc(m[0]) if m else None

    def set_watermark(self, scope: str, value: datetime, by: str = "") -> None:
        with self._lock:
            self._marks[scope] = (value.isoformat(), by, datetime.now(timezone.utc).isoformat())

    def last_sync(self) -> dict | None:
        m = self._marks.get("_sync")
        return {"at": _utc(m[0]), "by": m[1]} if m else None

    def profiles(self) -> dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._profiles.items()}

    def set_profile(self, key: str, by: str = "", **values) -> None:
        with self._lock:
            self._profiles.setdefault(key, {}).update(values)

    def try_lock(self):
        return _NullLock()


class _NullLock:
    acquired = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_DDL = [
    """CREATE TABLE IF NOT EXISTS dr_aura_facts (
        id BIGSERIAL PRIMARY KEY,
        fact_key VARCHAR(300) NOT NULL UNIQUE,
        kind VARCHAR(30) NOT NULL,
        person_key VARCHAR(200) NOT NULL,
        person_name VARCHAR(300) NOT NULL,
        app_name VARCHAR(200),
        occurred_at TIMESTAMPTZ NOT NULL,
        attrs JSONB NOT NULL DEFAULT '{}',
        recorded_by VARCHAR(100),
        recorded_at TIMESTAMPTZ DEFAULT now())""",
    "CREATE INDEX IF NOT EXISTS dr_aura_facts_person ON dr_aura_facts (person_key)",
    "CREATE INDEX IF NOT EXISTS dr_aura_facts_time ON dr_aura_facts (occurred_at)",
    """CREATE TABLE IF NOT EXISTS dr_aura_sync (
        scope VARCHAR(300) PRIMARY KEY,
        watermark TIMESTAMPTZ,
        updated_by VARCHAR(100),
        updated_at TIMESTAMPTZ DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS dr_aura_profiles (
        person_key VARCHAR(200) PRIMARY KEY,
        equipped VARCHAR(50),
        hidden BOOLEAN NOT NULL DEFAULT FALSE,
        updated_by VARCHAR(100),
        updated_at TIMESTAMPTZ DEFAULT now())""",
]


class DbStore:
    """Ortak PostgreSQL: herkes aynı sıralamayı görür."""
    persistent = True
    _ready_for: object = None
    _schema_lock = threading.Lock()

    def ensure_schema(self) -> None:
        from sqlalchemy import text

        from scripts.database import get_engine, transaction
        engine = get_engine()
        with DbStore._schema_lock:
            if DbStore._ready_for is engine:
                return
            with transaction() as conn:
                conn.execute(text("SELECT pg_advisory_xact_lock(7735103)"))
                for ddl in _DDL:
                    conn.execute(text(ddl))
                conn.execute(text("""INSERT INTO dr_meta (key, value) VALUES ('aura_schema', :v)
                                     ON CONFLICT (key) DO UPDATE SET value = :v"""), {"v": str(SCHEMA)})
            DbStore._ready_for = engine

    def add(self, facts: Iterable[Fact], by: str = "") -> int:
        from sqlalchemy import text

        from scripts.database import transaction
        self.ensure_schema()
        batch = [{"k": f.key[:300], "kind": f.kind, "pk": f.person_key[:200], "pn": f.person_name[:300],
                  "app": f.app, "at": f.at, "attrs": json.dumps(f.attrs, ensure_ascii=False), "by": by[:100]}
                 for f in facts]
        if not batch:
            return 0
        n = 0
        with transaction() as conn:
            for i in range(0, len(batch), 500):
                res = conn.execute(text("""
                    INSERT INTO dr_aura_facts (fact_key, kind, person_key, person_name, app_name, occurred_at,
                                               attrs, recorded_by)
                    VALUES (:k, :kind, :pk, :pn, :app, :at, CAST(:attrs AS JSONB), :by)
                    ON CONFLICT (fact_key) DO NOTHING"""), batch[i:i + 500])
                n += max(0, res.rowcount or 0)
        return n

    def facts(self) -> list[Fact]:
        from scripts.database import read_sql
        self.ensure_schema()
        out = []
        for r in read_sql("""SELECT fact_key, kind, person_name, app_name, occurred_at, attrs
                             FROM dr_aura_facts ORDER BY occurred_at"""):
            attrs = r["attrs"] if isinstance(r["attrs"], dict) else json.loads(r["attrs"] or "{}")
            out.append(Fact(r["fact_key"], r["kind"], r["person_name"], r["app_name"] or "",
                            r["occurred_at"].astimezone(timezone.utc), attrs))
        return out

    def count(self) -> int:
        from scripts.database import read_sql
        self.ensure_schema()
        return int(read_sql("SELECT count(*) AS n FROM dr_aura_facts")[0]["n"])

    def watermark(self, scope: str) -> datetime | None:
        from scripts.database import read_sql
        self.ensure_schema()
        r = read_sql("SELECT watermark FROM dr_aura_sync WHERE scope = :s", {"s": scope})
        return r[0]["watermark"].astimezone(timezone.utc) if r and r[0]["watermark"] else None

    def set_watermark(self, scope: str, value: datetime, by: str = "") -> None:
        from scripts.database import execute
        self.ensure_schema()
        execute("""INSERT INTO dr_aura_sync (scope, watermark, updated_by, updated_at) VALUES (:s, :w, :b, now())
                   ON CONFLICT (scope) DO UPDATE SET watermark = GREATEST(dr_aura_sync.watermark, :w),
                   updated_by = :b, updated_at = now()""", {"s": scope, "w": value, "b": by[:100]})

    def last_sync(self) -> dict | None:
        from scripts.database import read_sql
        self.ensure_schema()
        r = read_sql("SELECT updated_at, updated_by FROM dr_aura_sync WHERE scope = '_sync'")
        return {"at": r[0]["updated_at"], "by": r[0]["updated_by"]} if r else None

    def profiles(self) -> dict[str, dict]:
        from scripts.database import read_sql
        self.ensure_schema()
        return {r["person_key"]: {"equipped": r["equipped"] or "", "hidden": r["hidden"]}
                for r in read_sql("SELECT person_key, equipped, hidden FROM dr_aura_profiles")}

    def set_profile(self, key: str, by: str = "", **values) -> None:
        from sqlalchemy import text

        from scripts.database import audit, transaction
        self.ensure_schema()
        cur = self.profiles().get(key, {"equipped": "", "hidden": False})
        cur.update(values)
        with transaction() as conn:
            conn.execute(text("""INSERT INTO dr_aura_profiles (person_key, equipped, hidden, updated_by, updated_at)
                                 VALUES (:k, :e, :h, :b, now())
                                 ON CONFLICT (person_key) DO UPDATE SET equipped = :e, hidden = :h,
                                 updated_by = :b, updated_at = now()"""),
                         {"k": key, "e": cur["equipped"] or None, "h": bool(cur["hidden"]), "b": by[:100]})
            audit(conn, "aura", "profile", "person", key, values)

    def try_lock(self):
        return _PgLock()


class _PgLock:
    """pg_try_advisory_lock: başka bir istemci eşitliyorsa acquired=False."""

    def __enter__(self):
        from sqlalchemy import text

        from scripts.database import get_engine
        self.conn = get_engine().connect()
        self.acquired = bool(self.conn.execute(text("SELECT pg_try_advisory_lock(:i)"), {"i": LOCK_ID}).scalar())
        self.conn.commit()
        return self

    def __exit__(self, *exc):
        from sqlalchemy import text
        try:
            if self.acquired:
                self.conn.execute(text("SELECT pg_advisory_unlock(:i)"), {"i": LOCK_ID})
                self.conn.commit()
        finally:
            self.conn.close()
        return False


# ---------------------------------------------------------------------------
# DevOps'tan toplama
# ---------------------------------------------------------------------------
@dataclass
class SyncResult:
    new: int = 0
    seen: int = 0
    ignored: int = 0
    scopes: int = 0
    errors: list[str] = field(default_factory=list)
    skipped: bool = False          # başka istemci eşitliyordu


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _paged(client, path: str, params: dict, top: int = PAGE, limit: int = 20000) -> list[dict]:
    """$top/$skip sayfalama (git commit ve PR uçları devamlılık belirteci vermez)."""
    out: list[dict] = []
    skip = 0
    while True:
        page = client.get(path, {**params, "$top": top, "$skip": skip}).get("value", [])
        out += page
        if len(page) < top or len(out) >= limit:
            return out
        skip += top


def is_revert(message: str) -> bool:
    m = (message or "").strip()
    return m.lower().startswith("revert") or "this reverts commit" in m.lower()


def commit_facts(repo: str, app: str, items: list[dict]) -> list[Fact]:
    out = []
    for c in items:
        author = c.get("author") or {}
        at = _utc(author.get("date"))
        sha = c.get("commitId") or ""
        if not (at and sha):
            continue
        msg = c.get("comment") or ""
        kind = "revert" if is_revert(msg) else "commit"
        attrs = {"sha": sha[:12]}
        if kind == "commit" and (msg.startswith("Merge ") or msg.startswith("Merged PR")):
            attrs["merge"] = True
        out.append(Fact(f"commit:{repo}:{sha}", kind, author.get("name") or "", app, at, attrs))
    return out


def pr_facts(repo: str, app: str, items: list[dict], since: datetime) -> list[Fact]:
    out = []
    for p in items:
        no = p.get("pullRequestId")
        who = (p.get("createdBy") or {}).get("displayName", "")
        opened, closed = _utc(p.get("creationDate")), _utc(p.get("closedDate"))
        base = f"pr:{repo}:{no}"
        attrs = {"pr": no}
        if opened and opened >= since:
            out.append(Fact(f"{base}:open", "pr_open", who, app, opened, attrs))
        status = (p.get("status") or "").lower()
        if closed and closed >= since:
            if status == "completed":
                out.append(Fact(f"{base}:merged", "pr_merged", who, app, closed, attrs))
            elif status == "abandoned":
                out.append(Fact(f"{base}:abandoned", "pr_abandoned", who, app, closed, attrs))
        when = closed or opened
        if when and when >= since:
            for r in p.get("reviewers") or []:
                if int(r.get("vote") or 0) >= 5 and not r.get("isContainer"):
                    rid = r.get("id") or r.get("uniqueName") or r.get("displayName")
                    out.append(Fact(f"{base}:review:{rid}", "review", r.get("displayName", ""), app, when, attrs))
    return out


_BUILD_RESULT = {"succeeded": "ok", "partiallysucceeded": "partial", "failed": "fail", "canceled": "canceled"}


def build_facts(kind: str, app: str, items: list[dict], prod_pipeline: bool = False) -> list[Fact]:
    """kind: ci | cd (YAML pipeline)."""
    out = []
    for b in items:
        if (b.get("status") or "").lower() != "completed":
            continue
        res = _BUILD_RESULT.get((b.get("result") or "").lower())
        at = _utc(b.get("finishTime"))
        if not (res and at):
            continue
        if kind == "cd" and res == "partial":
            res = "ok"
        who = (b.get("requestedFor") or b.get("requestedBy") or {}).get("displayName", "")
        attrs = {"build": b.get("id"), "number": b.get("buildNumber", "")}
        if kind == "cd" and prod_pipeline:
            attrs["prod"] = True
        out.append(Fact(f"build:{b.get('id')}", f"{kind}_{res}", who, app, at, attrs))
    return out


def release_facts(app: str, items: list[dict]) -> list[Fact]:
    out = []
    for d in items:
        status = (d.get("deploymentStatus") or "").lower()
        res = {"succeeded": "ok", "partiallysucceeded": "ok", "failed": "fail", "canceled": "canceled"}.get(status)
        at = _utc(d.get("completedOn") or d.get("startedOn"))
        if not (res and at):
            continue
        stage = (d.get("releaseEnvironment") or {}).get("name", "")
        who = (d.get("requestedFor") or d.get("requestedBy") or {}).get("displayName", "")
        attrs = {"deployment": d.get("id"), "stage": stage}
        if "prod" in stage.lower() or "canl" in stage.lower():
            attrs["prod"] = True
        out.append(Fact(f"deploy:{d.get('id')}", f"cd_{res}", who, app, at, attrs))
    return out


def collect(store, client, apps: list, pipelines: dict, since_days: int, by: str = "",
            progress: Callable[[int, int, str], None] = lambda done, total, what: None,
            now: datetime | None = None, pause: float = 0.0) -> SyncResult:
    """
    Katalogdaki her repo için commit + PR, her uygulamanın CI / CD pipeline'ı için build / deployment.
    apps: AppRef listesi; pipelines: {app_name: AppPipelines}. Hatalar kapsam başına toplanır, eşitleme sürer.
    """
    import time as _time
    now = now or datetime.now(timezone.utc)
    floor = now - timedelta(days=since_days)
    result = SyncResult()
    with store.try_lock() as lock:
        if not lock.acquired:
            result.skipped = True
            return result
        jobs: list[tuple[str, Callable[[datetime], list[Fact]]]] = []
        for a in apps:
            if a.repo_id:
                jobs.append((f"commits:{a.repo}", lambda s, a=a: commit_facts(a.repo, a.name, _paged(
                    client, f"git/repositories/{a.repo_id}/commits", {"searchCriteria.fromDate": _iso(s)}))))
                jobs.append((f"prs:{a.repo}", lambda s, a=a: pr_facts(a.repo, a.name, _paged(
                    client, f"git/repositories/{a.repo_id}/pullrequests", {"searchCriteria.status": "all"}), s)))
            p = pipelines.get(a.name)
            for kind, pi in (("ci", p.ci if p else None), ("cd", p.cd if p else None)):
                if not pi:
                    continue
                if pi.kind == "release":
                    jobs.append((f"release:{pi.definition_id}", lambda s, a=a, pi=pi: release_facts(a.name, client.get(
                        "release/deployments", {"definitionId": pi.definition_id, "minStartedTime": _iso(s),
                                                "$top": 500, "queryOrder": "descending"}).get("value", []))))
                else:
                    prod = "prod" in pi.name.lower()
                    jobs.append((f"builds:{pi.definition_id}", lambda s, a=a, pi=pi, kind=kind, prod=prod: build_facts(
                        kind, a.name, client.get("build/builds", {
                            "definitions": pi.definition_id, "minTime": _iso(s), "statusFilter": "completed",
                            "$top": 1000, "queryOrder": "finishTimeDescending"}).get("value", []), prod)))
        total = len(jobs)
        for i, (scope, job) in enumerate(jobs):
            progress(i, total, scope)
            mark = store.watermark(scope)
            start = max(floor, mark - OVERLAP) if mark else floor
            try:
                facts = [f for f in job(start) if f.at >= start]
            except Exception as e:   # noqa: BLE001 - bir kapsam düşerse diğerleri sürer
                result.errors.append(f"{scope}: {e}")
                continue
            result.seen += len(facts)
            result.ignored += count_ignored(facts)
            result.new += store.add(facts, by)
            store.set_watermark(scope, now, by)
            result.scopes += 1
            if pause:
                _time.sleep(pause)
        progress(total, total, "")
        store.set_watermark("_sync", now, by)
    return result


# ---------------------------------------------------------------------------
# Demo olguları (asla veritabanına yazılmaz)
# ---------------------------------------------------------------------------
DEMO_PEOPLE = [
    ("Burak ALTINIŞIK (STRATEJİ DİJ. DÖNÜŞÜM VE Y.ZEKA DB)", 2.6),
    ("Ayşe YILDIRIM (VERİ ANALİTİĞİ MD)", 1.5),
    ("Mehmet KAYA (VERİ ANALİTİĞİ MD)", 1.3),
    ("Zeynep ÇELİK (YAPAY ZEKA UYGULAMALARI MD)", 1.1),
    ("Emre ŞAHİN (STRATEJİ DİJ. DÖNÜŞÜM VE Y.ZEKA DB)", 0.9),
    ("Elif ÖZTÜRK (VERİ ANALİTİĞİ MD)", 0.8),
    ("Can AYDIN (VERİ YÖNETİŞİMİ MD)", 0.6),
    ("İrem KOÇ (YAPAY ZEKA UYGULAMALARI MD)", 0.5),
    ("Oğuz ARSLAN (VERİ ANALİTİĞİ MD)", 0.35),
    ("Selin DOĞAN (VERİ YÖNETİŞİMİ MD)", 0.2),
]


def demo_facts(apps: list[str], me: str = "", days: int = 150, now: datetime | None = None, seed: int = 7) -> list[Fact]:
    """Gerçekçi görünen, belirlenimli örnek olgular (+ yok sayılacak 7 haneli kodlu commit'ler)."""
    now = (now or datetime.now(timezone.utc)).replace(minute=0, second=0, microsecond=0)
    rnd = random.Random(seed)
    apps = apps or ["Musteri360", "KrediSkor", "ChurnTahmin"]
    people = list(DEMO_PEOPLE)
    if me and is_person(me) and person_key(me) not in {person_key(n) for n, _ in people}:
        people.insert(3, (me, 1.0))
    out: list[Fact] = []
    n = 0

    def add(kind, who, app, at, **attrs):
        nonlocal n
        n += 1
        out.append(Fact(f"demo:{n}", kind, who, app, at, attrs))

    for who, energy in people:
        home = rnd.sample(apps, k=min(len(apps), max(1, round(energy * 3))))
        night = rnd.random() < 0.3
        for d in range(days, -1, -1):
            if rnd.random() > 0.25 + 0.4 * min(1.0, energy):
                continue
            day0 = now - timedelta(days=d)
            if day0.weekday() >= 5 and rnd.random() > 0.15:
                continue
            for _ in range(rnd.randint(1, max(1, round(4 * energy)))):
                hour = rnd.choice([1, 2, 3, 23]) if night and rnd.random() < 0.25 else rnd.randint(6, 18)
                at = day0.replace(hour=hour % 24) + timedelta(minutes=rnd.randint(0, 59))
                app = rnd.choice(home)
                if rnd.random() < 0.04:
                    add("revert", who, app, at)
                else:
                    add("commit", who, app, at, merge=rnd.random() < 0.1)
                if rnd.random() < 0.55:
                    add(rnd.choices(["ci_ok", "ci_fail", "ci_partial"], [0.8 * min(1.3, energy + 0.3), 0.18, 0.05])[0],
                        who, app, at + timedelta(minutes=12))
            if rnd.random() < 0.18 * energy:
                app = rnd.choice(home)
                at = day0.replace(hour=rnd.randint(9, 18))
                ok = rnd.random() < 0.85
                prod = rnd.random() < 0.35
                add("cd_ok" if ok else "cd_fail", who, app, at, prod=prod)
                if not ok and rnd.random() < 0.6:
                    add("cd_ok", who, app, at + timedelta(minutes=40), prod=prod)
            if rnd.random() < 0.1 * energy:
                app = rnd.choice(home)
                at = day0.replace(hour=10)
                add("pr_open", who, app, at)
                add("pr_merged" if rnd.random() < 0.85 else "pr_abandoned", who, app, at + timedelta(hours=5))
            if rnd.random() < 0.12 * energy:
                add("review", who, rnd.choice(apps), day0.replace(hour=14))
    chaos = "Kaan TUNÇ (VERİ ANALİTİĞİ MD)"          # örnek "Build Kıran": eksi aura
    for i in range(14):
        add("ci_fail", chaos, apps[-1], now - timedelta(days=20 - i, hours=3))
    for i in range(3):
        add("revert", chaos, apps[-1], now - timedelta(days=9 - i))
        add("commit", chaos, apps[-1], now - timedelta(days=9 - i, hours=1))
    add("cd_fail", chaos, apps[-1], now - timedelta(days=4), prod=True)
    for i in range(25):    # kullanıcı kodlu commit'ler: kimseye puan yazılmaz
        add("commit", f"{rnd.randint(1000000, 9999999)}", rnd.choice(apps), now - timedelta(days=rnd.randint(0, days)))
    add("ci_ok", "Project Collection Build Service (DefaultCollection)", apps[0], now - timedelta(days=2))
    return out


def fingerprint(facts: list[Fact]) -> str:
    h = hashlib.sha1()
    for f in sorted(facts, key=lambda x: x.key):
        h.update(f.key.encode())
    return h.hexdigest()[:12]


def fact_dict(f: Fact) -> dict:
    d = asdict(f)
    d["at"] = f.at.isoformat()
    return d
