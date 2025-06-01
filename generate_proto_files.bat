@echo off

set PROTO_DIR=uc-protos\proto

set PROTO_FILE=%PROTO_DIR%\adapter_interface.proto

set OUT_DIR=.

echo Generowanie plików z %PROTO_FILE%...
python -m grpc_tools.protoc -I=%PROTO_DIR% --python_out=%OUT_DIR% --grpc_python_out=%OUT_DIR% %PROTO_FILE%

IF %ERRORLEVEL% NEQ 0 (
    echo Błąd podczas generowania plików. Sprawdź, czy masz zainstalowany grpcio-tools: pip install grpcio-tools
    exit /b %ERRORLEVEL%
)

echo Wygenerowano pliki Python do katalogu %OUT_DIR%.
