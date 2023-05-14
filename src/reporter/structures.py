from typing import List


class SimplifiedDetailedReportItem:
    """
    Class containing the cut down fields of a regular DetailedReportItem
    that we are actually interested in for this program.
    """

    def __init__(self, my_dict=None):
        if my_dict:
            # If a dictionary is supplied then set the fields in this class to
            # the matching values in the dict
            for key in my_dict:
                setattr(self, key, my_dict[key])

    # time entry description
    description: str

    # start time of the time entry in ISO 8601 date and time format (YYYY-MM-DDTHH:MM:SS)
    start: str

    # end time of the time entry in ISO 8601 date and time format (YYYY-MM-DDTHH:MM:SS)
    end: str

    # time entry duration in milliseconds
    dur: int

    # array of tag names, which assigned for the time entry
    tags: List[str]


class TimeSummary:
    """
    Object holding the calculated times
    """

    # Total actual working hours (combination of booked and unbooked time)
    time_count: int

    # breaks (time between a marker item and the next non-marker)
    break_time: int

    # time booked against actual tasks (should match the API total time)
    booked_time: int

    # unbooked (any time between items that isn't covered as breaks)
    unbooked_time: int
