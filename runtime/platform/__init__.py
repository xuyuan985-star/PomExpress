"""platform.windows：Windows Reality Layer（Sprint D）——runtime 不关心 Win32 脏细节。

业务代码禁止直接 ctypes.windll（架构规则 FORBIDDEN_RUNTIME_IMPORTS 已拦）；
本包是唯一授权面：privilege / window / capture / coords / recovery。
"""
