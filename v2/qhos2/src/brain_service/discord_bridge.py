"""Opt-in Discord conversation bridge. No listener, shell, restart or trading.

Requires a separately configured bot and optional discord.py dependency.
Importing this module and --check never contact Discord.
"""
import argparse
import asyncio
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import time

import httpx

from brain_service.remote_access import remote_snapshot, safe_text, status_text

ROOT = Path(__file__).resolve().parent.parent
BRAIN_URL = "http://127.0.0.1:8091"


def snowflake(value):
    text = str(value)
    if not text.isascii() or not text.isdigit() or not 15 <= len(text) <= 22:
        raise ValueError("Use IDs numéricos do Discord, não nomes ou tokens.")
    return int(text)


@dataclass(frozen=True)
class DiscordConfig:
    enabled: bool
    user_ids: frozenset
    guild_id: int
    channel_id: int
    allow_dm: bool = True
    alerts: bool = False

    @classmethod
    def load(cls, path):
        with Path(path).open("rb") as handle:
            raw = handle.read(16_385)
        if len(raw) > 16_384:
            raise ValueError("Configuração excessiva.")
        data = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(data, dict) or any(k not in {
            "enabled", "user_ids", "guild_id", "channel_id", "allow_dm", "alerts"} for k in data):
            raise ValueError("Configuração inválida. Nunca guarde o token neste arquivo.")
        users = data.get("user_ids")
        if not isinstance(users, list) or not 1 <= len(users) <= 8:
            raise ValueError("Autorize explicitamente de 1 a 8 usuários.")
        for key in ("enabled", "allow_dm", "alerts"):
            if key in data and type(data[key]) is not bool:
                raise ValueError("Flags devem ser booleanas.")
        if data.get("alerts", False) and not data.get("allow_dm", True):
            raise ValueError("Alertas privados exigem allow_dm=true.")
        return cls(data.get("enabled", False), frozenset(snowflake(x) for x in users),
                   snowflake(data.get("guild_id")), snowflake(data.get("channel_id")),
                   data.get("allow_dm", True), data.get("alerts", False))

    def allowed(self, user_id, guild_id, channel_id, *, is_bot=False):
        if not self.enabled or is_bot or user_id not in self.user_ids:
            return False
        if guild_id is None:
            return self.allow_dm
        return guild_id == self.guild_id and channel_id == self.channel_id


class BrainBridge:
    def __init__(self, config, *, transport=None, now=time.monotonic):
        self.config = config
        self.client = httpx.AsyncClient(base_url=BRAIN_URL, timeout=65, trust_env=False,
                                       follow_redirects=False, transport=transport)
        self.now = now
        self._next_request = {}
        self._lock = asyncio.Lock()

    async def close(self):
        await self.client.aclose()

    async def _request(self, method, path, **kwargs):
        # This bridge can never turn user text into a URL or operational route.
        if (method, path) not in {("GET", "/api/assistant/state"),
                                  ("POST", "/api/assistant/remote-chat")}:
            raise ValueError("Rota remota proibida.")
        async with self.client.stream(method, path, **kwargs) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 1_048_576:
                    raise ValueError("Resposta local excessiva.")
            payload = json.loads(content)
        if not isinstance(payload, dict):
            raise ValueError("Resposta local inválida.")
        return payload

    async def snapshot(self):
        return remote_snapshot(await self._request("GET", "/api/assistant/state", timeout=8))

    async def ask(self, user_id, guild_id, channel_id, message, *, is_bot=False, status_only=False):
        if not self.config.allowed(user_id, guild_id, channel_id, is_bot=is_bot):
            return "Acesso não autorizado."
        if not isinstance(message, str) or not message.strip() or len(message) > 2500:
            return "Envie uma pergunta entre 1 e 2500 caracteres."
        if self.now() < self._next_request.get(user_id, 0) or self._lock.locked():
            return "Estou respondendo outra pergunta. Tente novamente em alguns segundos."
        self._next_request[user_id] = self.now() + 5
        async with self._lock:
            try:
                if status_only:
                    return status_text(await self.snapshot())
                reply = await self._request("POST", "/api/assistant/remote-chat",
                                            json={"user_id": str(user_id), "message": safe_text(message, 2500)})
                if reply.get("read_only") is not True:
                    return "O Brain local precisa da versão compatível com conversa remota segura."
                text = safe_text(reply.get("reply"))
                if reply.get("source") != "model":
                    text = "[Resposta local; modelo indisponível]\n" + text
                return text or "O Brain não retornou resposta."
            except (httpx.HTTPError, ValueError, OSError):
                # Exception strings may contain credentials or complete URLs.
                return "Não consegui consultar o Brain local. Isso não comprova que o Trader caiu."


