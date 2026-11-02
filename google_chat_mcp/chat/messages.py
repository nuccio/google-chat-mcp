class MessagesClient:
    def __init__(self, base):
        self._base = base

    def list(
        self,
        space_name: str,
        page_size: int = 25,
        filter_str: str | None = None,
    ) -> list[dict]:
        """Returns the latest `page_size` messages, oldest first."""
        # The API orders by createTime ASC by default, so pageSize alone would
        # return the oldest messages: fetch newest first, then restore
        # chronological order.
        data = self._base._get(
            f"{space_name}/messages",
            pageSize=min(page_size, 1000),
            filter=filter_str,
            orderBy="createTime desc",
        )
        return list(reversed(data.get("messages", [])))

    def send(self, space_name: str, text: str) -> dict:
        return self._base._post(f"{space_name}/messages", {"text": text})
