import logging
import math
from datetime import datetime, timedelta
from typing import List

from reporter import structures

logger = logging.getLogger(__name__)


def does_entry_have_break_start_marker(entry: structures.SimplifiedDetailedReportItem):
    """
    * Function to determine if an entry contains the tag for being a marker.
    * @param entry the entry to check
    * @returns true if it does, false if it doesn't
    """
    return hasattr(entry, "tags") and "marker" in entry.tags


def are_entries_for_the_same_day(
    a: structures.SimplifiedDetailedReportItem,
    b: structures.SimplifiedDetailedReportItem,
):
    """
    Function to determine if two entries are for the same day.
    * @param a the first entry to compare the end date for
    * @param b the second entry to compare the start date for
    """

    # Check the entries are for the same day
    current_date = a.end[:10]
    prev_date = b.start[:10]

    return current_date == prev_date


def was_previous_entry_break_start(
    current_index: int, array: List[structures.SimplifiedDetailedReportItem]
):
    """
    Helper function to work out if the previous entry was the start of a break.
    @param currentIndex the index in the array for the current entry
    @param array the array containing the entries
    """

    # A previous entry counts as a break start if:
    # - it has a marker tag (doesEntryHaveBreakStartMarker)
    # - the entry before it does is not a break start
    # - it is for the same day as the current entry

    is_marker = False
    if current_index > 0:
        prev_entry = array[current_index - 1]
        is_marker = does_entry_have_break_start_marker(prev_entry)

        if is_marker:
            # Check the previous entry to check it wasn't one too
            is_marker = not was_previous_entry_break_start(current_index - 1, array)

        if is_marker:
            # Check the entries are for the same day
            is_marker = are_entries_for_the_same_day(
                array[current_index - 1], array[current_index]
            )

    return is_marker


def get_time_between_entries(
    current_index: int, array: List[structures.SimplifiedDetailedReportItem]
):
    """
    Helper function to calculate the duration between the start of the current
    entry and the end of the previous one.
    @param currentIndex the index in the array for the current entry
    @param array the array containing the entries
    @param debug boolean for if debug logging should be used (true) or not (false)
    """

    # Work out the time between this entry and the previous one
    time_between_entries = timedelta()
    if current_index > 0:
        # There is a previous entry */

        prev_entry_end = datetime.fromisoformat(array[current_index - 1].end)
        current_entry_start = datetime.fromisoformat(array[current_index].start)

        time_between_entries = current_entry_start - prev_entry_end

    logger.debug("Time between entries: %s", time_between_entries)

    return time_between_entries


def format_millis(input_millis: int):
    seconds = math.floor((input_millis / 1000) % 60)
    minutes = math.floor((input_millis / (1000 * 60)) % 60)
    # Intentionally not "% 24" this as we want to be able to have more than 24 hours
    # not using a seperate day counter */
    hours = math.floor((input_millis / (1000 * 60 * 60)))

    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def calculate_time_totals(report_data: List[structures.SimplifiedDetailedReportItem]):
    """
    * Calculate summary time information for the report data.
    *
    * Includes:
    * - Total actual working hours (combination of booked and unbooked time)
    * - breaks (time between a marker item and the next non-marker)
    * - time booked against actual tasks (should match the API total time)
    * - unbooked time (any time between items that isn't covered as breaks)
    *
    * @param reportData The detailed time entry items for the reporting period
    * @param debug boolean for if debug logging should be used (true) or not (false)
    """

    # Setup the initial return object with initial values
    time_summary = structures.TimeSummary()
    time_summary.time_count = 0
    time_summary.break_time = 0
    time_summary.booked_time = 0
    time_summary.unbooked_time = 0

    # Sort the input data by the item start date & time
    sorted_report_data = sorted(
        report_data, key=lambda a: datetime.fromisoformat(a.start)
    )
    # reportData
    #     .sort((a, b) => ZonedDateTime.parse(a.start).compareTo(ZonedDateTime.parse(b.start)));

    for index, entry in enumerate(sorted_report_data):
        # reportData
        #     .forEach((entry, index, array) => {

        logger.debug("======================")
        logger.debug(
            "Counts so far: total %s, breaks %s, booked %s, unbooked: %s",
            format_millis(time_summary.time_count),
            format_millis(time_summary.break_time),
            format_millis(time_summary.booked_time),
            format_millis(time_summary.unbooked_time),
        )
        logger.debug(
            "Time entry for %s: %s (%s - %s)",
            entry.description,
            format_millis(entry.dur),
            entry.start,
            entry.end,
        )

        # An entry is a "break start" marker if all the following are true:
        # - it has the tag "marker"
        # - the previous entry did not also have the tag "marker"
        entry_has_marker = (
            not was_previous_entry_break_start(index, sorted_report_data)
        ) and does_entry_have_break_start_marker(entry)

        # Add the booked time for the entry to the running total,
        # if it isn't a 'break' entry */

        if not entry_has_marker:
            time_summary.booked_time += entry.dur

        # Work out the time between this entry and the previous one
        # and add it to the correct total based on our state */
        time_between_entries = get_time_between_entries(index, sorted_report_data)

        if was_previous_entry_break_start(index, sorted_report_data):
            # The previous entry was a 'break start' marker.
            # Gap time is break time
            time_summary.break_time += time_between_entries.total_seconds() * 1000
            logger.debug(
                "Break time! %s",
                format_millis(time_between_entries.total_seconds() * 1000),
            )
        elif index == 0 or are_entries_for_the_same_day(
            sorted_report_data[index - 1], sorted_report_data[index]
        ):
            # Gap time is unbooked time if the end of the last item is the same
            # day as the current item
            time_summary.unbooked_time += time_between_entries.total_seconds() * 1000

            # Only log it to the console if it is more than 5 minutes
            if time_between_entries.total_seconds() > 300:
                logger.debug(
                    "Unbooked time since last entry: %s",
                    format_millis(time_between_entries.total_seconds() * 1000),
                )

    # Total time is the combination of booked and unbooked time
    time_summary.time_count = time_summary.booked_time + time_summary.unbooked_time

    # All done, return
    return time_summary
