"""Oberflächensprache: Deutsch (Quelltext) oder Englisch.

Die deutschen Texte im Code sind zugleich die Schlüssel; ``tr()`` liefert die
englische Fassung, wenn Englisch eingestellt ist. Platzhalter im Stil von
``str.format`` (``{name}``) werden in beiden Sprachen gleich befüllt. Fehlt eine
Übersetzung, bleibt der deutsche Text stehen – nie ein leeres Feld.
"""

from __future__ import annotations

import sys

LANGUAGES = {"de": "Deutsch", "en": "English"}  # Anzeige immer in der jeweiligen Sprache

_language = "de"


def set_language(lang: str) -> None:
    global _language
    _language = lang if lang in LANGUAGES else "de"


def language() -> str:
    return _language


def system_language() -> str:
    """Sprache für den allerersten Start: deutsches Windows → Deutsch, sonst Englisch."""
    if sys.platform == "win32":
        try:
            import ctypes
            primary = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
            return "de" if primary == 0x07 else "en"  # 0x07 = LANG_GERMAN (DE, AT, CH, …)
        except (AttributeError, OSError):
            pass
    import locale
    code = (locale.getlocale()[0] or "").lower()
    return "de" if code.startswith(("de", "german")) else "en"


def tr(text: str, /, **values: object) -> str:
    out = EN.get(text, text) if _language == "en" else text
    return out.format(**values) if values else out


