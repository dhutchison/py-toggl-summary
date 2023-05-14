from datetime import timedelta

from reporter import detailed_time_reporter, structures


def test_positive_marker_entry_detection():
    test_entry = structures.SimplifiedDetailedReportItem()
    test_entry.tags = ["tag1", "marker", "tag3"]

    is_marker = detailed_time_reporter.does_entry_have_break_start_marker(test_entry)

    assert is_marker == True


def test_negative_marker_entry_detection():
    # first test where there are no tags at all
    test_entry = structures.SimplifiedDetailedReportItem()

    is_marker = detailed_time_reporter.does_entry_have_break_start_marker(test_entry)
    assert is_marker == False

    # Then test again with tags that just do not include our marker
    test_entry.tags = ["tag1", "tag2", "tag3"]

    is_marker = detailed_time_reporter.does_entry_have_break_start_marker(test_entry)
    assert is_marker == False


def test_positive_are_entries_for_the_same_day():
    test_entry_a = structures.SimplifiedDetailedReportItem()
    test_entry_a.start = "2020-09-04T10:10:10+01:00"
    test_entry_a.end = "2020-09-04T11:10:11+01:00"

    test_entry_b = structures.SimplifiedDetailedReportItem()
    test_entry_b.start = "2020-09-04T11:10:10+01:00"
    test_entry_b.end = "2020-09-04T12:10:11+01:00"

    result = detailed_time_reporter.are_entries_for_the_same_day(
        test_entry_b, test_entry_a
    )
    assert result == True


def test_negative_are_entries_for_the_same_day():
    test_entry_a = structures.SimplifiedDetailedReportItem()
    test_entry_a.start = "2020-09-04T10:10:10+01:00"
    test_entry_a.end = "2020-09-04T11:10:11+01:00"

    test_entry_b = structures.SimplifiedDetailedReportItem()
    test_entry_b.start = "2020-09-05T11:10:10+01:00"
    test_entry_b.end = "2020-09-05T12:10:11+01:00"

    result = detailed_time_reporter.are_entries_for_the_same_day(
        test_entry_b, test_entry_a
    )
    assert result == False


def test_positive_was_previous_entry_break_start():
    entries = [
        structures.SimplifiedDetailedReportItem(),
        structures.SimplifiedDetailedReportItem(),
    ]

    entries[0].start = "2020-09-04T10:10:10+01:00"
    entries[0].end = "2020-09-04T11:10:11+01:00"
    entries[0].tags = ["marker"]

    entries[1].start = "2020-09-04T11:10:10+01:00"
    entries[1].end = "2020-09-04T12:10:11+01:00"

    previous_is_marker = detailed_time_reporter.was_previous_entry_break_start(
        1, entries
    )
    assert previous_is_marker == True


def test_negative_was_previous_entry_break_start_no_tags():
    """
    should not count previous entry as a marker entry as previous has no tags
    """

    entries = [
        structures.SimplifiedDetailedReportItem(),
        structures.SimplifiedDetailedReportItem(),
    ]

    previous_is_marker = detailed_time_reporter.was_previous_entry_break_start(
        1, entries
    )
    assert previous_is_marker == False


def test_negative_was_previous_entry_break_start_no_previous():
    """
    should not count previous entry as a marker entry as no previous
    """

    entries = [structures.SimplifiedDetailedReportItem()]

    previous_is_marker = detailed_time_reporter.was_previous_entry_break_start(
        0, entries
    )
    assert previous_is_marker == False


def test_negative_was_previous_entry_break_start_previous_day():
    """
    should not count previous entry as a marker entry as previous day
    """

    entries = [
        structures.SimplifiedDetailedReportItem(),
        structures.SimplifiedDetailedReportItem(),
    ]

    entries[0].start = "2020-09-03T18:10:10+01:00"
    entries[0].end = "2020-09-03T18:10:11+01:00"
    entries[0].tags = ["marker"]

    entries[1].start = "2020-09-04T10:10:10+01:00"
    entries[1].end = "2020-09-04T11:10:11+01:00"

    previous_is_marker = detailed_time_reporter.was_previous_entry_break_start(
        1, entries
    )
    assert previous_is_marker == False


