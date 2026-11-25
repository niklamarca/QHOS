# Tarefas agendadas do Windows (definições exportadas em 29/10/2026, ~21:47)

`Export-ScheduledTask` de cada tarefa QHOS ativa, gravado em UTF-8 (a declaração XML foi ajustada de UTF-16 para
UTF-8). Não há senha: as tarefas rodam como o usuário (`S4U` ou `InteractiveToken`). A única troca foi o
`-StartToken` da `QHOS-V2-Linux`: o GUID real virou `GUID-GERADO-NO-REGISTRO`. É um nonce que
`finish_windows_startup_admin.ps1` gera a cada registro e que o `verify_keepalive.py` confere; não é segredo, mas
também não é reaproveitável.

| Tarefa | Gatilho | Executa | Quem registra |
|---|---|---|---|
| `QHOS-V2-Linux` | boot (+2 min) e logon (+1 min) | `AppData\Local\QHOS\V2\start_wsl_after_clock.ps1` | `../finish_windows_startup_admin.ps1` (admin; copia os gatilhos da `QHOS-Linux` da V1, que precisa existir) |
| `QHOS-V2-KeepAwake` | logon | `pythonw3.13.exe -B AppData\Local\QHOS\V2\keep_awake.py` | `../finish_windows_startup_admin.ps1` (copia os gatilhos da `QHOS-KeepAwake` da V1) |
| `QHOS-Discord-Heartbeat-6h` | a cada 6 h desde 10/10 18:00 | `Downloads\Rithmic\avisos_discord\heartbeat_6h.ps1` | nenhum script; só este XML |
| `QHOS-Discord-Alerta-Captura` | a cada 30 min desde 12/10 16:30 | `heartbeat_6h.ps1 -SoAlerta` | nenhum script; só este XML |
| `QHOS-Limpar-Brutos-ES` | diário às 19:30 | `Downloads\Rithmic\avisos_discord\limpar_brutos_es_diario.ps1` | outra sessão, em 29/10 à noite; o script e o registro dela ainda não tinham commit quando este XML foi exportado |

`QHOS-Arquivar-Capturas` não está aqui: ela estava desativada às 21:30 e já não existia às 21:46. O arquivador e o
`registrar_tarefa_arquivar.ps1` que a recria ficaram fora desta branch publicada, por decisão do autor; estão na branch
local `organizar-pasta-20261030` (não publicada) e ainda sem commit na `coleta-multi-20261029`.

As tarefas da V1 (`QHOS-Linux`, `QHOS-KeepAwake`, `QHOS QA - Continuo`, `QHOS Seguranca - Continuo`) estão desativadas e
ficam de fora; os XMLs delas estão em `C:\Users\usuario\AppData\Local\QHOS\V2\rollback\`.

## Restaurar uma tarefa

Os XMLs têm os caminhos e o SID desta máquina. Em outra máquina, troque `UserId` e os caminhos antes. Em PowerShell:

```powershell
Register-ScheduledTask -TaskName 'QHOS-Discord-Heartbeat-6h' -Xml (Get-Content -Raw -Encoding UTF8 .\QHOS-Discord-Heartbeat-6h.xml)
```

Para a `QHOS-V2-Linux`, prefira `finish_windows_startup_admin.ps1`, que gera o `StartToken` e confere a partida.
Se usar o XML, troque o marcador por um GUID novo (`[guid]::NewGuid()`).

## Webhook do Discord

`heartbeat_6h.ps1` não posta direto: chama `..\chamar_autor.ps1` (na mesma pasta instalada, `avisos_discord`), que lê a
URL da variável de ambiente **do usuário** `QHOS_AVISOS_WEBHOOK_URL` (registro `HKCU\Environment`) e recusa o envio
(rc 2) se ela faltar ou não for `https://discord.com/...`/`https://discordapp.com/...`. O valor nunca vai para o git.
Para recriar: `[Environment]::SetEnvironmentVariable('QHOS_AVISOS_WEBHOOK_URL', '<url do webhook>', 'User')`.
