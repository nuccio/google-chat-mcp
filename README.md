# MCP Server for Google Chat with ACL

Server MCP per Google Chat. Permette a Claude di leggere e scrivere in spazi Google Chat, con controllo granulare dei permessi per ogni spazio.

## Funzionalità

- **Controllo per spazio**: ogni spazio può essere configurato con sola lettura (`:r`), sola scrittura (`:w`), o entrambi (`:rw`)
- **Nessun DM**: il server non può inviare messaggi diretti a utenti specifici — solo spazi nominati
- **OAuth per-utente**: ogni persona usa il proprio account Google, il token viene salvato localmente
- **Distribuzione via `uvx`**: nessuna installazione manuale, funziona su Mac, Linux e Windows (con WSL)

## Tool esposti

| Tool | Permesso | Descrizione |
|------|----------|-------------|
| `list_spaces` | — | Spazi configurati con i loro permessi |
| `get_space` | lettura | Dettagli di uno spazio |
| `list_messages` | lettura | Messaggi dello spazio (con filtro opzionale) |
| `send_message` | scrittura | Invia un messaggio in uno spazio |
| `list_members` | lettura | Membri dello spazio |

---

## Configurazione

### 1. OAuth su Google Cloud Console

1. Apri [console.cloud.google.com](https://console.cloud.google.com) e seleziona (o crea) un progetto
2. Vai su **APIs & Services → Library**, cerca **Google Chat API** e abilitala
3. Vai su **APIs & Services → OAuth consent screen**:
   - Scegli **Internal** se usi Google Workspace aziendale (non richiede verifica), altrimenti **External**
   - Aggiungi gli scope (nella sezione "Scopes"):
     - `https://www.googleapis.com/auth/chat.spaces.readonly`
     - `https://www.googleapis.com/auth/chat.messages`
     - `https://www.googleapis.com/auth/chat.memberships.readonly`
4. Vai su **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Tipo applicazione: **Desktop app**
   - Assegna un nome (es. "Claude MCP")
5. Clicca **Download JSON** e salva il file come:
   ```
   ~/.config/google-chat-mcp/client_secrets.json
   ```
   > Su Windows con WSL, il percorso è dentro il filesystem WSL (vedi sotto).

### 2. Installazione WSL su Windows

> Salta questa sezione se usi Mac o Linux.

1. Apri **PowerShell come amministratore** e lancia:
   ```powershell
   wsl --install
   ```
   Questo installa WSL 2 con Ubuntu. Richiede un riavvio del sistema.

2. Al riavvio, Ubuntu si avvia automaticamente. Crea il tuo utente quando richiesto.

3. Dentro il terminale Ubuntu, installa `uv`:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   source ~/.bashrc
   ```

4. Crea la cartella di configurazione e copia il `client_secrets.json` scaricato in precedenza:
   ```bash
   mkdir -p ~/.config/google-chat-mcp
   # Copia il file da Windows (sostituisci "Utente" con il tuo nome utente Windows):
   cp /mnt/c/Users/Utente/Downloads/client_secrets*.json ~/.config/google-chat-mcp/client_secrets.json
   ```

---

## Primo avvio: autenticazione OAuth

Esegui questo comando **una sola volta** per autorizzare l'accesso al tuo account Google:

```bash
uvx "git+https://github.com/nuccio/google-chat-mcp" auth
```

Si aprirà il browser. Accedi con il tuo account Google e autorizza l'accesso. Il token viene salvato in `~/.config/google-chat-mcp/token.json` e rinnovato automaticamente.

## Trovare gli ID degli spazi

Per configurare quali spazi sono accessibili, hai bisogno del loro resource name (es. `spaces/AAABBBCCC`). Elencali tutti con:

```bash
uvx "git+https://github.com/nuccio/google-chat-mcp" spaces
```

---

## Configurazione Claude Desktop

Modifica il file di configurazione di Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "google-chat": {
      "command": "uvx",
      "args": [
        "git+https://github.com/nuccio/google-chat-mcp",
        "--space", "spaces/AAABBBCCC:rw",
        "--space", "spaces/DDDEEEFFF:r",
        "--space", "spaces/GGGHHH111:w"
      ]
    }
  }
}
```

**Formato permessi per `--space`:**
- `spaces/ID:r` — sola lettura
- `spaces/ID:w` — sola scrittura
- `spaces/ID:rw` — lettura e scrittura

Ripeti `--space` per ogni spazio che vuoi rendere accessibile. Spazi non elencati sono completamente inaccessibili.

---

## Sviluppo locale

```bash
git clone https://github.com/nuccio/google-chat-mcp
cd google-chat-mcp
uv pip install -e ".[dev]"

# Test
pytest

# Test con copertura
pytest -v

# Test di integrazione (richiede token OAuth valido)
pytest -m integration
```
