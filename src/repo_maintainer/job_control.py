"""Cooperative task boundaries; this never selects or terminates a PID."""
import threading
from .models import SafetyError

class OperationStopped(SafetyError):
    pass

class JobControl:
    def __init__(self, report):
        self.cancel = threading.Event()
        self.report = report

    def check(self):
        if self.cancel.is_set():
            raise OperationStopped('Operator requested stop; original approvals and consumption remain unchanged')

    def phase(self, value):
        self.report({'phase': value})
        self.check()
