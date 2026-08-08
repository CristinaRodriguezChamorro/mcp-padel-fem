# 🎾 MCP Pádel Femenino

Proyecto personal para centralizar información del **pádel femenino profesional** y experimentar con **APIs, IA y Model Context Protocol (MCP)** sobre un caso de uso real.

La aplicación reúne **noticias, ranking, calendario de torneos y seguimiento del torneo en juego**, y expone parte de esta información mediante un servidor MCP.

## 🏗️ Arquitectura

```text
FIP · RSS · fuentes web
          │
          ▼
 Extracción de datos
 aiohttp · BeautifulSoup
 feedparser · pypdf
          │
          ▼
 Filtrado y normalización
          │
          ├──► Groq / LLM
          │    extracción de datos
          │    no estructurados
          ▼
      FastAPI
   ┌──────┴──────┐
   ▼             ▼
Frontend      MCP Server
HTML/CSS/JS   FastMCP
```

La arquitectura separa la **obtención de datos**, su **procesamiento** y la **forma de consumirlos**. El LLM se utiliza como capa de extracción/normalización cuando la fuente no ofrece datos estructurados; no sustituye a la fuente original.

## 🔴 En juego

El backend detecta el torneo Premier Padel activo y consulta información oficial de **FIP**.

Procesa **Results, Draws y Order of Play**, filtra el cuadro femenino (`female / women`), extrae resultados y los agrupa por ronda:

```text
SEMIFINALES

Triay / Brea 🏆
Josemaría / Sánchez
6–3  6–4
```

También obtiene el **siguiente partido femenino** y convierte su hora local a **hora de España (`Europe/Madrid`)**.

Los resultados se incorporan a medida que terminan los partidos, sin esperar a que finalice el torneo.

## 📰 Noticias

Las noticias se obtienen desde diferentes **feeds RSS** y pasan por un filtro específico de pádel femenino basado en términos y nombres de jugadoras.

Cuando es necesario, **Groq** ayuda a transformar contenido no estructurado en información homogénea para la API.

## 🔌 MCP

El proyecto incorpora **Model Context Protocol** mediante FastMCP.

Esto permite que la lógica del proyecto no esté limitada a la web: un cliente de IA compatible con MCP puede descubrir y utilizar las tools expuestas por el servidor.

```text
Cliente IA → MCP → Tools → datos de pádel femenino
```

## 🧰 Stack

| Tecnología | Uso |
|---|---|
| **Python + FastAPI** | Backend y API REST |
| **FastMCP / MCP** | Tools para clientes de IA |
| **aiohttp + asyncio** | Peticiones asíncronas |
| **BeautifulSoup + feedparser** | HTML y RSS |
| **pypdf** | Documentos oficiales FIP |
| **Groq / LLM** | Extracción y normalización |
| **HTML + CSS + JavaScript** | Frontend |
| **Docker + Railway** | Contenedorización y despliegue |

## 📁 Estructura

```text
apps/mcp-server/
├── server.py          # FastAPI y endpoints
├── tools/
│   ├── news.py        # Pipeline de noticias
│   └── live_data.py   # Torneo, resultados y próximo partido
├── static/
│   └── index.html     # Frontend
├── Dockerfile
└── requirements.txt
```

Endpoints principales:

```http
GET /api/news
GET /api/live?gender=women
GET /api/ranking
GET /api/tournaments
```

## 🎯 Objetivo técnico

El proyecto explora cómo combinar **arquitectura de APIs + fuentes externas + procesamiento asíncrono + LLMs + MCP** manteniendo cada responsabilidad separada.

La IA es una pieza de la arquitectura, no la arquitectura completa.
