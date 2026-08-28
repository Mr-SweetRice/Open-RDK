# Servico independente do modulo de controle

O modulo de controle nao pertence mais ao runtime, SDK, descoberta ou WebView
do Open-RDK. Ele usa o servico independente em `services/control_hub`, que e o
unico processo autorizado a abrir sua porta serial enquanto estiver conectado.
Ao clicar em **Desconectar**, ou ao encerrar o servico, a porta e fechada e fica
livre para qualquer outro programa.

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

Abra `http://127.0.0.1:8770`, selecione a porta e clique em **Conectar**. A
configuracao e persistida no perfil do usuario:

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

## Scripts e comandos

O diretorio gerenciado pelo servico esta sempre ativo. Outros diretorios
absolutos podem ser adicionados pela pagina e funcionam simultaneamente. Todos
os arquivos `.py` diretamente dentro deles aparecem nos oito seletores do menu
do menu. Remover um diretorio da lista nao apaga seus arquivos.

Comandos usam `cmd` no Windows e `sh` no Linux quando o terminal esta em
`auto`. Tambem e possivel selecionar PowerShell, `cmd` ou `sh` explicitamente.
O servico inicia subprocessos sem `shell=True`, limita o tempo de execucao e
encerra a arvore do processo ao receber uma solicitacao de parada.

## Hardware mantido

| Funcao | GPIOs fisicos do ESP32 |
|---|---|
| Servos 1..6 | 13, 12, 23, 5, 2, 15 |
| Entrada/saida digital | 0, 21, 16, 17, 18, 19, 4, 22, 25 |
| Somente entrada | 34, 35, 39 |
| MPU6050/OLED I2C | SDA 33, SCL 32 |
| Encoder | CLK 14, DT 27, SW 26 |

A pagina preserva leitura/calibracao da IMU, controle dos seis servos, GPIO,
comando bruto do firmware e sincronizacao das oito entradas do display.

## Execucao como servico do usuario

Para iniciar automaticamente no login:

```powershell
# Windows, PowerShell
.\install-windows-service.ps1
```

```bash
# Linux, systemd do usuario
./install-linux-service.sh
```

No Linux, o usuario precisa ter permissao para a porta serial (normalmente pelo
grupo `dialout`). O servidor web escuta apenas em `127.0.0.1:8770` por padrao.
