"""Windows 当前用户的 DPAPI 凭据保护；密钥不随程序或设置文件分发。"""

import base64
import ctypes
from ctypes import wintypes


class SecretError(Exception):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(raw, decrypt=False):
    try:
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        buf = ctypes.create_string_buffer(raw)
        source = _Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        result = _Blob()
        fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        fn.restype = wintypes.BOOL
        if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            raise SecretError("密码无法解密，请在当前 Windows 账号下重新填写 WebDAV 密码" if decrypt
                              else "Windows 无法安全保存密码，设置未保存")
        try:
            return ctypes.string_at(result.data, result.size)
        finally:
            kernel.LocalFree(result.data)
    except (AttributeError, OSError) as e:
        raise SecretError("此功能需要 Windows 的 DPAPI 凭据保护") from e


def protect(password):
    return base64.b64encode(_crypt(password.encode("utf-8"))).decode("ascii")


def unprotect(ciphertext):
    try:
        return _crypt(base64.b64decode(ciphertext, validate=True), decrypt=True).decode("utf-8")
    except (ValueError, UnicodeError) as e:
        raise SecretError("保存的密码已损坏，请重新填写 WebDAV 密码") from e
