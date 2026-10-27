# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""letkupaja.fi:n CAD-lisäosarajapinnan asiakas (vain Pythonin vakiokirjasto, ei FreeCAD-riippuvuuksia).

Rajapinta (letkupaja.fi):
* GET  /api/cad/versio                    julkinen: lisäosien uusimmat versiot {plugins: {freecad: {...}}}
* GET  /cad/geometry.json                  julkinen: mittadata (ETag, If-None-Match -> 304)
* POST /api/cad/laite/aloita               {client, device_name, version, lang} -> {device_code, user_code, verification_uri(_complete), ...}
* POST /api/cad/laite/tunnus               {device_code} -> 428 pending | 429 slow_down | 410 expired/denied | 200 {token}
* POST /api/cad/laite/ulos                 (Bearer) peruu tunnuksen palvelimella
* GET  /api/cad/oikeudet                   (Bearer) tilin oikeudet
* POST /api/cad/oikeudet                   (Bearer) {config} -> {entitled, via (order | free_step), exact, code, ...}
* POST /api/cad/step                       (Bearer) {config} -> 200 model/step | 402 {buyUrl, buyText, buyEmail, priceEur,
                                           unpaid?: {order, line, payUrl, payText}} | 409 order_pending (tilattu, kesken;
                                           payUrl, payText) | 400 {details, details_en}
Virheet: {code, error (suomi), error_en (englanti), ...}.

Yrityksen verkko: varmenteet käyttöjärjestelmän säilöstä (truststore-paketti, jos asennettu; Windowsissa Pythonin
oletus lukee järjestelmän säilön), välityspalvelin HTTPS_PROXY/NO_PROXY- tai järjestelmäasetuksista (urllib).

