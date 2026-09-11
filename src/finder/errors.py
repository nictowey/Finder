"""Safe, actionable errors; never include credentials or response bodies."""


class FinderError(Exception):
    pass


class ConfigurationError(FinderError):
    pass


class MarketplaceError(FinderError):
    pass


class AuthenticationError(MarketplaceError):
    pass


class RateLimitError(MarketplaceError):
    pass


class RequestError(MarketplaceError):
    pass


class ResponseError(MarketplaceError):
    pass


class ItemUnavailableError(MarketplaceError):
    pass


class InvalidListingError(FinderError):
    pass


class PersistenceError(FinderError):
    pass


class CatalogError(FinderError):
    pass


class CatalogAuthenticationError(CatalogError):
    pass


class CatalogRateLimitError(CatalogError):
    pass


class CatalogRequestError(CatalogError):
    pass


class CatalogResponseError(CatalogError):
    pass
