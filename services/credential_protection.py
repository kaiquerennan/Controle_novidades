from __future__ import annotations

import base64
import ctypes
import os


class CredentialProtectionError(RuntimeError):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [
        ('cbData', ctypes.c_uint),
        ('pbData', ctypes.POINTER(ctypes.c_byte)),
    ]


class WindowsMachineCredentialProtector:
    CRYPTPROTECT_LOCAL_MACHINE = 0x4

    def protect(self, value: str) -> str:
        if os.name != 'nt':
            raise CredentialProtectionError('Protecao DPAPI esta disponivel apenas no Windows.')
        data = value.encode('utf-8')
        input_blob, _ = self._blob_from_bytes(data)
        output_blob = _Blob()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(input_blob), None, None, None, None,
            self.CRYPTPROTECT_LOCAL_MACHINE, ctypes.byref(output_blob),
        )
        if not ok:
            raise CredentialProtectionError('Nao foi possivel proteger o token.')
        try:
            protected = ctypes.string_at(output_blob.pbData, output_blob.cbData)
            return base64.b64encode(protected).decode('ascii')
        finally:
            ctypes.windll.kernel32.LocalFree(output_blob.pbData)

    def unprotect(self, value: str) -> str:
        if os.name != 'nt':
            raise CredentialProtectionError('Protecao DPAPI esta disponivel apenas no Windows.')
        protected = base64.b64decode(value.encode('ascii'), validate=True)
        input_blob, _ = self._blob_from_bytes(protected)
        output_blob = _Blob()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(input_blob), None, None, None, None, 0,
            ctypes.byref(output_blob),
        )
        if not ok:
            raise CredentialProtectionError('Nao foi possivel recuperar o token.')
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode('utf-8')
        finally:
            ctypes.windll.kernel32.LocalFree(output_blob.pbData)

    @staticmethod
    def _blob_from_bytes(data: bytes) -> tuple[_Blob, ctypes.Array]:
        buffer = ctypes.create_string_buffer(data)
        blob = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        return blob, buffer
