# 🎾 MCP Pádel Femenino Español

Web personal para seguir de forma sencilla el **pádel femenino profesional**, con especial foco en las jugadoras españolas.

## Qué incluye

- 📰 **Noticias** de pádel femenino.
- 🔴 **En juego**: torneo actual, partidos femeninos, resultados, rondas y próximo partido.
- 🏆 **Ranking** femenino Top 20.
- 📅 **Torneos** actuales y próximos.
- 👑 **Sponsors** y material de algunas de las principales jugadoras.
- 📱 Diseño adaptado a escritorio y móvil.

## Cómo funciona

La aplicación obtiene información de distintas fuentes públicas, principalmente **FIP / Premier Padel**, procesa los datos y los sirve mediante una API construida con **FastAPI**.

Para las noticias se utiliza IA como apoyo para generar resúmenes. Los resultados y marcadores de `En juego` se procesan de forma determinista a partir de los datos disponibles en FIP.

## Stack

- Python
- FastAPI
- FastMCP / MCP
- Playwright
- BeautifulSoup / feedparser
- HTML, CSS y JavaScript
- Groq + Llama para resúmenes de noticias
- Docker
- Railway

## Estructura básica

```text
apps/mcp-server/
├── server.py
├── tools/
│   ├── news.py
│   └── live_data.py
├── static/
│   └── index.html
├── Dockerfile
└── requirements.txt
```

## Endpoints principales

```text
GET /api/news
GET /api/live?gender=women
GET /api/ranking
GET /api/tournaments
```

## Ejecutar el proyecto

Instala las dependencias:

```bash
pip install -r apps/mcp-server/requirements.txt
playwright install chromium
```

Arranca el servidor:

```bash
uvicorn apps.mcp-server.server:app --host 0.0.0.0 --port 8080
```

> En despliegue se recomienda utilizar el `Dockerfile` incluido en el proyecto.

## Objetivo

El proyecto sirve como caso práctico para combinar **APIs, scraping/extracción web, procesamiento asíncrono, IA y Model Context Protocol (MCP)** en una aplicación real.
