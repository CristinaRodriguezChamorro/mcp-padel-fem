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

La arquitectura separa la **obtención de datos**, su **procesamiento** y la **forma de consumirlos**. El LLM se utiliza únicamente donde aporta valor editorial, como el resumen de noticias. **En juego no depende de un LLM**: FIP se procesa con Playwright/HTTP y parsers Python deterministas.

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

**Groq no decide si una noticia es femenina.** Se utiliza como API de inferencia para ejecutar Llama y generar un **titular-resumen** y una **síntesis redactada** de cada noticia que ya ha pasado el filtro. No se publican fragmentos RSS recortados como si fueran resúmenes.

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
| **Playwright + Chromium** | Interacción con resultados FIP renderizados por JavaScript |
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


### Objetivo de Noticias
Fecha real de publicación + titular-resumen + resumen redactado de 2–3 frases. Nunca se muestra un fragmento RSS truncado como resumen.

### Objetivo de En juego
Torneo activo + dónde verlo + próximo partido femenino en hora española + resultados femeninos finalizados agrupados por ronda.


## Contrato funcional

**Noticias**
- filtro por jugadoras del ranking femenino o términos explícitos `female / women / femenino`;
- fecha original de publicación visible en cada tarjeta;
- titular-resumen;
- resumen redactado de 2–3 frases;
- nunca extractos RSS truncados.

**En juego**
- torneo femenino activo;
- ciudad, fechas y dónde verlo;
- próximo partido femenino convertido a hora de España;
- resultados ya finalizados sin esperar al final del torneo;
- agrupados por ronda;
- ganadoras, perdedoras y marcador.


### En juego no usa Groq
La sección de resultados es determinista:

`FIP → Playwright/HTTP → parser Python → /api/live → frontend`

Los marcadores, rondas y próximo partido no dependen de cuotas ni disponibilidad de un modelo de lenguaje.


### Railway / Playwright
Chromium se instala en `/ms-playwright` mediante `PLAYWRIGHT_BROWSERS_PATH`, de forma que el usuario no-root que ejecuta la aplicación puede encontrar el navegador.

### Resiliencia de Noticias
Groq sigue generando el titular-resumen y la síntesis cuando está disponible. Si devuelve `429`, Noticias no queda vacía: se usa un fallback determinista basado únicamente en artículos cuyo titular ya es inequívocamente femenino y en frases completas del contenido filtrado.


### Noticias cuando Groq está limitado
Si Groq devuelve `429`, la web mantiene varias noticias usando un fallback determinista sobre el contenido femenino ya filtrado. Se aceptan titulares con términos femeninos, nombres completos o combinaciones de al menos dos apellidos del ranking; los titulares explícitamente masculinos se rechazan salvo que el cuerpo contenga una sección femenina clara.


### Groq es opcional
Groq se usa únicamente como mejora editorial de Noticias. Si devuelve `429`:
- se abre un circuit breaker de 30 minutos;
- no se hacen más llamadas durante ese periodo;
- Noticias continúa con fallback determinista;
- resúmenes ya generados se reutilizan desde caché durante 24 horas.

`En juego`, Ranking y Calendario no dependen de Groq.


### Número de noticias
No existe un límite fijo de 4 noticias. Se muestran todas las noticias que superan el filtro de pádel femenino, ordenadas de más reciente a más antigua.
