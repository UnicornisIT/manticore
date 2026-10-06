"""Central email validation with a bundled provider registry and DNS fallback."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time


REGISTRY_PATH = Path(__file__).resolve().parent / "data" / "email_providers.json"
DNS_CACHE_TTL_SECONDS = int(os.environ.get("EMAIL_DNS_CACHE_TTL", "86400"))
DNS_LOOKUPS_DISABLED = os.environ.get("EMAIL_DNS_DISABLE", "").casefold() in {"1", "true", "yes"}
LOCAL_PART_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")


@dataclass(frozen=True)
class EmailValidationResult:
    valid: bool
    severity: str
    code: str
    email: str
    domain: str = ""
    provider: str = ""
    suggested_domain: str = ""
    suggested_email: str = ""
    verification_source: str = ""
    confidence: str = ""
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def load_provider_registry(path: Path | str = REGISTRY_PATH) -> dict:
    with Path(path).open(encoding="utf-8-sig") as stream:
        registry = json.load(stream)
    if not isinstance(registry.get("providers"), list):
        raise ValueError("Некорректный registry почтовых провайдеров.")
    validate_provider_registry(registry)
    return registry


def validate_provider_registry(registry: dict) -> None:
    if registry.get("schema_version") != 1 or not registry.get("last_verified"):
        raise ValueError("Registry должен содержать schema_version=1 и last_verified.")
    providers = registry.get("providers")
    if not isinstance(providers, list) or not providers:
        raise ValueError("Registry не содержит провайдеров.")
    owners = {}
    all_domains = set()
    invalid_mappings = []
    for provider in providers:
        name = str(provider.get("provider") or "").strip()
        canonical = provider.get("canonical_domains")
        sources = provider.get("source_urls")
        if not name or not isinstance(canonical, list) or not canonical:
            raise ValueError("У каждого провайдера должны быть имя и canonical domain.")
        if not isinstance(sources, list) or not sources or not all(str(url).startswith("https://") for url in sources):
            raise ValueError(f"У провайдера {name} отсутствует официальный HTTPS source.")
        for kind in ("canonical_domains", "additional_domains", "legacy_domains"):
            domains = provider.get(kind, [])
            if not isinstance(domains, list):
                raise ValueError(f"Поле {kind} провайдера {name} должно быть списком.")
            for raw_domain in domains:
                normalized = str(raw_domain).encode("idna").decode("ascii").casefold()
                if normalized != raw_domain or not re.fullmatch(r"[a-z0-9.-]+", normalized):
                    raise ValueError(f"Домен {raw_domain!r} не нормализован через IDNA.")
                if normalized in owners:
                    raise ValueError(f"Домен {normalized} повторяется у {owners[normalized]} и {name}.")
                owners[normalized] = name
                all_domains.add(normalized)
        mappings = provider.get("known_invalid_domains", {})
        if not isinstance(mappings, dict):
            raise ValueError(f"known_invalid_domains провайдера {name} должен быть объектом.")
        for wrong, replacement in mappings.items():
            invalid_mappings.append((str(wrong).casefold(), str(replacement).casefold(), name))
    for wrong, replacement, name in invalid_mappings:
        if wrong in all_domains:
            raise ValueError(f"Ошибочный домен {wrong} одновременно зарегистрирован как действующий.")
        if replacement not in all_domains:
            raise ValueError(f"Подсказка {wrong} провайдера {name} указывает на неизвестный домен {replacement}.")


_REGISTRY = load_provider_registry()


def _provider_maps(registry: dict):
    exact = {}
    invalid = {}
    canonical = []
    for item in registry["providers"]:
        for kind in ("canonical_domains", "additional_domains", "legacy_domains"):
            for domain in item.get(kind, []):
                normalized = domain.casefold()
                exact[normalized] = (item, kind == "legacy_domains")
                canonical.append((normalized, item))
        for domain, replacement in item.get("known_invalid_domains", {}).items():
            invalid[domain.casefold()] = (replacement.casefold(), item)
    return exact, invalid, canonical


_EXACT_DOMAINS, _KNOWN_INVALID, _KNOWN_DOMAINS = _provider_maps(_REGISTRY)
_MEMORY_DNS_CACHE = {}
_MEMORY_DNS_CACHE_LOCK = threading.Lock()


def ensure_dns_cache_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS email_domain_cache (
            domain TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            checked_at REAL NOT NULL,
            mx_hosts TEXT,
            error_type TEXT
        )
        """
    )


