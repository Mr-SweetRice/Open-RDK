# Open-RDK Control Hub Service

Servico serial e WebView independente para o firmware
`control_hub_module`. O host Open-RDK nao registra, abre ou envia mensagens a
esse modulo.

## Executar

```powershell
# Windows
.\start.ps1
```

```bash
# Linux
./start.sh
```

No Raspberry Pi, para instalar e iniciar automaticamente no boot:

```bash
cd services/control_hub
sudo python3 install_raspberry_pi.py
```

A interface fica em `http://127.0.0.1:8770`. Para expor deliberadamente na
rede, passe `--host 0.0.0.0`; nao ha autenticacao embutida, portanto use somente
em uma rede confiavel.

O servico procura o modulo e conecta automaticamente, priorizando a ultima
porta reconhecida. Se o cabo for removido ou a conexao cair, ele tenta conectar
novamente sem intervencao. A pagina nao expoe selecao manual de porta: nela
ficam os slots de scripts/comandos, a selecao de diretorios e os registros de
execucao.

Para cada slot Python, o campo **Ambiente Python** aceita a pasta do ambiente
virtual ou o caminho completo do interpretador. Exemplos:

```text
C:\projeto\.venv
/home/pi/projeto/.venv
```

O executor usa `Scripts/python.exe` no Windows ou `bin/python` no Linux,
configura `VIRTUAL_ENV` e `PATH` e executa a partir do diretorio do script. Se o
campo ficar vazio, ele procura automaticamente por `.venv` ou `venv` no
diretorio do script e em seus diretorios pais; se nao encontrar, usa o Python
do proprio servico.

Instalacao manual equivalente:

```bash
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/control-hub-service
```

No PowerShell, os executaveis ficam em `.venv\Scripts`.

Para instalar com inicializacao automatica no Raspberry Pi, siga o
[guia de instalacao com systemd](README_RASPBERRY_PI.md). O metodo recomendado
usa `sudo python3 install_raspberry_pi.py` para preparar a venv, permissoes e
inicializacao automaticamente.

## Garantia de parada

A parada e uma rotina interna, pronta e imutavel do Open-RDK. O executor a chama
depois de qualquer encerramento da acao principal. Ela consulta o host, coloca
todos os modulos de tracao online em modo `CONTROL` e envia saida `0`. O
resultado aparece no log persistente. A URL padrao e
`http://127.0.0.1:8765`, substituivel por `OPENRDK_HOST_URL`.

Se um script Python possuir e encerrar sua propria instancia do Open-RDK, a
rotina usa o mesmo ambiente Python do slot para abrir temporariamente o runtime
sem WebView, repetir a parada e encerra-lo. Exemplos que criam `CommsRuntime`
tambem devem parar os motores no proprio bloco `finally`, antes de chamar
`openrdk.stop()`.
