#
# There are two report APIs which we could use to get this information pretty easily:
# - WEEKLY - this is fixed to 7 days since the "since" parameter. e.g.
#            GET "https://api.track.toggl.com/reports/api/v2/weekly?workspace_id=<workspaceid>&since=2021-07-11&until=2021-07-18&user_agent=api_test"
# - SUMMARY - this allows us to specify the range of dates, and specify the groupings
#             GET "https://api.track.toggl.com/reports/api/v2/summary?workspace_id=<workspaceid>&since=2021-07-11&user_agent=api_test&grouping=clients&subgrouping=projects"
#
# There is also an option to use the detailed data we already have gathered and parse it a
# different way to gain the same sort of summary.
#
# This file will work with the outputs of the summary API for now.
#

import logging
from enum import Enum
from functools import cmp_to_key
from typing import List, Self

from reporter import structures

logger = logging.getLogger(__name__)


class GroupingType(Enum):
    """
    Enum holding the types of grouping the API could use. While we parse this, we largely
    expect to use the client grouping, with project subgrouping.
    """

    CLIENT = 1
    PROJECT = 2
    USER = 3
    UNKNOWN = 4


class Summary:
    """
    A parsed summary output
    """

    # Name of the grouping member
    name: ascii

    # The type of the grouping.
    grouping_type: GroupingType

    # time booked against actual tasks (should match the API total time)
    booked_time: int

    # The percentage of the total time this client had booked against it.
    percentage_of_total_time: float

    subgroup_summary: List[Self] = []


class SummaryReportTitle:
    client: str
    project: str
    user: str


class SummaryReportItem:
    """
    Object holding fields for a single data item in the summary report call.
    """

    title: SummaryReportTitle
    time: int

    items: List[Self]


def get_grouping_type(title: SummaryReportTitle):
    """
    Get the type of grouping the supplied title object is for.
    :param dict title: title the object to parse
    :return: GroupingType
    """

    if "client" in title and title["client"] is not None:
        return GroupingType.CLIENT
    if "project" in title and title["project"] is not None:
        return GroupingType.PROJECT
    if "user" in title and title["user"] is not None:
        return GroupingType.USER

    return GroupingType.UNKNOWN


def get_grouping_name(title: SummaryReportTitle):
    """
    Get the name of the grouping member the supplied title object is for.
    :param dict title: title the object to parse
    :return: string
    """
    if "client" in title and title["client"] is not None:
        return title["client"]
    elif "project" in title and title["project"] is not None:
        return title["project"]
    elif "user" in title and title["user"] is not None:
        return title["user"]
    return "Unknown Client/Project"


def calculate_percentage(partial_value: int, total_value: int):
    """
    Helper function to calculate the percentage of one number to another
    :param int partial_value: the partial value
    :param int total_value: the total value
    :return: the calculated percentage
    """
    if total_value > 0:
        return (100 * partial_value) / total_value

    return 0


def calculate_total_time(
    report_data: List[SummaryReportItem], total_time: structures.TimeSummary = None
):
    """
    Calculate the total duration that should be used based on the input data.

    If a TimeSummary is supplied, the totalCount value from this will be used. Otherwise
    the total duration will be calculated from the summary report data.

    @param reportData the input summary report data items
    @param totalTime the time summary calculated from detailed entries
    @returns the total time value to use.
    """

    total_duration = 0
    if total_time is not None:
        # If a time summary has been passed through, use it for the total
        total_duration = total_time.time_count
    else:
        # A summary was not passed through, calculate based on the supplied items
        for item in report_data:
            total_duration += item["time"]

    return total_duration


def calculate_summary_totals(
    report_data: List[SummaryReportItem], total_time: structures.TimeSummary = None
):
    """
    Calculate summary information for the report data, split into per-client and per-project.

    This assumes the summary API has been invoked with the following parameters:
    - grouping=clients
    - subgrouping=projects


    @param reportData The summary report items from the api for the reporting period
    @param totalTime The total time calculated for the time period.
                     This is used to provide "unbooked" time in the summary.
    @param debug boolean for if debug logging should be used (true) or not (false)
    """

    # Get the total duration to use
    total_duration = calculate_total_time(report_data, total_time)

    # Process each data item
    ret_items = []
    for value in report_data:
        summary = Summary()
        summary.grouping_type = get_grouping_type(value["title"])
        summary.name = get_grouping_name(value["title"])
        summary.booked_time = value["time"]
        summary.percentage_of_total_time = calculate_percentage(
            value["time"], total_duration
        )

        logger.debug("Summary: %s", summary)

        if "items" in value:
            # If there are subgroup items, process recursively
            summary.subgroup_summary = calculate_summary_totals(value["items"])

        ret_items.append(summary)

    if total_time is not None:
        # If there are time totals supplied from detailed reports, include an item for unbooked
        unbooked = Summary()
        unbooked.booked_time = total_time.unbooked_time
        unbooked.grouping_type = GroupingType.UNKNOWN
        unbooked.percentage_of_total_time = calculate_percentage(
            total_time.unbooked_time, total_duration
        )
        unbooked.name = "Unbooked Time"

        ret_items.append(unbooked)

    # Sort by time
    return sorted(ret_items, key=cmp_to_key(lambda a, b: b.booked_time - a.booked_time))
