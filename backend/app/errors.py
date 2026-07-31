from fastapi import status


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


class AuthenticationError(ApiError):
    def __init__(self) -> None:
        super().__init__(status.HTTP_401_UNAUTHORIZED, "INVALID_CREDENTIALS", "Неверный логин или пароль")


class SessionExpiredError(ApiError):
    def __init__(self) -> None:
        super().__init__(status.HTTP_401_UNAUTHORIZED, "AUTH_EXPIRED", "Сессия истекла, войдите снова")


class CsrfError(ApiError):
    def __init__(self) -> None:
        super().__init__(status.HTTP_403_FORBIDDEN, "CSRF_INVALID", "Некорректный CSRF-токен")


class OriginError(ApiError):
    def __init__(self) -> None:
        super().__init__(status.HTTP_403_FORBIDDEN, "ORIGIN_FORBIDDEN", "Недопустимый источник запроса")


class ServiceUnavailableError(ApiError):
    def __init__(self, message: str = "Сервис временно недоступен") -> None:
        super().__init__(status.HTTP_503_SERVICE_UNAVAILABLE, "SERVICE_UNAVAILABLE", message)


class TechPortalNotConfiguredError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "TECHPORTAL_NOT_CONFIGURED",
            "Интеграция заявок ТехПортала не настроена",
        )


class TechPortalAuthError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_502_BAD_GATEWAY,
            "TECHPORTAL_AUTH_FAILED",
            "ТехПортал отклонил системный токен",
        )


class TechPortalCallError(ApiError):
    def __init__(self, message: str = "Не удалось выполнить запрос к ТехПорталу") -> None:
        super().__init__(status.HTTP_502_BAD_GATEWAY, "TECHPORTAL_CALL_FAILED", message)


class TechPortalResponseError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_502_BAD_GATEWAY,
            "TECHPORTAL_RESPONSE_INVALID",
            "ТехПортал вернул некорректный ответ",
        )


class TicketNotFoundError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_404_NOT_FOUND,
            "TICKET_NOT_FOUND",
            "Заявка не найдена среди назначенных пользователю",
        )


class EsbNotConfiguredError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "ESB_NOT_CONFIGURED",
            "Интеграция ESB не настроена",
        )


class EsbResponseError(ApiError):
    def __init__(self, message: str = "ESB вернул некорректный ответ") -> None:
        super().__init__(status.HTTP_502_BAD_GATEWAY, "ESB_RESPONSE_INVALID", message)


class EsbCallError(ApiError):
    def __init__(self, message: str = "ESB не выполнил операцию") -> None:
        super().__init__(status.HTTP_502_BAD_GATEWAY, "ESB_CALL_FAILED", message)


class FlatAlreadyAssignedError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_409_CONFLICT,
            "FLAT_ALREADY_ASSIGNED",
            "Квартира уже привязана к пользователю домофона",
        )


class MessengerLinkError(ApiError):
    def __init__(self, code: str, message: str, status_code: int = status.HTTP_409_CONFLICT) -> None:
        super().__init__(status_code, code, message)


class MessengerLinkTokenError(MessengerLinkError):
    def __init__(self) -> None:
        super().__init__("MESSENGER_LINK_TOKEN_INVALID", "Ссылка для привязки недействительна или истекла", status.HTTP_400_BAD_REQUEST)
