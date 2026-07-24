class SpacesClient:
    def __init__(self, base):
        self._base = base

    def list(self, page_size: int = 100) -> list[dict]:
        spaces: list[dict] = []
        page_token: str | None = None
        while True:
            data = self._base._get("spaces", pageSize=page_size, pageToken=page_token)
            spaces.extend(data.get("spaces", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return spaces

    def get(self, space_name: str) -> dict:
        return self._base._get(space_name)
