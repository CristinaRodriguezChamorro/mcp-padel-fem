# 🎾 MCP Pádel Femenino Español

Web personal para seguir de forma sencilla el **pádel femenino profesional**, con especial foco en las jugadoras españolas.

> **¿No sabes de informática?** Abre [`/como-funciona`](apps/mcp-server/static/como-funciona.html) en la web: explica todo con una tienda de barrio, sin palabras raras.

## Qué incluye

- 📰 **Noticias** de pádel femenino sobre las jugadoras del Top 20.
- 🔴 **En juego**: torneo actual, con enlace al marcador oficial.
- 🏆 **Ranking** femenino (hasta el Top 20, según lo que publique la fuente).
- 📅 **Torneos** actuales y próximos.
- 👑 **Qué usan**: material (pala, ropa, calzado) que se ve usar a algunas jugadoras, sin ningún dato de contratos.
- 💡 **¿Sabías que?**: reglas, historia y leyendas del pádel femenino
- 📱 Diseño adaptado a escritorio y móvil.
- 🤖 **MCP por HTTP** para consultarlo hablando con una IA (ver más abajo).

## Cómo funciona

La aplicación obtiene información de fuentes públicas con licencia o pensadas para ello, procesa los datos y los sirve mediante una API construida con **FastAPI**. **No hace scraping.**

| Dato | De dónde sale |
|---|---|
| Ranking, calendario y resultados | **Padel API** (padelapi.org) si hay `PADELAPI_TOKEN`: datos reales con licencia, incluidas las semifinales de cada torneo. Sin token, o si falla y no hay datos buenos guardados, la API de **Wikipedia** (página *Premier Padel <año>*, CC BY-SA 4.0), con retraso | Una vez al día |
| Noticias | Solo los **feeds RSS** que los medios publican, con una **regla por fuente** según lo que permiten sus condiciones: OKDiario (licencia Creative Commons no comercial) → resumen propio, con IA opcional, autor y enlace; Padel Addict → extracto literal corto y enlace; Marca, Mundo Deportivo, Padel Spain y Set Padel Magazine → solo titular y enlace. Cualquier fuente nueva empieza en «solo titular y enlace» hasta leer sus condiciones |
| Fotos de jugadoras | Wikipedia y Wikimedia Commons, **solo las que se pueden acreditar** (autor y licencia comprobados); cada foto lleva su crédito y hay una página de créditos |
| Marcadores en directo | **No se reproducen**: se enlaza al marcador oficial de la FIP |

### Padel API (la fuente principal)

[Padel API](https://padelapi.org) es un servicio comercial con datos de Premier Padel (Major, P1, P2 y Finals) y un **plan gratuito**: ranking mundial actual, calendario y resultados de los últimos 6 meses, 50.000 peticiones al mes (10 por minuto, 2.000 al día), sin tarjeta. Cuadros completos desde 2023, de donde salen las semifinalistas (las terceras).

1. Crea la cuenta y el token en <https://padelapi.org> (gratis).
2. Guárdalo **solo** como variable de entorno en Railway: `PADELAPI_TOKEN`. Nunca en el repositorio ni en un chat.
3. La tarea diaria lo usa: unas pocas peticiones, con una pausa de ~6,5 s entre ellas, y un torneo ya completado no se vuelve a pedir.

Sus [condiciones](https://padelapi.org/terms-of-service) permiten usar los datos en tu propia aplicación y guardar las respuestas; **no** permiten revenderlos como producto, copiar su base de datos ni extraerla en masa. Por eso el MCP ofrece respuestas filtradas y limitadas (no un paso directo de la API), las fotos de la API no se usan (no tienen licencia) y la web cita la fuente. Si un día el MCP se hace muy público, conviene escribirles para confirmar el uso.

### Por qué Padel API/Wikipedia y no la web de la FIP

Las condiciones de uso de la web de la FIP prohíben los robots y la extracción de datos, y copiar o retransmitir su contenido sin licencia escrita. Por eso esta aplicación **no lee la web de la FIP** (ni el ranking, ni el calendario, ni los marcadores). Si algún día la FIP concede una licencia, se integrará su fuente con las condiciones que fije.

### Límites que debes conocer

- Wikipedia la mantienen voluntarios: el ranking y los resultados pueden ir con **semanas de retraso**. Cada archivo de datos guarda la fecha que indica la propia página, y la web y el MCP avisan cuando pasan de 14 días.
- Solo se publican las **finales femeninas** de cada torneo, no los partidos de rondas previas.
- La tabla de ranking de Wikipedia puede tener menos de 20 filas.
- Antes de guardar, los datos se validan; si algo no cuadra se conservan los anteriores (el motivo queda en `/api/data-status`).

### En local

La actualización reescribe `data/ranking.json` y `data/calendario.json` (los de `data/` son los datos de arranque). Para no tocarlos, apunta `DATA_DIR` a otra carpeta o arranca con `WIKIPEDIA_INGEST=off`.

### Atribución

Ranking y calendario: [Wikipedia](https://en.wikipedia.org/wiki/Premier_Padel_2026), bajo licencia [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). La web muestra este crédito al pie. El ranking original lo publica la FIP.

## Stack

- Python
- FastAPI
- FastMCP / MCP
- feedparser
- HTML, CSS y JavaScript
- Groq + Llama para resúmenes de noticias
- Docker
- Railway

## Estructura básica

```text
apps/mcp-server/
├── server.py          # LA WEB (/api/*, páginas) y la tarea de actualización; solo monta el MCP
├── padel_mcp/         # EL MCP (nada de web): lo que habla con cualquier modelo
│   ├── tools.py       #   las herramientas y la puerta de acceso (límites, token, GitHub)
│   └── auth.py        #   verificación de tokens de GitHub
├── data/              # ranking.json, calendario.json (los reescribe la tarea de actualización)
├── tools/             # capa de datos COMPARTIDA por la web y el MCP (no son herramientas del MCP)
│   ├── padelapi_source.py   # fuente principal: Padel API (con token, a ritmo y sin repetir peticiones)
│   ├── wikipedia_source.py  # respaldo: lee la página de la temporada (API de MediaWiki)
│   ├── ingest.py            # elige la fuente, valida y guarda; actualización diaria
│   ├── local_data.py        # lee data/*.json (sin red)
│   ├── news.py              # noticias por RSS + filtro Top 20
│   ├── photos.py            # autor y licencia de cada foto (sin crédito, no hay foto)
│   ├── players.py           # reconoce jugadoras del Top 20 en un texto
│   └── http_policy.py       # User-Agent honesto, robots.txt y ritmo por dominio
├── static/
│   ├── index.html
│   └── como-funciona.html   # la arquitectura explicada para todos
├── tests/
├── Dockerfile
└── requirements.txt
```

## Endpoints principales

```text
GET /api/news
GET /api/live          torneo en curso (sin marcadores) y enlace al marcador oficial
GET /api/ranking
GET /api/calendar
GET /api/data-status   fuente, antigüedad, última actualización y estado de las fotos
GET /creditos-fotos    autor y licencia de cada foto mostrada
GET /como-funciona     la arquitectura explicada para todos
```

> En despliegue se recomienda utilizar el `Dockerfile` incluido en el proyecto.

## MCP (para hablar con una IA)

El servidor expone un **MCP por HTTP** en `/ai/mcp` (Streamable HTTP, solo lectura), en el mismo servicio que la web y compartiendo sus datos.

**Las tres herramientas por defecto:**

| Herramienta | Qué devuelve |
|---|---|
| `ranking_femenino` | El ranking: posición, nombre, país y puntos de cada jugadora (por defecto el top 10, hasta 20) |
| `ultimo_torneo` | El último torneo terminado: dónde fue, qué tipo de torneo es, quién jugó la final, primeras, segundas y terceras, el resultado final y una frase que lo resume |
| `proximo_torneo` | El próximo torneo: nombre, ciudad y país, fechas, días que faltan, tipo de torneo y dónde verlo; si hay uno en juego, también ese |

**Herramientas extra** (se activan con `MCP_TOOLS=extended`):

| Herramienta | Para qué |
|---|---|
| `jugadora` | Ficha de una jugadora: ranking, finales jugadas y noticias que la mencionan |
| `comparar_jugadoras` | Dos jugadoras lado a lado |
| `torneo_actual` | Torneo en juego y enlace al marcador oficial |
| `resultado_torneo` | Ficha de la final femenina de un torneo ya jugado: dónde fue, tipo de torneo (Major, P1, P2), quién jugó, primeras, segundas y terceras, y resultado final. Si un dato falta (las terceras, que la fuente no recoge) lo dice en una nota |
| `resultados_recientes` | Finales femeninas ya jugadas, con filtro opcional por jugadora |
| `calendario` | Próximos torneos |
| `noticias` | Noticias con enlace a la fuente (según la fuente: resumen, extracto literal o solo titular), con filtro opcional por jugadora |
| `resumen_del_dia` | Panorama rápido |
| `estado_de_los_datos` | Fuente, antigüedad y última actualización de cada dato |

Cada respuesta indica la fuente, la licencia, la antigüedad y los avisos (datos de ejemplo o con retraso).

### Quién puede conectarse (`MCP_AUTH`)

| Modo | Cómo se activa | Qué exige | Para qué |
|---|---|---|---|
| **GitHub** | `MCP_AUTH=github` | Un token de GitHub en `Authorization: Bearer <token>` | VS Code, Claude Code… con identificación por cuenta |
| **Token fijo** | `MCP_TOKEN=...` | Ese token | Uso personal sencillo |
| **Abierto** | nada | Nada (solo límite de peticiones) | Conectores de claude.ai |

Si `MCP_AUTH` tiene un valor desconocido o contradictorio, el servidor **se niega a arrancar** en lugar de abrirse por error. Si no defines nada, el endpoint queda **abierto** (y lo avisa al arrancar).

**Modo GitHub.** El servidor pregunta a GitHub de quién es el token (`GET https://api.github.com/user`) y deja pasar a esa cuenta:

- `MCP_GITHUB_USERS=cris,otra` limita el acceso a esas cuentas; vacío = cualquier cuenta de GitHub válida.
- Cada cuenta tiene su propio límite de uso (`MCP_USER_RATE_PER_MIN`), además del límite por IP.
- El resultado se guarda unos minutos en memoria (solo una huella SHA-256, nunca el token) y las comprobaciones contra GitHub están limitadas.
- El token **no necesita ningún permiso**: crea uno de solo identidad y con caducidad corta (*GitHub → Settings → Developer settings → Fine-grained personal access tokens*).
- El servidor no guarda el token ni lo escribe en los registros. Aun así, quien administre el servidor podría leerlo en memoria: usa tokens sin permisos.
- ⚠️ No sirve para los conectores de claude.ai, que no envían una cabecera fija.

### Conectar desde VS Code (con Copilot)

1. Despliega el servidor con `MCP_AUTH=github` y `MCP_GITHUB_USERS=tu-usuario`.
2. Crea tu token de GitHub sin permisos.
3. Copia `examples/vscode-mcp.json` a `.vscode/mcp.json` (un proyecto) o a tu configuración de usuario con **MCP: Open User Configuration** (todos), y pon tu URL.
4. VS Code te pedirá el token al arrancar el servidor, con la entrada oculta.
5. Usa el chat de Copilot en modo **Agent**: *"usa el MCP de pádel femenino: ¿cómo va Delfina Brea?"*.

Si tu organización gestiona Copilot, puede que un administrador tenga que habilitar los servidores MCP.

## Noticias centradas en el Top 20

Solo se publican noticias que hablan de alguna jugadora del Top 20: nombre completo → pasa; solo apellido → pasa si además hay señal femenina o se citan dos jugadoras; apellidos repetidos (p. ej. dos Sánchez) → no se atribuyen salvo que aparezca el nombre; una noticia genérica sin ninguna jugadora del Top 20 no se publica. Cada noticia lleva el campo `jugadoras`. Como solo se usan el título y el extracto del RSS, habrá pocas noticias.

## Variables de entorno

| Variable | Para qué | Por defecto |
|---|---|---|
| `GROQ_API_KEY` | Resúmenes de noticias con IA (sin ella, resumen determinista) | — |
| `PADELAPI_TOKEN` | Token de Padel API: activa la fuente principal de datos (¡solo como variable del servidor!) | — |
| `PADELAPI_INGEST` | `off` desactiva Padel API aunque haya token | activa |
| `PADELAPI_MIN_INTERVAL` | Segundos entre peticiones a Padel API | `6.5` |
| `MCP_TOOLS` | `extended` añade las herramientas extra (jugadora, noticias, calendario...) | solo las tres |
| `WIKIPEDIA_INGEST` | `off` desactiva toda la actualización de datos (Padel API y Wikipedia) | activa |
| `WIKIPEDIA_INGEST_HOURS` | Horas entre actualizaciones correctas (mínimo 1) | `24` |
| `DATA_DIR` | Carpeta de los archivos de datos | `apps/mcp-server/data` |
| `NEWS_RSS_ENABLED` / `NEWS_RSS_FEEDS` | Apagar las noticias / sustituir la lista de feeds (URLs separadas por comas; los que no estén en la tabla de política se tratan como «solo titular y enlace») | activas |
| `PLAYER_PHOTOS` | `off` apaga las fotos | activas |
| `MCP_AUTH` / `MCP_GITHUB_USERS` / `MCP_TOKEN` | Acceso al MCP (ver arriba) | abierto |
| `MCP_RATE_PER_MIN` / `MCP_USER_RATE_PER_MIN` / `MCP_GLOBAL_RATE_PER_MIN` | Límites de peticiones al MCP | `120` / `120` / `600` |
| `SCRAPER_CONTACT`, `SCRAPER_INFO_URL`, `SCRAPER_USER_AGENT`, `SCRAPER_MIN_INTERVAL` | Identificación honesta del programa que lee Wikipedia y los feeds | — |

## Uso responsable de las fuentes

- El programa se identifica como `PadelFemBot/1.0 (+URL del repo)`, respeta `robots.txt` en los feeds y espera entre peticiones al mismo dominio. A Wikipedia le pide **una página al día**.
- **Fotos con crédito obligatorio.** Las fotos de Wikimedia son de licencia libre, pero casi todas (CC BY, CC BY-SA) exigen citar autor y licencia. Una foto solo se muestra si se ha podido comprobar su autor y su licencia (solo se aceptan CC0, dominio público, CC BY y CC BY-SA); ante la duda, o si Wikimedia no responde, se muestran las iniciales. El crédito sale al pasar el ratón por la foto y en [`/creditos-fotos`](/creditos-fotos), enlazada al pie de la web. El proxy solo descarga dominios de Wikimedia y solo fotos ya acreditadas.
- **Qué usan** y **¿Sabías que?** son textos escritos a mano en la web. «Qué usan» no contiene datos de contratos y avisa de que el material no está verificado con las marcas; si algún día cobras de una marca o añades enlaces de afiliación, pasa a ser publicidad y las fuentes con licencia no comercial (OKDiario) dejarían de valer.
- Las condiciones de OKDiario exigen **atribución con enlace directo, sin `nofollow`**, a cada artículo; la web ya lo hace. Releélas de vez en cuando (la última versión leída es del 16 de junio de 2026).
- El **historial de git** conserva el código de scraping anterior. Si vas a hacer público el repositorio y eso te preocupa, considera empezar uno nuevo o reescribir el historial (`git filter-repo`).

Si eres titular de una web usada como fuente y prefieres que no se acceda a ella, escribe al contacto del repositorio y se retirará.

## Pruebas (sin internet)

```text
cd apps/mcp-server
python -m unittest discover -s tests -v
```

Incluyen una barrera que falla si el código apunta a un dominio que no esté en la lista permitida o si se reintroduce una librería de scraping.

## Objetivo

El proyecto sirve como caso práctico para combinar **APIs, fuentes abiertas, procesamiento asíncrono, IA y Model Context Protocol (MCP)** en una aplicación real.
