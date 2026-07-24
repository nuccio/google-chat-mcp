class MembersClient:
    def __init__(self, base):
        self._base = base

    def list(self, space_name: str) -> list[dict]:
        data = self._base._get(f"{space_name}/members")
        return data.get("memberships", [])
