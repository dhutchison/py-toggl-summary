import argparse
import logging
import os
import sys
from datetime import datetime, timedelta

from api import api_client
from reporter import detailed_time_reporter, summary_reporter

logger = logging.getLogger(__name__)


def configure_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    #     root.setLevel(logging.DEBUG)

    handler = logging.StreamHandler(sys.stdout)
    #     handler.setLevel(logging.DEBUG)
    #     formatter = logging.Formatter(
    #         "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    #     )
    # handler.setFormatter(formatter)
    root.addHandler(handler)


def parse_configuration():
    """
    Using argparse, setup the configuration parameters and parse the input.
    If an environment variable TOGGL_CONF is set, attempt to load configuration for
    the items: API_TOKEN, EMAIL and WORKSPACE_ID.
    """

    defaults = {}
    if "TOGGL_CONF" in os.environ:
        conf_file = os.environ["TOGGL_CONF"]

        with open(conf_file, "r", encoding="utf-8") as file:
            for line in file:
                name, var = line.strip().split("=")
                defaults[name.strip()] = var

    parser = argparse.ArgumentParser(
        prog="toggl-summary-cli",
        description="Utility for providing summary information from the Toggl API",
        epilog="If you didn't write this, it may not be for your use case!",
    )
    parser.add_argument(
        "--debug", help="output extra debugging", action="store_true", required=False
    )
    parser.add_argument(
        "--api-key",
        help="api token, found in Toggle profile settings",
        required=("API_TOKEN" not in defaults),
        default=defaults.get("API_TOKEN"),
        type=str,
    )
    parser.add_argument(
        "--email",
        help="your email address",
        required=("EMAIL" not in defaults),
        default=defaults.get("EMAIL"),
        type=str,
    )
    parser.add_argument(
        "--workspace-id",
        help="id of the Toggle workspace",
        required=("WORKSPACE_ID" not in defaults),
        default=defaults.get("WORKSPACE_ID"),
        type=str,
    )
    parser.add_argument(
        "-d",
        "--day",
        help="day to report on (in yyyy-MM-dd format). "
        "If a date is not supplied then this will default to today.",
        type=str,
        default=datetime.now().isoformat()[:10:],
    )
    parser.add_argument(
        "-w",
        "--week",
        help="If specified, interpret the day as the start of a week.",
        action="store_true",
    )
    parser.add_argument(
        "--include-summary",
        help="If specified, include client/project summary detail",
        action="store_true",
    )

    return parser.parse_args()


def generate_report(
    api_key: str,
    email: str,
    workspace_id: str,
    from_date: str,
    to_date: str,
    include_summary: bool,
):
    # Call the detailed report API

    try:
        detailed_items = api_client.get_detailed_report(
            api_key, email, workspace_id, from_date, to_date
        )

        # Iterate through the items to work out the time summary
        time_summary = detailed_time_reporter.calculate_time_totals(detailed_items)

        # Print out the output
        logger.info("# Totals for %s to %s\n", from_date, to_date)
        logger.info(
            "* Booked time: %s",
            detailed_time_reporter.format_millis(time_summary.booked_time),
        )
        logger.info(
            "* Unbooked time: %s",
            detailed_time_reporter.format_millis(time_summary.unbooked_time),
        )
        logger.info(
            "* Break time: %s",
            detailed_time_reporter.format_millis(time_summary.break_time),
        )
        logger.info(
            "* Total time (booked + unbooked): %s\n",
            detailed_time_reporter.format_millis(time_summary.time_count),
        )

        if include_summary:
            # Load the summary detail and process it
            summary_items = api_client.get_summary_report(
                api_key, email, workspace_id, from_date, to_date
            )

            logger.info("# Summary\n")

            try:
                client_summary = summary_reporter.calculate_summary_totals(
                    summary_items, time_summary
                )
                for item in client_summary:
                    logger.info(
                        "* %s: %02d%% (%s)",
                        item.name,
                        item.percentage_of_total_time,
                        detailed_time_reporter.format_millis(item.booked_time),
                    )
                    if item.subgroup_summary:
                        for sub_item in item.subgroup_summary:
                            logger.info(
                                "  * %s: %02d%% (%s)",
                                sub_item.name,
                                sub_item.percentage_of_total_time,
                                detailed_time_reporter.format_millis(
                                    sub_item.booked_time
                                ),
                            )
                    logger.info("\n")

            except Exception as error:
                logger.error("Failed to load summary API response: %s", error)
                logger.exception(error)

    except Exception as error:
        logger.error("Failed to load detailed API response: %s", error)
        logger.exception(error)


def main():
    # Configure logging
    configure_logging()

    # define the CLI arguments & parse the inputs
    args = parse_configuration()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.debug("Arguments: %s", args)

    if args.week:
        end_date = datetime.strptime(args.day.strip("'"), "%Y-%m-%d") + timedelta(
            days=6
        )
        end_date = end_date.isoformat()[:10:]
    else:
        end_date = args.day.strip("'")

    generate_report(
        args.api_key,
        args.email,
        args.workspace_id,
        args.day,
        end_date,
        args.include_summary,
    )


if __name__ == "__main__":
    main()
