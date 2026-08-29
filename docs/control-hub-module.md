# Servico independente do modulo de controle

O modulo de controle nao pertence ao runtime, SDK, descoberta ou WebView do
host Open-RDK. Ele usa o servico independente em `services/control_hub`. O
servico encontra e reconecta o modulo automaticamente e registra sua porta como
reservada para impedir que o host Open-RDK tente abri-la.

## Inicio rapido

Windows (PowerShell):

```powershell
cd services/control_hub
.\start.ps1
```

Linux:

```bash
cd services/control_hub
./start.sh
```

Abra `http://127.0.0.1:8770`. Nao existe selecao manual de porta; o estado da
conexao aparece no cabecalho. A configuracao e persistida no perfil do usuario:

- Windows: `%LOCALAPPDATA%\openrdk\control-hub-service`
- Linux: `$XDG_STATE_HOME/openrdk/control-hub-service` ou
  `~/.local/state/openrdk/control-hub-service`

A variavel `CONTROL_HUB_SERVICE_STATE_DIR` altera esse diretorio nos dois
sistemas.

## Parada obrigatoria dos motores

O servico inclui uma rotina de parada pronta e imutavel do Open-RDK. Ela e
executada depois de toda opcao do menu e de toda
execucao manual, independentemente de o processo principal:

- terminar normalmente;
- retornar erro;
- exceder o timeout;
- ser interrompido pelo encoder ou pela pagina.

Ela consulta `http://127.0.0.1:8765` (ou `OPENRDK_HOST_URL`), seleciona todos os
modulos de tracao online, muda cada um para `CONTROL` e envia saida `0`. A rotina
nao aparece como script editavel e nao pode ser desativada pela configuracao.
Se ela falhar depois de uma execucao, o conjunto e
registrado como falha de seguranca. O log guarda stdout, stderr, codigo de
retorno e o resultado separado da parada.

Quando o script encerra sua propria instancia Open-RDK, a rotina fixa usa o
mesmo ambiente Python do slot para abrir temporariamente um runtime sem WebView
e repetir a parada. Scripts que criam `CommsRuntime` devem ainda parar os
motores em seu `finally`, antes de chamar `openrdk.stop()`.

## Scripts e comandos

O diretorio gerenciado pelo servico esta sempre ativo. Outros diretorios
absolutos podem ser adicionados pela pagina e funcionam simultaneamente. Todos
os arquivos `.py` diretamente dentro deles aparecem nos oito seletores do menu.
Remover um diretorio da lista nao apaga seus arquivos.

Cada slot Python possui o campo **Ambiente Python**, que aceita a pasta do venv
ou o interpretador. O executor usa `Scripts/python.exe` no Windows ou
`bin/python` no Linux, configura `VIRTUAL_ENV` e `PATH` e usa o diretorio do
script como diretorio de trabalho. Com o campo vazio, procura `.venv` e `venv`
automaticamente nos diretorios pais.

Comandos usam `cmd` no Windows e `sh` no Linux quando o terminal esta em
`auto`. Tambem e possivel selecionar PowerShell, `cmd` ou `sh` explicitamente.
O servico inicia subprocessos sem `shell=True`, limita o tempo de execucao e
encerra a arvore do processo ao receber uma solicitacao de parada.

## Hardware do firmware

| Funcao | GPIOs fisicos do ESP32 |
|---|---|
| Servos 1..6 | 13, 12, 23, 5, 2, 15 |
| Entrada/saida digital | 0, 21, 16, 17, 18, 19, 4, 22, 25 |
| Somente entrada | 34, 35, 39 |
| MPU6050/OLED I2C | SDA 33, SCL 32 |
| Encoder | CLK 14, DT 27, SW 26 |

A pagina atual e dedicada aos scripts/comandos, diretorios e logs. Os recursos
de IMU, servo e GPIO permanecem no firmware e em suas APIs, mas nao ocupam a
tela principal de configuracao.

## Inicializacao automatica

Raspberry Pi:

```bash
cd services/control_hub
sudo python3 install_raspberry_pi.py
```

O instalador cria a venv, configura `dialout`, grava a unidade systemd, habilita
o boot e inicia o servico. Consulte
[`services/control_hub/README_RASPBERRY_PI.md`](../services/control_hub/README_RASPBERRY_PI.md).

Windows:

```powershell
.\install-windows-service.ps1
```
