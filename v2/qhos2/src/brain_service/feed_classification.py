"""Derive the feed classification from evidence; never accept one that is merely declared.

This pure module came from the V1 observation contract, where copies lived in the Trader
(``adapters/rithmic``, ``data``) and in the Microstructure. In QHOS V2 only this copy exists
(``grep`` finds no other, QHOS-AUD-211), so "byte-identical with the other consumers" is not a
property this repository can assert. A declared ``feed_classification`` is still accepted only
when it equals what this module derives; a mismatch is refused by name.

The policy constants below were copied verbatim from the sealed
``ENVIRONMENT_TAXONOMY_POLICY.json`` (17/09/2026: the only mapped system is "Rithmic Test" ->
"vendor_test") and from ``book_qualification.ENVIRONMENT_CEILING``. Neither sealed file is in
V2 and no V2 test compares against them; the constants are kept as recorded. Nothing in this
module can return ``production``: no production system is in the policy, and
``production_market_data`` is a constant ``False`` — a research surface reports a classification,
it never promotes one.

Evidence keys (all optional; absence resolves to ``unknown``):
  provider, system_name, plant, source_kind, login_confirmed, endpoint, verified
``evidence_from_status`` also reads the V2 ``Sessao.status()`` names (``sistema``,
``login_confirmado``); see QHOS-AUD-138.
"""
from urllib.parse import urlsplit
import ipaddress
import re

#: Verbatim from audit/rithmic_book_qualification_20260913/ENVIRONMENT_TAXONOMY_POLICY.json -> environment_of_system.
ENVIRONMENT_OF_SYSTEM = {"Rithmic Test": "vendor_test"}
#: Verbatim from adapters/rithmic/book_qualification.py -> ENVIRONMENT_CEILING.
ENVIRONMENT_CEILING = {
    "production": ("realtime", "delayed", "replay", "unproven"),
    "vendor_test": ("vendor_test", "replay", "unproven"),
    "local": ("synthetic", "replay", "unproven"),
    "unknown": ("unproven",),
}
#: The vendor domain a system is reached through; derived from adapters/rithmic/client.py TEST_ENDPOINT
#: (wss://rituz00100.rithmic.com:443). A session that reached any other host is not that system.
VENDOR_DOMAIN_OF_SYSTEM = {"Rithmic Test": "rithmic.com"}
POLICY_FILE = "ENVIRONMENT_TAXONOMY_POLICY.json"
ENVIRONMENT_KINDS = ("production", "vendor_test", "local", "unknown")
MODALITY_KINDS = ("realtime", "delayed", "replay", "synthetic", "unproven", "vendor_test")
_BASIS = re.compile(r"[A-Za-z0-9_:.-]{1,256}")
_SLUG = re.compile(r"[^A-Za-z0-9_.-]+")


def _slug(value):
    return _SLUG.sub("_", str(value))[:48] or "none"


def _not_established(reason):
    return {"environment_kind": "unknown", "modality": None, "production_market_data": False,
            "basis": "not_established:" + _slug(reason)}


def endpoint_host(endpoint):
    """Host of a wss:// endpoint, or None when it cannot be read."""
    if not isinstance(endpoint, str) or not endpoint:
        return None
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return None
    return (parts.hostname or "").lower() or None


