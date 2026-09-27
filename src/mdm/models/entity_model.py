"""The entity model: attributes, sources, match, survivorship and validation rules.

A model is data (`models/*.yaml`), never code: a new entity arrives as a file,
is validated here, and publishing it creates its table (principle P8).
`EntityModel.from_dict` validates everything and raises `ModelError` listing
every problem found, each a safe token `code:path` (the path names model
elements, never a value).

The tables of match forms, comparator levels and default thresholds live here
so the model can be validated without the engine; the engine reads the same
tables (`mdm.engine` may import `mdm.models`, never the reverse).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

from mdm.models.errors import ModelError, NotFound

ATTRIBUTE_TYPES = ("text", "integer", "number", "boolean", "date", "timestamp", "json", "reference")
MASKING = ("none", "personal")
CRITICALITY = ("normal", "critical")
STANDARDISERS = (
    "none",
    "text",
    "person_name",
    "org_name",
    "email",
    "phone",
    "postcode",
    "date",
    "registered_id",
    "code",
    "url",
)
STYLES = ("registry", "consolidated", "coexistence", "authored")
MODES = ("identify", "link", "consolidate")
COMPARATORS = (
    "exact",
    "name",
    "phonetic_name",
    "levenshtein",
    "token_set",
    "date",
    "identifier",
    "phone",
    "email",
    "postcode",
)
STRATEGIES = ("source_trust", "recency", "completeness", "frequency")
RULE_KINDS = ("required", "pattern", "range", "code_list", "checksum", "placeholder")
DIMENSIONS = ("completeness", "validity", "consistency", "timeliness", "uniqueness", "accuracy")
CHECKSUMS = ("luhn", "mod97", "mod11")
SEVERITIES = ("warn", "hold")
POLICY_FIELDS = ("new", "update", "critical_update", "end_date", "master_id")
POLICY_VALUES = ("auto", "hold")
#: a field's value when the model leaves it out: "auto", except an arrival naming an active master ID, which
#: rule RULE1 does not make automatic, so it is held for a steward unless the data owner says otherwise
POLICY_DEFAULTS: Mapping[str, str] = {"master_id": "hold"}
DATE_ORDERS = ("ymd", "dmy", "mdy")
HARD_RULE_KINDS = ("must_link", "cannot_link")
GROUP_MODES = ("whole_group", "keyed_union")
#: the named patterns a `pattern` validation rule may use; compiled in `mdm.engine.quality`, never in YAML
PATTERN_NAMES = ("email", "postcode", "url")
RESERVED_TABLES = ("xref", "retired_id", "relationship", "change", "commit_log")
RESERVED_COLUMNS = ("master_id", "status", "survivor_id")  # and every name starting with "_"
#: The union of Postgres reserved keywords (pg_get_keywords() catcode 'R') and DuckDB reserved keywords
#: (duckdb_keywords() category 'reserved'), plus every keyword either engine refuses as an unquoted table
#: or column name (measured on Postgres 16 and DuckDB 1.5.5, 2026-09-27).
SQL_RESERVED_WORDS: frozenset[str] = frozenset(
    {
        "all", "analyse", "analyze", "and", "anti", "any", "array", "as", "asc", "asof", "asymmetric", "at",
        "authorization", "binary", "both", "by", "case", "cast", "check", "collate", "collation", "column",
        "concurrently", "constraint", "create", "cross", "current_catalog", "current_date", "current_role",
        "current_schema", "current_time", "current_timestamp", "current_user", "default", "deferrable", "desc",
        "describe", "distinct", "do", "else", "end", "except", "false", "fetch", "for", "foreign", "freeze",
        "from", "full", "glob", "grant", "group", "having", "ilike", "in", "initially", "inner", "intersect",
        "into", "is", "isnull", "join", "lambda", "lateral", "leading", "left", "like", "limit", "localtime",
        "localtimestamp", "natural", "not", "notnull", "null", "offset", "on", "only", "or", "order", "outer",
        "overlaps", "pivot", "pivot_longer", "pivot_wider", "placing", "positional", "primary", "qualify",
        "references", "returning", "right", "select", "semi", "session_user", "show", "similar", "some",
        "summarize", "symmetric", "system_user", "table", "tablesample", "then", "to", "trailing", "true",
        "union", "unique", "unpack", "unpivot", "user", "using", "variadic", "verbose", "when", "where",
        "window", "with",
    }
)  # fmt: skip
NAME_RE = r"^[a-z][a-z0-9_]{0,40}\Z"
CODE_RE = r"^[A-Z][A-Z0-9]{1,7}\Z"  # the master-ID prefix: PER, ORG
SCHEME_RE = r"^[A-Z][A-Z0-9_]{0,30}\Z"  # a registered-ID scheme: PERSON_REF, ORG_REG

#: match forms per standardiser: "" is the attribute's own form `a`, others are `a.<suffix>` (B.9.1)
MATCH_FORMS: Mapping[str, tuple[str, ...]] = {
    "none": ("",),
    "text": ("",),
    "person_name": ("", "metaphone", "nysiis"),
    "org_name": ("", "first", "first.metaphone", "tokens", "legal"),
    "email": ("", "local", "domain"),
    "phone": ("", "last7"),
    "postcode": ("", "prefix"),
    "date": ("", "year"),
    "registered_id": ("", "valid"),
    "url": ("",),
    "code": ("",),
}
#: forms whose value is a list of tokens rather than one text
TOKEN_LIST_SUFFIXES = frozenset({"tokens"})
#: key functions of a blocking key expression, and whether each takes an integer argument (B.9.2)
KEY_FUNCTIONS: Mapping[str, bool] = {
    "metaphone": False,
    "soundex": False,
    "nysiis": False,
    "first": True,
    "last": True,
    "sorted_tokens": True,
    "id": False,
}
#: non-null levels per comparator (B.9.3); m and u carry one probability per level
COMPARATOR_LEVELS: Mapping[str, int] = {
    "exact": 2,
    "name": 4,
    "phonetic_name": 4,
    "levenshtein": 4,
    "token_set": 4,
    "date": 4,
    "identifier": 3,
    "phone": 3,
    "email": 3,
    "postcode": 3,
}
#: default thresholds per comparator (B.9.3); `ComparisonSpec.thresholds` overrides them, same length
COMPARATOR_THRESHOLDS: Mapping[str, tuple[float, ...]] = {
    "exact": (),
    "name": (0.95, 0.88),
    "phonetic_name": (0.88,),
    "levenshtein": (1.0, 2.0),
    "token_set": (0.90, 0.75),
    "date": (),
    "identifier": (),
    "phone": (),
    "email": (0.92,),
    "postcode": (),
}
#: the standardisers a comparator can read (it needs their match forms); absent = any
COMPARATOR_STANDARDISERS: Mapping[str, tuple[str, ...]] = {
    "phonetic_name": ("person_name",),
    "date": ("date",),
    "identifier": ("registered_id",),
    "phone": ("phone",),
    "email": ("email",),
    "postcode": ("postcode",),
}
#: the standardiser an attribute gets when its YAML names none
DEFAULT_STANDARDISER: Mapping[str, str] = {"text": "text", "date": "date"}  # every other type: "none"

_NAME = re.compile(NAME_RE)
_CODE = re.compile(CODE_RE)
_SCHEME = re.compile(SCHEME_RE)
_KEY_EXPR = re.compile(
    r"^\s*(?:(?P<fn>[a-z_]+)\(\s*(?P<arg>[a-z0-9_.]+)\s*(?:,\s*(?P<n>\d+)\s*)?\)|(?P<bare>[a-z0-9_.]+))\s*\Z"
)
_TOKEN_UNSAFE = re.compile(r"[^A-Za-z0-9_.:=/+\-]")
_PLACEHOLDER_PATTERN = re.compile(r"^(\*|\d{4})-(\*|\d{2})-(\*|\d{2})\Z")
_DISPLAY_FIELD = re.compile(r"\{([^{}]*)\}")


# --------------------------------------------------------------------------------------------- dataclasses


@dataclass(frozen=True, slots=True)
class PlaceholderSpec:
    """A date a source uses for "unknown": an exact `value` or a `pattern` such as `*-01-01`."""

    value: str | None = None  # "1900-01-01"
    pattern: str | None = None  # "*-01-01": any year, 1 January
    sources: tuple[str, ...] = ()  # empty = every source


@dataclass(frozen=True, slots=True)
class ReferenceSpec:
    """A reference attribute's target: published as a relationship, never as a column."""

    entity: str
    key_source: str  # the source system whose key the reference value carries
    relationship: str  # the relationship type, e.g. works_at


