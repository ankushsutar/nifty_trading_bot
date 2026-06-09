from abc import ABC, abstractmethod

class BaseSessionProvider(ABC):
    @abstractmethod
    def get_session(self, force_refresh: bool = False):
        """Retrieves or creates a session wrapper for the broker."""
        pass
