# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Asennustapa: FreeCADin Addon Manager vai Letkupajan zip-paketti (asennusmakro).

Addon Managerilla asennettu lisäosa päivitetään Addon Managerissa (Tools › Addon Manager), zip-paketista asennettu
lataamalla uusi paketti osoitteesta https://letkupaja.fi/cad-lisaosat#freecad. Tunnistus ilman verkkoa:

* Addon Managerin git-asennus: lisäosan kansiossa on .git
* Addon Manager 1.1+: Mod-kansion manifest.json kertoo kansion asennetuksi Addon Managerilla
* mukautettu varasto (Custom repositories): Addon Manager nimeää kansion repon mukaan (Mod/letkupaja-freecad),
  asennusmakro aina Mod/Letkupaja

FreeCAD 1.0:n Addon Manager ei merkitse zip-asennusta, joten tunnistamaton asennus saa ohjeen kumpaankin tapaan.
"""
from __future__ import annotations

import json
import os

ADDON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MACRO_DIR_NAME = "Letkupaja"  # asennusmakron kohde (install/Asenna-Letkupaja.FCMacro)
REPOSITORY_URL = "https://github.com/viltie/letkupaja-freecad"


def installed_with_addon_manager(addon_dir: str = ADDON_DIR) -> bool:
    """Onko tämä lisäosan kopio asennettu FreeCADin Addon Managerilla (julkisesta reposta)?"""
    if os.path.isdir(os.path.join(addon_dir, ".git")):
        return True
    name = os.path.basename(os.path.normpath(addon_dir))
    try:
        with open(os.path.join(os.path.dirname(os.path.normpath(addon_dir)), "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        if isinstance(manifest, dict) and name in manifest:
            return True
    except (OSError, ValueError):
        pass
    return name != MACRO_DIR_NAME