@dataclass(frozen=True, slots=True)
class Attribute:
    name: str
    type: str
    masking: str = "none"
    criticality: str = "normal"  # normal | critical
    standardise: str = "text"
    required: bool = False
    repeating: bool = False
    key: str | None = None  # repeating group key field
    fields: tuple[str, ...] = ()  # repeating group fields
    scheme: str | None = None  # registered_id: scheme name
    checksum: str | None = None  # registered_id: luhn | mod97 | mod11 | None
    strong: bool = False  # usable by hard rules; requires a checksum (a typo must not split or join people)
    code_list: str | None = None
    placeholders: tuple[PlaceholderSpec, ...] = ()  # dates a source uses for "unknown"
    end_date: bool = False  # an end date follows the source policy's end_date
    reference: ReferenceSpec | None = None  # type reference only; published as a relationship, never a column

    @property
    def personal(self) -> bool:
        return self.masking == "personal"

    @property
    def is_reference(self) -> bool:
        return self.type == "reference"

    def forms(self) -> tuple[str, ...]:
        """The match-form names this attribute gives (`a`, `a.metaphone`, …); none for groups and references."""
        if self.repeating or self.is_reference:
            return ()
        return tuple(
            self.name if not suffix else f"{self.name}.{suffix}" for suffix in MATCH_FORMS[self.standardise]
        )


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    new: str = "auto"
    update: str = "auto"
    critical_update: str = "auto"
    end_date: str = "auto"  # auto | hold
    master_id: str = (
        "hold"  # auto | hold: an arrival naming an active master ID links, or waits for a steward
    )

    def clause(self, system: str, field: str) -> str:
        """The clause an automated item cites: `crm.update=auto` (decision 16)."""
        return f"{system}.{field}={getattr(self, field)}"


@dataclass(frozen=True, slots=True)
class SourceSpec:
    system: str
    trust: int
    policy: SourcePolicy
    date_order: str = "ymd"  # ymd | dmy | mdy
    versioned: bool = False  # every row carries source_version, deletes included (landing interface)
    trust_by_attribute: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BlockingPass:
    name: str
    keys: tuple[str, ...]  # key expressions, B.9.2


@dataclass(frozen=True, slots=True)
class ComparisonSpec:
    name: str
    attribute: str
    comparator: str
    thresholds: tuple[float, ...] = ()  # override the comparator's default level thresholds
    m: tuple[float, ...] = ()  # one value per non-null level
    u: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class HardRule:
    kind: str  # must_link | cannot_link
    attribute: str  # a strong attribute


