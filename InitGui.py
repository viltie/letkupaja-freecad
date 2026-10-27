# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
# Letkupaja FreeCAD -lisäosa: hydrauliikkaletkun reititys ja tilaus Letkupajasta (https://letkupaja.fi).
# Huom: FreeCAD suorittaa tämän tiedoston exec-funktiolla, joten moduulitason nimet eivät näy luokan metodeille.
# Tuonnit tehdään siksi metodien sisällä. Tiedosto pidetään yksinkertaisena (myös vanha Python), jotta liian
# vanhassa FreeCADissa näytetään selkeä viesti.
import FreeCADGui as Gui


class LetkupajaWorkbench(Gui.Workbench):
    MenuText = "Letkupaja"
    ToolTip = "Hydrauliikkaletkut / hydraulic hoses – letkupaja.fi"

    def __init__(self):
        import os

        try:
            import letkupaja_fc

            base = os.path.dirname(os.path.dirname(os.path.abspath(letkupaja_fc.__file__)))
            self.__class__.Icon = os.path.join(base, "Resources", "icons", "Letkupaja.svg")
        except Exception:  # noqa: BLE001
            pass

    def Initialize(self):
        import sys

        import FreeCAD as App

        if sys.version_info < (3, 10):
            v = "%d.%d" % sys.version_info[:2]
            text = (
                "Letkupaja-lisäosa vaatii FreeCAD 1.0:n tai uudemman (Python 3.10+). Tämän FreeCADin Python on %s. "
                "Asenna uusi FreeCAD osoitteesta https://www.freecad.org/downloads.php.\n\n"
                "The Letkupaja add-on requires FreeCAD 1.0 or newer (Python 3.10+). This FreeCAD has Python %s. "
                "Install a newer FreeCAD from https://www.freecad.org/downloads.php." % (v, v)
            )
            App.Console.PrintError("Letkupaja: " + text + "\n")
            try:
                import FreeCADGui
                from PySide import QtGui

                QtGui.QMessageBox.warning(FreeCADGui.getMainWindow(), "Letkupaja", text)
            except Exception:  # noqa: BLE001
                pass
            return
        from letkupaja_fc import commands
        from letkupaja_fc.i18n import tr

        commands.register()
        self.appendToolbar("Letkupaja", commands.TOOLBAR)
        self.appendMenu(tr("wb.menu"), commands.MENU)

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def ContextMenu(self, recipient):
        try:
            from letkupaja_fc import commands

            if commands.selected_hose_groups():
                self.appendContextMenu("Letkupaja", ["Letkupaja_Route", "Letkupaja_Update", "Letkupaja_Order", "Letkupaja_Detailed"])
        except Exception:  # noqa: BLE001
            pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(LetkupajaWorkbench())
