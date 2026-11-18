class BusinessError(Exception):
    def __init__(self, code, message, http_status=422, **details):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.details = details

    def payload(self):
        return {'code': self.code, 'message': str(self), 'details': self.details}
