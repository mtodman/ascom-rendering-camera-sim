"""ASCOM Alpaca standard error numbers and the exception types used to
signal them from device endpoint handlers. See the Alpaca API reference,
"Error Codes and Numbers".
"""


class AlpacaError(Exception):
    error_number: int = 0x500  # DriverException base, subclasses override

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotImplementedException(AlpacaError):
    error_number = 0x400


class InvalidValueException(AlpacaError):
    error_number = 0x401


class ValueNotSetException(AlpacaError):
    error_number = 0x402


class NotConnectedException(AlpacaError):
    error_number = 0x407


class InvalidOperationException(AlpacaError):
    error_number = 0x40B


class ActionNotImplementedException(AlpacaError):
    error_number = 0x40C


class OperationCancelledException(AlpacaError):
    error_number = 0x40D


class DriverException(AlpacaError):
    error_number = 0x500
