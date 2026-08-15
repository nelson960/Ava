from __future__ import annotations

from dataclasses import dataclass

from .protocol import ErrorCode


@dataclass(slots=True)
class AvaBackendError(Exception):
    code: ErrorCode
    public_message: str
    status_code: int


class ProviderRefusalError(AvaBackendError):
    def __init__(self) -> None:
        super().__init__(
            code=ErrorCode.PROVIDER_REFUSAL,
            public_message="Ava could not answer that request safely.",
            status_code=422,
        )


class ProviderTimeoutError(AvaBackendError):
    def __init__(self) -> None:
        super().__init__(
            code=ErrorCode.PROVIDER_TIMEOUT,
            public_message="Ava's response provider timed out.",
            status_code=504,
        )


class ProviderUnavailableError(AvaBackendError):
    def __init__(self) -> None:
        super().__init__(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            public_message="Ava's response provider is temporarily unavailable.",
            status_code=503,
        )


class ProviderResponseError(AvaBackendError):
    def __init__(self) -> None:
        super().__init__(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            public_message="Ava's response provider returned an invalid response.",
            status_code=502,
        )


class SpeechProviderTimeoutError(AvaBackendError):
    def __init__(self) -> None:
        super().__init__(
            code=ErrorCode.PROVIDER_TIMEOUT,
            public_message="Ava's speech provider timed out.",
            status_code=504,
        )


class SpeechProviderUnavailableError(AvaBackendError):
    def __init__(self) -> None:
        super().__init__(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            public_message="Ava's speech provider is temporarily unavailable.",
            status_code=503,
        )


class SpeechProviderResponseError(AvaBackendError):
    def __init__(self) -> None:
        super().__init__(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            public_message="Ava's speech provider returned invalid audio.",
            status_code=502,
        )
