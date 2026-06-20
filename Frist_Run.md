# Run Open-RDK After Pull

Guia rapido para rodar o host relay e a interface em um computador novo apos `git pull`.

## Requisitos

- Python 3.11 ou superior.
- Git.
- Porta USB liberada para os modulos ESP32-C3.
- ESP-IDF v5.3 apenas se for compilar/flashar firmware.

Nao precisa de Docker para rodar a interface atual.

## Windows PowerShell

Entre na pasta do host:

```powershell
cd "C:\caminho\para\Open-RDK\host\main"
```

Crie e ative o ambiente Python:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
```

Inicie o relay com interface:

```powershell
openrdk
```

Alternativa equivalente, caso o comando `openrdk` nao esteja no PATH:

```powershell
.\.venv\Scripts\python.exe -m openrdk.ordk_run
```

Abra no navegador:

```text
http://127.0.0.1:8765
```

Se o mDNS estiver funcionando na rede:

```text
http://rdk.local:8765
```

## Linux / Raspberry Pi

Entre na pasta do host:

```bash
cd /caminho/para/Open-RDK/host/main
```

Crie e ative o ambiente Python:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

mDNS (`http://rdk.local:8765`) is optional. On Raspberry Pi 4 or newer, install with mDNS support if you want the `.local` address:

```bash
pip install -e ".[mdns]"
```

On Raspberry Pi 1, use the normal install above (`pip install -e .`) so `zeroconf` is not installed. The relay will still run, but use `http://<ip-do-host>:8765` instead of `http://rdk.local:8765`.

Permita acesso a portas seriais, se necessario:

```bash
sudo usermod -aG dialout $USER
```

Depois saia e entre novamente na sessao para o grupo valer.

Inicie o relay com interface:

```bash
openrdk
```

Ou:

```bash
python -m openrdk.ordk_run
```

Abra:

```text
http://<ip-do-host>:8765
```

ou:

```text
http://rdk.local:8765
```

## Verificacao Rapida

Com o relay rodando, teste a API:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/devices
```

No Linux:

```bash
curl http://127.0.0.1:8765/api/devices
```

Os modulos conectados devem aparecer como `online connected`.

## Firmware ESP32-C3

Use somente se precisar compilar ou flashar firmware.

No Windows, carregue o ambiente ESP-IDF antes:

```powershell
. C:\Users\<usuario>\esp\v5.3\esp-idf\export.ps1
```

Build e flash de um modulo:

```powershell
cd "C:\caminho\para\Open-RDK\firmware\esp\modules\traction_module"
idf.py set-target esp32c3
idf.py build
idf.py -p COM20 flash
```

Troque `COM20` pela porta real do modulo. Para listar portas no Windows:

```powershell
Get-CimInstance Win32_SerialPort | Select-Object DeviceID,Name,PNPDeviceID
```

No Linux:

```bash
cd /caminho/para/Open-RDK/firmware/esp/modules/traction_module
idf.py set-target esp32c3
idf.py build
idf.py -p /dev/ttyACM0 flash
```

## Observacoes

- A UI atual fica em `host/main/src/openrdk/web_new`.
- As tools dos modulos sao servidas pelo host relay, nao pelos firmwares.
- O host relay controla a serial. Para flashar um ESP, pare o relay antes se a porta estiver ocupada.
- `http://rdk.local` depende de mDNS. Se falhar, use `http://127.0.0.1:8765` no proprio computador ou `http://<ip-do-host>:8765` em outro dispositivo da rede.
