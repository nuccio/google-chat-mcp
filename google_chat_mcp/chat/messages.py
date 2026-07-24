class MessagesClient:
    def __init__(self, base):
        self._base = base

    def list(
        self,
        space_name: str,
        page_size: int = 25,
        filter_str: str | None = None,
    ) -> list[dict]:
        data = self._base._get(
            f"{space_name}/messages",
            pageSize=min(page_size, 1000),
            filter=filter_str,
        )
        return data.get("messages", [])

    def send(self, space_name: str, text: str) -> dict:
        return self._base._post(f"{space_name}/messages", {"text": text})
