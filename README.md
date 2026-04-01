# kubecontext

Interaktives TUI-Tool zur Verwaltung von Kubernetes-Kontexten in `~/.kube/config`. Es ermöglicht das Importieren von Kubeconfigs von Remote-Servern per SSH, das Wechseln des aktiven Kontexts, das Löschen von Kontexten sowie die Validierung der Cluster-Erreichbarkeit – alles über ein einfaches Auswahlmenü im Terminal.

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

## Starten

```sh
uv run main.py
```

`uv run` stellt sicher, dass die virtuelle Umgebung aktuell ist und alle Dependencies aus `uv.lock` installiert sind, bevor das Skript gestartet wird. Alternativ kann nach `uv sync` auch direkt `python main.py` verwendet werden.

Nach dem Start wird eine Tabelle aller vorhandenen Kontexte angezeigt, gefolgt vom Hauptmenü.

---

## Befehle

### SSH Import

Lädt die `~/.kube/config` eines Remote-Servers per SSH herunter und merged sie in die lokale Kubeconfig.

- Die verfügbaren Hosts werden aus `~/.ssh/config` gelesen (keine Wildcard-Einträge).
- Kontexte, Cluster und User werden auf den SSH-Hostnamen umbenannt. Bei mehreren Kontexten wird der ursprüngliche Name als Suffix angehängt (`hostname-originalname`).
- Vor dem Schreiben wird eine Vorschau der zusammengeführten Config angezeigt.
- Bestehende Einträge mit gleichem Namen werden überschrieben.
- Es wird automatisch ein Backup angelegt (`~/.kube/config.backup.<timestamp>`).

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

### Validate

Prüft die Erreichbarkeit aller konfigurierten Cluster mit `kubectl cluster-info`.

- Erfordert `kubectl` im `PATH`.
- Zeigt eine Tabelle mit Kontext, Server-URL und Status (`✓ OK` / `✗ Fehler` / `✗ timeout`).
- Timeout pro Kontext: 10 Sekunden.
