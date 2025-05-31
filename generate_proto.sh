#!/bin/bash

set -e

PROTO_DIR="uc-protos/proto"
PROTO_FILE="${PROTO_DIR}/adapter_interface.proto"
OUT_DIR="."

echo "Generowanie plików z ${PROTO_FILE}..."

python -m grpc_tools.protoc -I="${PROTO_DIR}" \
  --python_out="${OUT_DIR}" \
  --grpc_python_out="${OUT_DIR}" \
  "${PROTO_FILE}"

if [ $? -ne 0 ]; then
  echo "❌ Błąd podczas generowania plików. Sprawdź, czy masz zainstalowany grpcio-tools: pip install grpcio-tools"
  exit 1
fi

echo "✅ Wygenerowano pliki Python do katalogu ${OUT_DIR}."
