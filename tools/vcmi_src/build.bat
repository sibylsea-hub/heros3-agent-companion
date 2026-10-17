@echo off
rem Configure and build VCMI 1.7.5 plus the save reader. See README.md.
rem Usage: build.bat [configure]   (configure is needed only the first time)
rem Paths: VCMI_BUILD_ROOT, VS_ROOT and CONAN_HOME from the environment, else from
rem tools/h3tools/config.py (h3mod.local.json at the repository root, or its defaults).
setlocal
set "CONFIG_PY=%~dp0..\h3tools\config.py"
rem (no call :labels here: this file has LF line endings, which break label lookup in cmd)
if not defined VCMI_BUILD_ROOT for /f "usebackq delims=" %%i in (`python "%CONFIG_PY%" vcmi_build_root`) do set "VCMI_BUILD_ROOT=%%i"
if not defined VCMI_BUILD_ROOT exit /b 1
if not defined VS_ROOT for /f "usebackq delims=" %%i in (`python "%CONFIG_PY%" vs_root`) do set "VS_ROOT=%%i"
if not defined VS_ROOT exit /b 1
if not defined CONAN_HOME for /f "usebackq delims=" %%i in (`python "%CONFIG_PY%" conan_home`) do set "CONAN_HOME=%%i"
if not defined CONAN_HOME exit /b 1

call "%VS_ROOT%\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
call "%VCMI_BUILD_ROOT%\conan-generated\conanrun.bat" || exit /b 1
set PATH=%VS_ROOT%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin;%VS_ROOT%\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja;%PATH%

if "%1"=="configure" (
  cmake -S "%VCMI_BUILD_ROOT%\src" -B "%VCMI_BUILD_ROOT%\build" -G Ninja ^
    --toolchain "%VCMI_BUILD_ROOT%\conan-generated\conan_toolchain.cmake" ^
    -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_POLICY_DEFAULT_CMP0091=NEW ^
    -DCMAKE_INSTALL_PREFIX="%VCMI_BUILD_ROOT%\install" ^
    -DENABLE_LAUNCHER=OFF -DENABLE_EDITOR=OFF -DENABLE_TEST=OFF -DENABLE_TRANSLATIONS=OFF ^
    -DENABLE_MMAI=OFF -DENABLE_STRICT_COMPILATION=OFF ^
    -DENABLE_PCH=ON -DENABLE_MULTI_PROCESS_BUILDS=OFF ^
    -DCMAKE_PROJECT_VCMI_INCLUDE="%~dp0hook.cmake" || exit /b 1
)

cmake --build "%VCMI_BUILD_ROOT%\build" || exit /b 1
cmake --install "%VCMI_BUILD_ROOT%\build" || exit /b 1
