# Módulo de Controle

Firmware ESP-IDF para um ESP32 DevKit clássico operado pelo serviço independente
`services/control_hub`. O módulo não é descoberto nem controlado pelo host
Open-RDK. Ele reúne:

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
- `MODULOS` é uma tela legada e não é sincronizada pelo serviço independente;
- `SERVOS` seleciona um dos seis canais e ajusta o ângulo em passos de 5 graus;
- `IMU` mostra roll, pitch e yaw em graus e o estado da calibração;
- `EXECUCAO` contém os oito slots configurados em `http://127.0.0.1:8770`;
- gire o KY-040 para mudar o item e pressione para selecionar ou voltar;
- cada slot pode executar um comando de terminal ou um arquivo Python configurado no serviço;
- o ESP32 envia `EXEC,<slot>,<modo>,<payload_base64url>`;
- o serviço só executa a solicitação quando modo e payload coincidem com o perfil salvo;
- durante a execução, pressione novamente o encoder em `PARAR EXECUCAO` para enviar `STOP,<slot>`.

O timeout é configurável por item. Na página web, cada item permite selecionar
`Automático`, `CMD`, `PowerShell` ou `sh`. O modo automático usa `cmd.exe` no
Windows e `sh` no Linux. A rotina interna e imutável de parada do Open-RDK é
chamada depois de toda execução, inclusive falha, timeout ou interrupção. Use caminhos
absolutos e execute o serviço com uma conta de usuário com permissões mínimas.

## IMU e calibração

O filtro complementar combina a inclinação absoluta do acelerômetro com a velocidade angular do giroscópio. Roll e pitch são estabilizados pela gravidade. Como o MPU6050 não possui magnetômetro, yaw é um ângulo relativo integrado pelo giroscópio.

Na seção `IMU` da página do serviço, mantenha o módulo completamente parado e
pressione `Calibrar`. O firmware coleta 250 amostras durante aproximadamente 5
segundos, calcula a média do drift dos três eixos — incluindo o drift de yaw no
eixo Z —, zera o yaw e grava o bias na NVS.

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

O flasher do Open-RDK não oferece este firmware. Faça build e flash diretamente
com ESP-IDF para manter a separação do serviço.
