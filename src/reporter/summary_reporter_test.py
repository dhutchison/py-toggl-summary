import json
from pathlib import Path

from reporter import summary_reporter


def test_client_title_type():
    title = {"client": "test-client"}

    actual_type = summary_reporter.get_grouping_type(title)
    assert actual_type == summary_reporter.GroupingType.CLIENT
    actual_name = summary_reporter.get_grouping_name(title)
    assert actual_name == title["client"]


def test_project_title_type():
    title = {"project": "test-project"}

    actual_type = summary_reporter.get_grouping_type(title)
    assert actual_type == summary_reporter.GroupingType.PROJECT
    actual_name = summary_reporter.get_grouping_name(title)
    assert actual_name == title["project"]


def test_user_title_type():
    title = {"user": "test-user"}

    actual_type = summary_reporter.get_grouping_type(title)
    assert actual_type == summary_reporter.GroupingType.USER
    actual_name = summary_reporter.get_grouping_name(title)
    assert actual_name == title["user"]


def test_calculate_percentages():
    actual_value = summary_reporter.calculate_percentage(10, 100)
    assert actual_value == 10


def test_calculate_percentages_zero_values():
    actual_value = summary_reporter.calculate_percentage(10, 0)
    assert actual_value == 0


def test_calculate_summary():
    """
    summary-reporter calculate summary tests, without detailed time calculations
    """
    test_file_path = (
        Path(__file__).parent.resolve().joinpath("test_resources", "summary.json")
    )
    with open(test_file_path, encoding="UTF-8") as sample_file:
        test_data = json.load(sample_file)

        actual_result = summary_reporter.calculate_summary_totals(test_data["data"])

        assert actual_result is not None
        assert len(actual_result) == 5

        assert actual_result[0].name == "Client-2"
        assert actual_result[0].booked_time == 29893000
        assert "{:.2f}".format(actual_result[0].percentage_of_total_time) == "32.76"
        assert actual_result[0].subgroup_summary is not None

        assert actual_result[1].name == "Legacy"
        assert actual_result[1].booked_time == 22097000
        assert "{:.2f}".format(actual_result[1].percentage_of_total_time) == "24.22"
        # We will only test values for the a single item here.
        # Don't need to go through every item, the inner array uses the exact same process / object
        # structure as the outers.
        assert actual_result[1].subgroup_summary is not None
        assert len(actual_result[1].subgroup_summary) == 3
        assert actual_result[1].subgroup_summary[0] is not None
        assert actual_result[1].subgroup_summary[0].name == "LP-Support"
        assert actual_result[1].subgroup_summary[0].booked_time == 13455000
        assert (
            "{:.2f}".format(
                actual_result[1].subgroup_summary[0].percentage_of_total_time
            )
            == "60.89"
        )
        assert len(actual_result[1].subgroup_summary[0].subgroup_summary) == 0

        assert actual_result[2].name == "Client-1"
        assert actual_result[2].booked_time == 17407000
        assert "{:.2f}".format(actual_result[2].percentage_of_total_time) == "19.08"
        assert len(actual_result[2].subgroup_summary) > 0

        # This is the catch-all "unknown project" item
        assert actual_result[3].name == "Unknown Client/Project"
        assert actual_result[3].booked_time == 12492000
        assert "{:.2f}".format(actual_result[3].percentage_of_total_time) == "13.69"

        assert actual_result[4].name == "Client-3"
        assert actual_result[4].booked_time == 9352000
        assert "{:.2f}".format(actual_result[4].percentage_of_total_time) == "10.25"
        assert len(actual_result[4].subgroup_summary) > 0
