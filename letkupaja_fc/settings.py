# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Asetukset FreeCADin parametreissa (User parameter:BaseApp/Preferences/Mod/Letkupaja) ja kirjautumistunniste.

Kirjautumistunniste EI ole FreeCADin user.cfg-tiedostossa (näkyy Työkalut > Muokkaa parametreja -ikkunassa ja
päätyy helposti varmuuskopioihin ja vikailmoitusten liitteisiin), vaan omassa tiedostossaan, jonka oikeudet ovat
vain käyttäjälle (0600):
* Windows: %LOCALAPPDATA%\\Letkupaja\\FreeCAD\\token.json (paikallinen, ei verkkoprofiilin mukana siirtyvä kansio)
* macOS ja Linux: <FreeCADin käyttäjäkansio>/Letkupaja/token.json
Tunniste on sidottu palvelimeen, jolta se saatiin: jos ApiBase muuttuu, tunnistetta ei lähetetä uudelle palvelimelle.
Vanhentunutta tunnistetta ei käytetä. Tunnistetta ei koskaan tulosteta lokiin tai konsoliin.
Ilman FreeCADia (yksikkötestit) käytetään muistinvaraista tallennusta.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/Letkupaja"
DEFAULT_API_BASE = "https://letkupaja.fi"
SITE_HOST = "letkupaja.fi"
LOCAL_HOSTS = ("127.0.0.1", "localhost")


class _MemoryParams:
    """FreeCADin ParameterGrp-rajapinnan osa testejä varten."""

    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    def GetString(self, k: str, d: str = "") -> str:  # noqa: N802 - FreeCADin nimet
        return str(self.data.get("s:" + k, d))

    def SetString(self, k: str, v: str) -> None:  # noqa: N802
        self.data["s:" + k] = v

    def GetFloat(self, k: str, d: float = 0.0) -> float:  # noqa: N802
        return float(self.data.get("f:" + k, d))  # type: ignore[arg-type]

    def SetFloat(self, k: str, v: float) -> None:  # noqa: N802
        self.data["f:" + k] = float(v)

    def GetBool(self, k: str, d: bool = False) -> bool:  # noqa: N802
        return bool(self.data.get("b:" + k, d))

    def SetBool(self, k: str, v: bool) -> None:  # noqa: N802
        self.data["b:" + k] = bool(v)

    def RemString(self, k: str) -> None:  # noqa: N802
        self.data.pop("s:" + k, None)


_MEMORY = _MemoryParams()


def _params():
    try:
        import FreeCAD as App  # type: ignore

        return App.ParamGet(PARAM_PATH)
    except ImportError:
        return _MEMORY


def _have_freecad() -> bool:
    try:
        import FreeCAD  # type: ignore  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------------------------- tunniste


class MemoryTokenStore:
    def __init__(self) -> None:
        self.data: dict[str, str] | None = None

    def load(self) -> dict[str, str] | None:
        return dict(self.data) if self.data else None

    def save(self, data: dict[str, str]) -> None:
        self.data = dict(data)

    def clear(self) -> None:
        self.data = None


class FileTokenStore:
    """Tunniste JSON-tiedostossa, oikeudet 0600 (vain käyttäjä). Kirjoitus väliaikaistiedoston kautta."""

    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> dict[str, str] | None:
        try:
            with open(self.path, encoding="utf-8") as f:
                js = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(js, dict) or not isinstance(js.get("token"), str) or not js["token"]:
            return None
        return {k: str(v) for k, v in js.items() if isinstance(v, (str, int, float))}

    def save(self, data: dict[str, str]) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        try:
            os.remove(tmp)
        except OSError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)

    def clear(self) -> None:
        try:
            os.remove(self.path)
        except OSError:
            pass


def default_token_path() -> str:
    if sys.platform.startswith("win") and os.environ.get("LOCALAPPDATA"):
        return os.path.join(os.environ["LOCALAPPDATA"], "Letkupaja", "FreeCAD", "token.json")
    return os.path.join(Settings.data_dir(), "token.json")


def normalize_base(raw: str, allow_custom: bool = False) -> str | None:
    """Kelpaava rajapinnan osoite (ilman loppukauttaviivaa) tai None. https letkupaja.fi:hin (tai sen alidomainiin);
    http ja https vain täsmälleen 127.0.0.1:een tai localhostiin (testit); muut https-osoitteet vain, jos
    allow_custom (kehittäjän asetus AllowCustomApiBase)."""
    raw = (raw or "").strip().rstrip("/")
    if not raw or any(c in raw for c in "\\@ \t\r\n"):
        return None
    try:
        u = urlparse(raw)
        port = u.port
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if not host or u.username or u.password or u.query or u.fragment or (u.path not in ("", "/")):
        return None
    netloc = host + (f":{port}" if port else "")
    if host in LOCAL_HOSTS and u.scheme in ("http", "https"):
        return f"{u.scheme}://{netloc}"
    if u.scheme != "https":
        return None
    if host == SITE_HOST or host.endswith("." + SITE_HOST) or allow_custom:
        return f"https://{netloc}"
    return None


