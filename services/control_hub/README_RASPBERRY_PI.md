# Inicializacao automatica no Raspberry Pi

Este guia instala o servico independente do modulo de controle no Raspberry Pi
OS Bookworm e faz com que ele inicie automaticamente durante o boot. O modulo
serial e encontrado e reconectado sem selecao manual de porta.

## 1. Preparar o Raspberry Pi

Abra um terminal no Raspberry Pi e instale os pacotes necessarios:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip python3-tk
```

Adicione o usuario atual ao grupo que pode acessar portas seriais:

```bash
sudo usermod -aG dialout "$USER"
```

Reinicie a sessao ou o Raspberry Pi para aplicar o novo grupo:

```bash
sudo reboot
```

## 2. Instalar o Open-RDK

Depois de entrar novamente, instale o repositorio em um caminho fixo. Os
comandos abaixo preservam o nome do seu usuario automaticamente:

```bash
sudo mkdir -p /opt/open-rdk
sudo chown "$USER":"$(id -gn)" /opt/open-rdk
git clone https://github.com/Mr-SweetRice/Open-RDK /opt/open-rdk
cd /opt/open-rdk/services/control_hub
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
```

Se o repositorio ja estiver instalado, nao execute `git clone` novamente. Entre
no diretorio existente e prossiga para a criacao do ambiente virtual.

## 3. Testar antes da inicializacao automatica

Conecte o modulo de controle ao USB e execute:

```bash
cd /opt/open-rdk/services/control_hub
.venv/bin/python -m control_hub_service --host 0.0.0.0 --port 8770
```

Em outro computador da mesma rede, abra:

```text
http://IP_DO_RASPBERRY_PI:8770
```

Descubra o endereco do Raspberry Pi com:

```bash
hostname -I
```

O cabecalho da pagina deve mostrar **Modulo conectado**. Encerre o teste com
`Ctrl+C` antes de continuar.

## 4. Criar o diretorio de dados

O servico grava configuracao, scripts gerenciados e logs neste diretorio:

```bash
sudo mkdir -p /var/lib/openrdk-control-hub
sudo chown "$USER":"$(id -gn)" /var/lib/openrdk-control-hub
```

## 5. Criar o servico systemd

Descubra o nome exato do usuario que executara scripts e comandos:

```bash
whoami
```

Abra o arquivo do servico:

```bash
sudo nano /etc/systemd/system/openrdk-control-hub.service
```

Cole o conteudo abaixo e substitua `SEU_USUARIO` pelo resultado de `whoami`:

```ini
[Unit]
Description=Open-RDK Control Hub Service
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=SEU_USUARIO
SupplementaryGroups=dialout
WorkingDirectory=/opt/open-rdk/services/control_hub
Environment=PYTHONUNBUFFERED=1
Environment=CONTROL_HUB_SERVICE_STATE_DIR=/var/lib/openrdk-control-hub
Environment=OPENRDK_HOST_URL=http://127.0.0.1:8765
ExecStart=/opt/open-rdk/services/control_hub/.venv/bin/python -m control_hub_service --host 0.0.0.0 --port 8770
Restart=always
RestartSec=3
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
```

Salve com `Ctrl+O`, confirme com `Enter` e saia com `Ctrl+X`.

O usuario configurado no campo `User` tambem sera o usuario dos scripts e
comandos iniciados pelo modulo. Nao use `root` se os scripts nao precisarem de
privilegios administrativos.

## 6. Ativar e iniciar

Recarregue o systemd, habilite a inicializacao no boot e inicie o servico:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now openrdk-control-hub.service
```

Confira o estado:

```bash
systemctl status openrdk-control-hub.service --no-pager
```

Confirme a conexao automatica pela API:

```bash
curl -s http://127.0.0.1:8770/api/status
```

O campo `connected` deve ficar `true`. A primeira conexao pode levar alguns
segundos porque o firmware reinicia ao abrir a porta.

## 7. Configurar os scripts

Abra `http://IP_DO_RASPBERRY_PI:8770`, adicione um ou mais diretorios absolutos
e configure os oito slots de scripts/comandos. Todos os diretorios adicionados
funcionam simultaneamente.

O usuario definido no servico precisa ter permissao de leitura nos diretorios e
de execucao nos arquivos utilizados. Exemplo:

```bash
mkdir -p /home/SEU_USUARIO/openrdk-scripts
chmod 750 /home/SEU_USUARIO/openrdk-scripts
```

Quando o servico roda pelo systemd, informe o caminho absoluto no campo do
diretorio. O seletor grafico do sistema fica disponivel quando o servico e
iniciado interativamente dentro de uma sessao desktop.

## 8. Ver logs e diagnosticar

Mostrar os logs desta inicializacao:

```bash
journalctl -u openrdk-control-hub.service -b --no-pager
```

Acompanhar os logs em tempo real:

```bash
journalctl -u openrdk-control-hub.service -f
```

Listar portas seriais e confirmar as permissoes do usuario:

```bash
/opt/open-rdk/services/control_hub/.venv/bin/python -m serial.tools.list_ports -v
groups
```

Se `dialout` nao aparecer em `groups`, reinicie o Raspberry Pi. Se a pagina nao
abrir remotamente, confirme que o servico escuta na porta 8770:

```bash
sudo ss -lntp | grep 8770
```

A interface nao possui autenticacao. Use `--host 0.0.0.0` somente em uma rede
local confiavel e nao encaminhe a porta 8770 no roteador.

## 9. Atualizar o sistema

```bash
sudo systemctl stop openrdk-control-hub.service
cd /opt/open-rdk
git pull --ff-only origin main
services/control_hub/.venv/bin/python -m pip install -e services/control_hub
sudo systemctl start openrdk-control-hub.service
systemctl status openrdk-control-hub.service --no-pager
```

Os dados em `/var/lib/openrdk-control-hub` nao sao apagados durante a
atualizacao.

## 10. Desativar ou remover

Para somente impedir a inicializacao automatica:

```bash
sudo systemctl disable --now openrdk-control-hub.service
```

Para remover a unidade do systemd:

```bash
sudo systemctl disable --now openrdk-control-hub.service
sudo rm /etc/systemd/system/openrdk-control-hub.service
sudo systemctl daemon-reload
```

O ultimo comando nao remove o repositorio nem os dados persistentes.

## Dependencia da parada dos motores

A rotina fixa de parada acessa o host Open-RDK em
`http://127.0.0.1:8765`. Portanto, mantenha o host Open-RDK ativo no Raspberry
Pi quando scripts puderem controlar motores. Se o host estiver em outra
maquina, altere `OPENRDK_HOST_URL` na unidade e execute:

```bash
sudo systemctl daemon-reload
sudo systemctl restart openrdk-control-hub.service
```
