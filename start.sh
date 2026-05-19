#!/bin/bash

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo "🎾 Arrancando Padel Fem MCP..."

if [ ! -f .env ]; then
  echo -e "${RED}❌ No encuentro el archivo .env${NC}"
  echo "👉 Crea un archivo .env con tu clave de Groq:"
  echo "   GROQ_API_KEY=gsk_aqui-tu-clave"
  exit 1
fi

if ! docker info > /dev/null 2>&1; then
  echo -e "${RED}❌ Docker no está arrancado. Abre Docker Desktop primero.${NC}"
  exit 1
fi

echo -e "${GREEN}✅ Todo listo, arrancando...${NC}"
docker compose up --build
