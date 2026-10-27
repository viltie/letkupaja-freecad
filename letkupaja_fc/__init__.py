# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Letkupaja FreeCAD -lisäosa: hydrauliikkaletkun reititys, katkaisumitta ja tilauslinkki letkupaja.fi:hin.

Moduulit (FreeCAD-riippumattomat, testattavissa tavallisella Pythonilla):
* version   versio (ainoa lähde)
* core      letkuroute-ydin (alipaketti letkupaja_fc/letkuroute) ja ladatun mittadatan välimuisti
* channel   asennustapa (Addon Manager vai zip-paketti) päivitysohjetta varten
* i18n      suomi/englanti, ytimen varoitusten käännös
* catalog   letkujen ja liitinten nimet valikoihin
* envelope  liitinten yksinkertaistettu ulkomuoto (ilmainen "envelope") geometry.jsonista
* routing   päiden määrittely, reititys ytimellä, liitinten sijainnit
* api       letkupaja.fi-lisäosarajapinta (laitekirjautuminen, tarkka STEP, versiotarkistus)
* settings  asetukset ja tunniste (FreeCADin parametrit)

FreeCAD-riippuvaiset: fcgeom (Part-geometria, valinnat), model (dokumentin objektit), commands, task_route, login.
"""
from .version import VERSION

__version__ = VERSION