EN: dict[str, str] = {
    # Ausklappfenster
    "Anlage nicht erreichbar": "Raumfeld system not reachable",
    "Musik": "Music",
    "Sendersuche": "Find stations",
    "Räume koppeln": "Group rooms",
    "Einstellungen": "Settings",
    "Favoriten · Ziel: {names}": "Favorites · Target: {names}",
    "Favoriten · Ziel: Raum anklicken": "Favorites · Target: click a room",
    "Hier läuft nichts – Sender wählen": "Nothing playing here – pick a station",
    "{room} einschalten": "Turn on {room}",
    "{room} in Standby": "Put {room} in standby",
    "Vorheriger Titel": "Previous track",
    "Nächster Titel": "Next track",
    "Zufall": "Shuffle",
    "Zufall (nicht mit „einen wiederholen“)": "Shuffle (not with “repeat one”)",
    "Wiederholen: aus": "Repeat: off",
    "Wiederholen: alle": "Repeat: all",
    "Wiederholen: einen Titel": "Repeat: one track",
    "Als Ziel für Sender und Tastenkürzel wählen": "Use as target for stations and hotkeys",
    "Gruppe als Ziel für Sender und Tastenkürzel wählen": "Use group as target for stations and hotkeys",
    # Taskleistensymbol
    "DeskRemote – verbinde …": "DeskRemote – connecting …",
    "DeskRemote – Anlage nicht erreichbar": "DeskRemote – Raumfeld system not reachable",
    "Alle pausieren": "Pause all",
    "Beenden": "Quit",
    # Koppeln
    "Diese Räume gemeinsam abspielen:": "Play these rooms together:",
    "Haken setzen koppelt, Haken entfernen trennt.": "Tick to group, untick to ungroup.",
    "Übernehmen": "Apply",
    "Gekoppelt: {names}": "Grouped: {names}",
    "Keine Räume gekoppelt": "No rooms grouped",
    "Wird übernommen …": "Applying …",
    # Sendersuche
    "Österreich": "Austria",
    "Deutschland": "Germany",
    "Schweiz": "Switzerland",
    "Alle Länder": "All countries",
    "Sender suchen": "Find stations",
    "Sendername, z. B. FM4": "Station name, e.g. FM4",
    "Suche …": "Searching …",
    "Suche fehlgeschlagen": "Search failed",
    "Keine Sender gefunden": "No stations found",
    "spielt auf Raumfeld nicht": "won't play on Raumfeld",
    "Im Ziel-Raum abspielen": "Play in target room",
    "Zu den Favoriten": "Add to favorites",
    "HLS-Stream – den können die Raumfeld-Lautsprecher nicht abspielen":
        "HLS stream – Raumfeld speakers can't play it",
    "Senderverzeichnis nicht erreichbar": "Station directory not reachable",
    # Musik
    "Quelle:": "Source:",
    "Album suchen …": "Search albums …",
    "Künstler suchen …": "Search artists …",
    "Alben": "Albums",
    "Künstler": "Artists",
    "Ordner": "Folders",
    "‹ Zurück": "‹ Back",
    "Quelle nicht erreichbar": "Source not reachable",
    "Meine Musik (Raumfeld)": "My Music (Raumfeld)",
    "Keine Musikquelle gefunden – Musikordner in der Teufel-App hinzufügen":
        "No music source found – add your music folder in the Teufel app",
    "Suche Musikquellen …": "Looking for music sources …",
    "Suche: {text}": "Search: {text}",
    "Lade …": "Loading …",
    "Nichts gefunden": "Nothing found",
    "{count} Titel": "{count} tracks",
    "Öffnen": "Open",
    "Alle Titel im Zufallsmodus": "All tracks, shuffled",
    "Abspielen": "Play",
    "Ab diesem Titel abspielen": "Play from this track",
    "{name} · Zufall": "{name} · Shuffle",
    "Als Favorit speichern": "Save as favorite",
    "Dieser Eintrag ist zu groß für einen Favoriten": "This entry is too large for a favorite",
    "Favorit gespeichert: {name}": "Favorite saved: {name}",
    "Kein Ziel-Raum gewählt – im Ausklappfenster einen Raum anklicken":
        "No target room – click a room in the tray window",
    # Einstellungen
    "Lauter (Ziel-Raum)": "Volume up (target room)",
    "Leiser (Ziel-Raum)": "Volume down (target room)",
    "Play/Pause (Ziel-Raum)": "Play/Pause (target room)",
    "Favorit 1": "Favorite 1",
    "Favorit 2": "Favorite 2",
    "Favorit 3": "Favorite 3",
    "Standby (Ziel-Raum)": "Standby (target room)",
    "Album": "Album",
    "Titel": "Track",
    "Künstler · Zufall": "Artist · Shuffle",
    "Überschrift im Fenster:": "Window heading:",
    "Sprache:": "Language:",
    "Fehlende Cover online suchen (MusicBrainz – sendet nur Interpret und Album)":
        "Look up missing covers online (MusicBrainz – sends only artist and album)",
    "Favoriten": "Favorites",
    "Hoch": "Up",
    "Runter": "Down",
    "Umbenennen…": "Rename…",
    "Löschen": "Delete",
    "Adresse von Hand…": "Add URL manually…",
    "Tastenkürzel": "Hotkeys",
    "Funktionieren überall, auch wenn das Fenster zu ist. Alles geht auch per Maus.":
        "Work everywhere, even with the window closed. Everything also works with the mouse.",
    "Abbrechen": "Cancel",
    "Speichern": "Save",
    "Umbenennen": "Rename",
    "Neuer Name:": "New name:",
    "Sender von Hand": "Add station manually",
    "Name:": "Name:",
    "Stream-Adresse (http/https):": "Stream URL (http/https):",
    "Die Adresse muss mit http:// oder https:// beginnen.": "The URL must start with http:// or https://.",
    "Sprache": "Language",
    "Die neue Sprache gilt nach einem Neustart von DeskRemote.":
        "The new language takes effect after DeskRemote restarts.",
    "Jetzt neu starten": "Restart now",
    "Später": "Later",
    # Programm und Meldungen
    "Kein Ziel-Raum gewählt – im Fenster einen Raum anklicken.":
        "No target room – click a room in the window.",
    "Tastenkürzel schon belegt oder ungültig: {names}": "Hotkeys already taken or invalid: {names}",
    "Die Anlage hat die Aktion abgelehnt (HTTP {status})": "The Raumfeld system rejected the action (HTTP {status})",
    "Die Anlage hat eine unverständliche Antwort geschickt": "The Raumfeld system sent an unreadable answer",
    "Unbekannte Aktion: {action}": "Unknown action: {action}",
    "Gerät {udn} nicht gefunden": "Device {udn} not found",
    "Gerät {udn} meldet eine Adresse außerhalb des Heimnetzes": "Device {udn} reports an address outside the home network",
    "Raum {room} hat keinen Lautsprecher": "Room {room} has no speaker",
    "Raum nicht gefunden": "Room not found",
    "{room} konnte nicht vorbereitet werden": "{room} could not be prepared",
    "Die Zone ist nicht mehr da – bitte erneut versuchen": "The zone is gone – please try again",
    "Die Lautsprecher wachen nicht auf – bitte kurz warten und erneut versuchen":
        "The speakers don't wake up – please wait a moment and try again",
    "Album konnte nicht gestartet werden ({error})": "Album could not be started ({error})",
    "Diese Quelle kann Raumfeld nicht direkt abspielen": "Raumfeld can't play this source directly",
    "Musik läuft, aber der Zufallsmodus ließ sich nicht einschalten": "Music is playing, but shuffle couldn't be turned on",
    "{name} konnte nicht starten – noch in der Bibliothek?": "{name} couldn't start – still in the library?",
    "In {room} läuft nichts": "Nothing is playing in {room}",
    "{name} spielt auf Raumfeld nicht (HLS-Stream)": "{name} won't play on Raumfeld (HLS stream)",
    "Sender nicht erreichbar ({error})": "Station not reachable ({error})",
    "Sender nicht erreichbar: {name}": "Station not reachable: {name}",
    "Lautstärke von {room} nicht lesbar": "Can't read the volume of {room}",
    "Dienst {service} nicht vorhanden": "Service {service} not available",
    "{action} fehlgeschlagen: {code} {desc}": "{action} failed: {code} {desc}",
    "{action}: ungültige Antwort (HTTP {status})": "{action}: invalid answer (HTTP {status})",
    # Teufel-Favoriten (gemeinsame Liste mit der Teufel-App)
    "(nicht mehr in der Liste)": "(no longer in the list)",
    "Diese Favoriten werden auf der Anlage entfernt – auch in der Teufel-App:\n\n{names}":
        "These favorites will be removed from the Raumfeld system – also in the Teufel app:\n\n{names}",
    "Diese Liste ist dieselbe wie in der Teufel-App. Neue Favoriten über ☆ in Sendersuche oder Musik. "
    "Hoch/Runter sortiert auch die Teufel-App um.":
        "This is the same list as in the Teufel app. Add new favorites with ☆ in station search or music. "
        "Up/Down also reorders the Teufel app.",
    "Pause": "Pause",
    "Weiterspielen": "Resume",
    "Entfernen": "Remove",
    "Entfernt den Favoriten auch in der Teufel-App": "Also removes the favorite in the Teufel app",
    "Favorit {n} spielt:": "Favorite {n} plays:",
    "Platz {n} der Liste": "Position {n} in the list",
    "Raumfeld-Medienserver nicht gefunden": "Raumfeld media server not found",
    "Suche fehlgeschlagen – ist die Anlage erreichbar?": "Search failed – is the Raumfeld system reachable?",
    "Sucht bei TuneIn über die Anlage. ☆ legt den Sender in den Teufel-Favoriten an.":
        "Searches TuneIn through the Raumfeld system. ☆ adds the station to the Teufel favorites.",
    "Tastenkürzel neu festlegen (Einstellungen): {names} steht nicht in den Teufel-Favoriten":
        "Please set the hotkeys again (Settings): {names} is not among the Teufel favorites",
    "Teufel-Favorit": "Teufel favorite",
    "Teufel-Favorit · Album": "Teufel favorite · album",
    "Teufel-Favorit · Künstler": "Teufel favorite · artist",
    "Teufel-Favorit · Sender": "Teufel favorite · station",
    "Teufel-Favorit · Titel": "Teufel favorite · track",
    "Teufel-Favorit · Wiedergabeliste": "Teufel favorite · playlist",
    "Teufel-Favoriten": "Teufel favorites",
    "Zu den Teufel-Favoriten (auch in der Teufel-App)": "Add to Teufel favorites (also in the Teufel app)",
    "{name} konnte nicht starten": "{name} could not start",
    "{name} konnte nicht starten ({error})": "{name} could not start ({error})",
    "{name} steht nicht mehr in den Teufel-Favoriten": "{name} is no longer among the Teufel favorites",
}
