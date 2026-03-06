import requests

class ConfluenceClient:
    def __init__(self, base_url, username=None, api_token=None, bearer_token=None):
        self.base_url = base_url
        self.username = username
        self.api_token = api_token
        self.bearer_token = bearer_token

    def get_headers(self):
        headers = {'Content-Type': 'application/json'}
        if self.bearer_token:
            headers['Authorization'] = f'Bearer {self.bearer_token}'
        else:
            if self.username and self.api_token:
                headers['Authorization'] = requests.auth._basic_auth_str(self.username, self.api_token)
        return headers

    def request(self, method, endpoint, **kwargs):
        headers = self.get_headers()
        url = f'{self.base_url}/{endpoint}'
        return requests.request(method, url, headers=headers, **kwargs)

    # Example of a request method
    def get_page(self, page_id):
        return self.request('GET', f'page/{page_id}')
