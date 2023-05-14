import json
import logging
from datetime import timedelta

import requests

logger = logging.getLogger(__name__)

from reporter import structures


def get_summary_report(
    api_key: ascii, email: ascii, workspace_id: ascii, from_date, to_date
):
    """
    Function for loading our summary report data from the API.
    :return: array of summary report items loaded from the API.
    """

    url = "https://api.track.toggl.com/reports/api/v2/summary"
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en-gb",
    }
    payload = {
        "page": 1,
        "user_agent": email.strip("'"),
        "workspace_id": workspace_id.strip("'"),
        "since": from_date.strip("'"),
        "until": to_date,
        "grouping": "clients",
        "subgrouping": "projects",
    }

    logger.debug("Summary Payload: %s", payload)
    response = requests.get(
        url, headers=headers, params=payload, auth=(api_key.strip("'"), "api_token")
    )

    response_obj = json.loads(response.text)

    logger.debug("Summary Response: %s", response_obj)

    return response_obj["data"]


def get_detailed_report(
    api_key: ascii, email: ascii, workspace_id: ascii, from_date, to_date, page: int = 1
):
    url = "https://api.track.toggl.com/reports/api/v2/details"
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en-gb",
    }
    payload = {
        "page": page,
        "user_agent": email.strip("'"),
        "workspace_id": workspace_id.strip("'"),
        "since": from_date.strip("'"),
        "until": to_date,
    }

    logger.debug("Detailed Payload: %s", payload)
    response = requests.get(
        url, headers=headers, params=payload, auth=(api_key.strip("'"), "api_token")
    )

    response_obj = json.loads(response.text)

    logger.debug("Detailed Response: %s", response_obj)

    # Print out the total time as reported from the API
    logger.debug(
        "Report page loaded %s. total booked time: %s",
        page,
        # TODO: HH:MM:SS format
        timedelta(milliseconds=response_obj["total_grand"]),
    )

    logger.debug(
        "Pagination details: total_count: %s, per_page: %s",
        response_obj["total_count"],
        response_obj["per_page"],
    )

    # Convert any objects into our class for future use
    ret_data = []
    for item in response_obj["data"]:
        ret_data.append(structures.SimplifiedDetailedReportItem(item))

    # If there are more pages, call the API again, otherwise return the data
    if (
        len(response_obj["data"]) > 0
        and len(response_obj["data"]) == response_obj["per_page"]
    ):
        return ret_data + get_detailed_report(
            api_key, email, workspace_id, from_date, to_date, page + 1
        )
    else:
        return ret_data
