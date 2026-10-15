#!/usr/bin/env python3
"""Monitor per-camera detection topic rates during a stability run."""

import argparse
import os
import statistics
import sys
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from vision_msgs.msg import Detection2DArray


DEFAULT_TOPICS = (
    "/deepstream/camera_0/detections",
    "/deepstream/camera_1/detections",
    "/deepstream/camera_2/detections",
)


def evaluate_windows(
    windows,
    min_rate_hz,
    max_relative_jitter,
    max_cross_topic_spread_hz,
    warmup_windows=0,
):
    """Return ``(ok, reason)`` for completed topic-rate windows."""
    if len(windows) <= warmup_windows:
        return False, "no complete rate window was observed after warmup"

    windows = windows[warmup_windows:]

    topics = tuple(windows[0])
    rates_by_topic = {topic: [] for topic in topics}
    for window in windows:
        rates = [window[topic] for topic in topics]
        if min(rates) < min_rate_hz:
            return (
                False,
                "a topic rate fell below the minimum: "
                f"{min(rates):.3f} < {min_rate_hz:.3f} Hz",
            )
        if max(rates) - min(rates) > max_cross_topic_spread_hz:
            return (
                False,
                "the three topic rates diverged by "
                f"{max(rates) - min(rates):.3f} Hz",
            )
        for topic, rate in window.items():
            rates_by_topic[topic].append(rate)

    for topic, rates in rates_by_topic.items():
        median = statistics.median(rates)
        if median <= 0:
            return False, f"{topic} has no positive rate"
        relative_jitter = (max(rates) - min(rates)) / median
        if relative_jitter > max_relative_jitter:
            return (
                False,
                f"{topic} jitter is {relative_jitter:.3f}, "
                f"limit is {max_relative_jitter:.3f}",
            )

    return True, "all topic-rate windows passed"


class DetectionTopicMonitor(Node):
    """Count messages and emit one rate record per completed time window."""

    def __init__(self, topics, duration, window, launch_pid):
        super().__init__("detection_topic_monitor")
        self._topics = tuple(topics)
        self._counts = {topic: 0 for topic in self._topics}
        self._windows = []
        self._duration = duration
        self._window = window
        self._launch_pid = launch_pid
        self._started = time.monotonic()
        self._window_started = self._started
        self._stopped_early = None
        self._finished = False

        for topic in self._topics:
            self.create_subscription(
                Detection2DArray,
                topic,
                lambda _message, name=topic: self._message_received(name),
                10,
            )
        self.create_timer(1.0, self._tick)

    @property
    def windows(self):
        return self._windows

    @property
    def stopped_early(self):
        return self._stopped_early

    @property
    def finished(self):
        return self._finished

    def _message_received(self, topic):
        self._counts[topic] += 1

    def _launch_is_alive(self):
        if self._launch_pid is None:
            return True
        try:
            os.kill(self._launch_pid, 0)
        except OSError:
            return False
        return True

    def _tick(self):
        now = time.monotonic()
        if not self._launch_is_alive():
            self._stopped_early = "the DeepStream launch process exited"
            self._finished = True
            return

        elapsed = now - self._window_started
        if elapsed >= self._window:
            rates = {
                topic: count / elapsed for topic, count in self._counts.items()
            }
            self._windows.append(rates)
            print(
                "window="
                f"{len(self._windows)} "
                + " ".join(
                    f"{topic.rsplit('/', 2)[1]}={rate:.3f}Hz"
                    for topic, rate in rates.items()
                ),
                flush=True,
            )
            self._counts = {topic: 0 for topic in self._topics}
            self._window_started = now

        if now - self._started >= self._duration:
            self._finished = True


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Monitor the three DeepStream detection topics."
    )
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--window", type=float, default=10.0)
    parser.add_argument("--launch-pid", type=int, default=None)
    parser.add_argument("--min-rate", type=float, default=8.0)
    parser.add_argument("--max-relative-jitter", type=float, default=0.25)
    parser.add_argument("--max-topic-spread", type=float, default=1.0)
    parser.add_argument("--warmup-windows", type=int, default=1)
    parser.add_argument("topics", nargs="*", default=list(DEFAULT_TOPICS))
    args = parser.parse_args()
    if args.duration <= 0 or args.window <= 0:
        parser.error("duration and window must be positive")
    if args.min_rate < 0 or args.max_relative_jitter < 0:
        parser.error("rate limits must be non-negative")
    if args.max_topic_spread < 0:
        parser.error("max-topic-spread must be non-negative")
    if args.warmup_windows < 0:
        parser.error("warmup-windows must be non-negative")
    return args


def main():
    args = _parse_args()
    rclpy.init()
    monitor = DetectionTopicMonitor(
        args.topics,
        args.duration,
        args.window,
        args.launch_pid,
    )
    try:
        while rclpy.ok() and not monitor.finished:
            rclpy.spin_once(monitor, timeout_sec=1.0)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        windows = monitor.windows
        if monitor.stopped_early:
            print(f"monitor failed: {monitor.stopped_early}", file=sys.stderr)
            result = False
            reason = monitor.stopped_early
        else:
            result, reason = evaluate_windows(
                windows,
                args.min_rate,
                args.max_relative_jitter,
                args.max_topic_spread,
                args.warmup_windows,
            )
            if not result:
                print(f"monitor failed: {reason}", file=sys.stderr)
        if result:
            print(f"monitor passed: {reason}", flush=True)
        monitor.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0 if result else 1


if __name__ == "__main__":
    raise SystemExit(main())
