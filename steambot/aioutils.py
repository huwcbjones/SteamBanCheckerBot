"""asyncio utils"""
# pylint: disable=protected-access
import asyncio
import functools
import logging
import sys
import threading
import time
import traceback
from asyncio import (
    tasks,
    AbstractEventLoop,
    TimerHandle,
)
from concurrent.futures.thread import ThreadPoolExecutor
from pathlib import Path
from threading import Thread, Condition, Lock
from typing import (
    Coroutine,
    Optional,
    Callable,
    Any,
    TypeVar,
    Set,
    Iterable,
    Union,
    Dict,
)

LOGGER = logging.getLogger(__name__)

UNACCEPTABLE_CALL_DURATION = 0.2
TICKER_DELAY = 2.5

_THREAD_POOL_EXECUTOR = ThreadPoolExecutor(thread_name_prefix="run_in_thread")


T = TypeVar("T")  # pylint: disable=invalid-name


async def run_in_thread(func: Callable[..., T], *args, **kwargs) -> T:
    """Run a callable in a thread and return the result"""
    assert callable(func), f"{func} is not callable"

    return await asyncio.get_running_loop().run_in_executor(
        _THREAD_POOL_EXECUTOR, functools.partial(func, *args, **kwargs)
    )


def dump_gathered_exceptions(
    message: str, results: Iterable[Union[BaseException, Any]]
) -> bool:
    """
    Dump exceptions from `asyncio.gather(..., return_exception=True) call
    :param message: message/action that was in progress (e.g.: processing missed crashes)
    :param results: list of results from `asyncio.gather` call
    :return: true if exceptions were dumped, false if there were no exception
    """
    exceptions = [r for r in results if isinstance(r, BaseException)]
    if not exceptions:
        return False

    LOGGER.error("Encountered the following errors whilst %s:", message)
    for err in exceptions:
        LOGGER.exception(err)
    return True


