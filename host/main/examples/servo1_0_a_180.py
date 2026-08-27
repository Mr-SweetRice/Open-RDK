"""Move o Servo 1 de 0 a 180 graus em 3 segundos."""

from __future__ import annotations

import time

from openrdk import CommsRuntime


DURACAO_SEGUNDOS = 3.0
INTERVALO_SEGUNDOS = 0.05


def encontrar_modulo_controle(runtime: CommsRuntime, timeout_sec: float = 10.0):
    limite = time.monotonic() + timeout_sec
    while time.monotonic() < limite:
        for dispositivo in runtime.list_devices():
            if (
                dispositivo.get("module_type") == "control_hub_module"
                and dispositivo.get("status") == "online connected"
            ):
                return runtime.control_hub(str(dispositivo["serial_number"]))
        time.sleep(0.1)
    raise RuntimeError("Modulo de controle online nao encontrado")


def main() -> None:
    runtime = CommsRuntime(auto_start=True, enable_webview=False)
    try:
        modulo = encontrar_modulo_controle(runtime)
        modulo.set_servo_angle(1, 0)

        inicio = time.monotonic()
        proxima_atualizacao = inicio

        while True:
            agora = time.monotonic()
            tempo_decorrido = agora - inicio
            if tempo_decorrido >= DURACAO_SEGUNDOS:
                break

            angulo = round(180 * tempo_decorrido / DURACAO_SEGUNDOS)
            modulo.set_servo_angle(1, angulo)

            proxima_atualizacao += INTERVALO_SEGUNDOS
            time.sleep(max(0.0, proxima_atualizacao - time.monotonic()))

        modulo.set_servo_angle(1, 180)
        print("Servo 1 chegou a 180 graus em 3 segundos.")
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
