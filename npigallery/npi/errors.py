class NpiError(Exception):
    pass


class NpiFormatError(NpiError):
    pass


class NpiIntegrityError(NpiError):
    pass


class UnsupportedImageError(NpiError):
    pass