Tietosuoja: pyyntöjä tehdään vain käyttäjän painaessa painiketta. Mukana ei ole käyttötietoja eikä tunnisteita
(paitsi kirjautumisen jälkeen Authorization-otsake). Tunnistetta ei koskaan kirjoiteta lokiin: virheilmoituksissa
näkyy vain palvelimen vastaus. Tunnistetta ei lähetetä uudelleenohjauksen kohteeseen (uudelleenohjaus hylätään).
"""
from __future__ import annotations

import json
import platform
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

from .version import CLIENT_ID, VERSION

DEFAULT_BASE = "https://letkupaja.fi"
SITE_HOST = "letkupaja.fi"
STEP_MAGIC = b"ISO-10303-21"
MAX_STEP_BYTES = 200 * 1024 * 1024
_VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}([-+.].*)?$")
_BAD_URL_CHARS = re.compile(r"[\\@\s\x00-\x1f\x7f]")


class ApiError(Exception):
    """Palvelimen tai yhteyden virhe. message = palvelimen suomenkielinen teksti (tai HTTP-tila), message_en =
    englanninkielinen; code = palvelimen pysyvä tunniste; details = lisätiedot (esim. miksi asennelmaa ei voi tilata);
    retry_after = sekunnit (429/503)."""

    def __init__(self, message: str, status: int | None = None, payload: Any = None, retry_after: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.payload = payload
        self.retry_after = retry_after
        js = payload if isinstance(payload, dict) else {}
        self.code = str(js.get("code") or "")
        en = js.get("error_en")
        self.message_en = en if isinstance(en, str) and en else message
        self.details = _details(js.get("details"))
        self.details_en = _details(js.get("details_en"))

    def text(self, lang: str = "fi") -> str:
        """Käyttöliittymän kielen mukainen teksti lisätietoineen (enintään 5 riviä)."""
        base = self.message_en if lang == "en" else self.message
        details = self.details_en if lang == "en" and self.details_en else self.details
        if details:
            base += "".join("\n• " + d for d in details[:5])
        return base


def _details(raw: Any) -> list[str]:
    out: list[str] = []
    if isinstance(raw, list):
        for it in raw:
            if isinstance(it, str):
                out.append(it[:300])
            elif isinstance(it, dict):
                msg = it.get("message")
                path = it.get("path")
                if isinstance(msg, str):
                    out.append((f"{path}: " if isinstance(path, str) and path else "") + msg[:300])
    return out


class NetworkError(ApiError):
    """Ei yhteyttä. certificate = True, jos TLS-varmenteen tarkistus epäonnistui (esim. yrityksen HTTPS-tarkastus)."""

    def __init__(self, message: str, certificate: bool = False):
        super().__init__(message)
        self.certificate = certificate


class AuthRequired(ApiError):
    """401/403: kirjautuminen puuttuu tai tunniste on vanhentunut tai peruttu."""


def _text(v: Any, limit: int = 200) -> str | None:
    return v.strip()[:limit] if isinstance(v, str) and v.strip() else None


def _pay_label(js: dict[str, Any], lang: str) -> str | None:
    """payText / payText_en palvelimelta (tilaussivun painike, esim. "Jatka maksamiseen")."""
    return _text(js.get("payText_en") if lang == "en" else js.get("payText")) or _text(js.get("payText"))


class PaymentRequired(ApiError):
    """402: tarkka malli ei sisälly tiliin. buy_url avaa ostosivun (Letkukone) letkupaja.fi:ssä.
    unpaid_url: sama asennelma (tai sama letku ja liittimet) on tilauksessa, jonka verkkomaksu jäi kesken (unpaid.payUrl,
    tilaussivu "Jatka maksamiseen"); maksetun tilauksen malli on maksuton, joten tarjotaan myös maksun jatkamista."""

    def __init__(self, message: str, buy_url: str | None, price_eur: float | None, payload: Any = None, base: str = DEFAULT_BASE):
        super().__init__(message, 402, payload)
        self.buy_url = buy_url
        self.price_eur = price_eur
        js = payload if isinstance(payload, dict) else {}
        self.buy_text = js.get("buyText") if isinstance(js.get("buyText"), str) else None
        self.buy_text_en = js.get("buyText_en") if isinstance(js.get("buyText_en"), str) else None
        email = js.get("buyEmail")
        self.buy_email = email[:200] if isinstance(email, str) and "@" in email else None
        self.owned: list[dict[str, Any]] = parse_owned(js.get("owned"))
        unpaid = js.get("unpaid") if isinstance(js.get("unpaid"), dict) else {}
        self._unpaid = unpaid
        self.unpaid_url = safe_site_url(unpaid.get("payUrl"), base)
        self.unpaid_order = _int(unpaid.get("order"))
        self.unpaid_line = _int(unpaid.get("line"))

    def buy_label(self, lang: str = "fi") -> str | None:
        t = self.buy_text_en if lang == "en" else self.buy_text
        return t[:200] if t else None

    def unpaid_label(self, lang: str = "fi") -> str | None:
        return _pay_label(self._unpaid, lang)

    def unpaid_ref(self) -> str:
        """"#50013/1" (tilaus ja rivi) tai tyhjä."""
        if self.unpaid_order is None:
            return ""
        return f"#{self.unpaid_order}" + (f"/{self.unpaid_line}" if self.unpaid_line is not None else "")


class OrderPending(ApiError):
    """409 order_pending: sama asennelma on tilattu, mutta malli ei ole vielä ladattavissa (verkkomaksu aloitettu alle
    30 min sitten tai yrityksen tiedot tarkistamatta). Ostoa ei tarjota; tilaus ja rivi kohteissa order/line.
    pay_url: verkkomaksun tilaussivu (payUrl, painike payText "Jatka maksamiseen")."""

    def __init__(self, message: str, status: int | None = None, payload: Any = None, base: str = DEFAULT_BASE):
        super().__init__(message, status, payload)
        js = payload if isinstance(payload, dict) else {}
        pend = js.get("pending") if isinstance(js.get("pending"), dict) else {}
        self.order = pend.get("order") if isinstance(pend.get("order"), int) and not isinstance(pend.get("order"), bool) else None
        self.line = pend.get("line") if isinstance(pend.get("line"), int) and not isinstance(pend.get("line"), bool) else None
        self._js = js
        self.pay_url = safe_site_url(js.get("payUrl"), base)

    def pay_label(self, lang: str = "fi") -> str | None:
        return _pay_label(self._js, lang)


class NotAvailable(ApiError):
    """501: palvelu ei ole käytettävissä tälle asennelmalle."""


def _int(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_owned(raw: Any) -> list[dict[str, Any]]:
    """POST /api/cad/oikeudet -vastauksen owned (rajapinnan versio 1: palvelin palauttaa aina tyhjän listan, koska tilattu
    tai ostettu malli kelpaa kaikilla pituuksilla ja kiertokulmilla; jäsennys säilyy yhteensopivuuden vuoksi): mallit, jotka
    eroavat vain pituudelta tai kiertokulmalta -> [{"order", "line", "lengthMm", "orientation", "swapped"}] (enintään 3, tyypit tarkistettu)."""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for it in raw[:3]:
        if not isinstance(it, dict):
            continue
        length = _int(it.get("lengthMm"))
        if length is None or length <= 0:
            continue
        out.append({"order": _int(it.get("order")), "line": _int(it.get("line")), "lengthMm": length, "orientation": _int(it.get("orientation")) or 0, "swapped": it.get("swapped") is True})
    return out


class RedirectRefused(urllib.error.URLError):
    """Uudelleenohjaus hylättiin (tunniste olisi lähtenyt toiseen osoitteeseen tai https -> http)."""


@dataclass
class DeviceStart:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int
    verification_uri_complete: str | None = None  # RFC 8628: osoite, jossa koodi on valmiina


@dataclass
class DevicePoll:
    status: str  # "pending" | "ok" | "expired" | "denied" | "slow_down"
    token: str = ""
    expires_at: str = ""
    interval: int | None = None


def compare_versions(a: str, b: str) -> int:
    """-1, 0 tai 1 (numerot pisteillä erotettuina, loppuliite ohitetaan)."""

    def parts(v: str) -> list[int]:
        out = []
        for p in re.split(r"[.]", re.split(r"[-+ ]", v.strip().lstrip("vV"), maxsplit=1)[0]):
            m = re.match(r"\d+", p)
            out.append(int(m.group(0)) if m else 0)
        while len(out) < 4:
            out.append(0)
        return out

    pa, pb = parts(a), parts(b)
    return (pa > pb) - (pa < pb)


def parse_version_info(data: Any, client: str = CLIENT_ID) -> dict[str, Any] | None:
    """Tulkitsee /api/cad/versio-vastauksen tälle lisäosalle:
    {"latest", "url", "download", "notes", "notes_en", "minimum"} tai None.
    Hyväksyy muodot {"plugins": {"freecad": {...}}} (sopimus), {"clients": {...}}, {"freecad": {...}|"1.2.3"},
    [{"client": "freecad", ...}], {"version": "1.2.3"}."""
    cands: list[Any] = []
    if isinstance(data, list):
        cands += [it for it in data if isinstance(it, dict) and client in (it.get("client"), it.get("id"), it.get("name"))]
    elif isinstance(data, dict):
        for key in ("plugins", "clients", "lisaosat", "addons", "versions", "versiot"):
            sub = data.get(key)
            if isinstance(sub, dict) and client in sub:
                cands.append(sub[client])
            elif isinstance(sub, list):
                cands += [it for it in sub if isinstance(it, dict) and client in (it.get("client"), it.get("id"), it.get("name"))]
        if client in data:
            cands.append(data[client])
        cands.append(data)

    def s(v: Any) -> str | None:
        return v if isinstance(v, str) and v else None

    for c in cands:
        if isinstance(c, str) and _VERSION_RE.match(c):
            return {"latest": c, "url": None, "download": None, "notes": None, "notes_en": None, "minimum": None}
        if isinstance(c, dict):
            v = c.get("version") or c.get("latest") or c.get("versio")
            if isinstance(v, str) and _VERSION_RE.match(v):
                minimum = s(c.get("minimum") or c.get("min"))
                return {
                    "latest": v,
                    "url": s(c.get("url")) or s(c.get("lataus")),
                    "download": s(c.get("download")) or s(c.get("downloadUrl")),
                    "notes": s(c.get("notes")) or s(c.get("changelog")) or s(c.get("muutokset")),
                    "notes_en": s(c.get("notes_en")),
                    "minimum": minimum if minimum and _VERSION_RE.match(minimum) else None,
                }
    return None


def _host_ok(host: str) -> bool:
    return host == SITE_HOST or host.endswith("." + SITE_HOST)


def safe_site_url(url: Any, base: str = DEFAULT_BASE) -> str | None:
    """Selaimessa avattava osoite vain, jos se on https-osoite letkupaja.fi:ssä (tai testipalvelimessa base).
    Estää avaamasta vierasta osoitetta palvelimen vastauksesta tai CAD-dokumentista: hylkää file:, mailto: ja muut
    skeemat, käyttäjätunnukset (user@host), kenoviivat, välilyönnit ja ohjausmerkit. Palauttaa uudelleen kootun
    osoitteen tai None."""
    if not isinstance(url, str) or not url or len(url) > 4096 or _BAD_URL_CHARS.search(url):
        return None
    b = urlparse(base)
    if url.startswith("/") and not url.startswith("//"):
        if b.scheme not in ("https", "http") or not b.netloc:
            return None
        url = f"{b.scheme}://{b.netloc}{url}"
    try:
        u = urlparse(url)
        port = u.port
    except ValueError:
        return None
    if u.username or u.password:
        return None
    host = (u.hostname or "").lower()
    if not host:
        return None
    ok = u.scheme == "https" and _host_ok(host) and port in (None, 443)
    if not ok and b.hostname and u.scheme == b.scheme and host == b.hostname.lower() and port == b.port:
        ok = True
    if not ok:
        return None
    netloc = host + (f":{port}" if port else "")
    return urlunparse((u.scheme, netloc, u.path or "/", u.params, u.query, u.fragment))


_OS_NAMES = {"Darwin": "macOS"}
_NAME_BAD = re.compile(r"[^\w ./()\-+,]", re.UNICODE)


def device_name() -> str:
    """Laitteen nimi tilin laitelistaan: ohjelma ja käyttöjärjestelmä (ei konenimeä eikä käyttäjänimeä). Vain
    palvelimen hyväksymät merkit (kirjaimet, numerot, välilyönti ja . / ( ) - _ + ,)."""
    fc = ""
    try:
        import FreeCAD as App  # type: ignore

        fc = " " + ".".join(str(x) for x in App.Version()[:2])
    except Exception:  # noqa: BLE001
        pass
    system = platform.system() or "OS"
    name = f"FreeCAD{fc} / {_OS_NAMES.get(system, system)}"
    return re.sub(r" {2,}", " ", _NAME_BAD.sub(" ", name)).strip()[:60]


def _origin(url: str) -> tuple[str, str, int | None]:
    u = urlparse(url)
    try:
        port = u.port
    except ValueError:
        port = -1
    return (u.scheme, (u.hostname or "").lower(), port)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Uudelleenohjaukset: tunnisteellista pyyntöä ei ohjata minnekään (tunniste ei saa lähteä muualle), eikä mitään
    pyyntöä ohjata https:stä http:hen. Muut (esim. julkinen www -> apex) sallitaan."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802 - urllib-rajapinta
        if req.has_header("Authorization") or req.get_header("Authorization"):
            raise RedirectRefused("unexpected redirect")
        old, new = _origin(req.full_url), _origin(newurl)
        if old[0] == "https" and new[0] != "https":
            raise RedirectRefused("insecure redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _retry_after(headers: dict[str, str], js: Any) -> int | None:
    raw = headers.get("retry-after")
    try:
        if raw is not None:
            return max(1, min(3600, int(float(raw))))
    except ValueError:
        pass
    if isinstance(js, dict) and isinstance(js.get("interval"), (int, float)) and not isinstance(js.get("interval"), bool):
        return max(1, min(3600, int(js["interval"])))
    return None


def _ssl_context() -> ssl.SSLContext:
    """Käyttöjärjestelmän varmennesäilö (truststore), jos paketti on saatavilla; muuten Pythonin oletus."""
    try:
        import truststore  # type: ignore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001 - ei asennettu tai vanha Python
        return ssl.create_default_context()


def _is_cert_error(e: BaseException) -> bool:
    reason = getattr(e, "reason", None)
    for x in (e, reason):
        if isinstance(x, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(x):
            return True
    return False


class Client:
    def __init__(self, base: str = DEFAULT_BASE, token: str = "", timeout: float = 30.0, opener: Callable[..., Any] | None = None):
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._open = opener or self._default_open

    @staticmethod
    def _default_open(req: urllib.request.Request, timeout: float):
        ctx = _ssl_context()
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), _SafeRedirect())
        return opener.open(req, timeout=timeout)  # noqa: S310 - https (tai paikallinen testi)

    def _request(self, method: str, path: str, body: Any = None, auth: bool = False, accept: str = "application/json", headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        hdrs = {"User-Agent": f"Letkupaja-FreeCAD/{VERSION}", "Accept": accept}
        hdrs.update(headers or {})
        if data is not None:
            hdrs["Content-Type"] = "application/json"
        if auth:
            if not self.token:
                raise AuthRequired("not signed in", 401)
            hdrs["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.base + path, data=data, headers=hdrs, method=method)
        try:
            with self._open(req, self.timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                return int(status), {k.lower(): v for k, v in resp.headers.items()}, resp.read(MAX_STEP_BYTES + 1)
        except urllib.error.HTTPError as e:
            try:
                raw = e.read(1024 * 1024)
            except Exception:  # noqa: BLE001
                raw = b""
            return int(e.code), {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}, raw
        except RedirectRefused as e:
            raise NetworkError(str(e.reason)) from None
        except (urllib.error.URLError, OSError, ssl.SSLError) as e:
            reason = getattr(e, "reason", None) or e
            raise NetworkError(str(reason), certificate=_is_cert_error(e)) from None

    @staticmethod
    def _json(raw: bytes) -> Any:
        try:
            return json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, ValueError):
            return None

    def _error(self, status: int, raw: bytes, headers: dict[str, str] | None = None) -> ApiError:
        js = self._json(raw)
        msg = ""
        if isinstance(js, dict):
            for k in ("error", "message", "virhe", "viesti"):
                if isinstance(js.get(k), str):
                    msg = js[k][:500]
                    break
        if not msg:
            msg = f"HTTP {status}"
        retry = _retry_after(headers or {}, js) if status in (429, 503) else None
        if status in (401, 403):
            return AuthRequired(msg, status, js)
        if status == 501:
            return NotAvailable(msg, status, js)
        if status == 409 and isinstance(js, dict) and js.get("code") == "order_pending":
            return OrderPending(msg, status, js, self.base)
        return ApiError(msg, status, js, retry)

    def get_json(self, path: str, auth: bool = False) -> Any:
        status, h, raw = self._request("GET", path, auth=auth)
        if status != 200:
            raise self._error(status, raw, h)
        js = self._json(raw)
        if js is None:
            raise ApiError("invalid JSON", status)
        return js

    # julkiset
    def version_info(self) -> Any:
        return self.get_json("/api/cad/versio")

    def geometry(self, etag: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
        """Mittadata: (data, ETag). etag annettuna lähetetään If-None-Match; 304 -> (None, etag)."""
        hdrs = {"If-None-Match": etag} if etag else None
        status, h, raw = self._request("GET", "/cad/geometry.json", headers=hdrs)
        if status == 304:
            return None, etag
        if status != 200:
            raise self._error(status, raw, h)
        js = self._json(raw)
        if not isinstance(js, dict):
            raise ApiError("invalid geometry", 200)
        return js, h.get("etag")

    # laitekirjautuminen
    def device_start(self, name: str | None = None, lang: str | None = None) -> DeviceStart:
        """Laitekirjautumisen aloitus. lang = käyttöliittymän kieli ("fi"/"en"): englanniksi vahvistussivu ja
        kirjautumiskoodin sähköposti ovat englanniksi."""
        body: dict[str, Any] = {"client": CLIENT_ID, "device_name": name or device_name(), "version": VERSION}
        if lang in ("fi", "en"):
            body["lang"] = lang
        status, h, raw = self._request("POST", "/api/cad/laite/aloita", body)
        if status != 200:
            raise self._error(status, raw, h)
        js = self._json(raw)
        if not isinstance(js, dict) or not js.get("device_code") or not js.get("user_code"):
            raise ApiError("invalid device login response", status)
        uri = safe_site_url(js.get("verification_uri"), self.base) or self.base + "/"
        complete = safe_site_url(js.get("verification_uri_complete"), self.base)
        return DeviceStart(str(js["device_code"]), str(js["user_code"])[:20], uri, int(js.get("expires_in") or 900), max(1, int(js.get("interval") or 5)), complete)

    def device_poll(self, device_code: str) -> DevicePoll:
        status, h, raw = self._request("POST", "/api/cad/laite/tunnus", {"device_code": device_code})
        js = self._json(raw)
        st = js.get("status") if isinstance(js, dict) else None
        code = js.get("code") if isinstance(js, dict) else None
        if status == 200 and isinstance(js, dict) and (js.get("token") or js.get("access_token")):
            return DevicePoll("ok", str(js.get("token") or js.get("access_token")), str(js.get("expires_at") or ""))
        if status == 428 or st == "pending":
            return DevicePoll("pending")
        if status == 429 or st == "slow_down":
            return DevicePoll("slow_down", interval=_retry_after(h, js))
        if st == "denied" or code == "access_denied":
            return DevicePoll("denied")
        if status in (400, 404, 410):
            return DevicePoll("expired")
        raise self._error(status, raw, h)

    def logout(self) -> bool:
        """Peru tämä tunniste palvelimella (POST /api/cad/laite/ulos). True = peruttu tai jo virheellinen."""
        status, h, raw = self._request("POST", "/api/cad/laite/ulos", {}, auth=True)
        if status in (200, 204, 401):
            return True
        raise self._error(status, raw, h)

    # kirjautuneena
    def entitlements(self) -> Any:
        return self.get_json("/api/cad/oikeudet", auth=True)

    def entitlement_check(self, config: dict[str, Any]) -> dict[str, Any]:
        """POST /api/cad/oikeudet {config}: onko tähän asennelmaan oikeus (ei kuormita CAD-palvelua).
        {"entitled", "via" (order | free_step), "exact" (täsmälleen tilattu malli; false = sama letku ja liittimet toisella
        pituudella), "code" (order_pending | payment_required), "owned": parse_owned(...) (aina tyhjä), ...}."""
        status, h, raw = self._request("POST", "/api/cad/oikeudet", {"config": config}, auth=True)
        if status != 200:
            raise self._error(status, raw, h)
        js = self._json(raw)
        if not isinstance(js, dict):
            raise ApiError("invalid JSON", status)
        js["owned"] = parse_owned(js.get("owned"))
        return js

    def step(self, config: dict[str, Any]) -> bytes:
        """Tarkka STEP-malli asennelmalle. 402 -> PaymentRequired, 401/403 -> AuthRequired, muut -> ApiError."""
        status, h, raw = self._request("POST", "/api/cad/step", {"config": config}, auth=True, accept="model/step, application/step, application/json")
        if status == 200:
            if len(raw) > MAX_STEP_BYTES:
                raise ApiError("STEP too large", status)
            if not raw.lstrip()[:32].startswith(STEP_MAGIC):
                raise ApiError("response is not a STEP file", status)
            return raw
        if status == 402:
            js = self._json(raw)
            js = js if isinstance(js, dict) else {}
            price = js.get("priceEur")
            return_price = float(price) if isinstance(price, (int, float)) and not isinstance(price, bool) else None
            raise PaymentRequired(str(js.get("error") or js.get("message") or "payment required")[:500], safe_site_url(js.get("buyUrl"), self.base), return_price, js, self.base)
        raise self._error(status, raw, h)
