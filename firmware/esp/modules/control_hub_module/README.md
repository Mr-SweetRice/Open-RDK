# Módulo de Controle

Firmware ESP-IDF para um ESP32 DevKit clássico integrado ao Open-RDK. O módulo reúne:

- OLED SSD1306 I2C 128x64 (`0x3C`)
- MPU6050 I2C (`0x68`, AD0 em GND) com ângulos de Euler e calibração de drift
- Encoder rotativo KY-040 para navegação e seleção
- 6 saídas PWM de servo a 50 Hz
- 12 GPIO digitais de uso geral
- 8 itens de menu persistentes em NVS
- protocolo serial enquadrado Open-RDK a 512000 baud

## Pinagem

| Função | GPIO |
|---|---|
| I2C SDA (OLED + MPU6050) | 33 |
| I2C SCL (OLED + MPU6050) | 32 |
| Servo 1..6 | 13, 12, 23, 5, 2, 15 |
| Controle, saídas 1..9 | 0, 21, 16, 17, 18, 19, 4, 22, 25 |
| Controle, entradas 10..12 | 34, 35, 39 |
| KY-040 CLK | 14 |
| KY-040 DT | 27 |
| KY-040 SW | 26 |
| Serial Open-RDK | UART0 / USB do DevKit (TX 1, RX 3) |

GPIO 2, 5, 12 e 15 são pinos de *strapping*. Os sinais dos servos normalmente são alta impedância, mas nenhum circuito conectado a esses pinos pode forçar nível durante reset/boot. Em especial, não aplique pull-up externo no GPIO12. O GPIO0 passou a ocupar a primeira saída digital configurável e continua exigindo cuidado durante o boot.

## Alimentação

Não alimente os servos pelo pino 3V3 nem pelo regulador do DevKit. Use uma fonte externa de 5–6 V dimensionada para a corrente de travamento dos seis servos e una o GND dessa fonte ao GND do ESP32. OLED e MPU6050 devem usar lógica/alimentação compatível com 3,3 V.

GPIO34, GPIO35 e GPIO39 são somente entrada e não possuem resistores internos de pull-up/pull-down. O circuito externo deve definir o nível lógico desses pinos.

### KY-040

| KY-040 | ESP32-WROOM |
|---|---|
| CLK | GPIO14 |
| DT | GPIO27 |
| SW | GPIO26 |
| GND | GND |
| VCC | 3.3 V |

Não alimente o KY-040 com 5 V: suas saídas chegariam aos GPIO do ESP32 acima da tensão permitida.

## Uso do menu

- o menu principal do OLED contém `MODULOS`, `SERVOS`, `IMU` e `EXECUCAO`;
- `MODULOS` mostra até oito módulos conectados, sincronizados automaticamente pelo host;
- ao selecionar um `traction_module`, use `POSICAO` (-3600..3600 graus), `VELOCIDADE`
  (-150..150 RPM) ou `FORCE OUTPUT` (-100..100%);
- cada passo do encoder aplica imediatamente o valor de tração e pressionar volta ao menu;
- `SERVOS` seleciona um dos seis canais e ajusta o ângulo em passos de 5 graus;
- `IMU` mostra roll, pitch e yaw em graus e o estado da calibração;
- `EXECUCAO` contém os oito slots configurados pela WebView;
- gire o KY-040 para mudar o item e pressione para selecionar ou voltar;
- cada slot pode executar um comando de terminal ou um arquivo Python carregado pela página `/control-hub`;
- o ESP32 envia `EXEC,<slot>,<modo>,<payload_base64url>`;
- o host só executa a solicitação quando modo e payload coincidem com o perfil salvo;
- durante a execução, pressione novamente o encoder em `PARAR EXECUCAO` para enviar `STOP,<slot>`.

Os comandos têm limite de 30 segundos. Na página web, cada item permite selecionar `Automático`, `CMD`, `PowerShell` ou `sh`. O modo automático usa `cmd.exe` no Windows e `sh` no Linux. Portanto, use caminhos absolutos e execute o serviço com uma conta de usuário com permissões mínimas.

## IMU e calibração

O filtro complementar combina a inclinação absoluta do acelerômetro com a velocidade angular do giroscópio. Roll e pitch são estabilizados pela gravidade. Como o MPU6050 não possui magnetômetro, yaw é um ângulo relativo integrado pelo giroscópio.

Na aba `IMU` da página `/control-hub`, mantenha o módulo completamente parado e pressione `Calibrar IMU`. O firmware coleta 250 amostras durante aproximadamente 5 segundos, calcula a média do drift dos três eixos — incluindo o drift de yaw no eixo Z —, zera o yaw e grava o bias na NVS.

Comandos disponíveis:

- `GET IMU`: Euler, velocidade angular compensada e estado/progresso da calibração;
- `GET IMU RAW`: valores brutos do acelerômetro e giroscópio;
- `CALIBRATE IMU`: inicia a calibração estacionária sem bloquear a comunicação.

## Build e flash

```powershell
. C:\Users\SEU_USUARIO\esp\v5.3\esp-idf\export.ps1
cd firmware\esp\modules\control_hub_module
idf.py set-target esp32
idf.py build
idf.py -p COM5 flash
```

Depois de empacotado pelo script `tools/scripts/package_firmware.ps1`, ele também aparece como `control_hub_module` no flasher do Open-RDK.