@dataclass(frozen=True, slots=True)
class Bands:
    upper: float
    lower: float  # 0 < lower < upper < 100


@dataclass(frozen=True, slots=True)
class MatchRules:
    version: int
    mode: str
    prior: float  # the global prior: P(two random records match)
    bands: Bands
    blocking: tuple[BlockingPass, ...]
    comparisons: tuple[ComparisonSpec, ...]
    hard_rules: tuple[HardRule, ...] = ()
    estimation: Mapping[str, Any] = field(default_factory=dict)

    def comparison(self, name: str) -> ComparisonSpec:
        for spec in self.comparisons:
            if spec.name == name:
                return spec
        raise NotFound("unknown_comparison", comparison=_token(name))


@dataclass(frozen=True, slots=True)
class SurvivorshipSpec:
    strategies: tuple[str, ...]
    group: str | None = None  # whole_group | keyed_union


@dataclass(frozen=True, slots=True)
class SurvivorshipRules:
    version: int
    default: tuple[str, ...]
    attributes: Mapping[str, SurvivorshipSpec]
    steward_rank: int = 0

    def strategies(self, attribute: str) -> tuple[str, ...]:
        spec = self.attributes.get(attribute)
        return spec.strategies if spec is not None else self.default


@dataclass(frozen=True, slots=True)
class ValidationRule:
    rule_id: str
    attribute: str
    kind: str
    dimension: str
    params: Mapping[str, Any] = field(default_factory=dict)
    severity: str = "warn"  # warn | hold


@dataclass(frozen=True, slots=True)
class ValidationRules:
    version: int
    rules: tuple[ValidationRule, ...]


@dataclass(frozen=True, slots=True)
class EntityModel:
    entity: str
    code: str
    domain: str
    style: str
    version: int
    display_name: str
    attributes: tuple[Attribute, ...]
    sources: tuple[SourceSpec, ...]
    match: MatchRules
    survivorship: SurvivorshipRules
    validation: ValidationRules
    ai_enabled: bool = False
    defaults: Mapping[str, Any] = field(default_factory=dict)  # e.g. {"calling_code": "999"}

    def attribute(self, name: str) -> Attribute:
        for attribute in self.attributes:
            if attribute.name == name:
                return attribute
        raise NotFound("unknown_attribute", entity=self.entity, attribute=_token(name))

    def source(self, system: str) -> SourceSpec:
        for source in self.sources:
            if source.system == system:
                return source
        raise NotFound("unknown_source", entity=self.entity, source=_token(system))

    def has_source(self, system: str) -> bool:
        return any(source.system == system for source in self.sources)

    def personal_attributes(self) -> tuple[str, ...]:
        return tuple(a.name for a in self.attributes if a.masking == "personal")

    def column_attributes(self) -> tuple[Attribute, ...]:
        """Every attribute except references, in model order: the entity table's value columns."""
        return tuple(a for a in self.attributes if a.type != "reference")

    def reference_attributes(self) -> tuple[Attribute, ...]:
        return tuple(a for a in self.attributes if a.type == "reference")

    def strong_attributes(self) -> tuple[Attribute, ...]:
        return tuple(a for a in self.attributes if a.strong)

    def trust(self, system: str, attribute: str) -> int:
        """The source's trust rank for one attribute (lower is more trusted)."""
        source = self.source(system)
        return source.trust_by_attribute.get(attribute, source.trust)

    def match_forms(self) -> frozenset[str]:
        """Every match-form name the model's attributes give; blocking keys and comparisons read these."""
        return frozenset(form for a in self.attributes for form in a.forms())

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> EntityModel:
        """A validated model; `ModelError` lists every problem (safe tokens `code:path`)."""
        parser = _Parser()
        model = parser.model(doc)
        if parser.problems or model is None:
            raise ModelError(parser.problems or ["bad_document:model"])
        return model

    def to_dict(self) -> dict[str, Any]:
        """A plain document (lists and dicts) that `from_dict` reads back to an equal model."""
        return _plain(asdict(self))


def check_compatible(old: EntityModel, new: EntityModel) -> list[str]:
    """Problems that stop `new` replacing `old`: attributes are only ever added, types never change, and a
    personal attribute stays personal (unmasking it would open `<p>_read`, stop vaulting it and put its
    values into prompts; no model version may do that on its own)."""
    problems: list[str] = []
    if old.entity != new.entity:
        problems.append(f"entity_changed:{_token(new.entity)}")
    if old.code != new.code:
        problems.append(f"code_changed:{_token(new.entity)}.code")
    new_attributes = {a.name: a for a in new.attributes}
    for before in old.attributes:
        after = new_attributes.get(before.name)
        where = f"{_token(old.entity)}.{_token(before.name)}"
        if after is None:
            problems.append(f"attribute_removed:{where}")
            continue
        if after.type != before.type:
            problems.append(f"type_changed:{where}")
        if after.repeating != before.repeating:
            problems.append(f"repeating_changed:{where}")
        if before.personal and not after.personal:
            problems.append(f"masking_relaxed:{where}")
    return problems


def split_key_expression(expression: str) -> tuple[str | None, str, int | None] | None:
    """(function, form, integer argument) of a blocking key expression, or None when it does not parse.

    Grammar: `FORM | fn(FORM[, INT])`. Only the syntax is checked here; `from_dict` checks that the
    function exists, takes the argument it is given, and reads a form the model gives.
    """
    found = _KEY_EXPR.match(expression) if isinstance(expression, str) else None
    if found is None:
        return None
    if found.group("bare") is not None:
        return None, found.group("bare"), None
    n = found.group("n")
    return found.group("fn"), found.group("arg"), int(n) if n is not None else None


