from __future__ import annotations

import argparse
import json
import sys
import time

from .calibration import CalibrationError, refresh_empty_reference, run_calibration
from .camera import CameraError, open_frame_source
from .configuration import ConfigError, load_config
from .estimator import CaddyFullnessEstimator, EstimatorError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Estimate how full the Trash Crab basket is from the PiCam.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common_arguments(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--config", type=str, default=None, help="Optional path to a YAML config file.")
        command_parser.add_argument(
            "--source",
            type=str,
            choices=["picamera2", "image", "directory"],
            default=None,
            help="Temporarily use a different frame source than the one in the config.",
        )
        command_parser.add_argument("--input-image", type=str, default=None, help="Path to one test image.")
        command_parser.add_argument("--input-dir", type=str, default=None, help="Path to a folder of test images.")
        command_parser.add_argument("--debug", action="store_true", help="Save debug images for each analysis.")

    calibrate_parser = subparsers.add_parser("calibrate", help="Set up the basket ROI.")
    add_common_arguments(calibrate_parser)
    calibrate_parser.add_argument(
        "--use-rectangle",
        action="store_true",
        help="Use a rectangle instead of clicking out a polygon.",
    )
    calibrate_parser.add_argument(
        "--headless",
        action="store_true",
        help="Skip the OpenCV window and rely on calibration.manual_roi in the config.",
    )

    run_parser = subparsers.add_parser("run", help="Keep analyzing frames until stopped.")
    add_common_arguments(run_parser)
    run_parser.add_argument(
        "--refresh-reference",
        action="store_true",
        help="Capture a fresh empty-basket reference before the run starts.",
    )
    run_parser.add_argument(
        "--skip-reference-refresh",
        action="store_true",
        help="Skip the automatic empty-reference refresh for this run.",
    )
    run_parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Optional frame limit, mainly useful while testing.",
    )

    analyze_once_parser = subparsers.add_parser("analyze-once", help="Analyze one frame and print the result.")
    add_common_arguments(analyze_once_parser)

    refresh_reference_parser = subparsers.add_parser(
        "refresh-reference",
        help="Capture a fresh empty-basket reference with the saved ROI.",
    )
    add_common_arguments(refresh_reference_parser)

    return parser


def run_calibrate_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with open_frame_source(
        config,
        source_override=args.source,
        input_image=args.input_image,
        input_dir=args.input_dir,
    ) as frame_source:
        frame = frame_source.read()

    result = run_calibration(
        frame,
        config,
        use_polygon=not args.use_rectangle,
        headless=args.headless,
    )
    print(json.dumps(result, indent=2))
    return 0


def _log_recoverable_error(prefix: str, consecutive_failures: int, exc: Exception) -> None:
    print(f"{prefix} ({consecutive_failures} consecutive): {exc}", file=sys.stderr)


def _recoverable_failure_limit_reached(max_failures: int, consecutive_failures: int) -> bool:
    return max_failures > 0 and consecutive_failures >= max_failures


def _close_frame_source(frame_source) -> None:
    if frame_source is not None:
        frame_source.close()


def _should_refresh_reference_on_run(args: argparse.Namespace, config: dict) -> bool:
    if args.refresh_reference and args.skip_reference_refresh:
        raise ConfigError("Choose either --refresh-reference or --skip-reference-refresh, not both.")

    if args.skip_reference_refresh:
        return False
    if args.refresh_reference:
        return True

    resolved_source = args.source or config["camera"]["source"]
    return bool(config["mission"].get("refresh_reference_on_run", False)) and resolved_source == "picamera2"


def run_refresh_reference_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    recovery_delay = float(config["runtime"]["recovery_delay_seconds"])
    max_failures = int(config["runtime"]["max_consecutive_failures"])
    consecutive_failures = 0
    frame_source = None

    try:
        while True:
            try:
                if frame_source is None:
                    frame_source = open_frame_source(
                        config,
                        source_override=args.source,
                        input_image=args.input_image,
                        input_dir=args.input_dir,
                    )

                result = refresh_empty_reference(frame_source, config)
                print(json.dumps(result, indent=2))
                return 0
            except CameraError as exc:
                consecutive_failures += 1
                _log_recoverable_error(
                    "Recoverable camera error while capturing the mission-start empty reference",
                    consecutive_failures,
                    exc,
                )
                _close_frame_source(frame_source)
                frame_source = None

                if _recoverable_failure_limit_reached(max_failures, consecutive_failures):
                    print("Exceeded the maximum consecutive camera failures while refreshing the reference.", file=sys.stderr)
                    return 1
                time.sleep(recovery_delay)
    finally:
        _close_frame_source(frame_source)


def run_loop_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    refresh_reference_on_start = _should_refresh_reference_on_run(args, config)
    interval = float(config["camera"]["processing_interval_seconds"])
    recovery_delay = float(config["runtime"]["recovery_delay_seconds"])
    max_failures = int(config["runtime"]["max_consecutive_failures"])

    frame_source = None
    estimator: CaddyFullnessEstimator | None = None
    iteration = 0
    consecutive_failures = 0
    reference_ready = not refresh_reference_on_start

    try:
        while args.max_iterations is None or iteration < args.max_iterations:
            try:
                if frame_source is None:
                    frame_source = open_frame_source(
                        config,
                        source_override=args.source,
                        input_image=args.input_image,
                        input_dir=args.input_dir,
                    )

                if not reference_ready:
                    refresh_result = refresh_empty_reference(frame_source, config)
                    print(
                        "Mission-start empty reference captured from "
                        f"{refresh_result['captured_frames']} frame(s): {refresh_result['empty_reference_image']}",
                        file=sys.stderr,
                    )
                    reference_ready = True
                    estimator = None
                    consecutive_failures = 0
                    continue

                if estimator is None:
                    estimator = CaddyFullnessEstimator(config_path=args.config)

                result = estimator.analyze_frame(frame_source.read(), save_debug=args.debug)
                print(json.dumps(result, indent=2))
                iteration += 1
                consecutive_failures = 0
                if args.max_iterations is None or iteration < args.max_iterations:
                    time.sleep(interval)
            except (CameraError, EstimatorError) as exc:
                consecutive_failures += 1
                _log_recoverable_error("Recoverable runtime error", consecutive_failures, exc)
                _close_frame_source(frame_source)
                frame_source = None

                if _recoverable_failure_limit_reached(max_failures, consecutive_failures):
                    print("Exceeded the maximum consecutive runtime failures; stopping estimator.", file=sys.stderr)
                    return 1
                time.sleep(recovery_delay)
    finally:
        _close_frame_source(frame_source)

    return 0


def run_analyze_once_command(args: argparse.Namespace) -> int:
    estimator = CaddyFullnessEstimator(config_path=args.config)
    result = estimator.analyze_once(
        save_debug=args.debug,
        source_override=args.source,
        input_image=args.input_image,
        input_dir=args.input_dir,
    )
    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "calibrate":
            return run_calibrate_command(args)
        if args.command == "run":
            return run_loop_command(args)
        if args.command == "analyze-once":
            return run_analyze_once_command(args)
        if args.command == "refresh-reference":
            return run_refresh_reference_command(args)
        parser.error(f"Unsupported command: {args.command}")
        return 2
    except (ConfigError, CalibrationError, CameraError, EstimatorError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Stopping the estimator.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
