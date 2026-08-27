"""Example for GPIO, MPU6050/IMU, and servo control with the Open RDK SDK.

Reading is always enabled. Outputs and servos only move when --actuate is used.
Keep the IMU completely still when using --calibrate.
"""

from __future__ import annotations

import argparse
import time

from openrdk import CommsRuntime, ControlHubModule


def find_control_hub(runtime: CommsRuntime, serial: str | None, timeout_sec: float = 10.0):
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        for device in runtime.list_devices():
            if device.get("module_type") != "control_hub_module":
                continue
            if serial is not None and device.get("serial_number") != serial:
                continue
            if device.get("status") == "online connected":
                return runtime.control_hub(str(device["serial_number"]))
        time.sleep(0.1)
    wanted = f" com serial {serial}" if serial else ""
    raise RuntimeError(f"Modulo de controle online nao encontrado{wanted}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Testa GPIO, IMU e servo do modulo de controle")
    parser.add_argument("--serial", help="serial do modulo; por padrao usa o primeiro online")
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="calibra a IMU; mantenha o modulo completamente parado",
    )
    parser.add_argument(
        "--actuate",
        action="store_true",
        help="permite alternar uma saida e movimentar um servo",
    )
    parser.add_argument(
        "--gpio",
        type=int,
        choices=ControlHubModule.OUTPUT_GPIO_PINS,
        default=4,
        help="GPIO fisico testado com --actuate (padrao: 4)",
    )
    parser.add_argument(
        "--servo",
        type=int,
        choices=range(1, 7),
        default=1,
        help="numero do servo testado com --actuate (padrao: 1)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    runtime = CommsRuntime(auto_start=True, enable_webview=False)
    hub = None
    gpio_activated = False
    servo_activated = False
    try:
        hub = find_control_hub(runtime, args.serial)
        print(f"Modulo: {hub.serial_number}")
        print(f"Servos 1..6 nos GPIOs: {hub.SERVO_PINS}")
        print(f"GPIOs de saida: {hub.OUTPUT_GPIO_PINS}")
        print(f"GPIOs somente de entrada: {hub.INPUT_ONLY_GPIO_PINS}")

        print("\nLeitura dos pinos:")
        for gpio in hub.GPIO_PINS:
            pin = hub.read_pin(gpio)
            print(f"  GPIO {gpio:>2}: {pin['value']} ({pin['mode']})")

        imu = hub.read_imu()
        print("\nAngulos de Euler:")
        print(
            f"  Roll={imu['roll_deg']:.2f} graus  "
            f"Pitch={imu['pitch_deg']:.2f} graus  "
            f"Yaw={imu['yaw_deg']:.2f} graus"
        )
        print(f"  Calibrada={imu['calibrated']}  Progresso={imu['calibration_progress']}%")
        print(f"  Valores brutos: {hub.read_imu_raw()}")

        if args.calibrate:
            print("\nCalibrando IMU. Nao mova o modulo...")
            imu = hub.calibrate_imu(wait=True)
            print(f"Calibracao concluida. Yaw={imu['yaw_deg']:.2f} graus")

        if not args.actuate:
            print("\nLeituras concluidas. Use --actuate para testar GPIO e servo.")
            return

        print(f"\nAlternando GPIO {args.gpio}...")
        gpio_activated = True
        hub.write_pin(args.gpio, 1)
        time.sleep(0.5)
        hub.write_pin(args.gpio, 0)
        gpio_activated = False
        print(f"GPIO {args.gpio} voltou para LOW.")

        print(f"Movendo servo {args.servo} para 75, 105 e 90 graus...")
        servo_activated = True
        for angle in (75, 105, 90):
            hub.set_servo_angle(args.servo, angle)
            time.sleep(0.5)
        servo_activated = False
        print("Teste concluido; servo centralizado em 90 graus.")
    finally:
        if hub is not None:
            if gpio_activated:
                try:
                    hub.write_pin(args.gpio, 0)
                except Exception as exc:
                    print(f"Nao foi possivel colocar GPIO {args.gpio} em LOW: {exc}")
            if servo_activated:
                try:
                    hub.center_servo(args.servo)
                except Exception as exc:
                    print(f"Nao foi possivel centralizar o servo {args.servo}: {exc}")
        runtime.stop()


if __name__ == "__main__":
    main()