class StallingEventLoopWatchdog:
    """Stalling event loop watchdog"""

    # pylint: disable=too-many-instance-attributes
    def __init__(self, loop: AbstractEventLoop):
        self._loop = loop
        self._counter = 0
        self._loop_ticker: Optional[TimerHandle] = None
        self._loop_thread_id: Optional[int] = None
        self._loop_ticker_timestamp = 0
        self._cond = Condition(Lock())
        self._stalled_in_debugger = False
        self._watchdog_thread: Optional[Thread] = None
        self._stopped = True
        self._init_watchdog()
        self._stall_counter_ids: Set[int] = set()
        self._ticker_time = TICKER_DELAY

    # pylint: enable=too-many-instance-attributes
    def _init_watchdog(self, daemon=True):
        if self._watchdog_thread:
            self.stop()
        self._watchdog_thread = Thread(
            target=self._watchdog_loop,
            daemon=daemon,
            name=f"aiowatchdog-{self._get_watchdog_id()}",
        )

    @staticmethod
    async def _get_thread_id() -> Optional[int]:
        return getattr(asyncio.get_running_loop(), "_thread_id")

    def _set_loop_thread_id(self, thread_id: Optional[int]):
        self._loop_thread_id = thread_id

    def _get_watchdog_id(self) -> str:
        if self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._get_thread_id(), self._loop
            ).add_done_callback(lambda f: self._set_loop_thread_id(f.result()))
        else:
            self._set_loop_thread_id(
                self._loop.run_until_complete(self._get_thread_id())
            )

        loop_thread_id = getattr(self._loop, "_thread_id", self._loop_thread_id)
        threads = [t for t in threading.enumerate() if t.ident == loop_thread_id]

        if not threads:
            return str(id(self._loop))

        return threads[0].name

    @property
    def running(self) -> bool:
        """is watchdog running"""
        return not self._stopped

    def start(self):
        """
        Start event loop watchdog
        """
        no_thread_msg = "(no thread ID)"
        LOGGER.debug(
            "Starting aiowatchdog for %s...", self._loop_thread_id or no_thread_msg
        )
        self._stopped = False
        self._ticker_loop()
        self._watchdog_thread.start()
        LOGGER.debug(
            "Started aiowatchdog for %s!", self._loop_thread_id or no_thread_msg
        )

    def stop(self):
        """
        Stop event loop watchdog
        """
        with self._cond:
            self._stopped = True
            self._cond.notify()
        if self._loop_ticker and not self._loop_ticker.cancelled():
            self._loop_ticker.cancel()

    def notify(self):
        """
        Notify event loop watchdog about event loop iteration
        """
        with self._cond:
            self._loop_ticker_timestamp = time.monotonic()
            self._counter += 1

    @property
    def ticker_time(self) -> float:
        """Ticker time delay"""
        return self._ticker_time

    def set_ticker_time(self, ticker_time: float):
        """Set the ticker time"""
        LOGGER.info(
            "Changing ticker time from %.3f to %.3f", self._ticker_time, ticker_time
        )
        self._ticker_time = ticker_time
        if self._loop_ticker and not self._loop_ticker.cancelled():
            LOGGER.debug("Cancelling existing _ticker_loop")
            self._loop_ticker.cancel()
        LOGGER.debug("Calling _ticker_loop")

        self._loop.call_soon_threadsafe(self._ticker_loop)

        # Wake up the event stall watchdog to make sure the new ticker time is used
        with self._cond:
            self._cond.notify_all()

    def _watchdog_loop(self):
        """
        Watchdog thread routine
        """
        self._watchdog_thread.setName(f"aiowatchdog-{self._loop_thread_id}")
        LOGGER.debug("Monitoring for event loop %s for stalls", self._loop_thread_id)
        with self._cond:
            while not self._stopped:
                elapsed = 0
                last_seen_counter = self._counter
                start = time.monotonic()

                # Wait for UNACCEPTABLE_LOOP_ITERATION_DURATION
                # Wait again if woken up spuriously
                while elapsed < self._ticker_time:
                    self._cond.wait(self._ticker_time - elapsed)
                    if self._loop.is_closed():
                        if self._loop_ticker and not self._loop_ticker.cancelled():
                            self._loop_ticker.cancel()
                        return
                    now = time.monotonic()
                    elapsed = now - start

                if last_seen_counter == self._counter:
                    self._stall_counter_ids.add(self._counter)
                    self._dump_reactor_stacktrace(now)

    @property
    def stall_count(self) -> int:
        """Number of times the event loop has stalled"""
        return len(self._stall_counter_ids)

    def reset_stall_count(self) -> int:
        """Reset the stall count, returns the count before it was reset"""
        size = self.stall_count
        self._stall_counter_ids.clear()
        return size

    @staticmethod
    def is_paused_in_pydevd(stacktrace: traceback.StackSummary) -> bool:
        """determines whether the stacktrace is paused in pydevd"""
        # pylint: disable=import-outside-toplevel
        # pylint: disable=unused-import
        try:
            import pydevd
        except ModuleNotFoundError:
            return False
        # pylint: enable=import-outside-toplevel
        # pylint: enable=unused-import

        if not stacktrace:
            return False
        last_frame: traceback.FrameSummary = stacktrace[-1]
        return (
            last_frame.name == "_do_wait_suspend"
            and Path(last_frame.filename).stem == "pydevd"
        )

    def _dump_reactor_stacktrace(self, now):
        """
        Dump reactor thread stacktrace
        """

        for thread_id, stack in sys._current_frames().items():
            loop_thread_id = getattr(self._loop, "_thread_id", self._loop_thread_id)

            if thread_id != loop_thread_id:
                continue

            stalled_traceback = traceback.extract_stack(stack)
            if self.is_paused_in_pydevd(stalled_traceback):
                if not self._stalled_in_debugger:
                    LOGGER.warning("Event loop paused in debugger")
                self._stalled_in_debugger = True
                return

            if self._stalled_in_debugger:
                self._stalled_in_debugger = False

            trace = ["Traceback (most recent call last):"]
            if stalled_traceback is not None:
                for filename, lineno, name, line in stalled_traceback:
                    trace.append(
                        '  File "%s", line %d, in %s' % (filename, lineno, name)
                    )
                    if line:
                        trace.append("    %s" % (line.strip()))
            traceback_string = "\n".join(trace) + "\nEventLoopStallingException"

            time_since_loop_ticker = now - self._loop_ticker_timestamp
            if time_since_loop_ticker <= 0:
                return
            LOGGER.error(
                (
                    "Event Loop stalling!\n"
                    "Time since event loop ticker was called: %.3f\n"
                    "Event Loop Thread: %s\n"
                    "%s\n"
                ),
                time_since_loop_ticker,
                thread_id,
                traceback_string,
            )

    def _ticker_loop(self):
        """
        Periodically wakes up the reactor
        """
        self._loop_thread_id = threading.get_ident()
        call_in = self._ticker_time * 0.85
        LOGGER.debug("Calling ticker in %.5f", call_in)
        self._loop_ticker = self._loop.call_later(call_in, self._ticker_loop)
        self.notify()