def _normalize(value: str):
    value = str(value or "").strip()
    if not value:
        return value, "", "", None
    if value.count("@") != 1:
        return value, "", "", "Адрес должен содержать один символ @."
    local, raw_domain = value.rsplit("@", 1)
    if not local or len(local) > 64 or not LOCAL_PART_RE.fullmatch(local):
        return value, local, "", "Некорректная часть адреса перед @."
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return value, local, "", "Некорректные точки в части адреса перед @."
    try:
        domain = raw_domain.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError:
        return value, local, "", "Некорректное доменное имя."
    labels = domain.split(".")
    if len(domain) > 253 or len(labels) < 2 or any(
        not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
        or not re.fullmatch(r"[a-z0-9-]+", label)
        for label in labels
    ):
        return value, local, domain, "Некорректное доменное имя."
    return f"{local}@{domain}", local, domain, None


def _distance(left: str, right: str) -> int:
    """Optimal-string-alignment Damerau-Levenshtein distance."""
    rows = len(left) + 1
    cols = len(right) + 1
    matrix = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        matrix[i][0] = i
    for j in range(cols):
        matrix[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if left[i - 1] == right[j - 1] else 1
            matrix[i][j] = min(
                matrix[i - 1][j] + 1,
                matrix[i][j - 1] + 1,
                matrix[i - 1][j - 1] + cost,
            )
            if i > 1 and j > 1 and left[i - 1] == right[j - 2] and left[i - 2] == right[j - 1]:
                matrix[i][j] = min(matrix[i][j], matrix[i - 2][j - 2] + 1)
    return matrix[-1][-1]


@lru_cache(maxsize=4096)
def _suggest_domain(domain: str):
    first_label, _, tld = domain.partition(".")
    wrong_tld = []
    typo = []
    for candidate, provider in _KNOWN_DOMAINS:
        candidate_label, _, candidate_tld = candidate.partition(".")
        if first_label == candidate_label and tld != candidate_tld:
            wrong_tld.append((candidate, provider))
            continue
        distance = _distance(domain, candidate)
        if distance == 1 or (distance == 2 and len(domain) >= 8 and len(candidate) >= 8):
            typo.append((distance, candidate, provider))
    if wrong_tld:
        return "provider_wrong_tld", "high", wrong_tld[0]
    if typo:
        typo.sort(key=lambda item: (item[0], abs(len(domain) - len(item[1])), item[1]))
        distance, candidate, provider = typo[0]
        return "probable_domain_typo", "high" if distance == 1 else "medium", (candidate, provider)
    return None


def _dns_query(domain: str, resolver=None) -> tuple[str, list[str], str]:
    if resolver is None and DNS_LOOKUPS_DISABLED:
        return "verification_unavailable", [], "disabled"
    try:
        if resolver is None:
            import dns.resolver
            resolver = dns.resolver.Resolver(configure=True)
            resolver.timeout = 1.5
            resolver.lifetime = 2.5

        def resolve(record_type):
            if hasattr(resolver, "resolve"):
                return resolver.resolve(domain, record_type)
            return resolver(domain, record_type)

        try:
            answers = resolve("MX")
            hosts = [str(getattr(answer, "exchange", answer)).rstrip(".") for answer in answers]
            if hosts and hosts != [""] and hosts != ["."]:
                return "domain_valid_mx", hosts, ""
            if hosts == ["."]:
                return "domain_no_mail", [], "null_mx"
        except Exception as exc:
            name = exc.__class__.__name__
            if name == "NXDOMAIN":
                return "domain_not_found", [], name
            if name not in {"NoAnswer", "NoNameservers"}:
                raise
            if name == "NoNameservers" and "SERVFAIL" in str(exc).upper():
                raise

        found_address = False
        for record_type in ("A", "AAAA"):
            try:
                if list(resolve(record_type)):
                    found_address = True
                    break
            except Exception as exc:
                name = exc.__class__.__name__
                if name == "NXDOMAIN":
                    return "domain_not_found", [], name
                if name not in {"NoAnswer", "NoNameservers"}:
                    raise
        return ("domain_valid_implicit_mx" if found_address else "domain_no_mail"), [], ""
    except Exception as exc:
        logging.warning("Email DNS verification failed for domain %s: %s", domain, exc)
        return "verification_unavailable", [], exc.__class__.__name__


def _cached_dns(domain: str, database_path: str | None, resolver=None, now=None):
    current = float(now if now is not None else time.time())
    # Keep an explicit resolver alive as part of the key. Using id(resolver)
    # allowed Python to reuse an id after a short-lived resolver was collected,
    # returning a DNS result produced by a different resolver.
    resolver_key = resolver
    try:
        hash(resolver_key)
    except TypeError:
        resolver_key = ("unhashable-resolver", id(resolver))
    cache_key = (domain, str(database_path or ""), resolver_key)
    with _MEMORY_DNS_CACHE_LOCK:
        memory_row = _MEMORY_DNS_CACHE.get(cache_key)
    if memory_row and current - memory_row[1] < DNS_CACHE_TTL_SECONDS:
        return memory_row[0], list(memory_row[2]), memory_row[3]
    if database_path:
        try:
            connection = sqlite3.connect(database_path)
            try:
                ensure_dns_cache_table(connection)
                row = connection.execute(
                    "SELECT status, checked_at, mx_hosts, error_type FROM email_domain_cache WHERE domain=?",
                    (domain,),
                ).fetchone()
                if row and current - float(row[1]) < DNS_CACHE_TTL_SECONDS:
                    with _MEMORY_DNS_CACHE_LOCK:
                        _MEMORY_DNS_CACHE[cache_key] = (row[0], float(row[1]), json.loads(row[2] or "[]"), row[3] or "")
                    return row[0], json.loads(row[2] or "[]"), row[3] or ""
            finally:
                connection.close()
        except sqlite3.Error:
            logging.exception("Could not read email DNS cache for domain %s", domain)
    status, hosts, error_type = _dns_query(domain, resolver)
    with _MEMORY_DNS_CACHE_LOCK:
        _MEMORY_DNS_CACHE[cache_key] = (status, current, list(hosts), error_type)
    if database_path:
        try:
            connection = sqlite3.connect(database_path)
            try:
                ensure_dns_cache_table(connection)
                connection.execute(
                    """
                    INSERT INTO email_domain_cache (domain, status, checked_at, mx_hosts, error_type)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(domain) DO UPDATE SET
                        status=excluded.status, checked_at=excluded.checked_at,
                        mx_hosts=excluded.mx_hosts, error_type=excluded.error_type
                    """,
                    (domain, status, current, json.dumps(hosts), error_type),
                )
                connection.commit()
            finally:
                connection.close()
        except sqlite3.Error:
            logging.exception("Could not store email DNS cache for domain %s", domain)
    return status, hosts, error_type


def validate_email(value: str, *, database_path: str | None = None, resolver=None, now=None) -> EmailValidationResult:
    normalized, local, domain, syntax_error = _normalize(value)
    if not normalized:
        return EmailValidationResult(True, "ok", "empty", "", message="Адрес не указан.")
    if syntax_error:
        return EmailValidationResult(False, "error", "invalid_syntax", normalized, domain, message=syntax_error)
    exact = _EXACT_DOMAINS.get(domain)
    if exact:
        provider, legacy = exact
        return EmailValidationResult(
            True, "ok", "known_provider_legacy_valid" if legacy else "known_provider_valid",
            normalized, domain, provider["provider"], verification_source="provider_registry",
            message="Официальный legacy-домен почтового сервиса." if legacy else "Официальный домен почтового сервиса.",
        )
    known_invalid = _KNOWN_INVALID.get(domain)
    if known_invalid:
        suggestion, provider = known_invalid
        return EmailValidationResult(
            True, "warning", "known_provider_domain_mismatch", normalized, domain,
            provider["provider"], suggestion, f"{local}@{suggestion}", "provider_registry", "high",
            f"Домен {domain} не является официальным доменом {provider['provider']}.",
        )
    suggested = _suggest_domain(domain)
    if suggested:
        code, confidence, (suggestion, provider) = suggested
        return EmailValidationResult(
            True, "warning", code, normalized, domain, provider["provider"], suggestion,
            f"{local}@{suggestion}", "provider_registry", confidence,
            f"Возможно, в домене {domain} ошибка.",
        )
    status, _hosts, _error_type = _cached_dns(domain, database_path, resolver, now)
    messages = {
        "domain_valid_mx": "Почтовый домен подтверждён MX-записью.",
        "domain_valid_implicit_mx": "Домен принимает почту через адресную запись.",
        "domain_not_found": "Почтовый домен не найден.",
        "domain_no_mail": "У домена не найдена почтовая инфраструктура.",
        "verification_unavailable": "Сейчас не удалось проверить почтовый домен.",
    }
    valid = True  # DNS is advisory: only deterministic syntax errors block data entry.
    severity = "ok" if status in {"domain_valid_mx", "domain_valid_implicit_mx"} else "unknown" if status == "verification_unavailable" else "warning"
    return EmailValidationResult(
        valid, severity, status, normalized, domain, verification_source="dns",
        message=messages[status],
    )


def registry_metadata() -> dict:
    return _REGISTRY
