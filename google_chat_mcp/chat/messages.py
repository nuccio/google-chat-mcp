class MessagesClient:
    def __init__(self, base):
        self._base = base

    def list(
        self,
        space_name: str,
        page_size: int = 25,
        filter_str: str | None = None,
        page_token: str | None = None,
    ) -> dict:
        """Returns one page of messages, newest pages first.

        Each page is in chronological order. `next_page_token` is None on the
        last page, otherwise it selects the next, older page.
        """
        # The API orders by createTime ASC by default, so pageSize alone would
        # return the oldest messages: fetch newest first, then restore
        # chronological order within the page.
        data = self._base._get(
            f"{space_name}/messages",
            pageSize=min(page_size, 1000),
            filter=filter_str,
            orderBy="createTime desc",
            pageToken=page_token,
        )
        return {
            "messages": list(reversed(data.get("messages", []))),
            "next_page_token": data.get("nextPageToken") or None,
        }

    def send(self, space_name: str, text: str) -> dict:
        return self._base._post(f"{space_name}/messages", {"text": text})
