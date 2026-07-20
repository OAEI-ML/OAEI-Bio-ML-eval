"""Deterministic ``coherence-provenance/1`` report construction."""

from __future__ import annotations

import hashlib
import importlib
import json
import platform
import re
from collections.abc import Iterable, Mapping

from .. import __version__ as OAEI_VERSION
from .bridge import BridgeNormalization
from .reasoner import UnsatResult

PROVENANCE_SCHEMA = "coherence-provenance/1"
METRIC_METHODOLOGY = "oaei-bioml-coherence/1"
_INGESTION_PATHS = frozenset(
    {"scalar-python", "scalar-native", "scalar-wire", "encoded-native"}
)
_COMPILER_COUNTERS = frozenset(
    {
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
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def build_coherence_provenance(
    ontology: object,
    bridge: BridgeNormalization,
    denominator: Iterable[str],
    result: UnsatResult,
    *,
    requested_reasoner: str,
    timeout_s: float | None,
    api_options: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a JSON-safe record without paths, object IDs, or timestamps."""

    denominator_values = tuple(sorted(set(denominator)))
    numerator_values = tuple(sorted(set(result.unsatisfiable)))
    core = _core_metadata(ontology)
    reasoner_details = _json_mapping(result.provenance)
    compiler_handoff = _compiler_handoff(
        ontology,
        requested_reasoner=requested_reasoner,
        selected_reasoner=result.reasoner_used,
        reasoner_details=reasoner_details,
    )
    profile = _json_mapping(reasoner_details.get("profile"))
    transport = reasoner_details.get("transport", "in-process-identity")
    if isinstance(transport, Mapping):
        transport_record: object = _json_mapping(transport)
    else:
        transport_record = {"mode": str(transport)}
    return {
        "schema": PROVENANCE_SCHEMA,
        "metric": {
            "package": "oaei-bioml-eval",
            "package_version": OAEI_VERSION,
            "methodology": METRIC_METHODOLOGY,
            "historical_key": "global_coherence",
            "meaning": "degree-of-incoherence; zero-is-clean",
        },
        "execution": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": {
                "system": platform.system(),
                "machine": platform.machine(),
            },
            "api_options": _json_mapping(api_options or {}),
        },
        "core": core,
        "compiler_handoff": compiler_handoff,
        "bridge": {
            "input_count": bridge.input_count + bridge.invalid_dropped_count,
            "normalized_count": len(bridge.correspondences),
            "duplicate_count": bridge.duplicate_count,
            "self_pair_count": bridge.self_pair_count,
            "invalid_dropped_count": bridge.invalid_dropped_count,
            "fingerprint": bridge.fingerprint,
            "fingerprint_scheme": "sha256-domain-json-v1",
        },
        "reasoner": {
            "requested": requested_reasoner,
            "actual": result.reasoner_used,
            "timeout_seconds": timeout_s,
            "elapsed_seconds": result.elapsed_seconds,
            "fallback_reason": result.fallback_reason,
            "lower_bound": result.reasoner_used == "elk",
            "inconsistent": result.inconsistent,
            "package": reasoner_details.get("package"),
            "package_version": reasoner_details.get("package_version"),
            "backend": _json_mapping(reasoner_details.get("backend")),
            "profile": profile,
            "transport": transport_record,
            "prior_attempts": _json_attempts(reasoner_details.get("attempts")),
        },
        "result": {
            "denominator_count": len(denominator_values),
            "denominator_sha256": sorted_line_sha256(denominator_values),
            "numerator_count": len(numerator_values),
            "numerator_sha256": sorted_line_sha256(numerator_values),
            "digest_scheme": "sorted-utf8-lines-sha256-v1",
        },
    }


def _core_compiler_handoff(ontology: object) -> dict[str, object]:
    """Record only public core shape; do not infer the reasoner's compiler path."""

    capabilities = getattr(ontology, "capabilities", None)
    if capabilities is None:
        return {
            "core_encoded_view_schemas": {},
            "owner_kind": None,
            "storage_kind": None,
        }
    features = frozenset(str(item) for item in getattr(capabilities, "features", ()))
    context_kind = getattr(getattr(ontology, "structural_context", None), "kind", None)
    context_value = getattr(context_kind, "value", None)
    if context_value in {"composite", "overlay"}:
        owner_kind = context_value
    elif "ontology-composite" in features:
        owner_kind = "composite"
    elif "ontology-overlay" in features:
        owner_kind = "overlay"
    else:
        owner_kind = "direct"

    storage_kind: str | None
    if "mmap-snapshot" in features:
        storage_kind = "mmap"
    elif "wire-verified" in features:
        storage_kind = "decoded"
    else:
        backend = getattr(capabilities, "backend", None)
        storage_kind = backend if isinstance(backend, str) and backend else None
    return {
        "core_encoded_view_schemas": _encoded_view_schemas(capabilities),
        "owner_kind": owner_kind,
        "storage_kind": storage_kind,
    }


def _compiler_handoff(
    ontology: object,
    *,
    requested_reasoner: str,
    selected_reasoner: str,
    reasoner_details: Mapping[str, object],
) -> dict[str, object]:
    """Extend core handoff provenance with public reasoner diagnostics."""

    result = _core_compiler_handoff(ontology)

    result["requested_reasoner"] = requested_reasoner
    result["selected_reasoner"] = selected_reasoner
    package_version = reasoner_details.get("package_version")
    if isinstance(package_version, str) and package_version:
        result["reasoner_package_version"] = package_version
    backend = reasoner_details.get("backend")
    if not isinstance(backend, Mapping):
        return result
    for source, target in (
        ("name", "selected_backend"),
        ("implementation_version", "implementation_version"),
        ("ir_schema_version", "reasoner_ir_schema_version"),
    ):
        value = backend.get(source)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            result[target] = value
    schema = backend.get("compiler_handoff")
    if isinstance(schema, Mapping):
        result["reasoner_encoded_schema"] = _json_mapping(schema)
    diagnostics = backend.get("compiler_diagnostics")
    if isinstance(diagnostics, Mapping):
        result.update(_bounded_compiler_diagnostics(diagnostics))
    return result


def _bounded_compiler_diagnostics(values: Mapping[str, object]) -> dict[str, object]:
    if set(values) - {"ingestion_path", "compiler_digest", "counters"}:
        raise TypeError("reasoner compiler diagnostics contain unsupported fields")
    ingestion_path = values.get("ingestion_path")
    if ingestion_path not in _INGESTION_PATHS:
        raise TypeError("reasoner compiler ingestion path is incompatible")
    result: dict[str, object] = {"ingestion_path": ingestion_path}
    compiler_digest = values.get("compiler_digest")
    if compiler_digest is not None:
        if not isinstance(compiler_digest, str) or _SHA256.fullmatch(compiler_digest) is None:
            raise TypeError("reasoner compiler digest must be lowercase SHA-256")
        result["compiler_digest"] = compiler_digest
    counters = values.get("counters")
    if counters is None:
        return result
    if not isinstance(counters, Mapping) or any(
        not isinstance(name, str) or name not in _COMPILER_COUNTERS for name in counters
    ):
        raise TypeError("reasoner compiler counters are incompatible")
    bounded: dict[str, int | bool] = {}
    for name, value in sorted(counters.items()):
        if name == "encoded_compiler_gil_released":
            if not isinstance(value, bool):
                raise TypeError("reasoner compiler GIL diagnostic must be bool")
        elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TypeError("reasoner compiler counters must be nonnegative integers")
        bounded[name] = value
    result["counters"] = bounded
    return result


def _encoded_view_schemas(capabilities: object) -> dict[str, int]:
    raw = getattr(capabilities, "encoded_view_schemas", None)
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise TypeError("core encoded_view_schemas must be a mapping")
    result: dict[str, int] = {}
    for name, version in raw.items():
        if type(name) is not str or not name:
            raise TypeError("core encoded-view schema names must be nonempty exact strings")
        if type(version) is not int or version < 1:
            raise TypeError("core encoded-view schema versions must be positive exact integers")
        result[name] = version
    return dict(sorted(result.items()))


def sorted_line_sha256(values: Iterable[str]) -> str:
    """Hash a canonical sorted UTF-8 line set, including a final newline."""

    ordered = tuple(sorted(set(values)))
    payload = b"" if not ordered else ("\n".join(ordered) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _core_metadata(ontology: object) -> dict[str, object]:
    try:
        core = importlib.import_module("pyowl_core")
    except ModuleNotFoundError:
        core = None
    roles = _composition_members(ontology)
    fingerprints = {
        name.removesuffix("_fingerprint"): _fingerprint(ontology, name)
        for name in (
            "structural_fingerprint",
            "logical_fingerprint",
            "signature_fingerprint",
        )
    }
    composition_digest = getattr(ontology, "composition_provenance_digest", None)
    capabilities = getattr(ontology, "capabilities", None)
    member_roles = getattr(ontology, "member_roles", {})
    return {
        "package": "pyowl-core",
        "package_version": getattr(core, "__version__", None),
        "api_version": _json_sequence(getattr(core, "API_VERSION", None)),
        "model_schema": getattr(core, "MODEL_SCHEMA_VERSION", None),
        "wire_version": _json_sequence(getattr(core, "WIRE_FORMAT_VERSION", None)),
        "adapter_protocol": getattr(core, "ADAPTER_PROTOCOL_VERSION", None),
        "fingerprints": fingerprints,
        "composition_provenance_digest": (
            composition_digest.hex()
            if isinstance(composition_digest, bytes)
            else None
        ),
        "roles": roles,
        "role_manifest": {
            str(key): value
            for key, value in sorted(
                member_roles.items() if isinstance(member_roles, Mapping) else ()
            )
        },
        "capabilities": {
            "backend": getattr(capabilities, "backend", None),
            "features": sorted(str(item) for item in getattr(capabilities, "features", ())),
        },
        "documents": _document_manifests(ontology),
        "loader_diagnostics": _loader_diagnostics(ontology),
    }


def _composition_members(ontology: object) -> list[dict[str, object]]:
    members = getattr(ontology, "provenance_tree", None)
    if members is None:
        members = getattr(ontology, "members", None)
    if members is None and hasattr(ontology, "source") and hasattr(ontology, "target"):
        members = (
            _SyntheticMember(ontology.source, "source"),
            _SyntheticMember(ontology.target, "target"),
        )
    output: list[dict[str, object]] = []
    for member in members or ():
        view = getattr(member, "view", None)
        role = getattr(member, "role", None)
        output.append(
            {
                "role": role,
                "structural": _fingerprint(view, "structural_fingerprint"),
                "logical": _fingerprint(view, "logical_fingerprint"),
                "signature": _fingerprint(view, "signature_fingerprint"),
            }
        )
    return output


class _SyntheticMember:
    __slots__ = ("role", "view")

    def __init__(self, view: object, role: str) -> None:
        self.view = view
        self.role = role


def _fingerprint(view: object, name: str) -> str | None:
    value = getattr(getattr(view, name, None), "hex", None)
    return value if isinstance(value, str) else None


def _document_manifests(ontology: object) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for role, leaf in _role_leaves(ontology):
        manifest = getattr(leaf, "import_manifest", None)
        if manifest is None:
            continue
        canonical = getattr(manifest, "canonical_bytes", None)
        manifest_digest = None
        if callable(canonical):
            manifest_digest = hashlib.sha256(canonical()).hexdigest()
        documents: list[dict[str, object]] = []
        leaf_documents = tuple(getattr(leaf, "documents", ()))
        for index, record in enumerate(getattr(manifest, "documents", ())):
            iri = getattr(getattr(record, "document_iri", None), "value", None)
            document = leaf_documents[index] if index < len(leaf_documents) else None
            source_sha256 = getattr(
                getattr(document, "provenance", None), "source_sha256", None
            )
            documents.append(
                {
                    "document_key": getattr(record, "document_key", None),
                    "document_iri_sha256": _optional_text_digest(iri),
                    "document_fingerprint": getattr(
                        getattr(record, "document_fingerprint", None), "hex", None
                    ),
                    "source_sha256": (
                        source_sha256.hex()
                        if isinstance(source_sha256, bytes)
                        else None
                    ),
                }
            )
        edges: list[dict[str, object]] = []
        for edge in getattr(manifest, "edges", ()):
            import_iri = getattr(getattr(edge, "import_iri", None), "value", None)
            status = getattr(getattr(edge, "status", None), "value", None)
            edges.append(
                {
                    "importing_document_key": getattr(
                        edge, "importing_document_key", None
                    ),
                    "import_iri_sha256": _optional_text_digest(import_iri),
                    "status": status,
                    "resolved_document_key": getattr(
                        edge, "resolved_document_key", None
                    ),
                    "resolver_name": getattr(edge, "resolver_name", None),
                }
            )
        output.append(
            {
                "role": role,
                "complete": bool(getattr(manifest, "is_complete", False)),
                "policy": getattr(getattr(manifest, "policy", None), "value", None),
                "offline": getattr(manifest, "offline", None),
                "manifest_sha256": manifest_digest,
                "documents": documents,
                "edges": edges,
            }
        )
    return output


def _role_leaves(ontology: object) -> Iterable[tuple[str | None, object]]:
    members = getattr(ontology, "provenance_tree", None)
    if members is None:
        members = getattr(ontology, "members", None)
    if members is None:
        yield None, ontology
        return
    for member in members:
        role = getattr(member, "role", None)
        yield from ((role, leaf) for leaf in _leaves(getattr(member, "view", None)))


def _leaves(view: object) -> Iterable[object]:
    members = getattr(view, "members", None)
    if members is not None:
        for member in members:
            yield from _leaves(getattr(member, "view", None))
        return
    base = getattr(view, "base", None)
    if base is not None:
        yield from _leaves(base)
        return
    yield view


def _loader_diagnostics(ontology: object) -> list[dict[str, object]]:
    # Reading ``OntologyComposite.report`` also computes an effective axiom count.
    # Provenance needs loader diagnostics, not that ontology-sized aggregation, so
    # retain the leaf reports that composition already owns by identity.
    diagnostics: set[tuple[object, object]] = set()
    for _role, leaf in _role_leaves(ontology):
        report = getattr(leaf, "report", None)
        for item in getattr(report, "diagnostics", ()):
            diagnostics.add(
                (
                    getattr(item, "code", None),
                    getattr(getattr(item, "severity", None), "value", None),
                )
            )
    return [
        {"code": code, "severity": severity}
        for code, severity in sorted(diagnostics, key=lambda item: (str(item[0]), str(item[1])))
    ]


def _optional_text_digest(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_sequence(value: object) -> list[object] | None:
    if not isinstance(value, tuple):
        return None
    return [_json_value(item) for item in value]


def _json_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): _json_value(item)
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }


def _json_attempts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, (tuple, list)):
        return []
    return [_json_mapping(item) for item in value if isinstance(item, Mapping)]


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return _json_mapping(value)
    if isinstance(value, (frozenset, set)):
        return [_json_value(item) for item in sorted(value, key=str)]
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return {"type": type(value).__name__}


def canonical_provenance_json(value: Mapping[str, object]) -> str:
    """Stable representation used by parity and reproducibility tests."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


__all__ = [
    "METRIC_METHODOLOGY",
    "PROVENANCE_SCHEMA",
    "build_coherence_provenance",
    "canonical_provenance_json",
    "sorted_line_sha256",
]
