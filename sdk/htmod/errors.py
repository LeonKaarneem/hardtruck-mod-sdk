class PatchError(Exception):
    pass


class ManifestError(PatchError):
    pass


class PEError(PatchError):
    pass


class SignatureError(PatchError):
    pass


class StateError(PatchError):
    pass


class TransactionError(PatchError):
    pass
