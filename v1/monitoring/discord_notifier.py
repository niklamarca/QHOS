"""
QUANT HPC OS — Discord Notifier
===============================
Envio de mensagens para um webhook do Discord usando SO a stdlib (urllib) —
sem dependencias externas, funciona em qualquer Python. A URL do webhook e um
SEGREDO e vem do ambiente (DISCORD_WEBHOOK_URL, carregado do .env) ou de
config.discord_webhook_url. NUNCA e escrita no codigo.

Se a URL nao estiver definida, entra em modo DRY-RUN (imprime em vez de enviar),
util para testar sem postar nada.
"""
from __future__ import annotations
import json
import os
import urllib.request
import urllib.error
from typing import Optional, List, Dict


def _read_env_file(key: str) -> str:
    """Le uma chave direto do arquivo .env do projeto (sem dependencia externa).
    Necessario porque o config (pydantic) so mapeia campos declarados e NAO
    injeta esta chave no ambiente."""
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env_path = os.path.join(root, ".env")
        if not os.path.exists(env_path):
            return ""
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _webhook_url() -> str:
    url = (os.environ.get("DISCORD_WEBHOOK_URL", "") or "").strip()
    if url:
        return url
    try:
        from core.config import config
        url = str(getattr(config, "discord_webhook_url", "") or "").strip()
    except Exception:
        url = ""
    if url:
        return url
    return _read_env_file("DISCORD_WEBHOOK_URL")


def send(content: Optional[str] = None,
         embeds: Optional[List[Dict]] = None,
         username: str = "Quant HPC Monitor",
         timeout: int = 15) -> bool:
    """Posta no webhook. Retorna True se enviou (2xx). Dry-run se sem URL."""
    url = _webhook_url()
    payload: Dict = {"username": username}
    if content:
        payload["content"] = content[:1900]  # limite do Discord ~2000
    if embeds:
        payload["embeds"] = embeds[:10]

    if not url:
        print("[discord_notifier] DISCORD_WEBHOOK_URL nao definido — DRY-RUN.")
        if content:
            print(content)
        if embeds:
            print(json.dumps(embeds, ensure_ascii=False, indent=2))
        return False

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            # User-Agent explicito: a Cloudflare do Discord bloqueia o UA padrao
            # do Python (urllib) com HTTP 403 error 1010. Um UA proprio resolve.
            "User-Agent": "QuantHPC-Monitor/1.0 (+https://github.com/quant-hpc)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        try:
            body = e.read()[:200].decode("utf-8", "ignore")
        except Exception:
            body = ""
        print(f"[discord_notifier] HTTP {e.code}: {body}")
        return False
    except Exception as e:
        print(f"[discord_notifier] falha ao enviar: {e}")
        return False


def make_embed(title: str, description: str = "", color: int = 0x999999,
               fields: Optional[List[Dict]] = None) -> Dict:
    e: Dict = {"title": title[:256], "color": int(color)}
    if description:
        e["description"] = description[:4000]
    if fields:
        e["fields"] = fields[:25]
    return e


def field(name: str, value: str, inline: bool = True) -> Dict:
    return {"name": str(name)[:256], "value": str(value)[:1024], "inline": bool(inline)}