def _log_blocking_call(handle, time_elapsed: float):
    callback = handle._callback

    if not isinstance(getattr(callback, "__self__", None), tasks.Task):
        return

    task: tasks.Task = callback.__self__
    coro: Coroutine = task.get_coro()

    # Add offending coroutine to bottom of traceback
    offender = 'File "%s", line %d, in %s' % (
        coro.cr_code.co_filename,
        coro.cr_code.co_firstlineno,
        coro.cr_code.co_name,
    )

    LOGGER.warning(
        (
            "New potential event loop blocker:\n"
            "  Duration: %.3f\n"
            "  Offender: %s\n"
            "  File: %s\n"
        ),
        time_elapsed,
        task,
        offender,
    )


_SLOW_CALL_LOOPS: Set[AbstractEventLoop] = set()


def _run_with_time(_run):
    """wrapper to time how long a coro takes to run and warn if it too took long"""

    @functools.wraps(_run)
    def _wrapper(self):
        start_time = time.monotonic()
        try:
            return _run(self)
        finally:
            if asyncio.get_running_loop() in _SLOW_CALL_LOOPS:
                # only log if the running loop is being monitored
                delta_time = time.monotonic() - start_time
                if delta_time >= UNACCEPTABLE_CALL_DURATION:
                    _log_blocking_call(self, delta_time)

    return _wrapper


def _unhandled_exception_handler(_loop: AbstractEventLoop, context: Dict[str, Any]):
    # See https://docs.python.org/3/library/asyncio-eventloop.html for keys in the context
    LOGGER.critical("Encountered an unhandled exception: %s", context["message"])
    if future := context.get("future"):
        LOGGER.info("Exception thrown by: %s", future)
    if err := context.get("exception"):
        LOGGER.exception(err)


def _install_slow_coro_patch(loop: Optional[AbstractEventLoop]):
    loop = loop or asyncio.get_event_loop()
    loop.slow_callback_duration = UNACCEPTABLE_CALL_DURATION
    _SLOW_CALL_LOOPS.add(loop)
    LOGGER.info("Installed slow coroutine handler")


def _remove_slow_coro_patch(loop: Optional[AbstractEventLoop]):
    loop = loop or asyncio.get_event_loop()
    _SLOW_CALL_LOOPS.remove(loop)
    LOGGER.info("Uninstalled slow coroutine handler")


def _install_stall_watchdog(loop: Optional[AbstractEventLoop]):
    loop = loop or asyncio.get_event_loop()
    watchdog = StallingEventLoopWatchdog(loop)
    watchdog.start()
    LOGGER.info("Installed event loop stall watchdog")
    setattr(loop, "watchdog", watchdog)


def _remove_stall_watchdog(loop: Optional[AbstractEventLoop]):
    loop = loop or asyncio.get_event_loop()
    if (watchdog := get_stall_watchdog(loop)) is None:
        return
    watchdog.stop()
    delattr(loop, "watchdog")
    LOGGER.info("Uninstalled event loop stall watchdog")


def get_stall_watchdog(loop: AbstractEventLoop) -> Optional[StallingEventLoopWatchdog]:
    """Get the event loop stall watchdog (if the loop has one)"""
    return getattr(loop, "watchdog", None)


# pylint: disable=comparison-with-callable
def _install_unhandled_exception_handler(loop: Optional[AbstractEventLoop]):
    loop = loop or asyncio.get_event_loop()
    current_handler: Optional[Callable] = loop.get_exception_handler()
    if current_handler != _unhandled_exception_handler:
        loop.set_exception_handler(_unhandled_exception_handler)
    LOGGER.info("Installed default exception handler")


# pylint: enable=comparison-with-callable


def _set_loop_debug(loop_debug: bool, loop: Optional[AbstractEventLoop] = None):
    loop = loop or asyncio.get_event_loop()
    debug_state = loop_debug and __debug__
    loop.set_debug(debug_state)
    if debug_state:
        LOGGER.warning("Setting Event Loop debug to ON, this may harm performance!")
    else:
        LOGGER.info("Setting Event Loop debug to OFF")


def install(loop: AbstractEventLoop = None, loop_debug: bool = False):
    """Install asyncio debugging utils"""
    LOGGER.info("Installing Event Loop utilities...")
    _install_unhandled_exception_handler(loop)
    _install_slow_coro_patch(loop)
    _install_stall_watchdog(loop)
    _set_loop_debug(loop_debug, loop)


def uninstall(loop: AbstractEventLoop):
    """Uninstall asyncio debugging utils"""
    LOGGER.info("Removing Event Loop utilities...")
    _set_loop_debug(False, loop)
    _remove_slow_coro_patch(loop)
    _remove_stall_watchdog(loop)


setattr(asyncio.events.Handle, "_run", _run_with_time(asyncio.events.Handle._run))
