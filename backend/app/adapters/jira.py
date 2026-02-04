class JiraAdapter:
    def __init__(self, base_url: str, email: str, api_token: str):
        self.base_url = base_url
        self.email = email
        self.api_token = api_token

    def create_test_cases(self, project_key: str, issue_type: str, test_cases):
        raise NotImplementedError("JIRA adapter not implemented yet")
