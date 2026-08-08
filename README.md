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

La arquitectura separa la **obtención de datos**, su **procesamiento** y la **forma de consumirlos**. El LLM se utiliza para resumir o normalizar contenido no estructurado; las decisiones deterministas, como identificar jugadoras mediante el ranking femenino, se resuelven con datos y código.

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

El filtro de noticias **no depende de una lista de jugadoras hardcodeada**.

Primero se obtiene el **ranking femenino actualizado** y se construye una lista dinámica con los nombres completos de las jugadoras. Una noticia pasa el filtro si aparece al menos una jugadora del ranking **o** si contiene una señal explícita de contenido femenino como `women`, `female`, `woman`, `femenino`, `femenina` o `mujeres`. Después, Groq/Llama resume únicamente el contenido que ya ha superado ese filtro.

```text
Ranking femenino → nombres de jugadoras
                         │
RSS → filtro femenino ───┘
          │
          ▼
      Groq + Llama
  resumen / normalización
          │
          ▼
       /api/news
```

**Groq no decide si una noticia es femenina.** Se utiliza como API de inferencia para ejecutar Llama y resumir/estructurar contenido que ya ha pasado el filtro.

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
| **Groq API + Llama** | Inferencia LLM para resumen y normalización |
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
