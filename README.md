# kubecontext

Ein interaktives TUI-Tool (Terminal User Interface) zur effizienten und sicheren Verwaltung von Kubernetes-Kontexten in der lokalen ~/.kube/config.

kubecontext vereinfacht das Handling mehrerer Cluster, indem es das manuelle Editieren von Kubeconfigs überflüssig macht. Es kombiniert nahtlosen Remote-Import per SSH, integriertes Tunnel-Management und schnelle Kontext-Wechsel in einer intuitiven Terminal-Oberfläche.

## Hauptfunktionen (Features)

* Interaktiver Kontext-Wechsel: Schnelles Umschalten des aktiven Kubernetes-Kontexts über ein übersichtliches Auswahlmenü im Terminal.
* Remote-Kubeconfig-Import: Sicherer Import von Kubeconfigs direkt von Remote-Servern via SSH.
* Lokaler Datei-Import: Kubeconfig-Dateien von Cloud-Anbietern (AWS EKS, GKE, Azure …) oder lokalen Pfaden in die eigene Config mergen.
* Integriertes SSH-Tunnel-Management: Automatisches Erstellen und Verwalten von SSH-Tunneln für Cluster, die nicht direkt öffentlich erreichbar sind.
* Live-Validierung: Direktes Überprüfen der Cluster-Erreichbarkeit aller konfigurierten Kontexte über einen eigenen Menüpunkt.
* Kontext-Export: Ausgewählte Kontexte als eigenständige Kubeconfig in eine Datei oder auf stdout ausgeben.
* Sicheres Bereinigen: Alte oder ungenutzte Kontexte direkt und sauber aus der Konfiguration löschen.

## Voraussetzungen

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- `kubectl` (nur für den Validate-Befehl)
- SSH-Zugang zu den Remote-Hosts (für SSH Import)

## Entwicklung

Dependencies installieren und virtuelle Umgebung einrichten:

```sh
uv sync
```

Dependencies auf die neuesten kompatiblen Versionen aktualisieren und `uv.lock` neu schreiben:

```sh
uv lock --upgrade
```

Statische Typprüfung:

```sh
uv run pyright
```

Linting:

```sh
uv run ruff check
```

Tests ausführen:

```sh
uv run pytest
```

## Starten (Entwicklung)

```sh
uv run -m kubecontext.main
```

`uv run` stellt sicher, dass die virtuelle Umgebung aktuell ist und alle Dependencies aus `uv.lock` installiert sind, bevor das Skript gestartet wird.

Vor jedem Menüaufruf wird eine Tabelle aller vorhandenen Kontexte angezeigt, gefolgt vom Hauptmenü.

## Paket bauen und installieren

### Wheel bauen

```sh
uv build
# Ergebnis:
#   dist/kubecontext-0.1.0-py3-none-any.whl
#   dist/kubecontext-0.1.0.tar.gz
```

### Wheel testen ohne Installation

```sh
uv run --with dist/kubecontext-0.1.0-py3-none-any.whl kubecontext
uv run --with dist/kubecontext-0.1.0-py3-none-any.whl kubecontext --help
```

Nach einem neuen Build den Cache leeren, damit uv die neue Version lädt:

```sh
uv cache clean kubecontext
```

### Systemweit installieren

```sh
uv tool install dist/kubecontext-0.1.0-py3-none-any.whl

# Danach direkt aufrufbar:
kubecontext

# Deinstallieren:
uv tool uninstall kubecontext

# Neu installieren (Update):
uv tool install --force dist/kubecontext-0.1.0-py3-none-any.whl
```

### Als Abhängigkeit in einem anderen Projekt nutzen

```sh
uv add dist/kubecontext-0.1.0-py3-none-any.whl
```

---

## Befehle

### SSH Import

Lädt die `~/.kube/config` eines Remote-Servers per SSH herunter und merged sie in die lokale Kubeconfig.

- Die verfügbaren Hosts werden aus `~/.ssh/config` gelesen (keine Wildcard-Einträge).
- Kontexte, Cluster und User werden auf den SSH-Hostnamen umbenannt. Der ursprüngliche Name wird mit `@` getrennt angehängt (`hostname@originalname`).
- Vor dem Schreiben wird eine Vorschau der zusammengeführten Config angezeigt.
- Bestehende Einträge mit gleichem Namen werden überschrieben.
- Es wird automatisch ein Backup angelegt (`~/.kube/config.backup.<timestamp>`).

---

### Datei-Import

Merged eine lokale Kubeconfig-Datei in die eigene `~/.kube/config`.

- Typischer Anwendungsfall: Kubeconfig-Dateien von Cloud-Anbietern (AWS EKS, GKE, Azure AKS) oder aus einem `KUBECONFIG`-Export.
- Dateipfad wird manuell eingegeben; `~`-Expansion wird unterstützt.
- Bei mehreren Kontexten in der Datei: Checkbox-Auswahl der zu importierenden Kontexte.
- Bestehende Einträge mit gleichem Namen werden überschrieben.
- Vor dem Schreiben wird eine Vorschau der zusammengeführten Config angezeigt.
- Es wird automatisch ein Backup angelegt (`~/.kube/config.backup.<timestamp>`).

---

### Tunnels

Verwaltet SSH-Port-Forwards für per SSH importierte Kontexte.

- Zeigt eine Übersicht aller SSH-importierten Kontexte (Kontextname enthält `@`) mit aktuellem Tunnel-Status (`● open` / `○ closed` / `? no port`).
- Tunnels werden als lokale Port-Forwards aufgebaut: `localhost:<port> → <ssh-host>:<remote-host>:<port>`.
- Der Tunnel-Zustand wird in `~/.kube/kubecontext_tunnels.json` gespeichert und beim nächsten Start wiederhergestellt (noch laufende Prozesse bleiben erhalten, beendete werden entfernt).
- Beim Beenden des Programms wird gefragt, ob laufende Tunnels offen bleiben sollen.
- Erfordert `ssh` im `PATH`.

---

### Set context

Wechselt den aktiven Kubernetes-Kontext (`current-context` in der Kubeconfig).

- Zeigt alle vorhandenen Kontexte in einer Liste, der aktuell aktive ist mit `→` markiert.
- Die Änderung wird sofort in `~/.kube/config` gespeichert.

---

### Delete

Löscht einen Kontext aus der Kubeconfig.

- Zeigt alle Kontexte zur Auswahl; der aktuell aktive ist gekennzeichnet.
- Wird der zugehörige Cluster oder User von keinem anderen Kontext mehr referenziert, werden diese ebenfalls entfernt (Orphan-Bereinigung).
- Ist der gelöschte Kontext der aktive, wird automatisch der erste verbleibende Kontext aktiviert.
- Vor dem Löschen wird ein Backup erstellt.

---

### Export

Exportiert einen oder mehrere Kontexte als eigenständige Kubeconfig.

- Zeigt alle vorhandenen Kontexte zur Mehrfachauswahl (Checkbox).
- Die exportierte Config enthält nur die gewählten Kontexte sowie zugehörige Cluster und User.
- Ausgabe wahlweise auf stdout (YAML-Vorschau) oder in eine Datei (mit Berechtigungen `0600`).
- Bereits vorhandene Zieldatei wird nur nach Bestätigung überschrieben.

---

### Validate

Prüft die Erreichbarkeit aller konfigurierten Cluster mit `kubectl cluster-info`.

- Erfordert `kubectl` im `PATH`.
- Zeigt eine Tabelle mit Kontext, Server-URL und Status (`✓ OK` / `✗ Fehler` / `✗ timeout`).
- Timeout pro Kontext: 10 Sekunden.