def _expired(expires_at: str) -> bool:
    if not expires_at:
        return False
    try:
        t = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t <= datetime.now(timezone.utc)


class Settings:
    def __init__(self, params=None, token_store=None) -> None:
        self.p = params or _params()
        if token_store is None:
            token_store = FileTokenStore(default_token_path()) if (params is None and _have_freecad()) else _memory_store_for(self.p)
        self.tokens = token_store
        self._migrate_param_token()

    # yleiset
    def get_str(self, key: str, default: str = "") -> str:
        return self.p.GetString(key, default)

    def set_str(self, key: str, value: str) -> None:
        self.p.SetString(key, value)

    def get_float(self, key: str, default: float) -> float:
        return self.p.GetFloat(key, default)

    def set_float(self, key: str, value: float) -> None:
        self.p.SetFloat(key, value)

    def get_bool(self, key: str, default: bool) -> bool:
        return self.p.GetBool(key, default)

    def set_bool(self, key: str, value: bool) -> None:
        self.p.SetBool(key, value)

    @staticmethod
    def freecad_language() -> str:
        try:
            import FreeCAD as App  # type: ignore

            return App.ParamGet("User parameter:BaseApp/Preferences/General").GetString("Language", "")
        except ImportError:
            return ""

    # palvelin
    @property
    def api_base(self) -> str:
        """Rajapinnan osoite: letkupaja.fi (https), testeissä 127.0.0.1/localhost. Muu osoite vain kehittäjän
        asetuksella AllowCustomApiBase; virheellinen arvo -> letkupaja.fi."""
        return normalize_base(self.get_str("ApiBase", ""), self.get_bool("AllowCustomApiBase", False)) or DEFAULT_API_BASE

    # kirjautuminen
    def _migrate_param_token(self) -> None:
        """Vanhat versiot tallensivat tunnisteen FreeCADin parametreihin: siirretään tiedostoon ja poistetaan."""
        try:
            old = self.p.GetString("Token", "")
        except Exception:  # noqa: BLE001
            return
        if not old:
            return
        if self.tokens.load() is None:
            self.tokens.save({"token": old, "expires_at": self.p.GetString("TokenExpires", ""), "base": self.api_base})
        for k in ("Token", "TokenExpires"):
            try:
                self.p.RemString(k)
            except Exception:  # noqa: BLE001
                self.p.SetString(k, "")

    def _record(self) -> dict[str, str] | None:
        rec = self.tokens.load()
        if not rec:
            return None
        if _expired(rec.get("expires_at", "")):
            self.tokens.clear()
            return None
        return rec

    @property
    def token(self) -> str:
        """Tunniste vain, jos se on tämän palvelimen (ApiBase) ja voimassa."""
        rec = self._record()
        if not rec or rec.get("base", "").rstrip("/") != self.api_base:
            return ""
        return rec.get("token", "")

    @property
    def token_expires(self) -> str:
        rec = self._record()
        return rec.get("expires_at", "") if rec else ""

    def save_token(self, token: str, expires_at: str = "") -> None:
        self.tokens.save({"token": token, "expires_at": expires_at or "", "base": self.api_base})

    def clear_token(self) -> None:
        self.tokens.clear()
        for k in ("Token", "TokenExpires", "Account"):
            try:
                self.p.RemString(k)
            except Exception:  # noqa: BLE001 - vanhat FreeCAD-versiot
                self.set_str(k, "")

    def token_location(self) -> str:
        return getattr(self.tokens, "path", "")

    # tiedostot
    @staticmethod
    def data_dir() -> str:
        """Lisäosan tiedostot (ladatut mallit, päivitetty mittadata) käyttäjän FreeCAD-hakemistossa."""
        try:
            import FreeCAD as App  # type: ignore

            base = App.getUserAppDataDir()
        except ImportError:
            base = os.path.join(os.path.expanduser("~"), ".letkupaja-freecad")
        d = os.path.join(base, "Letkupaja")
        os.makedirs(d, exist_ok=True)
        return d

    def geometry_cache(self) -> str:
        return os.path.join(self.data_dir(), "geometry.json")

    def models_dir(self) -> str:
        d = os.path.join(self.data_dir(), "mallit")
        os.makedirs(d, exist_ok=True)
        return d


def _memory_store_for(params) -> MemoryTokenStore:
    """Muistinvarainen tunnistevarasto parametriobjektia kohden (sama Settings(params) näkee saman tunnisteen)."""
    store = getattr(params, "_lp_token_store", None)
    if store is None:
        store = MemoryTokenStore()
        try:
            params._lp_token_store = store
        except (AttributeError, TypeError):
            pass
    return store
