"""Public authentication service."""

from .common import DomainError
from .identity import IdentityService


class AuthService(IdentityService):
    """Add password policy to the credential storage implementation."""

    def register(self, username, password, email=""):
        """Require a nontrivial password before deriving the stored hash."""
        if not isinstance(password, str) or not 8 <= len(password) <= 1024:
            raise DomainError("密码长度必须为 8 至 1024 个字符")
        return super().register(username, password, email)
