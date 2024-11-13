import base64
import json
import os
import re
from getpass import getpass
from pprint import pprint
from typing import Optional, TypedDict, Union
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from rich.prompt import Prompt
from rich.status import Status

BASE_URL = "https://www.gradescope.com"
CSRF_TOKEN_HEADER = "X-Csrf-Token"


class GradeTableRow(TypedDict):
    name: str
    email: str
    score: Optional[float]
    submission: Optional[str]


class GradescopeAPI:
    """
    Gradescope API wrapper.
    """

    def __init__(self, cookie_file=None):
        # load environment variables
        load_dotenv()

        self.cookie_file = cookie_file
        # initialize requests session
        self.session = requests.Session()

        # login user; this will populate self.sesion with the correct cookies.
        self.login(
            email=os.environ.get("GRADESCOPE_EMAIL", None),
            password=os.environ.get("GRADESCOPE_PASSWORD", None),
        )

    def login(self, email: str, password: str):
        """
        Logs in a user with the given email and password.

        For ease and speed, this method uses the requests library for the login request,
        and transfers the resulting cookies to the selenium webdriver.
        This allows for the webdriver to be used in future actions,
        without needing to login through the frontend form.
        """
        login_url = urljoin(BASE_URL, "/login")

        if self.cookie_file is not None and os.path.isfile(self.cookie_file):
            status = Status(f"Restoring cookies from [green]{self.cookie_file}[/green]")
            status.start()

            # load cookies
            with open(self.cookie_file, "r", encoding="utf-8") as in_file:
                cookies = json.load(in_file)

            # ensure that the user is actually logged in
            status.update("Ensuring user is logged in")
            self.session.cookies.update(cookies)

            response = self.session.get(login_url, timeout=20)
            status.stop()
            try:
                json_response = json.loads(response.content)
                # should give {"warning":"You must be logged out to access this page."}
                if (
                    json_response["warning"]
                    == "You must be logged out to access this page."
                ):
                    # all good to go
                    return True
            except json.JSONDecodeError:
                # invalid json, so use html
                pass

            soup = BeautifulSoup(response.content, "html.parser")
            login_btn = soup.find("input", {"value": "Log In", "type": "submit"})

            if login_btn is None:
                # form does not show, so stop and return
                return True

        if email is None:
            # ask for email
            email = Prompt.ask("Gradescope email")
        if password is None:
            # ask for password, hiding input
            password = getpass("Gradescope password: ")

        status = Status("Logging in")
        status.start()

        # visit login page
        response = self.session.get(login_url, timeout=20)

        soup = BeautifulSoup(response.content, "html.parser")

        # get authenticity token from form
        form = soup.find("form")
        token_input = form.find("input", {"name": "authenticity_token"})
        token = token_input.get("value")

        # prepare payload and headers
        payload = {
            "utf8": "✓",
            "authenticity_token": token,
            "session[email]": email,
            "session[password]": password,
            "session[remember_me]": 1,
            "commit": "Log In",
            "session[remember_me_sso]": 0,
        }
        headers = {
            "Host": "www.gradescope.com",
            "Origin": "https://www.gradescope.com",
            "Referer": login_url,
        }
        # login
        response = self.session.post(
            login_url, data=payload, headers=headers, timeout=20
        )
        if not response.ok:
            raise RuntimeError(
                f"Failed to log in; (status {response.status_code})\nReponse: {response.content}"
            )
        # also check content
        page = BeautifulSoup(response.content, "html.parser")
        spans = page.select(".alert-error span")
        if any("Invalid email/password combination" in span.text for span in spans):
            raise RuntimeError("Failed to log in; invalid email/password combination.")

        if self.cookie_file is not None:
            # save cookies as json
            with open(self.cookie_file, "w", encoding="utf-8") as out_file:
                json.dump(self.session.cookies.get_dict(), out_file)

        status.stop()
        return True

    def fetch_submission_page_data(
        self, course_id: int, assignment_id: int
    ) -> tuple[dict, tuple[str, str]]:
        """
        Fetch roster data from an assignment submission page,
        along with the authenticity token (CSRF token) embedded in the page.

        Returns: tuple of
            - roster data as a dict
            - tuple of (csrf_field, csrf_token) for the csrf token on the page
        """
        submissions_url = urljoin(
            BASE_URL, f"/courses/{course_id}/assignments/{assignment_id}/submissions"
        )
        response = self.session.get(submissions_url)
        if not response.ok:
            raise RuntimeError(
                f"Failed to fetch assignment page; (status {response.status_code})\nResponse: {response.content}"
            )

        # parse page content
        page = BeautifulSoup(response.content, "html.parser")

        # find the script tag with the roster data
        scripts = page.select("script")
        data_tag = None
        for script_tag in scripts:
            if re.search(r"<!\[CDATA\[", script_tag.text):
                # script tag matches; exit loop
                data_tag = script_tag
                break
        if data_tag is None:
            raise RuntimeError("Failed to find roster data!")

        # match and parse roster data
        roster_data_match = re.search(r"gon\.roster=(.*?);", data_tag.text)
        roster_data = roster_data_match.group(1)

        # get the csrf token
        csrf_token_meta = page.find("meta", {"name": "csrf-token"})
        csrf_field_meta = page.find("meta", {"name": "csrf-param"})
        assert csrf_token_meta is not None, "<meta> tag for csrf token not found"
        assert csrf_field_meta is not None, "<meta> tag for csrf parameter not found"
        csrf_token = csrf_token_meta["content"]
        csrf_field = csrf_field_meta["content"]

        return json.loads(roster_data), (csrf_field, csrf_token)

    def fetch_grades_data(
        self, course_id: int, assignment_id: int
    ) -> list[GradeTableRow]:
        """
        Fetch grade data on the "review grades" page.
        """
        grades_url = urljoin(
            BASE_URL, f"courses/{course_id}/assignments/{assignment_id}/review_grades"
        )
        response = self.session.get(grades_url)

        page = BeautifulSoup(response.content, "html.parser")

        grades_table = page.select_one("table.js-reviewGradesTable")
        assert grades_table is not None, "Grade table not found"

        # get the header to see which indices we need to look at
        table_header = grades_table.select("thead th")

        name_idx = -1
        email_idx = -1
        score_idx = -1
        # overwrite index values
        for col_idx, header_item in enumerate(table_header):
            if "name" in header_item.text.lower():
                name_idx = col_idx
            elif "email" in header_item.text.lower():
                email_idx = col_idx
            elif "score" in header_item.text.lower():
                score_idx = col_idx

        if name_idx < 0 or email_idx < 0 or score_idx < 0:
            raise RuntimeError(
                "Unable to find one of name, email, or score columns in the grades table"
            )

        # iterate through each row in the table, extracting the necessary information
        table_data = []
        for table_row in grades_table.select("tbody tr"):
            row_elements = table_row.select("td")

            name = row_elements[name_idx].text
            email = row_elements[email_idx].text
            score = row_elements[score_idx].text

            submission_link_tag = row_elements[name_idx].select_one("a")

            if submission_link_tag is None:
                # no submission for the student
                table_data.append(
                    {"name": name, "email": email, "score": None, "submission": None}
                )
            else:
                submission_link = submission_link_tag.get("href")

                table_data.append(
                    {
                        "name": name,
                        "email": email,
                        "score": float(score),
                        "submission": submission_link,
                    }
                )

        return table_data

    def fetch_autograder_submission_status(
        self, course_id: int, assignment_id: int, submission_id: int
    ):
        submission_url = urljoin(
            BASE_URL,
            f"courses/{course_id}/assignments/{assignment_id}/submissions/{submission_id}",
        )
        response = self.session.get(submission_url)

        page = BeautifulSoup(response.content, "html.parser")

        # get the csrf token
        csrf_token_meta = page.find("meta", {"name": "csrf-token"})
        csrf_field_meta = page.find("meta", {"name": "csrf-param"})
        assert csrf_token_meta is not None, "<meta> tag for csrf token not found"
        assert csrf_field_meta is not None, "<meta> tag for csrf parameter not found"
        csrf_token = csrf_token_meta.get("content")
        csrf_field = csrf_field_meta.get("content")

        submission_viewer = page.select_one(
            'div[data-react-class="AssignmentSubmissionViewer"]'
        )
        assert submission_viewer is not None, "Cannot find submission viewer"
        props_str = submission_viewer.get("data-react-props")
        assert (
            props_str is not None
        ), "Submission viewer component doesn't have data-react-props attr"
        props_json = json.loads(props_str)

        submission_metadata = props_json["assignment_submission"]
        autograder_results = props_json["autograder_results"]

        return {
            "metadata": submission_metadata,
            "autograder_results": autograder_results,
            "csrf": (csrf_field, csrf_token),
        }

    def autograder_regrade_submission(
        self, course_id: int, assignment_id: int, submission_id: int, csrf_token: str
    ) -> None:
        regrade_url = urljoin(
            BASE_URL,
            f"courses/{course_id}/assignments/{assignment_id}/submissions/{submission_id}/regrade",
        )
        response = self.session.post(
            regrade_url, headers={CSRF_TOKEN_HEADER: csrf_token}
        )

        if not response.ok:
            raise RuntimeError(
                f"Bad response when regrading submission id {submission_id} (course {course_id}, assignment {assignment_id})"
            )

    def upload(
        self,
        course_id: Union[str, int],
        assignment_id: Union[str, int],
        user_id: Union[str, int],
        file_content: str = "",
        filename: str = "upload.txt",
        csrf_data: Optional[tuple[str, str]] = None,
    ):
        """
        Upload a file for a user in a given course and assignment.
        """
        submissions_url = urljoin(
            BASE_URL, f"/courses/{course_id}/assignments/{assignment_id}/submissions"
        )

        if csrf_data is None:
            _, csrf_data = self.fetch_submission_page_data(
                int(course_id), int(assignment_id)
            )

        csrf_field, csrf_token = csrf_data

        files = {"submission[files][]": (filename, file_content)}
        data = {
            csrf_field: csrf_token,
            "submission[owner_id]": user_id,
            "submission[method]": "upload",
        }

        response = self.session.post(submissions_url, data=data, files=files)
        if not response.ok:
            raise RuntimeError(
                f"Failed to upload file; (status {response.status_code})\n"
                f"Response: {response.content}"
            )
        return True
