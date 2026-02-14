"""
scheduler.py - APScheduler setup for daily job digest generation.
Schedules the main job search pipeline to run at user-specified time.
"""

import logging
import signal
import sys

logger = logging.getLogger(__name__)


def parse_time(time_str):
    """Parse a time string like '6:00 AM' or '18:30' into (hour, minute)."""
    time_str = time_str.strip().upper()

    # Handle 12-hour format
    if "AM" in time_str or "PM" in time_str:
        is_pm = "PM" in time_str
        time_str = time_str.replace("AM", "").replace("PM", "").strip()
        parts = time_str.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if is_pm and hour != 12:
            hour += 12
        elif not is_pm and hour == 12:
            hour = 0
        return hour, minute

    # Handle 24-hour format
    parts = time_str.split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def setup_scheduler(run_fn, preferences):
    """
    Set up APScheduler to run run_fn daily at the user-specified time.
    run_fn should be the main pipeline function (no arguments).
    """
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.error("APScheduler not installed. Run: pip install APScheduler")
        print("Error: APScheduler not installed. Run: pip install APScheduler")
        return

    digest_time = preferences.get("digest_time", "6:00 AM")
    hour, minute = parse_time(digest_time)

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_fn,
        CronTrigger(hour=hour, minute=minute),
        id="daily_job_digest",
        name=f"Daily Job Digest at {digest_time}",
        replace_existing=True,
    )

    # Graceful shutdown
    def shutdown(signum, frame):
        print("\nShutting down scheduler...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"\nScheduler started. Daily digest will run at {digest_time} ({hour:02d}:{minute:02d}).")
    print("Press Ctrl+C to stop.\n")
    logger.info("Scheduler started: daily at %02d:%02d", hour, minute)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\nScheduler stopped.")
        logger.info("Scheduler stopped")
