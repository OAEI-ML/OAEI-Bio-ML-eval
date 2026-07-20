"""Native pyHermiT/pyELK adapters over one retained shared ontology view.

The sibling reasoner facades are imported only when classification is requested.
This keeps the dependency-free metric surface importable while using only the
released public facades of the sibling reasoner distributions.  An absent or
incompatible facade fails explicitly instead of substituting a private reasoner.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import math
import multiprocessing
import re
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, TypeAlias, cast

if TYPE_CHECKING:
    from pyowl_core import OntologyView
else:
    OntologyView = object

from .bridge import named_class_iris
from .reasoner import CoherenceReasoner, UnsatResult

_VERSION = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)"
    r"(?:(?:a|b|rc)\d+|(?:\.dev|\.post)\d+|[.+-].*)?$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_REASONER_LINE = (0, 1)
_MAX_WORKER_RESPONSE_BYTES = 16 * 1024 * 1024
_WORKER_SHUTDOWN_SECONDS = 1.0

# Frozen pyowl-core encoded-view v1 compatibility fence.  OAEI never requests or
# inspects these buffers; it only validates an optional public reasoner attestation.
_ENCODED_SCHEMA_NAME = "pyowl-core/structural-columns"
_ENCODED_SCHEMA_VERSION = 1
_ENCODED_MODEL_SCHEMA = 1
_ENCODED_DESCRIPTOR_SHA256 = (
    "9ad29db6a7e616f65cea2957bc5ba8d1f9b99ef0eb1fe1432c09be25786267b5"
)
_ENCODED_BUFFER_WIDTHS = {
    "field_kinds": 1,
    "field_lengths": 8,
    "field_values": 8,
    "item_kinds": 1,
    "item_lengths": 8,
    "item_values": 8,
    "node_field_offsets": 8,
    "node_tags": 2,
    "root_ids": 4,
    "root_kinds": 1,
    "scalar_bytes": 1,
}
_COMPILER_HANDOFF_FIELDS = frozenset(
    {
        "buffer_widths",
        "descriptor_sha256",
        "model_schema",
        "schema_name",
        "schema_version",
    }
)
_INGESTION_PATHS = frozenset({"scalar-python", "scalar-native", "scalar-wire", "encoded-native"})
_COMPILER_COUNTERS = frozenset(
    {
        "base_flattening_bytes",
        "encoded_buffer_count",
        "encoded_buffer_bytes",
        "encoded_zero_copy_buffers",
        "encoded_detached_buffer_count",
        "encoded_indexed_buffer_count",
        "encoded_staging_copy_bytes",
        "encoded_private_ir_bytes",
        "encoded_segment_count",
        "encoded_referenced_view_count",
        "encoded_posting_bytes",
        "encoded_compiler_gil_released",
        "materialized_scalar_rows",
        "parser_calls",
        "per_row_ffi_calls",
        "resolver_calls",
        "scalar_axiom_materializations",
        "scalar_term_materializations",
        "structural_copy_bytes",
        "wire_decoder_calls",
        "wire_encoder_calls",
    }
)
_COMPILER_TIMINGS = frozenset(
    {
        "consumer_compile_seconds",
        "encoded_compiler_seconds",
        "encoded_native_boundary_seconds",
        "encoded_session_build_seconds",
        "encoded_validation_seconds",
        "encoded_view_publication_seconds",
    }
)
_COMPILER_SCHEMA_FIELDS = frozenset(
    {
        "compiler_cache_schema_version",
        "ir_schema_version",
        "native_abi_version",
    }
)
_MISSING = object()

JsonValue: TypeAlias = object


class NativeReasonerError(RuntimeError):
    """Base error for the OAEI native adapter boundary."""


class NativeReasonerUnavailableError(NativeReasonerError, ImportError):
    """A required sibling native reasoner package is not installed."""


class NativeReasonerCompatibilityError(NativeReasonerError):
    """An installed sibling package does not expose the frozen public facade."""


class NativeWorkerError(NativeReasonerError):
    """The bounded pyELK worker failed without producing a semantic result."""


class HermiTTimeoutError(TimeoutError):
    """Only the pyHermiT cooperative timeout maps to the ELK fallback gate."""

    def __init__(
        self,
        message: str,
        *,
        elapsed_seconds: float = 0.0,
        attempt: Mapping[str, JsonValue] | None = None,
    ) -> None:
        self.elapsed_seconds = elapsed_seconds
        self.attempt = dict(attempt or {})
        super().__init__(message)


class ELKTimeoutError(TimeoutError):
    """A bounded pyELK wire worker exceeded its wall-clock limit."""


@dataclass(frozen=True, slots=True)
class _HermiTAPI:
    module: ModuleType
    version: str
    reasoner_type: type[Any]
    config_type: type[Any]
    inconsistent_error: type[BaseException]
    timeout_error: type[BaseException]


@dataclass(frozen=True, slots=True)
class _ELKAPI:
    module: ModuleType
    version: str
    reasoner_type: type[Any]
    config_type: type[Any]


@dataclass(frozen=True, slots=True)
class CoreWireEnvelope:
    """Core wire bytes plus semantic fingerprints verified by the child."""

    payload: bytes
    fingerprints: Mapping[str, str]
    wire_version: tuple[int, int]
    core_version: str


@dataclass(frozen=True, slots=True)
class ELKWorkerResult:
    """Small JSON-safe result returned by a terminable worker."""

    unsatisfiable: tuple[str, ...]
    inconsistent: bool
    reasoner: Mapping[str, JsonValue]
    profile: Mapping[str, JsonValue]
    wire_verified: bool
    mmap_verified: bool
    owl_parse_count: int


class HermiTReasoner(CoherenceReasoner):
    """DL adapter for the frozen pyHermiT ``Reasoner`` public facade."""

    name = "hermit"
    def unsatisfiable_classes_view(
        self,
        ontology: object,
        *,
        which: str,
        timeout_s: float | None,
    ) -> UnsatResult:
        if which != "hermit":
            raise ValueError("HermiTReasoner only accepts which='hermit'")
        _validate_timeout(timeout_s)
        api = _load_pyhermit_api()
        started = time.perf_counter()
        session: Any | None = None
        backend: dict[str, JsonValue] = {}
        try:
            config = api.config_type(timeout=timeout_s)
            session = api.reasoner_type(ontology, config=config)
            _require_identity(session, ontology, "pyHermiT")
            backend = _backend_metadata(session, api.version)
            try:
                consistent = session.is_consistent()
                if not isinstance(consistent, bool):
                    raise NativeReasonerCompatibilityError(
                        "pyHermiT Reasoner.is_consistent() must return bool"
                    )
                if consistent:
                    values = session.unsatisfiable_classes()
                    unsatisfiable = _entity_iris(values)
                    inconsistent = False
                else:
                    unsatisfiable = named_class_iris(cast(OntologyView, ontology))
                    inconsistent = True
            except api.inconsistent_error:
                # HermiT deliberately raises for ordinary queries over an
                # inconsistent ontology. Coherence uses classical explosion.
                unsatisfiable = named_class_iris(cast(OntologyView, ontology))
                inconsistent = True
        except api.timeout_error as error:
            elapsed = time.perf_counter() - started
            raise HermiTTimeoutError(
                f"pyHermiT classification exceeded {timeout_s}s",
                elapsed_seconds=elapsed,
                attempt={
                    "status": "timeout",
                    "package": "pyHermiT",
                    "package_version": api.version,
                    "backend": backend,
                    "transport": {"mode": "in-process-identity"},
                    "elapsed_seconds": elapsed,
                },
            ) from error
        finally:
            if session is not None:
                session.dispose()
        return UnsatResult(
            unsatisfiable=tuple(sorted(unsatisfiable)),
            reasoner_used="hermit",
            elapsed_seconds=time.perf_counter() - started,
            provenance={
                "schema": "native-reasoner-provenance/1",
                "package": "pyHermiT",
                "package_version": api.version,
                "backend": backend,
                "transport": "in-process-identity",
                "profile": {"complete": True, "reasons": []},
            },
            inconsistent=inconsistent,
        )


class ELKReasoner(CoherenceReasoner):
    """OWL 2 EL adapter; bounded calls cross a verified core mmap worker."""

    name = "elk"
    def unsatisfiable_classes_view(
        self,
        ontology: object,
        *,
        which: str,
        timeout_s: float | None,
    ) -> UnsatResult:
        if which != "elk":
            raise ValueError("ELKReasoner only accepts which='elk'")
        _validate_timeout(timeout_s)
        # Validate the installed facade in the parent before paying wire cost or
        # creating a process. The worker repeats the check independently.
        api = _load_pyelk_api()
        started = time.perf_counter()
        if timeout_s is None:
            raw = _classify_elk_identity(ontology, api=api)
            transport: dict[str, JsonValue] = {
                "mode": "in-process-identity",
                "wire_verified": False,
                "owl_parse_count": 0,
            }
        else:
            envelope = _encode_core_wire(ontology)
            outcome = _run_elk_worker(envelope, timeout_s=timeout_s)
            if (
                not outcome.wire_verified
                or not outcome.mmap_verified
                or outcome.owl_parse_count != 0
            ):
                raise NativeWorkerError(
                    "pyELK worker did not prove verified core mmap with zero OWL parses"
                )
            raw = {
                "unsatisfiable": list(outcome.unsatisfiable),
                "inconsistent": outcome.inconsistent,
                "reasoner": dict(outcome.reasoner),
                "profile": dict(outcome.profile),
            }
            transport = {
                "mode": "core-wire-worker",
                "wire_verified": outcome.wire_verified,
                "mmap_verified": outcome.mmap_verified,
                "owl_parse_count": outcome.owl_parse_count,
                "wire_bytes": len(envelope.payload),
                "wire_sha256": hashlib.sha256(envelope.payload).hexdigest(),
                "wire_version": list(envelope.wire_version),
                "wire_core_version": envelope.core_version,
            }
        inconsistent = _require_bool(raw.get("inconsistent"), "pyELK inconsistent result")
        unsatisfiable = (
            named_class_iris(cast(OntologyView, ontology))
            if inconsistent
            else _string_tuple(raw.get("unsatisfiable"), "pyELK unsatisfiable classes")
        )
        reasoner = _require_mapping(raw.get("reasoner"), "pyELK reasoner metadata")
        profile = _require_mapping(raw.get("profile"), "pyELK profile metadata")
        return UnsatResult(
            unsatisfiable=tuple(sorted(set(unsatisfiable))),
            reasoner_used="elk",
            elapsed_seconds=time.perf_counter() - started,
            provenance={
                "schema": "native-reasoner-provenance/1",
                "package": "pyELK",
                "package_version": str(reasoner.get("package_version", "unknown")),
                "backend": dict(reasoner),
                "transport": transport,
                "profile": dict(profile),
            },
            inconsistent=inconsistent,
        )


class NativeReasoner(CoherenceReasoner):
    """Small native dispatcher retaining two independently testable adapters."""

    name = "native"
    def __init__(
        self,
        *,
        hermit: CoherenceReasoner | None = None,
        elk: CoherenceReasoner | None = None,
    ) -> None:
        self._hermit = hermit
        self._elk = elk

    def unsatisfiable_classes_view(
        self,
        ontology: object,
        *,
        which: str,
        timeout_s: float | None,
    ) -> UnsatResult:
        if which == "hermit":
            adapter = self._hermit
            if adapter is None:
                adapter = self._hermit = HermiTReasoner()
        elif which == "elk":
            adapter = self._elk
            if adapter is None:
                adapter = self._elk = ELKReasoner()
        else:
            raise ValueError(f"reasoner must be 'hermit' or 'elk', got {which!r}.")
        return adapter.unsatisfiable_classes_view(
            ontology,
            which=which,
            timeout_s=timeout_s,
        )


def _load_pyhermit_api() -> _HermiTAPI:
    module = _import_reasoner("pyhermit", "pyHermiT")
    required = (
        "Reasoner",
        "ReasonerConfig",
        "InconsistentOntologyError",
        "ReasonerTimeoutError",
    )
    _require_exports(module, "pyHermiT", required)
    timeout_error = module.ReasonerTimeoutError
    inconsistent_error = module.InconsistentOntologyError
    if not isinstance(timeout_error, type) or not issubclass(timeout_error, TimeoutError):
        raise NativeReasonerCompatibilityError(
            "pyHermiT ReasonerTimeoutError must inherit TimeoutError"
        )
    if not isinstance(inconsistent_error, type) or not issubclass(
        inconsistent_error, BaseException
    ):
        raise NativeReasonerCompatibilityError(
            "pyHermiT InconsistentOntologyError must be an exception class"
        )
    return _HermiTAPI(
        module=module,
        version=_reasoner_version(module, "pyHermiT"),
        reasoner_type=module.Reasoner,
        config_type=module.ReasonerConfig,
        inconsistent_error=inconsistent_error,
        timeout_error=timeout_error,
    )


def _load_pyelk_api() -> _ELKAPI:
    module = _import_reasoner("pyelk", "pyELK")
    _require_exports(module, "pyELK", ("Reasoner", "ReasonerConfig"))
    return _ELKAPI(
        module=module,
        version=_reasoner_version(module, "pyelk-reasoner"),
        reasoner_type=module.Reasoner,
        config_type=module.ReasonerConfig,
    )


def _import_reasoner(module_name: str, display_name: str) -> ModuleType:
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name != module_name:
            raise
        raise NativeReasonerUnavailableError(
            f"{display_name} is required for native coherence; install the reasoner extra"
        ) from error
    if not isinstance(module, ModuleType):
        raise NativeReasonerCompatibilityError(
            f"{display_name} import did not return a Python module"
        )
    return module


def _require_exports(module: ModuleType, display_name: str, names: tuple[str, ...]) -> None:
    missing = tuple(name for name in names if not hasattr(module, name))
    if missing:
        raise NativeReasonerCompatibilityError(
            f"installed {display_name} lacks the frozen public facade: "
            + ", ".join(missing)
        )


def _reasoner_version(module: ModuleType, distribution: str) -> str:
    value = getattr(module, "__version__", None)
    if not isinstance(value, str) or not value:
        try:
            value = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError as error:
            raise NativeReasonerCompatibilityError(
                f"installed {distribution} exposes no package version"
            ) from error
    match = _VERSION.fullmatch(value)
    if match is None or tuple(map(int, match.groups()[:2])) != _EXPECTED_REASONER_LINE:
        raise NativeReasonerCompatibilityError(
            f"incompatible {distribution} version {value!r}; expected >=0.1,<0.2"
        )
    return value


def _require_identity(session: Any, ontology: object, display_name: str) -> None:
    if not hasattr(session, "ontology"):
        raise NativeReasonerCompatibilityError(
            f"{display_name} Reasoner lacks the public ontology property"
        )
    if session.ontology is not ontology:
        raise NativeReasonerCompatibilityError(
            f"{display_name} did not retain the supplied OntologyView by identity"
        )


def _entity_iris(values: object) -> tuple[str, ...]:
    try:
        entities: tuple[object, ...] = tuple(cast(Iterable[object], values))
    except TypeError as error:
        raise NativeReasonerCompatibilityError(
            "native reasoner unsatisfiable classes must be iterable"
        ) from error
    iris: set[str] = set()
    for entity in entities:
        iri = getattr(entity, "iri", None)
        value = getattr(iri, "value", None)
        if not isinstance(value, str):
            raise NativeReasonerCompatibilityError(
                "native reasoner returned a class without pyowl_core Class.iri.value"
            )
        if value not in {
            "http://www.w3.org/2002/07/owl#Thing",
            "http://www.w3.org/2002/07/owl#Nothing",
        }:
            iris.add(value)
    return tuple(sorted(iris))


def _backend_metadata(session: Any, package_version: str) -> dict[str, JsonValue]:
    backend = getattr(session, "backend", None)
    if backend is None:
        raise NativeReasonerCompatibilityError(
            "native Reasoner lacks the public backend property"
        )
    metadata: dict[str, JsonValue] = {"package_version": package_version}
    for name in (
        "name",
        "implementation_version",
        "ir_schema_version",
        "ir_major",
        "ir_minor",
        "core_package_version",
        "core_model_schema_version",
        "core_adapter_protocol_version",
        "accelerated",
        "native_available",
        "effective_workers",
        "requested_workers",
        "fallback_reason",
    ):
        value = getattr(backend, name, None)
        if isinstance(value, (str, int, float, bool)):
            metadata[name] = value
        elif name == "fallback_reason" and value is None:
            metadata[name] = None
    features = getattr(backend, "complete_features", None)
    if features is not None:
        metadata["complete_features"] = sorted(str(item) for item in features)
    for name in ("core_api_version", "core_wire_format_version"):
        value = getattr(backend, name, None)
        if (
            isinstance(value, tuple)
            and len(value) == 2
            and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        ):
            metadata[name] = list(value)
    compiler_handoff = getattr(backend, "compiler_handoff", _MISSING)
    if compiler_handoff is not _MISSING:
        metadata["compiler_handoff"] = _validate_compiler_handoff(compiler_handoff)
    compiler_diagnostics = _compiler_diagnostics(session)
    if compiler_diagnostics is not None:
        metadata["compiler_diagnostics"] = compiler_diagnostics
    return metadata


def _compiler_diagnostics(session: Any) -> dict[str, JsonValue] | None:
    """Capture only frozen public compiler diagnostics from a reasoner facade."""

    provider = getattr(session, "diagnostics", None)
    if provider is None:
        return None
    if not callable(provider):
        raise NativeReasonerCompatibilityError(
            "native Reasoner diagnostics must be callable when present"
        )
    values = provider()
    if not isinstance(values, Mapping) or not all(isinstance(name, str) for name in values):
        raise NativeReasonerCompatibilityError(
            "native Reasoner diagnostics must be a string-keyed mapping"
        )
    ingestion_path = values.get("ingestion_path")
    if ingestion_path is None:
        return None
    if ingestion_path not in _INGESTION_PATHS:
        raise NativeReasonerCompatibilityError(
            "native Reasoner ingestion_path diagnostic is incompatible"
        )
    result: dict[str, JsonValue] = {"ingestion_path": ingestion_path}
    compiler_digest = values.get("compiler_digest")
    if compiler_digest is not None:
        if not isinstance(compiler_digest, str) or _SHA256.fullmatch(compiler_digest) is None:
            raise NativeReasonerCompatibilityError(
                "native Reasoner compiler_digest diagnostic must be lowercase SHA-256"
            )
        result["compiler_digest"] = compiler_digest
    for name in sorted(_COMPILER_SCHEMA_FIELDS):
        if name not in values:
            continue
        value = values[name]
        if name == "native_abi_version" and isinstance(value, str):
            if not value:
                raise NativeReasonerCompatibilityError(
                    "native Reasoner native_abi_version diagnostic must be nonempty"
                )
        elif isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise NativeReasonerCompatibilityError(
                f"native Reasoner {name} diagnostic must be a positive integer"
            )
        result[name] = value
    for name in sorted(_COMPILER_TIMINGS):
        if name not in values:
            continue
        value = values[name]
        if type(value) is not float or not math.isfinite(value) or value < 0.0:
            raise NativeReasonerCompatibilityError(
                f"native Reasoner {name} diagnostic must be a finite nonnegative float"
            )
        result[name] = value
    counters: dict[str, int | bool] = {}
    for name in sorted(_COMPILER_COUNTERS):
        if name not in values:
            continue
        value = values[name]
        if name == "encoded_compiler_gil_released":
            if not isinstance(value, bool):
                raise NativeReasonerCompatibilityError(
                    "native Reasoner encoded_compiler_gil_released diagnostic must be bool"
                )
        elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise NativeReasonerCompatibilityError(
                f"native Reasoner {name} diagnostic must be a nonnegative integer"
            )
        counters[name] = value
    if counters:
        result["counters"] = counters
    return result


def _validate_compiler_handoff(value: object) -> dict[str, JsonValue]:
    """Validate the optional, currently unadvertised encoded-schema attestation.

    This metadata is compatibility evidence only.  It neither selects a reasoner path nor
    proves that a particular session used encoded-native compilation.
    """

    if not isinstance(value, Mapping):
        raise NativeReasonerCompatibilityError(
            "native backend compiler_handoff must be a mapping"
        )
    fields = set(value)
    if not all(isinstance(name, str) for name in fields):
        raise NativeReasonerCompatibilityError(
            "native backend compiler_handoff keys must be strings"
        )
    missing = sorted(_COMPILER_HANDOFF_FIELDS - fields)
    extra = sorted(fields - _COMPILER_HANDOFF_FIELDS)
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise NativeReasonerCompatibilityError(
            "native backend compiler_handoff fields are invalid: " + "; ".join(details)
        )

    expected_scalars: tuple[tuple[str, object], ...] = (
        ("schema_name", _ENCODED_SCHEMA_NAME),
        ("schema_version", _ENCODED_SCHEMA_VERSION),
        ("model_schema", _ENCODED_MODEL_SCHEMA),
        ("descriptor_sha256", _ENCODED_DESCRIPTOR_SHA256),
    )
    for name, expected in expected_scalars:
        actual = value[name]
        if type(actual) is not type(expected) or actual != expected:
            raise NativeReasonerCompatibilityError(
                f"native backend compiler_handoff {name} is incompatible; "
                f"expected {expected!r}, received {actual!r}"
            )

    widths = value["buffer_widths"]
    if not isinstance(widths, Mapping):
        raise NativeReasonerCompatibilityError(
            "native backend compiler_handoff buffer_widths must be a mapping"
        )
    width_names = set(widths)
    if not all(isinstance(name, str) for name in width_names):
        raise NativeReasonerCompatibilityError(
            "native backend compiler_handoff buffer names must be strings"
        )
    missing_widths = sorted(set(_ENCODED_BUFFER_WIDTHS) - width_names)
    extra_widths = sorted(width_names - set(_ENCODED_BUFFER_WIDTHS))
    if missing_widths or extra_widths:
        details = []
        if missing_widths:
            details.append("missing " + ", ".join(missing_widths))
        if extra_widths:
            details.append("unexpected " + ", ".join(extra_widths))
        raise NativeReasonerCompatibilityError(
            "native backend compiler_handoff buffer widths are invalid: "
            + "; ".join(details)
        )
    for name, expected in _ENCODED_BUFFER_WIDTHS.items():
        actual = widths[name]
        if type(actual) is not int or actual != expected:
            raise NativeReasonerCompatibilityError(
                f"native backend compiler_handoff width for {name!r} is incompatible; "
                f"expected {expected}, received {actual!r}"
            )

    # Build the JSON-safe copy only after the complete envelope has passed.
    return {
        "buffer_widths": dict(sorted(_ENCODED_BUFFER_WIDTHS.items())),
        "descriptor_sha256": _ENCODED_DESCRIPTOR_SHA256,
        "model_schema": _ENCODED_MODEL_SCHEMA,
        "schema_name": _ENCODED_SCHEMA_NAME,
        "schema_version": _ENCODED_SCHEMA_VERSION,
    }


def _classify_elk_identity(
    ontology: object, *, api: _ELKAPI | None = None
) -> dict[str, JsonValue]:
    api = api or _load_pyelk_api()
    session: Any | None = None
    try:
        session = api.reasoner_type(ontology, api.config_type())
        _require_identity(session, ontology, "pyELK")
        backend = _backend_metadata(session, api.version)
        consistency = session.is_consistent()
        consistent = _reasoning_result_value(consistency, bool, "pyELK consistency")
        taxonomy_result = session.classify()
        taxonomy = _reasoning_result_value(taxonomy_result, object, "pyELK taxonomy")
        bottom = getattr(taxonomy, "bottom", None)
        if bottom is None or not hasattr(bottom, "members"):
            raise NativeReasonerCompatibilityError(
                "pyELK Taxonomy.bottom must be an EntityNode"
            )
        unsatisfiable = _entity_iris(bottom.members)
        profile = {
            "complete": _require_bool(
                getattr(taxonomy_result, "complete", None),
                "pyELK taxonomy completeness",
            ),
            "reasons": _elk_reasons(getattr(taxonomy_result, "reasons", None)),
        }
        return {
            "unsatisfiable": list(unsatisfiable),
            "inconsistent": not consistent,
            "reasoner": backend,
            "profile": profile,
        }
    finally:
        if session is not None:
            session.close()


def _reasoning_result_value(result: Any, expected: type[Any], label: str) -> Any:
    if not hasattr(result, "value") or not hasattr(result, "complete") or not hasattr(
        result, "reasons"
    ):
        raise NativeReasonerCompatibilityError(
            f"{label} must return pyELK ReasoningResult"
        )
    value = result.value
    if expected is not object and not isinstance(value, expected):
        raise NativeReasonerCompatibilityError(
            f"{label} ReasoningResult.value has unexpected type {type(value).__name__}"
        )
    return value


def _elk_reasons(values: object) -> list[JsonValue]:
    if not isinstance(values, tuple):
        raise NativeReasonerCompatibilityError(
            "pyELK ReasoningResult.reasons must be a tuple"
        )
    reasons: list[JsonValue] = []
    for issue in values:
        task = getattr(getattr(issue, "task", None), "value", None)
        features = getattr(issue, "features", None)
        constructors = getattr(issue, "constructors", None)
        polarities = getattr(issue, "polarities", None)
        if not isinstance(task, str) or not all(
            isinstance(item, tuple) for item in (features, constructors, polarities)
        ):
            raise NativeReasonerCompatibilityError(
                "pyELK completeness issue does not match the public contract"
            )
        assert isinstance(features, tuple)
        assert isinstance(constructors, tuple)
        assert isinstance(polarities, tuple)
        reasons.append(
            {
                "task": task,
                "features": [str(item) for item in features],
                "constructors": [str(item) for item in constructors],
                "polarities": [str(item) for item in polarities],
            }
        )
    return reasons


def _encode_core_wire(ontology: object) -> CoreWireEnvelope:
    core = importlib.import_module("pyowl_core")
    encoder = getattr(core, "encode_snapshot", None)
    opener = getattr(core, "open_snapshot", None)
    version = getattr(core, "WIRE_FORMAT_VERSION", None)
    missing = tuple(
        name
        for name, value in (
            ("encode_snapshot", encoder),
            ("open_snapshot", opener),
            ("WIRE_FORMAT_VERSION", version),
        )
        if value is None or (name != "WIRE_FORMAT_VERSION" and not callable(value))
    )
    if missing:
        raise NativeReasonerCompatibilityError(
            "installed pyowl-core lacks the bounded-worker wire contract: "
            + ", ".join(missing)
        )
    if not callable(encoder):
        raise NativeReasonerCompatibilityError(
            "installed pyowl-core encode_snapshot is not callable"
        )
    payload = encoder(ontology)
    if not isinstance(payload, bytes):
        raise NativeReasonerCompatibilityError("pyowl-core encode_snapshot must return bytes")
    if (
        not isinstance(version, tuple)
        or len(version) != 2
        or not all(isinstance(item, int) for item in version)
    ):
        raise NativeReasonerCompatibilityError(
            "pyowl-core WIRE_FORMAT_VERSION must be an integer pair"
        )
    capability_version = cast(tuple[int, int], version)
    wire_version = _wire_header_version(payload)
    if (
        wire_version[0] != capability_version[0]
        or wire_version[1] > capability_version[1]
    ):
        raise NativeReasonerCompatibilityError(
            "pyowl-core encoded a wire version outside its advertised capability"
        )
    return CoreWireEnvelope(
        payload=payload,
        fingerprints=_view_fingerprints(ontology),
        wire_version=wire_version,
        core_version=str(getattr(core, "__version__", "unknown")),
    )


def _wire_header_version(payload: bytes) -> tuple[int, int]:
    if len(payload) < 12 or payload[:8] != b"PYOCORE\0":
        raise NativeReasonerCompatibilityError(
            "pyowl-core encode_snapshot returned an invalid PYOCORE header"
        )
    return (
        int.from_bytes(payload[8:10], "little"),
        int.from_bytes(payload[10:12], "little"),
    )


def _view_fingerprints(ontology: object) -> dict[str, str]:
    values: dict[str, str] = {}
    for name in (
        "structural_fingerprint",
        "logical_fingerprint",
        "signature_fingerprint",
    ):
        fingerprint = getattr(ontology, name, None)
        value = getattr(fingerprint, "hex", None)
        if not isinstance(value, str):
            raise NativeReasonerCompatibilityError(
                f"OntologyView lacks public {name}.hex"
            )
        values[name] = value
    return values


WorkerEntrypoint: TypeAlias = Callable[[Connection, str, Mapping[str, str]], None]


def _run_elk_worker(
    envelope: CoreWireEnvelope,
    *,
    timeout_s: float,
    entrypoint: WorkerEntrypoint | None = None,
) -> ELKWorkerResult:
    """Run one verified mmap worker and terminate it at the wall-clock gate."""

    _validate_timeout(timeout_s)
    target = entrypoint or _elk_worker_entry
    context = multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory(prefix="oaei-core-wire-") as directory:
        wire_path = Path(directory) / "ontology.pyocore"
        written = wire_path.write_bytes(envelope.payload)
        if written != len(envelope.payload):
            raise NativeWorkerError("failed to persist the complete core wire artifact")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(
            target=target,
            args=(child, str(wire_path), dict(envelope.fingerprints)),
            name="oaei-pyelk-mmap",
        )
        started = False
        try:
            process.start()
            started = True
            child.close()
            if not parent.poll(timeout_s):
                _terminate_process(process)
                raise ELKTimeoutError(f"pyELK classification exceeded {timeout_s}s")
            try:
                response = parent.recv_bytes(_MAX_WORKER_RESPONSE_BYTES)
            except (EOFError, OSError) as error:
                _terminate_process(process)
                raise NativeWorkerError("pyELK worker exited without a valid response") from error
            process.join(_WORKER_SHUTDOWN_SECONDS)
            if started and process.is_alive():
                _terminate_process(process)
                raise NativeWorkerError("pyELK worker did not exit after returning a result")
            return _decode_worker_response(response, process.exitcode)
        finally:
            parent.close()
            child.close()
            if process.is_alive():
                _terminate_process(process)


def _terminate_process(process: Any) -> None:
    if not process.is_alive():
        process.join()
        return
    process.terminate()
    process.join(_WORKER_SHUTDOWN_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(_WORKER_SHUTDOWN_SECONDS)


def _elk_worker_entry(
    connection: Connection,
    wire_path: str,
    expected_fingerprints: Mapping[str, str],
) -> None:
    """Open one verified mapped snapshot and call only the frozen pyELK facade."""

    try:
        core = importlib.import_module("pyowl_core")
        opener = getattr(core, "open_snapshot", None)
        if not callable(opener):
            raise NativeReasonerCompatibilityError(
                "worker pyowl-core lacks open_snapshot"
            )
        ontology = opener(wire_path, mmap=True, verify=True)
        try:
            features = frozenset(
                str(item)
                for item in getattr(getattr(ontology, "capabilities", None), "features", ())
            )
            if not {"wire-verified", "mmap-snapshot"}.issubset(features):
                raise NativeWorkerError("core worker snapshot is not a verified mmap owner")
            actual = _view_fingerprints(ontology)
            if actual != dict(expected_fingerprints):
                raise NativeWorkerError("mapped core wire fingerprints do not match the parent")
            result = _classify_elk_identity(ontology)
        finally:
            close = getattr(ontology, "close", None)
            if callable(close):
                close()
        response: dict[str, JsonValue] = {
            "ok": True,
            "result": {
                **result,
                "wire_verified": True,
                "mmap_verified": True,
                "owl_parse_count": 0,
            },
        }
    except Exception as error:
        response = {
            "ok": False,
            "error": {
                "type": type(error).__name__,
                "message": str(error),
            },
        }
    try:
        connection.send_bytes(
            json.dumps(
                response,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
    finally:
        connection.close()


def _decode_worker_response(payload: bytes, exitcode: int | None) -> ELKWorkerResult:
    if exitcode not in {0, None}:
        raise NativeWorkerError(f"pyELK worker exited with status {exitcode}")
    try:
        response = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NativeWorkerError("pyELK worker returned malformed JSON") from error
    if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
        raise NativeWorkerError("pyELK worker response lacks its protocol status")
    if not response["ok"]:
        response_error = response.get("error")
        if isinstance(response_error, dict):
            error_type = response_error.get("type", "Error")
            message = response_error.get("message", "worker failure")
            raise NativeWorkerError(f"pyELK worker {error_type}: {message}")
        raise NativeWorkerError("pyELK worker failed without error metadata")
    result = _require_mapping(response.get("result"), "pyELK worker result")
    return ELKWorkerResult(
        unsatisfiable=_string_tuple(
            result.get("unsatisfiable"), "pyELK worker unsatisfiable classes"
        ),
        inconsistent=_require_bool(result.get("inconsistent"), "pyELK worker consistency"),
        reasoner=_require_mapping(result.get("reasoner"), "pyELK worker reasoner metadata"),
        profile=_require_mapping(result.get("profile"), "pyELK worker profile metadata"),
        wire_verified=_require_bool(result.get("wire_verified"), "pyELK wire verification"),
        mmap_verified=_require_bool(result.get("mmap_verified"), "pyELK mmap verification"),
        owl_parse_count=_require_nonnegative_int(
            result.get("owl_parse_count"), "pyELK worker OWL parse count"
        ),
    )


def _validate_timeout(timeout_s: float | None) -> None:
    if timeout_s is None:
        return
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not math.isfinite(timeout_s)
        or timeout_s <= 0
    ):
        raise ValueError("timeout_s must be a finite positive number or None")


def _require_mapping(value: object, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise NativeReasonerCompatibilityError(f"{label} must be a string-keyed mapping")
    return {str(key): item for key, item in value.items()}


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise NativeReasonerCompatibilityError(f"{label} must be a string sequence")
    ordered = tuple(value)
    if ordered != tuple(sorted(set(ordered))):
        raise NativeReasonerCompatibilityError(
            f"{label} must be sorted and duplicate-free"
        )
    return ordered


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise NativeReasonerCompatibilityError(f"{label} must be bool")
    return value


def _require_nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NativeReasonerCompatibilityError(f"{label} must be a nonnegative integer")
    return value


__all__ = [
    "CoreWireEnvelope",
    "ELKReasoner",
    "ELKTimeoutError",
    "ELKWorkerResult",
    "HermiTReasoner",
    "HermiTTimeoutError",
    "NativeReasoner",
    "NativeReasonerCompatibilityError",
    "NativeReasonerError",
    "NativeReasonerUnavailableError",
    "NativeWorkerError",
]
