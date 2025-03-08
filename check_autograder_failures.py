import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from api.client import GradescopeAPI

GRADESCOPE_BASEURL = "https://www.gradescope.com"
AUTOGRADER_WAIT_SECONDS = 60

CONSOLE = Console(highlight=False)


def validate_and_fix_submission(
    api: GradescopeAPI,
    course_id: int,
    assignment_id: int,
    submission_id: int,
    # for logging purposes
    name: str,
    email: str,
    # options
    dry_run=False,
    verbose=False,
) -> bool:
    """
    Fetch a submission's status, and if the autograder failed,
    submit a POST request to regrade it.

    Returns whether the submission validated.
      - If False, either a request was sent to regrade the submission, or the submission was not finished processing.
      - If True, no further actions were taken, and the submission is validated.
    """
    autograder_output = api.fetch_autograder_submission_status(
        course_id, assignment_id, submission_id
    )

    metadata = autograder_output["metadata"]

    if metadata["status"] == "processed":
        if verbose:
            CONSOLE.print(f"[green]processed: {name} ({email})[/green]")
        return True

    if metadata["status"] == "failed":
        CONSOLE.print(f"[red]failed: {name} ({email})[/red]")
    else:
        # other status (likely still regrading, but we should've waited long enough);
        # be conservative and still regrade
        CONSOLE.print(f"[violet]{metadata['status']}: {name} ({email})[/violet]")

    if dry_run:
        CONSOLE.print(
            "\t[italic red]Dry run: Submission would be regraded[/italic red]"
        )
    else:
        CONSOLE.print("\t[italic red]Regrading submission...[/italic red]")

        (_, csrf_token) = autograder_output["csrf"]

        api.autograder_regrade_submission(
            course_id, assignment_id, submission_id, csrf_token
        )

    return False


def main(
    course_id: int,
    assignment_id: int,
    cookie_file="cookies.json",
    only_check_zero=False,
    max_workers=8,
    dry_run=False,
    verbose=False,
):
    if dry_run:
        CONSOLE.print("[green]DRY RUN - NO REGRADES WILL BE SENT[/green]")
    api = GradescopeAPI(cookie_file=cookie_file)

    grade_data = api.fetch_grades_data(course_id, assignment_id)

    if only_check_zero:
        # filter only for submissions that got 0's
        filtered_rows = [
            row for row in grade_data if row["score"] is not None and row["score"] == 0
        ]
    else:
        # don't do any filtering
        filtered_rows = grade_data

    submissions_to_validate = []

    i = 0
    for table_row in filtered_rows:
        i += 1
        if table_row["submission"] is None:
            # no submission associated
            continue

        match = re.search(
            r"courses/(?P<course_id>\d+)/assignments/(?P<assignment_id>\d+)/submissions/(?P<submission_id>\d+)",
            table_row["submission"],
        )
        assert match is not None, "submission URL is not of the expected format"
        submission_id = match.group("submission_id")
        assert submission_id is not None, "Failed to extract submission id from URL"
        submission_id = int(submission_id)

        submissions_to_validate.append(
            (submission_id, table_row["name"], table_row["email"])
        )

    while len(submissions_to_validate) > 0:
        next_submissions_to_validate = []
        with Progress(
            # columns
            "[progress.description]{task.description}",
            BarColumn(),
            MofNCompleteColumn(),
            "|",
            TimeRemainingColumn(),
            # options
            transient=True,
            console=CONSOLE,
        ) as progress, ProcessPoolExecutor(max_workers) as pool:
            future_map = {}
            futures = []
            for submission_data in submissions_to_validate:
                (submission_id, name, email) = submission_data
                future = pool.submit(
                    validate_and_fix_submission,
                    api,
                    course_id,
                    assignment_id,
                    submission_id,
                    name,
                    email,
                    dry_run=dry_run,
                    verbose=verbose,
                )
                futures.append(future)

                # save data for reference upon completion
                future_map[future] = submission_data

            validation_task = progress.add_task(
                "Validating submissions...", total=len(submissions_to_validate)
            )

            for future in as_completed(futures):
                progress.advance(validation_task)
                validated = future.result(0)

                if not validated:
                    submission_data = future_map[future]
                    next_submissions_to_validate.append(submission_data)

        num_failed = len(next_submissions_to_validate)
        num_to_validate = len(submissions_to_validate)
        if num_failed > 0:
            CONSOLE.print(
                f"[red]{num_failed}/{num_to_validate}[/red] [blue]submissions failed.[/blue]"
            )
        else:
            CONSOLE.print(
                f"[green]All {num_to_validate} submissions succeeded![/green]"
            )

        submissions_to_validate = next_submissions_to_validate
        if len(submissions_to_validate) > 0 and not dry_run:
            with Progress(
                # columns
                "[progress.description]{task.description}",
                BarColumn(),
                TimeElapsedColumn(),
                # options
                console=CONSOLE,
            ) as progress:
                timer_task = progress.add_task(
                    "[yellow]Waiting 1 minute for submissions to regrade...[/yellow]",
                    total=AUTOGRADER_WAIT_SECONDS,
                )
                for _ in range(AUTOGRADER_WAIT_SECONDS):
                    time.sleep(1)
                    progress.advance(timer_task)

        if dry_run:
            # don't loop if we're doing a dry run
            break


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument("course_id", type=int, help="Gradescope course id")
    parser.add_argument("assignment_id", type=int, help="Gradescope assignment id")

    parser.add_argument(
        "--cookies", default="cookies.json", help="Filename for the cookie cache"
    )

    parser.add_argument(
        "--parallel",
        type=int,
        default=8,
        help="Number of processes to use when sending requests",
    )

    parser.add_argument(
        "--only-zero",
        action="store_true",
        help="Whether to only check submissions that got a score of zero",
    )

    parser.add_argument(
        "--dry-run",
        "-n",
        help="Dry run; does not submit any requests to regrade submissions, and only prints out the submissions that would be regraded",
        action="store_true",
    )

    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()
    main(
        args.course_id,
        args.assignment_id,
        only_check_zero=args.only_zero,
        max_workers=args.parallel,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )
