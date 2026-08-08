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


### Parser de resultados FIP
FIP renderiza nombres y juegos en celdas separadas, no como `6-3 6-4`.
La extracción de `En juego` captura cada bloque de partido alrededor del símbolo de ganador `✓`, identifica las cuatro jugadoras mediante el ranking femenino y reconstruye el marcador set a set sin utilizar un LLM.


### Corrección v26
El fallback determinista de Noticias vuelve a incluir `_sentencias_completas`, que había desaparecido en una refactorización y provocaba `NameError` + HTTP 500. Además `/api/news` tiene protección final para que un fallo de procesamiento no tumbe la pestaña.


### Corrección v27 de En juego
El parser ya no depende de encontrar un marcador `6-3` ni de localizar el símbolo `✓` como nodo HTML. Recorre el texto realmente renderizado por FIP, identifica las jugadoras mediante el ranking femenino y reconstruye cada set a partir de las celdas numéricas situadas entre parejas.


### Corrección v28 de Female en FIP
Los logs mostraron `female=False` y `female_hits=0`: el navegador funcionaba, pero el control visual `Female` no se estaba activando. La extracción ahora prueba el control por texto, labels, inputs vecinos y radios/inputs reales. Cada intento se valida contra nombres del ranking femenino; si no aparecen al menos dos jugadoras, la vista no se considera femenina y no se parsean datos masculinos por error.


### Corrección v29 de En juego
Los logs demostraron que el selector visual `Female` de FIP no cambia de forma fiable en Chromium (`female=False`). La extracción ya no depende de ese click: inspecciona respuestas XHR/fetch y contenido DOM oculto/pre-cargado, y solo acepta una fuente si contiene varias jugadoras del ranking femenino. Después reconstruye los resultados con Python, sin Groq.


### Corrección v30 de resultados
Los logs de v29 mostraron 103 coincidencias de jugadoras pero 0 partidos parseados. Eso demuestra que la información sí está cargada, pero el orden lineal del texto no representa cada partido. v30 deja de parsear texto lineal: Playwright localiza directamente el contenedor DOM más pequeño que contiene 4 jugadoras del ranking y 4–8 celdas de marcador, y entrega esos bloques estructurados al backend.


### Contrato visual exacto de En juego (v31)
Cada resultado finalizado se normaliza antes de llegar al frontend:
`ronda → ganadoras → perdedoras → marcador`.

Visualmente:
- nombre del torneo + ciudad + fechas;
- dónde verlo;
- tarjeta morada de Próximo partido con ronda, parejas y hora española;
- una única cabecera por ronda;
- debajo, cada partido con `Ganadoras 🏆`, perdedoras y marcador;
- marcador con `–` y desde la perspectiva de la pareja ganadora;
- estado `Finalizado` y fecha cuando FIP la proporciona.


### Estrategia v32 de En juego
Se elimina la dependencia del selector Female. Playwright captura todos los bloques de resultados visibles para cada día; después el backend filtra cada partido contra el ranking femenino dinámico. Solo se conserva un bloque si contiene exactamente cuatro jugadoras del ranking. A partir de ese bloque se determina la pareja ganadora, la perdedora y el marcador.


### v34 — En juego únicamente
Se restauran los dos normalizadores requeridos por `/api/live` y se añade validación de despliegue para evitar otro `NameError`. La captura recorre todos los estados de controles de género, organiza todos los bloques y filtra mujeres solo después.


### v35 — En juego únicamente
Corrección importante de refactorización: el parser de resultados se sustituye sin borrar los normalizadores usados por `/api/live`. Los partidos finalizados se localizan desde `✓`, se prueban todos los elementos clicables alrededor de `Female`, y el filtro femenino se aplica después usando el ranking.


### v36 — En juego únicamente
La pestaña recorre todos los días disponibles del torneo y acumula todos los partidos femeninos finalizados desde el inicio hasta hoy. Cada resultado conserva fecha, ronda, ganadoras, perdedoras y marcador; los partidos se deduplican antes de enviarse al frontend.


### v37 — rediseño visual de En juego
Solo se modifica la presentación de `En juego`: tarjeta principal con acento rosa/morado, bloque de próximo partido, rondas diferenciadas y marcadores resaltados en violeta. El backend v36 y las demás pestañas permanecen intactos.


### v38 — diseño exacto de En juego
Solo se modifica la presentación de `En juego` para acercarla literalmente al mockup compartido: misma jerarquía, misma paleta oscura con acentos violeta/rosa, tarjeta principal, bloque de próximo partido y tarjetas de resultados compactas.


### v39 — En juego: cuadro completo
Solo afecta a `En juego`. El extractor deja de depender de un único `✓`: activa el control Female de forma precisa, recorre el cuadro completo y todos los días disponibles, localiza contenedores mínimos con cuatro jugadoras y marcador, y acumula todos los partidos finalizados del torneo hasta la fecha actual.