class AlertChanges:
    """Debounced state changes; advancing generations alone never cause spam."""
    def __init__(self):
        self.previous = None
        self.candidate = None
        self.count = 0
        self.last_sent = -float("inf")

    def update(self, snapshot, now):
        reduced = json.loads(json.dumps(snapshot))
        reduced.get("trader", {}).pop("generation", None)
        fingerprint = json.dumps(reduced, sort_keys=True)
        if self.previous is None:
            if (reduced.get("brain", {}).get("running") is True
                    and reduced.get("trader", {}).get("connection_state") == "ONLINE"):
                self.previous = fingerprint
                return False
            # Starting while unavailable is actionable after confirmation too.
            self.previous = "initial_unavailable"
        if fingerprint == self.previous:
            self.candidate, self.count = None, 0
            return False
        self.count = self.count + 1 if self.candidate == fingerprint else 1
        self.candidate = fingerprint
        if self.count < 3 or now - self.last_sent < 300:
            return False
        return True

    def mark_sent(self, snapshot, now):
        reduced = json.loads(json.dumps(snapshot))
        reduced.get("trader", {}).pop("generation", None)
        self.previous = json.dumps(reduced, sort_keys=True)
        self.last_sent = now
        self.candidate, self.count = None, 0


def build_client(config):
    import discord
    from discord import app_commands

    bridge = BrainBridge(config)
    intents = discord.Intents.none()
    intents.guilds = True
    intents.dm_messages = True
    intents.guild_messages = True
    # DMs and messages mentioning the bot are available without privileged content intent.
    intents.message_content = False

    class Client(discord.Client):
        async def setup_hook(self):
            await self.tree.sync(guild=discord.Object(id=config.guild_id))
            if config.alerts:
                self.alert_task = asyncio.create_task(self.send_alerts())

        async def close(self):
            if getattr(self, "alert_task", None):
                self.alert_task.cancel()
                await asyncio.gather(self.alert_task, return_exceptions=True)
            await bridge.close()
            await super().close()

        async def on_error(self, event_method, *args, **kwargs):
            print("Falha de integração Discord; conteúdo e credenciais omitidos.")

        async def on_message(self, message):
            guild_id = message.guild.id if message.guild else None
            if not config.allowed(message.author.id, guild_id, message.channel.id,
                                  is_bot=message.author.bot):
                return
            if guild_id is not None and self.user not in message.mentions:
                return
            question = message.content
            if self.user:
                question = question.replace(f"<@{self.user.id}>", "").replace(f"<@!{self.user.id}>", "")
            async with message.channel.typing():
                answer = await bridge.ask(message.author.id, guild_id, message.channel.id, question)
            if guild_id is None:
                await message.reply(safe_text(answer), mention_author=False,
                                    allowed_mentions=discord.AllowedMentions.none())
            else:
                # Never leak a private conversation's memory into a channel.
                try:
                    if not config.allow_dm:
                        raise discord.ClientException("Private replies disabled")
                    await message.author.send(safe_text(answer), allowed_mentions=discord.AllowedMentions.none())
                except discord.DiscordException:
                    await message.reply("Use /brain para uma resposta privada ou habilite mensagens diretas.",
                                        mention_author=False, allowed_mentions=discord.AllowedMentions.none())

        async def send_alerts(self):
            await self.wait_until_ready()
            changes = {user_id: AlertChanges() for user_id in config.user_ids}
            while not self.is_closed():
                try:
                    snap = await bridge.snapshot()
                except (httpx.HTTPError, ValueError, OSError):
                    # Probe failure is indeterminate, never evidence that Trader fell.
                    snap = remote_snapshot({})
                if config.allow_dm:
                    for user_id, tracker in changes.items():
                        now = time.monotonic()
                        if not tracker.update(snap, now):
                            continue
                        try:
                            user = await self.fetch_user(user_id)
                            await user.send("Mudança confirmada no monitoramento:\n" + status_text(snap),
                                            allowed_mentions=discord.AllowedMentions.none())
                            tracker.mark_sent(snap, now)
                        except (OSError, discord.DiscordException):
                            # Keep this recipient pending; do not starve other users.
                            print("Alerta privado sem confirmação de entrega; conteúdo omitido.")
                await asyncio.sleep(60)

    client = Client(intents=intents, allowed_mentions=discord.AllowedMentions.none())
    client.tree = app_commands.CommandTree(client)
    guild = discord.Object(id=config.guild_id)

    async def respond(interaction, question, status_only=False):
        if not config.allowed(interaction.user.id, interaction.guild_id, interaction.channel_id,
                              is_bot=interaction.user.bot):
            await interaction.response.send_message("Acesso não autorizado.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        answer = await bridge.ask(interaction.user.id, interaction.guild_id, interaction.channel_id,
                                  question, status_only=status_only)
        await interaction.followup.send(safe_text(answer), ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())

    @client.tree.command(name="brain", description="Converse com o Brain sobre o sistema", guild=guild)
    async def ask_brain(interaction: discord.Interaction, pergunta: str):
        await respond(interaction, pergunta)

    @client.tree.command(name="status", description="Consulte a saúde atual sem executar ações", guild=guild)
    async def status(interaction: discord.Interaction):
        await respond(interaction, "Como está o sistema?", status_only=True)

    @client.tree.command(name="testes", description="Leia os últimos resultados; não dispara testes", guild=guild)
    async def tests(interaction: discord.Interaction):
        await respond(interaction, "Explique os resultados dos testes e da regressão ampla.")

    @client.tree.error
    async def command_error(interaction: discord.Interaction, error):
        if interaction.response.is_done():
            await interaction.followup.send("Não consegui concluir a consulta.", ephemeral=True)
        else:
            await interaction.response.send_message("Não consegui concluir a consulta.", ephemeral=True)

    return client


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "discord-config.json")
    parser.add_argument("--run", action="store_true", help="Ativa a conexão após configuração explícita")
    args = parser.parse_args(argv)
    try:
        config = DiscordConfig.load(args.config)
    except (OSError, ValueError, TypeError):
        print("Configure discord-config.json com IDs autorizados. Não coloque tokens no arquivo.")
        return 2
    available = importlib.util.find_spec("discord") is not None
    print(json.dumps({"enabled": config.enabled, "discord_py_available": available,
                      "token_configured": bool(os.environ.get("BRAIN_DISCORD_BOT_TOKEN")),
                      "authorized_users": len(config.user_ids), "read_only": True}))
    if not args.run:
        return 0
    token = os.environ.pop("BRAIN_DISCORD_BOT_TOKEN", "")
    if not config.enabled or not available or not token:
        print("Conexão não iniciada: habilitação, dependência ou token ausente.")
        return 2
    from brain_service.supervisor import SingleInstanceLock
    lock = SingleInstanceLock(ROOT / "data_lake" / "service" / "discord_bridge.lock")
    if not lock.acquire():
        print("Ponte Discord já ativa ou lock indisponível.")
        return 2
    try:
        build_client(config).run(token, log_handler=None)
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("Conexão Discord falhou; verifique permissões e configuração local.")
        return 1
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
