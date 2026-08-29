#!/usr/bin/env python3
"""Install the Open-RDK Control Hub service as a Raspberry Pi systemd unit."""

from __future__ import annotations

import argparse
import getpass
import os
import platform
import re
import subprocess
import sys
from pathlib import Path


UNIT_NAME = "openrdk-control-hub.service"
UNIT_PATH = Path("/etc/systemd/system") / UNIT_NAME
DEFAULT_STATE_DIR = Path("/var/lib/openrdk-control-hub")


def run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(list(args), check=True)


def selected_user(explicit: str) -> str:
    user = explicit.strip() or os.environ.get("SUDO_USER", "").strip() or getpass.getuser()
    if not user or user == "root":
        raise RuntimeError("informe o usuario que executara os scripts com --user NOME")
    return user


def systemd_quote(value: str | Path) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def render_unit(
    service_root: Path,
    user: str,
    state_dir: Path,
    listen_host: str,
    port: int,
    openrdk_host_url: str,
) -> str:
    python = service_root / ".venv" / "bin" / "python"
    return f"""[Unit]
Description=Open-RDK Control Hub Service
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User={user}
SupplementaryGroups=dialout
WorkingDirectory={systemd_quote(service_root)}
Environment={systemd_quote('PYTHONUNBUFFERED=1')}
Environment={systemd_quote(f'CONTROL_HUB_SERVICE_STATE_DIR={state_dir}')}
Environment={systemd_quote(f'OPENRDK_HOST_URL={openrdk_host_url}')}
ExecStart={systemd_quote(python)} -m control_hub_service --host {systemd_quote(listen_host)} --port {port}
Restart=always
RestartSec=3
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Instala e inicia o servico do modulo de controle no Raspberry Pi"
    )
    parser.add_argument("--user", default="", help="usuario que executara scripts e comandos")
    parser.add_argument("--host", default="0.0.0.0", help="endereco da interface web")
    parser.add_argument("--port", type=int, default=8770, help="porta da interface web")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--openrdk-host-url", default="http://127.0.0.1:8765")
    parser.add_argument("--dry-run", action="store_true", help="mostra a unidade sem alterar o sistema")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service_root = Path(__file__).resolve().parent
    state_dir = Path(args.state_dir).expanduser().resolve()
    user = selected_user(args.user)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,31}", user):
        raise RuntimeError(f"nome de usuario invalido: {user}")
    if any(character in args.host for character in ("\x00", "\r", "\n")):
        raise RuntimeError("endereco da interface web invalido")
    if any(character in args.openrdk_host_url for character in ("\x00", "\r", "\n")):
        raise RuntimeError("URL do host Open-RDK invalida")
    if not 1 <= args.port <= 65535:
        raise RuntimeError("a porta deve estar entre 1 e 65535")

    unit = render_unit(
        service_root, user, state_dir, args.host, args.port,
        args.openrdk_host_url.rstrip("/"),
    )
    if args.dry_run:
        print(unit)
        return 0

    if platform.system() != "Linux" or not Path("/proc/device-tree/model").exists():
        raise RuntimeError("este instalador deve ser executado em um Raspberry Pi com Linux")
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise RuntimeError(f"execute novamente com: sudo {sys.executable} {Path(__file__).name}")
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 ou mais recente e necessario")

    try:
        import pwd

        account = pwd.getpwnam(user)
    except KeyError as exc:
        raise RuntimeError(f"usuario Linux nao encontrado: {user}") from exc

    venv = service_root / ".venv"
    venv_python = venv / "bin" / "python"
    if not venv_python.is_file():
        try:
            run(sys.executable, "-m", "venv", str(venv))
        except subprocess.CalledProcessError:
            print("Instalando suporte a ambientes virtuais...", flush=True)
            run("apt-get", "update")
            run("apt-get", "install", "-y", "python3-venv", "python3-pip", "python3-tk")
            run(sys.executable, "-m", "venv", str(venv))
    run(str(venv_python), "-m", "pip", "install", "--disable-pip-version-check", "-e", str(service_root))

    state_dir.mkdir(parents=True, exist_ok=True)
    os.chown(state_dir, account.pw_uid, account.pw_gid)
    run("usermod", "-aG", "dialout", user)

    temporary = UNIT_PATH.with_suffix(".service.tmp")
    temporary.write_text(unit, encoding="utf-8")
    os.replace(temporary, UNIT_PATH)
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", UNIT_NAME)
    run("systemctl", "restart", UNIT_NAME)
    run("systemctl", "--no-pager", "--full", "status", UNIT_NAME)

    print(f"\nInstalacao concluida. Interface: http://IP_DO_RASPBERRY:{args.port}")
    print(f"Logs: journalctl -u {UNIT_NAME} -f")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        raise SystemExit(1)