def endpoint_is_local(endpoint):
    """True for loopback/private/unspecified hosts — the local rehearsal plant, never the vendor."""
    host = endpoint_host(endpoint)
    if host is None:
        return False
    if host in ("localhost", "localhost.localdomain"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_unspecified or address.is_link_local


def classify_feed(evidence):
    """The classification the evidence supports, exactly four keys, ``production_market_data`` always False."""
    evidence = evidence if isinstance(evidence, dict) else {}
    if evidence.get("provider") != "rithmic_protocol":
        return _not_established("provider_unknown")
    source_kind = evidence.get("source_kind")
    if source_kind in ("fixture", "synthetic_fixture"):
        return {"environment_kind": "local", "modality": "synthetic", "production_market_data": False,
                "basis": "local:synthetic_fixture"}
    endpoint = evidence.get("endpoint")
    if endpoint_is_local(endpoint):
        return {"environment_kind": "local", "modality": "synthetic", "production_market_data": False,
                "basis": "local:loopback_endpoint_" + _slug(endpoint_host(endpoint))}
    system = evidence.get("system_name")
    if system not in ENVIRONMENT_OF_SYSTEM:
        return _not_established("system_not_in_policy_" + _slug(system))
    environment = ENVIRONMENT_OF_SYSTEM[system]
    if environment not in ENVIRONMENT_KINDS or environment == "production":
        # No production system is mapped today; if one ever is, a research surface still may not classify it here.
        return _not_established("environment_not_classifiable_here_" + _slug(environment))
    host = endpoint_host(endpoint)
    if host is None:
        return _not_established("endpoint_not_recorded")
    domain = VENDOR_DOMAIN_OF_SYSTEM.get(system)
    if not domain or not (host == domain or host.endswith("." + domain)):
        return _not_established("endpoint_not_vendor_" + _slug(host))
    if evidence.get("login_confirmed") is not True:
        return _not_established("login_not_confirmed")
    if source_kind == "current_session":
        modality = "vendor_test" if environment == "vendor_test" else "unproven"
        basis = "current_session:" + _slug(system) + ":login_confirmed:endpoint_" + _slug(host) + ":policy_" + POLICY_FILE
    elif source_kind == "historical_capture":
        verified = evidence.get("verified") is True
        modality = "replay" if verified else "unproven"
        basis = ("recorded_capture:" + _slug(system) + ":login_confirmed:endpoint_" + _slug(host) + ":"
                 + ("replay_verified" if verified else "replay_unverified") + ":policy_" + POLICY_FILE)
    else:
        return _not_established("source_kind_" + _slug(source_kind))
    if modality not in ENVIRONMENT_CEILING.get(environment, ()):
        modality = "unproven"
    result = {"environment_kind": environment, "modality": modality, "production_market_data": False, "basis": basis}
    assert _BASIS.fullmatch(result["basis"]) and result["production_market_data"] is False
    return result


def evidence_from_status(status, *, source_kind, verified=None, endpoint=None):
    """Evidence dict from a session status.

    Accepts the V1 capture-manifest names (``system_name``, ``login_confirmed``, ``endpoint``) and the
    V2 ``Sessao.status()`` names (``sistema``, ``login_confirmado``). The V2 status carries no endpoint,
    so the caller passes the configured one as ``endpoint``; an explicit ``endpoint`` key in the status
    still wins (QHOS-AUD-138).
    """
    status = status if isinstance(status, dict) else {}
    system = status["system_name"] if "system_name" in status else status.get("sistema")
    login = status["login_confirmed"] if "login_confirmed" in status else status.get("login_confirmado")
    return {"provider": "rithmic_protocol", "system_name": system, "plant": status.get("plant"),
            "source_kind": source_kind, "login_confirmed": login is True,
            "endpoint": status["endpoint"] if "endpoint" in status else endpoint, "verified": verified}


def evidence_from_operational(value):
    """Evidence dict from an operational payload (``qhos.rithmic.operational.v1``)."""
    value = value if isinstance(value, dict) else {}
    session = value.get("session") if isinstance(value.get("session"), dict) else {}
    connection = value.get("connection") if isinstance(value.get("connection"), dict) else {}
    return {"provider": value.get("provider"), "system_name": session.get("system_name", value.get("environment")),
            "plant": session.get("plant"), "source_kind": value.get("source_kind"),
            "login_confirmed": (session.get("login_confirmed") if "login_confirmed" in session else connection.get("login_confirmed")) is True,
            "endpoint": session.get("endpoint"), "verified": None}


def validate_declared(declared, evidence):
    """Return the derived classification; refuse a declaration that the evidence does not support."""
    derived = classify_feed(evidence)
    if declared is None:
        return derived
    if not isinstance(declared, dict) or set(declared) != {"environment_kind", "modality", "production_market_data", "basis"}:
        raise ValueError("invalid_feed_classification")
    if declared["production_market_data"] is not False:
        raise ValueError("observation_may_not_assert_production_market_data")
    if declared != derived:
        raise ValueError("feed_classification_not_derived_from_evidence:" + _slug(declared.get("environment_kind")) + "_vs_" + derived["environment_kind"])
    return derived