def test_get_time_between_entries_non_zero():
    """
    should calculate the duration between entries
    """

    entries = [
        structures.SimplifiedDetailedReportItem(),
        structures.SimplifiedDetailedReportItem(),
    ]

    entries[0].start = "2020-09-03T10:10:00+01:00"
    entries[0].end = "2020-09-03T10:10:00+01:00"
    entries[1].start = "2020-09-03T10:20:00+01:00"
    entries[1].end = "2020-09-03T10:20:00+01:00"

    duration = detailed_time_reporter.get_time_between_entries(1, entries)

    assert (duration.total_seconds() / 60) == 10


def test_get_time_between_entries_zero():
    """
    should calculate the duration between entries to be zero when there is only one
    """

    entries = [structures.SimplifiedDetailedReportItem()]

    entries[0].start = "2020-09-03T10:10:00+01:00"
    entries[0].end = "2020-09-03T10:10:00+01:00"

    duration = detailed_time_reporter.get_time_between_entries(0, entries)

    assert duration.total_seconds() == 0


def test_format_millis():
    """
    Validate should format milliseconds into HH:mm:ss format
    """

    short_duration = timedelta(minutes=9, seconds=2)
    formatted_short = detailed_time_reporter.format_millis(
        1000 * short_duration.total_seconds()
    )
    assert formatted_short == "00:09:02"

    long_duration = timedelta(hours=10, minutes=30, seconds=59)
    formatted_long = detailed_time_reporter.format_millis(
        1000 * long_duration.total_seconds()
    )
    assert formatted_long == "10:30:59"


def test_calculate_time_totals_same_day():
    """
    Validate should calculate totals in the same day
    """

    entries = [
        structures.SimplifiedDetailedReportItem(),
        structures.SimplifiedDetailedReportItem(),
        structures.SimplifiedDetailedReportItem(),
        structures.SimplifiedDetailedReportItem(),
    ]

    entries[0].start = "2020-09-03T10:10:00+01:00"
    entries[0].end = "2020-09-03T10:20:00+01:00"
    entries[0].description = "one"
    entries[0].dur = timedelta(minutes=10).total_seconds() * 1000

    entries[1].start = "2020-09-03T10:30:00+01:00"
    entries[1].end = "2020-09-03T10:50:00+01:00"
    entries[1].description = "two"
    entries[1].dur = timedelta(minutes=20).total_seconds() * 1000

    entries[2].start = "2020-09-03T10:50:00+01:00"
    entries[2].end = "2020-09-03T10:50:00+01:00"
    entries[2].description = "three"
    entries[2].dur = 0
    entries[2].tags = ["marker"]

    entries[3].start = "2020-09-03T12:00:00+01:00"
    entries[3].end = "2020-09-03T12:50:00+01:00"
    entries[3].description = "four"
    entries[3].dur = timedelta(minutes=50).total_seconds() * 1000

    totals = detailed_time_reporter.calculate_time_totals(entries)

    assert totals.booked_time == timedelta(minutes=80).total_seconds() * 1000
    assert totals.break_time == timedelta(minutes=70).total_seconds() * 1000
    assert totals.unbooked_time == timedelta(minutes=10).total_seconds() * 1000
    assert totals.time_count == timedelta(minutes=90).total_seconds() * 1000


def test_calculate_time_totals_across_days():
    """
    Validate should calculate totals across days
    """

    entries = [
        structures.SimplifiedDetailedReportItem(),
        structures.SimplifiedDetailedReportItem(),
        structures.SimplifiedDetailedReportItem(),
    ]

    entries[0].start = "2020-09-03T10:10:00+01:00"
    entries[0].end = "2020-09-03T10:20:00+01:00"
    entries[0].description = "one"
    entries[0].dur = timedelta(minutes=10).total_seconds() * 1000

    entries[1].start = "2020-09-03T10:50:00+01:00"
    entries[1].end = "2020-09-03T10:50:00+01:00"
    entries[1].description = "two"
    entries[1].dur = 0
    entries[1].tags = ["marker"]

    entries[2].start = "2020-09-04T12:00:00+01:00"
    entries[2].end = "2020-09-04T12:50:00+01:00"
    entries[2].description = "three"
    entries[2].dur = timedelta(minutes=50).total_seconds() * 1000

    totals = detailed_time_reporter.calculate_time_totals(entries)

    assert totals.booked_time == timedelta(minutes=60).total_seconds() * 1000
    assert totals.break_time == timedelta().total_seconds() * 1000
    assert totals.unbooked_time == timedelta(minutes=30).total_seconds() * 1000
    assert totals.time_count == timedelta(minutes=90).total_seconds() * 1000