# --------------------------------------------------------------------------------------------- helpers


def _token(value: Any) -> str:
    """A safe token for a problem path: characters outside the safe set become `_`, at most 60 long."""
    return _TOKEN_UNSAFE.sub("_", str(value))[:60]


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _iso_text(value: Any) -> Any:
    """YAML reads an unquoted date as a date: keep every date as ISO text in the model."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _valid_iso_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except (TypeError, ValueError):
        return False
    return True


class _Parser:
    """Reads a model document into dataclasses, collecting every problem rather than stopping at one."""

    MODEL_KEYS = frozenset(
        {
            "entity",
            "code",
            "domain",
            "style",
            "version",
            "display_name",
            "ai_enabled",
            "defaults",
            "attributes",
            "sources",
            "match",
            "survivorship",
            "validation",
        }
    )
    ATTRIBUTE_KEYS = frozenset(
        {
            "name",
            "type",
            "masking",
            "criticality",
            "standardise",
            "required",
            "repeating",
            "key",
            "fields",
            "scheme",
            "checksum",
            "strong",
            "code_list",
            "placeholders",
            "end_date",
            "reference",
        }
    )
    SOURCE_KEYS = frozenset({"system", "trust", "policy", "date_order", "versioned", "trust_by_attribute"})
    MATCH_KEYS = frozenset(
        {"version", "mode", "prior", "bands", "blocking", "comparisons", "hard_rules", "estimation"}
    )
    COMPARISON_KEYS = frozenset({"name", "attribute", "comparator", "thresholds", "m", "u"})
    SURVIVORSHIP_KEYS = frozenset({"version", "default", "attributes", "steward_rank"})
    VALIDATION_KEYS = frozenset({"version", "rules"})
    RULE_KEYS = frozenset({"rule_id", "attribute", "kind", "dimension", "params", "severity"})

    def __init__(self) -> None:
        self.problems: list[str] = []

    # -- bookkeeping

    def problem(self, code: str, *path: Any) -> None:
        text = f"{code}:{'.'.join(_token(p) for p in path)}"
        if text not in self.problems:
            self.problems.append(text[:120])

    def mapping(self, value: Any, *path: Any, allowed: frozenset[str] | None = None) -> Mapping[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            self.problem("not_a_mapping", *path)
            return {}
        if allowed is not None:
            for key in value:
                if key not in allowed:
                    self.problem("unknown_key", *path, key)
        return value

    def sequence(self, value: Any, *path: Any) -> Sequence[Any]:
        if value is None:
            return ()
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            self.problem("not_a_list", *path)
            return ()
        return value

    def text(self, doc: Mapping[str, Any], key: str, *path: Any, required: bool = True) -> str | None:
        value = doc.get(key)
        if value is None:
            if required:
                self.problem("missing", *path, key)
            return None
        if not isinstance(value, str) or not value.strip():
            self.problem("not_text", *path, key)
            return None
        return value

    def choice(
        self, doc: Mapping[str, Any], key: str, options: Sequence[str], default: str | None, *path: Any
    ) -> str:
        value = doc.get(key, default)
        if value is None:
            self.problem("missing", *path, key)
            return options[0]
        if value not in options:
            self.problem("bad_value", *path, key)
            return options[0] if default is None else default
        return value

    def flag(self, doc: Mapping[str, Any], key: str, *path: Any) -> bool:
        value = doc.get(key, False)
        if value is None:
            return False
        if not isinstance(value, bool):
            self.problem("not_boolean", *path, key)
            return False
        return value

    def integer(
        self, doc: Mapping[str, Any], key: str, *path: Any, default: int | None = None, minimum: int = 0
    ) -> int:
        value = doc.get(key, default)
        if value is None:
            self.problem("missing", *path, key)
            return minimum
        if not _is_int(value) or value < minimum:
            self.problem("bad_integer", *path, key)
            return minimum
        return value

    def name(self, value: Any, *path: Any, sql: bool = False) -> bool:
        """A lower-case name; with `sql`, also not a reserved word of either engine."""
        if not isinstance(value, str) or not _NAME.match(value):
            self.problem("bad_name", *path)
            return False
        if sql and value in SQL_RESERVED_WORDS:
            self.problem("reserved_word", *path)
            return False
        return True

    def probabilities(self, value: Any, count: int, *path: Any) -> tuple[float, ...]:
        items = self.sequence(value, *path)
        if not items:
            return ()
        if not all(_is_number(v) and 0.0 <= float(v) <= 1.0 for v in items):
            self.problem("bad_probability", *path)
            return ()
        if len(items) != count:
            self.problem("bad_length", *path)
            return ()
        if abs(sum(float(v) for v in items) - 1.0) > 0.001:
            self.problem("bad_sum", *path)
            return ()
        return tuple(float(v) for v in items)

    # -- the model

    def model(self, doc: Any) -> EntityModel | None:
        if not isinstance(doc, Mapping):
            self.problem("not_a_mapping", "model")
            return None
        self.mapping(doc, "model", allowed=self.MODEL_KEYS)
        entity = self.text(doc, "entity", "model") or "entity"
        if self.name(entity, "model", "entity", sql=True) and entity in RESERVED_TABLES:
            self.problem("reserved_table", "model", "entity")
        code = self.text(doc, "code", entity) or "X"
        if not _CODE.match(code):
            self.problem("bad_code", entity, "code")
        domain = self.text(doc, "domain", entity) or "domain"
        self.name(domain, entity, "domain")
        style = self.choice(doc, "style", STYLES, None, entity)
        version = self.integer(doc, "version", entity, default=1, minimum=1)
        ai_enabled = self.flag(doc, "ai_enabled", entity)
        defaults = self.defaults(doc.get("defaults"), entity)

        sources = self.sources(doc.get("sources"), entity)
        systems = {s.system for s in sources}
        attributes = self.attributes(doc.get("attributes"), entity, systems)
        by_name = {a.name: a for a in attributes}
        self.source_trust(sources, by_name, entity)

        display_name = self.text(doc, "display_name", entity) or ""
        for found in _DISPLAY_FIELD.findall(display_name):
            if found not in by_name:
                self.problem("unknown_attribute", entity, "display_name", found)

        match = self.match(doc.get("match"), entity, by_name)
        survivorship = self.survivorship(doc.get("survivorship"), entity, by_name)
        validation = self.validation(doc.get("validation"), entity, by_name)
        return EntityModel(
            entity=entity,
            code=code,
            domain=domain,
            style=style,
            version=version,
            display_name=display_name,
            attributes=attributes,
            sources=sources,
            match=match,
            survivorship=survivorship,
            validation=validation,
            ai_enabled=ai_enabled,
            defaults=defaults,
        )

    def defaults(self, value: Any, entity: str) -> dict[str, Any]:
        doc = self.mapping(value, entity, "defaults")
        out: dict[str, Any] = {}
        for key, item in doc.items():
            if not self.name(key, entity, "defaults", key):
                continue
            if isinstance(item, (dict, list, tuple)):
                self.problem("not_scalar", entity, "defaults", key)
                continue
            out[key] = _iso_text(item)
        calling_code = out.get("calling_code")
        if calling_code is not None and not re.fullmatch(r"\d{1,3}", str(calling_code)):
            self.problem("bad_calling_code", entity, "defaults", "calling_code")
        return out

    # -- sources

    def sources(self, value: Any, entity: str) -> tuple[SourceSpec, ...]:
        items = self.sequence(value, entity, "sources")
        if not items:
            self.problem("missing", entity, "sources")
        out: list[SourceSpec] = []
        seen: set[str] = set()
        for index, item in enumerate(items):
            doc = self.mapping(item, entity, "sources", index, allowed=self.SOURCE_KEYS)
            system = self.text(doc, "system", entity, "sources", index) or f"source{index}"
            path = (entity, "sources", system)
            self.name(system, *path, "system")
            if system in seen:
                self.problem("duplicate", *path)
            seen.add(system)
            trust = self.integer(doc, "trust", *path, minimum=0)
            policy_doc = self.mapping(doc.get("policy"), *path, "policy", allowed=frozenset(POLICY_FIELDS))
            policy_values = {}
            for policy_field in POLICY_FIELDS:
                policy_values[policy_field] = self.choice(
                    policy_doc,
                    policy_field,
                    POLICY_VALUES,
                    POLICY_DEFAULTS.get(policy_field, "auto"),
                    *path,
                    "policy",
                )
            date_order = self.choice(doc, "date_order", DATE_ORDERS, "ymd", *path)
            versioned = self.flag(doc, "versioned", *path)
            trust_doc = self.mapping(doc.get("trust_by_attribute"), *path, "trust_by_attribute")
            trust_by_attribute: dict[str, int] = {}
            for attribute, rank in trust_doc.items():
                if not _is_int(rank) or rank < 0:
                    self.problem("bad_integer", *path, "trust_by_attribute", attribute)
                    continue
                trust_by_attribute[str(attribute)] = rank
            out.append(
                SourceSpec(
                    system=system,
                    trust=trust,
                    policy=SourcePolicy(**policy_values),
                    date_order=date_order,
                    versioned=versioned,
                    trust_by_attribute=trust_by_attribute,
                )
            )
        return tuple(out)

    def source_trust(
        self, sources: Sequence[SourceSpec], by_name: Mapping[str, Attribute], entity: str
    ) -> None:
        for source in sources:
            for attribute in source.trust_by_attribute:
                if attribute not in by_name or by_name[attribute].is_reference:
                    self.problem(
                        "unknown_attribute", entity, "sources", source.system, "trust_by_attribute", attribute
                    )

    # -- attributes

    def attributes(self, value: Any, entity: str, systems: set[str]) -> tuple[Attribute, ...]:
        items = self.sequence(value, entity, "attributes")
        if not items:
            self.problem("missing", entity, "attributes")
        out: list[Attribute] = []
        seen: set[str] = set()
        for index, item in enumerate(items):
            attribute = self.attribute(item, entity, index, systems)
            if attribute is None:
                continue
            if attribute.name in seen:
                self.problem("duplicate", entity, "attributes", attribute.name)
            seen.add(attribute.name)
            out.append(attribute)
        return tuple(out)

    def attribute(self, item: Any, entity: str, index: int, systems: set[str]) -> Attribute | None:
        doc = self.mapping(item, entity, "attributes", index, allowed=self.ATTRIBUTE_KEYS)
        if not doc:
            return None
        name = self.text(doc, "name", entity, "attributes", index)
        if name is None:
            return None
        path = (entity, "attributes", name if isinstance(name, str) and _NAME.match(name) else index)
        if self.name(name, *path, "name", sql=True):
            if name in RESERVED_COLUMNS:
                self.problem("reserved_column", *path, "name")
        type_ = self.choice(doc, "type", ATTRIBUTE_TYPES, None, *path)
        masking = self.choice(doc, "masking", MASKING, "none", *path)
        criticality = self.choice(doc, "criticality", CRITICALITY, "normal", *path)
        default_standardiser = DEFAULT_STANDARDISER.get(type_, "none")
        standardise = self.choice(doc, "standardise", STANDARDISERS, default_standardiser, *path)
        required = self.flag(doc, "required", *path)
        repeating = self.flag(doc, "repeating", *path)
        strong = self.flag(doc, "strong", *path)
        end_date = self.flag(doc, "end_date", *path)

        key = doc.get("key")
        fields_value = self.sequence(doc.get("fields"), *path, "fields")
        group_fields: list[str] = []
        for group_field in fields_value:
            if self.name(group_field, *path, "fields"):
                group_fields.append(group_field)
        if repeating:
            if type_ != "json":
                self.problem("repeating_needs_json", *path)
            if not group_fields:
                self.problem("missing", *path, "fields")
            if key is not None and key not in group_fields:
                self.problem("key_not_a_field", *path, "key")
        elif key is not None or group_fields:
            self.problem("fields_without_repeating", *path)

        scheme = doc.get("scheme")
        checksum = doc.get("checksum")
        if standardise == "registered_id":
            if not isinstance(scheme, str) or not _SCHEME.match(scheme):
                self.problem("bad_scheme", *path, "scheme")
            if checksum is not None and checksum not in CHECKSUMS:
                self.problem("bad_value", *path, "checksum")
        elif scheme is not None or checksum is not None:
            self.problem("scheme_without_registered_id", *path)
        if strong and (checksum is None or standardise != "registered_id"):
            self.problem("strong_needs_checksum", *path, "strong")

        code_list = doc.get("code_list")
        if code_list is not None:
            self.name(code_list, *path, "code_list")

        placeholders: list[PlaceholderSpec] = []
        for p_index, p_item in enumerate(self.sequence(doc.get("placeholders"), *path, "placeholders")):
            placeholder = self.placeholder(p_item, systems, *path, "placeholders", p_index)
            if placeholder is not None:
                placeholders.append(placeholder)
        if placeholders and type_ != "date":
            self.problem("placeholders_need_date", *path)
        if end_date and type_ != "date":
            self.problem("end_date_needs_date", *path)

        reference = None
        reference_doc = doc.get("reference")
        if type_ == "reference":
            if reference_doc is None:
                self.problem("missing", *path, "reference")
            else:
                reference = self.reference(reference_doc, *path, "reference")
        elif reference_doc is not None:
            self.problem("reference_without_type", *path)

        return Attribute(
            name=name,
            type=type_,
            masking=masking,
            criticality=criticality,
            standardise=standardise,
            required=required,
            repeating=repeating,
            key=key if isinstance(key, str) else None,
            fields=tuple(group_fields),
            scheme=scheme if isinstance(scheme, str) else None,
            checksum=checksum if isinstance(checksum, str) else None,
            strong=strong,
            code_list=code_list if isinstance(code_list, str) else None,
            placeholders=tuple(placeholders),
            end_date=end_date,
            reference=reference,
        )

    def placeholder(self, item: Any, systems: set[str], *path: Any) -> PlaceholderSpec | None:
        doc = self.mapping(item, *path, allowed=frozenset({"value", "pattern", "sources"}))
        value = _iso_text(doc.get("value"))
        pattern = doc.get("pattern")
        if (value is None) == (pattern is None):
            self.problem("value_or_pattern", *path)
            return None
        if value is not None and (not isinstance(value, str) or not _valid_iso_date(value)):
            self.problem("bad_date", *path, "value")
        if pattern is not None and (not isinstance(pattern, str) or not _PLACEHOLDER_PATTERN.match(pattern)):
            self.problem("bad_pattern", *path, "pattern")
        sources = []
        for system in self.sequence(doc.get("sources"), *path, "sources"):
            if system not in systems:
                self.problem("unknown_source", *path, "sources", system)
            else:
                sources.append(system)
        return PlaceholderSpec(value=value, pattern=pattern, sources=tuple(sources))

    def reference(self, value: Any, *path: Any) -> ReferenceSpec | None:
        doc = self.mapping(value, *path, allowed=frozenset({"entity", "key_source", "relationship"}))
        entity = self.text(doc, "entity", *path)
        key_source = self.text(doc, "key_source", *path)
        relationship = self.text(doc, "relationship", *path)
        if entity is None or key_source is None or relationship is None:
            return None
        self.name(entity, *path, "entity")
        self.name(key_source, *path, "key_source")
        self.name(relationship, *path, "relationship")
        return ReferenceSpec(entity=entity, key_source=key_source, relationship=relationship)

    # -- match rules

    def match(self, value: Any, entity: str, by_name: Mapping[str, Attribute]) -> MatchRules:
        path = (entity, "match")
        doc = self.mapping(value, *path, allowed=self.MATCH_KEYS)
        if not doc:
            self.problem("missing", *path)
        version = self.integer(doc, "version", *path, minimum=1)
        mode = self.choice(doc, "mode", MODES, None, *path)
        prior = doc.get("prior")
        if not _is_number(prior) or not 0.0 < float(prior) < 1.0:
            self.problem("bad_prior", *path, "prior")
            prior = 0.001
        bands_doc = self.mapping(doc.get("bands"), *path, "bands", allowed=frozenset({"upper", "lower"}))
        upper, lower = bands_doc.get("upper"), bands_doc.get("lower")
        if not (_is_number(upper) and _is_number(lower) and 0 < float(lower) < float(upper) < 100):
            self.problem("bad_bands", *path, "bands")
            upper, lower = 90.0, 60.0
        forms = frozenset(form for a in by_name.values() for form in a.forms())

        blocking: list[BlockingPass] = []
        seen_passes: set[str] = set()
        for index, item in enumerate(self.sequence(doc.get("blocking"), *path, "blocking")):
            pass_doc = self.mapping(item, *path, "blocking", index, allowed=frozenset({"name", "keys"}))
            name = self.text(pass_doc, "name", *path, "blocking", index) or f"pass{index}"
            self.name(name, *path, "blocking", index, "name")
            if name in seen_passes:
                self.problem("duplicate", *path, "blocking", name)
            seen_passes.add(name)
            keys = [k for k in self.sequence(pass_doc.get("keys"), *path, "blocking", name, "keys")]
            if not keys:
                self.problem("missing", *path, "blocking", name, "keys")
            for k_index, expression in enumerate(keys):
                self.key_expression(expression, forms, by_name, *path, "blocking", name, "keys", k_index)
            blocking.append(BlockingPass(name=name, keys=tuple(str(k) for k in keys)))
        if not blocking:
            self.problem("missing", *path, "blocking")

        comparisons: list[ComparisonSpec] = []
        seen_comparisons: set[str] = set()
        for index, item in enumerate(self.sequence(doc.get("comparisons"), *path, "comparisons")):
            spec = self.comparison(item, by_name, *path, "comparisons", index)
            if spec is None:
                continue
            if spec.name in seen_comparisons:
                self.problem("duplicate", *path, "comparisons", spec.name)
            seen_comparisons.add(spec.name)
            comparisons.append(spec)
        if not comparisons:
            self.problem("missing", *path, "comparisons")

        hard_rules: list[HardRule] = []
        for index, item in enumerate(self.sequence(doc.get("hard_rules"), *path, "hard_rules")):
            rule_doc = self.mapping(
                item, *path, "hard_rules", index, allowed=frozenset({"kind", "attribute"})
            )
            kind = self.choice(rule_doc, "kind", HARD_RULE_KINDS, None, *path, "hard_rules", index)
            attribute = self.text(rule_doc, "attribute", *path, "hard_rules", index)
            if attribute is None:
                continue
            target = by_name.get(attribute)
            if target is None:
                self.problem("unknown_attribute", *path, "hard_rules", index, "attribute")
                continue
            if not target.strong:
                self.problem("hard_rule_not_strong", *path, "hard_rules", attribute)
            hard_rules.append(HardRule(kind=kind, attribute=attribute))

        estimation = self.mapping(doc.get("estimation"), *path, "estimation")
        return MatchRules(
            version=version,
            mode=mode,
            prior=float(prior),
            bands=Bands(upper=float(upper), lower=float(lower)),
            blocking=tuple(blocking),
            comparisons=tuple(comparisons),
            hard_rules=tuple(hard_rules),
            estimation=_plain(dict(estimation)),
        )

    def key_expression(
        self, expression: Any, forms: frozenset[str], by_name: Mapping[str, Attribute], *path: Any
    ) -> None:
        parts = split_key_expression(expression) if isinstance(expression, str) else None
        if parts is None:
            self.problem("bad_key", *path)
            return
        function, form, number = parts
        if form not in forms:
            self.problem("unknown_form", *path)
            return
        is_token_list = "." in form and form.split(".", 1)[1] in TOKEN_LIST_SUFFIXES
        if function is None:
            if is_token_list:
                self.problem("token_list_needs_function", *path)
            return
        if function not in KEY_FUNCTIONS:
            self.problem("unknown_function", *path)
            return
        if KEY_FUNCTIONS[function] != (number is not None):
            self.problem("bad_argument", *path)
        if function == "id":
            attribute = by_name.get(form)
            if attribute is None or attribute.standardise != "registered_id":
                self.problem("id_needs_registered_id", *path)
        elif function == "sorted_tokens":
            if not is_token_list:
                self.problem("sorted_tokens_needs_tokens", *path)
        elif is_token_list:
            self.problem("token_list_needs_sorted_tokens", *path)

    def comparison(self, item: Any, by_name: Mapping[str, Attribute], *path: Any) -> ComparisonSpec | None:
        doc = self.mapping(item, *path, allowed=self.COMPARISON_KEYS)
        name = self.text(doc, "name", *path)
        attribute_name = self.text(doc, "attribute", *path)
        if name is None or attribute_name is None:
            return None
        path = (*path[:-1], name)
        self.name(name, *path, "name")
        comparator = self.choice(doc, "comparator", COMPARATORS, None, *path)
        attribute = by_name.get(attribute_name)
        if attribute is None:
            self.problem("unknown_attribute", *path, "attribute")
        elif attribute.is_reference or attribute.repeating:
            self.problem("not_comparable", *path, "attribute")
        elif (
            comparator in COMPARATOR_STANDARDISERS
            and attribute.standardise not in COMPARATOR_STANDARDISERS[comparator]
        ):
            self.problem("comparator_needs_standardiser", *path, "comparator")
        thresholds_value = self.sequence(doc.get("thresholds"), *path, "thresholds")
        thresholds: tuple[float, ...] = ()
        if thresholds_value:
            if not all(_is_number(v) for v in thresholds_value):
                self.problem("bad_threshold", *path, "thresholds")
            elif len(thresholds_value) != len(COMPARATOR_THRESHOLDS[comparator]):
                self.problem("bad_length", *path, "thresholds")
            else:
                thresholds = tuple(float(v) for v in thresholds_value)
        levels = COMPARATOR_LEVELS[comparator]
        m = self.probabilities(doc.get("m"), levels, *path, "m")
        u = self.probabilities(doc.get("u"), levels, *path, "u")
        return ComparisonSpec(
            name=name, attribute=attribute_name, comparator=comparator, thresholds=thresholds, m=m, u=u
        )

    # -- survivorship

    def survivorship(self, value: Any, entity: str, by_name: Mapping[str, Attribute]) -> SurvivorshipRules:
        path = (entity, "survivorship")
        doc = self.mapping(value, *path, allowed=self.SURVIVORSHIP_KEYS)
        if not doc:
            self.problem("missing", *path)
        version = self.integer(doc, "version", *path, minimum=1)
        default = self.strategies(doc.get("default"), *path, "default")
        steward_rank = self.integer(doc, "steward_rank", *path, default=0, minimum=0)
        attributes: dict[str, SurvivorshipSpec] = {}
        attributes_doc = self.mapping(doc.get("attributes"), *path, "attributes")
        for name, item in attributes_doc.items():
            spec_path = (*path, "attributes", name)
            spec_doc = self.mapping(item, *spec_path, allowed=frozenset({"strategies", "group"}))
            attribute = by_name.get(name)
            if attribute is None or attribute.is_reference:
                self.problem("unknown_attribute", *spec_path)
                continue
            strategies = self.strategies(spec_doc.get("strategies"), *spec_path, "strategies")
            group = spec_doc.get("group")
            if group is not None:
                if not attribute.repeating:
                    self.problem("group_without_repeating", *spec_path, "group")
                elif group not in GROUP_MODES:
                    self.problem("bad_value", *spec_path, "group")
                elif group == "keyed_union" and attribute.key is None:
                    self.problem("keyed_union_needs_key", *spec_path, "group")
            attributes[name] = SurvivorshipSpec(
                strategies=strategies, group=group if isinstance(group, str) else None
            )
        return SurvivorshipRules(
            version=version, default=default, attributes=attributes, steward_rank=steward_rank
        )

    def strategies(self, value: Any, *path: Any) -> tuple[str, ...]:
        items = self.sequence(value, *path)
        if not items:
            self.problem("missing", *path)
            return ("source_trust",)
        out = []
        for item in items:
            if item not in STRATEGIES:
                self.problem("bad_value", *path)
            else:
                out.append(item)
        return tuple(out) or ("source_trust",)

    # -- validation

    def validation(self, value: Any, entity: str, by_name: Mapping[str, Attribute]) -> ValidationRules:
        path = (entity, "validation")
        doc = self.mapping(value, *path, allowed=self.VALIDATION_KEYS)
        if not doc:
            self.problem("missing", *path)
        version = self.integer(doc, "version", *path, minimum=1)
        rules: list[ValidationRule] = []
        seen: set[str] = set()
        for index, item in enumerate(self.sequence(doc.get("rules"), *path, "rules")):
            rule = self.rule(item, by_name, *path, "rules", index)
            if rule is None:
                continue
            if rule.rule_id in seen:
                self.problem("duplicate", *path, "rules", rule.rule_id)
            seen.add(rule.rule_id)
            rules.append(rule)
        return ValidationRules(version=version, rules=tuple(rules))

    def rule(self, item: Any, by_name: Mapping[str, Attribute], *path: Any) -> ValidationRule | None:
        doc = self.mapping(item, *path, allowed=self.RULE_KEYS)
        rule_id = self.text(doc, "rule_id", *path)
        attribute_name = self.text(doc, "attribute", *path)
        if rule_id is None or attribute_name is None:
            return None
        if _TOKEN_UNSAFE.search(rule_id) or len(rule_id) > 40:
            self.problem("bad_rule_id", *path, "rule_id")
        path = (*path[:-1], rule_id)
        kind = self.choice(doc, "kind", RULE_KINDS, None, *path)
        dimension = self.choice(doc, "dimension", DIMENSIONS, None, *path)
        severity = self.choice(doc, "severity", SEVERITIES, "warn", *path)
        params = {str(k): _iso_text(v) for k, v in self.mapping(doc.get("params"), *path, "params").items()}
        attribute = by_name.get(attribute_name)
        if attribute is None or attribute.is_reference:
            self.problem("unknown_attribute", *path, "attribute")
            return None
        if kind == "pattern" and params.get("pattern") not in PATTERN_NAMES:
            self.problem("unknown_pattern", *path, "params", "pattern")
        elif kind == "range":
            if "min" not in params and "max" not in params:
                self.problem("missing", *path, "params", "min")
            for bound in ("min", "max"):
                if bound in params and not self.range_bound(params[bound]):
                    self.problem("bad_bound", *path, "params", bound)
        elif kind == "code_list":
            name = params.get("code_list", attribute.code_list)
            if name is None:
                self.problem("missing", *path, "params", "code_list")
            else:
                self.name(name, *path, "params", "code_list")
        elif kind == "checksum" and attribute.standardise != "registered_id":
            self.problem("checksum_needs_registered_id", *path)
        elif kind == "placeholder" and attribute.type != "date":
            self.problem("placeholder_needs_date", *path)
        return ValidationRule(
            rule_id=rule_id,
            attribute=attribute_name,
            kind=kind,
            dimension=dimension,
            params=params,
            severity=severity,
        )

    @staticmethod
    def range_bound(value: Any) -> bool:
        return _is_number(value) or value == "today" or (isinstance(value, str) and _valid_iso_date(value))
